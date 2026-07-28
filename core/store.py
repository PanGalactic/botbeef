"""Disk-backed data store.

Doctrine: scrape once, cache to disk, never touch the network on stage.
Everything the app serves during a demo comes from data/cache/.
"""
import json
import os
import pathlib
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "cache"
SEED = ROOT / "data" / "seed"

for d in (RAW, CACHE, SEED):
    d.mkdir(parents=True, exist_ok=True)


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_cache(name, payload):
    path = CACHE / f"{name}.json"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, path)
    return path


def load(name):
    """Prefer real ingested cache; fall back to seed so the app always boots."""
    hit = _read(CACHE / f"{name}.json", None)
    if hit is not None:
        return hit
    return _read(SEED / f"{name}.json", {})


def bots():
    return load("bots").get("bots", [])


def fights():
    return load("fights").get("fights", [])


def chatter():
    return load("chatter").get("posts", [])


def bot_index():
    return {b["slug"]: b for b in bots()}


def is_http_url(value):
    """Return whether *value* is a usable absolute HTTP(S) URL."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value or any(char.isspace() for char in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def record_source_url(record):
    """Read the canonical source URL while preserving legacy ``url`` records."""
    for field in ("source_url", "url"):
        value = record.get(field)
        if is_http_url(value):
            return value.strip()
    return None


def provenance():
    """Is what we're serving real, ingested data or placeholder seed?

    The UI renders a loud banner off this. It only goes green when every
    fight and fan-comment record carries a usable HTTP(S) source URL.
    """
    fs = fights()
    ps = chatter()

    def sourced(record):
        return (
            str(record.get("source") or "").upper() != "PLACEHOLDER"
            and record_source_url(record) is not None
        )

    real_fights = [f for f in fs if sourced(f)]
    real_posts = [p for p in ps if sourced(p)]
    return {
        "fights_total": len(fs),
        "fights_real": len(real_fights),
        "posts_total": len(ps),
        "posts_real": len(real_posts),
        "fights_missing_source_url": len(fs) - len(real_fights),
        "posts_missing_source_url": len(ps) - len(real_posts),
        "is_real": bool(fs) and len(real_fights) == len(fs)
        and bool(ps) and len(real_posts) == len(ps),
        "fights_ingested_at": load("fights").get("ingested_at"),
        "chatter_ingested_at": load("chatter").get("ingested_at"),
    }
