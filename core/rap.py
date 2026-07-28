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

import requests

from . import facts, store

BATTLES = store.ROOT / "data" / "battles"
BATTLES.mkdir(parents=True, exist_ok=True)

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


def battle(a_slug, b_slug, backend="cached", force=False):
    """Get a battle. Defaults to disk — the stage never waits on an API."""
    path = cache_path(a_slug, b_slug)
    if not force and path.exists():
        with open(path) as fh:
            return json.load(fh)

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
    for p in sorted(BATTLES.glob("*.json")):
        try:
            with open(p) as fh:
                d = json.load(fh)
            out.append({"a": d["a"], "b": d["b"], "bars": len(d.get("bars", []))})
        except (json.JSONDecodeError, KeyError):
            continue
    return out
