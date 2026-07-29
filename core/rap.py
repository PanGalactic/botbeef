"""The rap battle generator.

The model never freestyles. It gets a numbered list of facts scraped from
real BattleBots data and must cite one on every single bar. Any bar that
cites nothing — or cites a fact id that doesn't exist — is rejected before
it reaches the stage.

Backends, in order of what we actually use:
  cached    — disk. What the stage demo runs on. Zero network.
  anthropic — quality. What we pre-generate with.
  cerebras  — speed. Live generation on stage if someone shouts a matchup.
"""
import json
import os
import pathlib
import re
from copy import deepcopy

import requests

from . import facts, store

BATTLES = store.ROOT / "data" / "battles"
BATTLES.mkdir(parents=True, exist_ok=True)
AUDIO = store.ROOT / "audio"

EXPECTED_BAR_COUNT = 16
PROVENANCE_FIELDS = (
    "fights_total",
    "fights_real",
    "posts_total",
    "posts_real",
    "fights_ingested_at",
    "chatter_ingested_at",
)

BAR_SCHEMA = {
    "type": "object",
    "properties": {
        "bars": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "bot": {"type": "string"},
                    "text": {"type": "string"},
                    "fact_id": {"type": "string"},
                },
                "required": ["bot", "text", "fact_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["bars"],
    "additionalProperties": False,
}

SYSTEM = """You are the battle-rap writer for BOT BEEF, a BattleBots hack-night app.

You will be given two robots and a numbered list of FACTS scraped from real
BattleBots data. Write a rap battle: alternating verses, 4 bars per bot per
round, 2 rounds — 16 bars total, starting with bot A.

Hard rules, in order of importance:
1. EVERY bar cites exactly one fact by its id. No exceptions.
2. A bar may only claim something the cited fact actually supports. Do not
   invent fights, seasons, scores, or quotes. If a fact says a bot is 9-4,
   you may not say it is undefeated.
3. Bars that diss the opponent should cite a fact about the OPPONENT.
   Bars that brag should cite a fact about the bot speaking.
4. Punchlines land on the numbers. "You're 3-8 and the crowd still chants
   your name" is a better bar than a generic insult.
5. Keep each bar to roughly 8-16 words so it reads on a projector and speaks
   cleanly through TTS.
6. Trash talk is in-character robot bravado — combat, weapons, scrap metal,
   the arena. Keep it about the machines. No slurs, no real-person insults,
   nothing about the human teams.

Return JSON matching the schema. `bot` is the slug of the bot speaking."""


def _prompt(a, b, fact_list, ctx):
    lines = [f"{f['id']}: {f['text']}" for f in fact_list]
    return (
        f"BOT A: {ctx['a'].get('name', a)} (slug: {a})\n"
        f"BOT B: {ctx['b'].get('name', b)} (slug: {b})\n\n"
        "FACTS:\n" + "\n".join(lines) + "\n\n"
        f"Write the battle. {ctx['a'].get('name', a)} goes first."
    )


def validate(bars, fact_list, a, b):
    """Drop any bar that doesn't cite a real fact. This is the whole point."""
    by_id = {f["id"]: f for f in fact_list}
    kept, rejected = [], []
    for bar in bars:
        fid = (bar.get("fact_id") or "").strip().upper()
        slug = bar.get("bot")
        fact = by_id.get(fid)
        candidate_urls = []
        if fact:
            candidate_urls = [
                fact.get("source_url"),
                *(fact.get("source_urls") or []),
            ]
        source_urls = list(dict.fromkeys(
            url.strip() for url in candidate_urls
            if store.is_http_url(url)
        ))
        if (
            slug not in (a, b)
            or not bar.get("text")
            or not fact
            or not source_urls
        ):
            rejected.append({**bar, "reason": "unsourced or malformed"})
            continue
        kept.append({
            "bot": slug,
            "text": bar["text"].strip(),
            "fact_id": fid,
            "fact": fact["text"],
            "source_url": source_urls[0],
            "source_urls": source_urls,
        })
    return kept, rejected


# ---------------------------------------------------------------- backends

def _anthropic(a, b, fact_list, ctx):
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": BAR_SCHEMA},
        },
        messages=[{"role": "user", "content": _prompt(a, b, fact_list, ctx)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined to write this battle")
    text = next(bl.text for bl in response.content if bl.type == "text")
    return json.loads(text)["bars"]


def _cerebras(a, b, fact_list, ctx):
    """Fast path — sub-second, for live matchups called from the room."""
    key = json.load(open(os.path.expanduser("~/.claude/secrets/cerebras.json")))["api_key"]
    r = requests.post(
        "https://api.cerebras.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "llama-3.3-70b",
            "messages": [
                {"role": "system", "content": SYSTEM + "\n\nReturn ONLY raw JSON."},
                {"role": "user", "content": _prompt(a, b, fact_list, ctx)},
            ],
            "temperature": 0.8,
            "max_tokens": 2000,
        },
        timeout=60,
    )
    r.raise_for_status()
    body = r.json()["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", body, re.S)
    return json.loads(match.group(0))["bars"]


BACKENDS = {"anthropic": _anthropic, "cerebras": _cerebras}


# ------------------------------------------------------------------- entry

def cache_path(a, b):
    return BATTLES / f"{'__'.join(sorted([a, b]))}.json"


def _audit_provenance(payload, current_provenance):
    embedded = (payload.get("context") or {}).get("provenance")
    if not isinstance(embedded, dict):
        return "missing", "battle has no embedded corpus provenance"
    if embedded.get("is_real") is not True:
        return "placeholder", "battle was generated from placeholder or unsourced data"
    if not isinstance(current_provenance, dict) or current_provenance.get("is_real") is not True:
        return "unverifiable", "current local corpus is not fully sourced"
    if any(embedded.get(key) != current_provenance.get(key) for key in PROVENANCE_FIELDS):
        return "stale", "battle provenance does not match the current local corpus"
    return "current", "battle matches the current fully sourced local corpus"


def _audit_audio(a, b, bar_count, audio_dir=None):
    audio_dir = pathlib.Path(audio_dir or AUDIO)
    path = audio_dir / f"manifest__{'__'.join(sorted([a, b]))}.json"
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except FileNotFoundError:
        return {
            "manifest": False,
            "complete": False,
            "clips_expected": bar_count,
            "clips_present": 0,
            "reason": "audio manifest is missing",
        }
    except (json.JSONDecodeError, OSError):
        return {
            "manifest": False,
            "complete": False,
            "clips_expected": bar_count,
            "clips_present": 0,
            "reason": "audio manifest is unreadable",
        }

    entries = manifest.get("bars")
    if not isinstance(entries, list):
        entries = []
    by_index = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        index, filename = entry.get("index"), entry.get("file")
        if (
            isinstance(index, int)
            and 0 <= index < bar_count
            and isinstance(filename, str)
            and filename
            and pathlib.Path(filename).name == filename
            and (audio_dir / filename).is_file()
        ):
            by_index[index] = filename
    intro = manifest.get("intro")
    intro_present = (
        isinstance(intro, str)
        and bool(intro)
        and pathlib.Path(intro).name == intro
        and (audio_dir / intro).is_file()
    )
    complete = (
        bar_count > 0
        and len(by_index) == bar_count
        and set(by_index) == set(range(bar_count))
        and intro_present
    )
    return {
        "manifest": True,
        "complete": complete,
        "clips_expected": bar_count,
        "clips_present": len(by_index),
        "intro_present": intro_present,
        "reason": "complete" if complete else "manifest has missing or invalid audio files",
    }


def audit_cached(payload, current_provenance=None, audio_dir=None):
    """Audit a cached battle using local data only."""
    errors = []
    if not isinstance(payload, dict):
        payload = {}
        errors.append("battle payload is not an object")

    a, b = payload.get("a"), payload.get("b")
    if not isinstance(a, str) or not a.strip():
        errors.append("battle is missing robot a")
    if not isinstance(b, str) or not b.strip():
        errors.append("battle is missing robot b")
    if isinstance(a, str) and isinstance(b, str) and a == b:
        errors.append("battle robots must be different")

    facts_list = payload.get("facts")
    if not isinstance(facts_list, list):
        facts_list = []
        errors.append("battle facts are missing or malformed")
    fact_by_id = {
        fact.get("id"): fact
        for fact in facts_list
        if isinstance(fact, dict) and isinstance(fact.get("id"), str)
    }

    bars = payload.get("bars")
    if not isinstance(bars, list):
        bars = []
        errors.append("battle bars are missing or malformed")
    elif len(bars) != EXPECTED_BAR_COUNT:
        errors.append(f"battle has {len(bars)} bars; expected {EXPECTED_BAR_COUNT}")

    for index, bar in enumerate(bars):
        if not isinstance(bar, dict):
            errors.append(f"bar {index} is malformed")
            continue
        fact_id = bar.get("fact_id")
        fact = fact_by_id.get(fact_id)
        bar_urls = [
            bar.get("source_url"),
            *(bar.get("source_urls") or []),
        ]
        valid_bar_urls = {
            url.strip() for url in bar_urls if store.is_http_url(url)
        }
        fact_urls = set()
        if fact:
            fact_urls = {
                url.strip()
                for url in [
                    fact.get("source_url"),
                    *(fact.get("source_urls") or []),
                ]
                if store.is_http_url(url)
            }
        if bar.get("bot") not in (a, b):
            errors.append(f"bar {index} has an invalid speaker")
        if not isinstance(bar.get("text"), str) or not bar["text"].strip():
            errors.append(f"bar {index} has no text")
        if not fact:
            errors.append(f"bar {index} cites an unknown fact")
        elif (
            not valid_bar_urls
            or not fact_urls
            or valid_bar_urls.isdisjoint(fact_urls)
        ):
            errors.append(f"bar {index} has no citation backed by its fact")

    current_provenance = (
        store.provenance() if current_provenance is None else current_provenance
    )
    provenance_status, provenance_reason = _audit_provenance(
        payload, current_provenance
    )
    audio = (
        _audit_audio(a, b, len(bars), audio_dir)
        if isinstance(a, str) and isinstance(b, str) and a and b
        else {
            "manifest": False,
            "complete": False,
            "clips_expected": len(bars),
            "clips_present": 0,
            "reason": "battle identity is invalid",
        }
    )
    valid = not errors
    ready = valid and provenance_status == "current" and audio["complete"]
    return {
        "valid": valid,
        "ready": ready,
        "playable": ready,
        "bar_count": len(bars),
        "provenance": {
            "status": provenance_status,
            "reason": provenance_reason,
        },
        "audio": audio,
        "errors": errors,
    }


def cached_summary(payload, current_provenance=None, audio_dir=None):
    """Return a compact API-safe description of one cached battle."""
    audit = audit_cached(payload, current_provenance, audio_dir)
    return {
        "a": payload.get("a") if isinstance(payload, dict) else None,
        "b": payload.get("b") if isinstance(payload, dict) else None,
        "bars": audit["bar_count"],
        "valid": audit["valid"],
        "ready": audit["ready"],
        "playable": audit["playable"],
        "provenance": audit["provenance"],
        "audio": audit["audio"],
        "error_count": len(audit["errors"]),
        "errors": audit["errors"][:3],
    }


def _cached_for_request(payload, a_slug, b_slug):
    stored_pair = {payload.get("a"), payload.get("b")}
    requested_pair = {a_slug, b_slug}
    if len(requested_pair) != 2 or stored_pair != requested_pair:
        raise ValueError("cached battle does not match the requested robots")
    result = deepcopy(payload)
    result["orientation"] = {
        "requested_a": a_slug,
        "requested_b": b_slug,
        "a": result["a"],
        "b": result["b"],
        "normalized": (result["a"], result["b"]) != (a_slug, b_slug),
    }
    return result


def battle(a_slug, b_slug, backend="cached", force=False):
    """Get a battle. Defaults to disk — the stage never waits on an API."""
    path = cache_path(a_slug, b_slug)
    if not force and path.exists():
        try:
            with open(path, encoding="utf-8") as fh:
                cached = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"cached battle is unreadable: {path.name}") from exc
        audit = audit_cached(cached)
        if not audit["ready"]:
            reasons = list(audit["errors"])
            if audit["provenance"]["status"] != "current":
                reasons.append(audit["provenance"]["reason"])
            if not audit["audio"]["complete"]:
                reasons.append(audit["audio"]["reason"])
            suffix = f"; plus {len(reasons) - 3} more errors" if len(reasons) > 3 else ""
            raise ValueError(
                f"cached battle is not trusted: {'; '.join(reasons[:3])}{suffix}"
            )
        return _cached_for_request(cached, a_slug, b_slug)

    if backend == "cached":
        raise FileNotFoundError(
            f"no cached battle for {a_slug} vs {b_slug} — pre-generate it with "
            f"`python -m ingest.pregen {a_slug} {b_slug}`"
        )

    fact_list, ctx = facts.for_matchup(a_slug, b_slug)
    if not fact_list:
        raise ValueError(f"no facts for {a_slug} vs {b_slug} — ingest data first")

    raw = BACKENDS[backend](a_slug, b_slug, fact_list, ctx)
    kept, rejected = validate(raw, fact_list, a_slug, b_slug)

    result = {
        "a": a_slug,
        "b": b_slug,
        "names": {a_slug: ctx["a"].get("name"), b_slug: ctx["b"].get("name")},
        "bars": kept,
        "rejected": rejected,
        "facts": fact_list,
        "context": ctx,
        "backend": backend,
    }
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2)
    return result


def list_cached():
    out = []
    current_provenance = store.provenance()
    for p in sorted(BATTLES.glob("*.json")):
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            summary = cached_summary(d, current_provenance)
            if (
                isinstance(summary["a"], str)
                and summary["a"]
                and isinstance(summary["b"], str)
                and summary["b"]
                and summary["a"] != summary["b"]
            ):
                out.append(summary)
        except (json.JSONDecodeError, OSError):
            # Preserve the historic frontend-safe contract: only entries with
            # usable robot IDs are selectable. Direct reads still fail closed.
            continue
    return out
