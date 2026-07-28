#!/usr/bin/env python3
"""Bright Data ingest — the real data behind every bar.

Two layers, because the app joins two datasets:

  Web Unlocker   -> official BattleBots fight records and bot stats.
                    Single request in, unblocked HTML out. There is no
                    pre-built scraper for these pages, so this is the tool.

  Scraper Library -> Reddit / YouTube fan chatter, via dataset triggers.
                    Batch jobs run ASYNC: you get a snapshot_id back and
                    poll it. Fire these FIRST and let them cook while you
                    build; don't sit watching a progress bar.

Usage:
    export BRIGHTDATA_API_TOKEN=...            # or ~/.claude/secrets/brightdata.json
    python3 ingest/brightdata.py chatter-trigger    # fire async jobs, get snapshot ids
    python3 ingest/brightdata.py chatter-collect    # poll + write data/cache/chatter.json
    python3 ingest/brightdata.py fights             # Web Unlocker -> data/cache/fights.json
    python3 ingest/brightdata.py status
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import store  # noqa: E402

API = "https://api.brightdata.com"
UNLOCKER_ZONE = os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "mcp_unlocker")

# Scraper Library dataset ids. Grab these from the dataset's page in the
# Bright Data console — they are per-scraper and start with `gd_`.
DATASETS = {
    "reddit": os.environ.get("BRIGHTDATA_DATASET_REDDIT", ""),
    "youtube": os.environ.get("BRIGHTDATA_DATASET_YOUTUBE", ""),
}

SNAPSHOTS = store.RAW / "snapshots.json"

# Cheap lexicon sentiment. Deliberately simple and inspectable — we show the
# raw post next to the score in the UI, so a judge can check our working.
POS = set("""great best amazing incredible insane beast dominant clean brutal
legend legendary goat unstoppable perfect love loved favorite favourite king
destroyed demolished savage elite underrated clutch impressive deserved""".split())
NEG = set("""boring overrated disappointing weak broken bad worst awful trash
lucky robbed cheated sloppy embarrassing washed mid slow useless overhyped
predictable""".split())


def token():
    t = os.environ.get("BRIGHTDATA_API_TOKEN")
    if t:
        return t
    path = pathlib.Path.home() / ".claude" / "secrets" / "brightdata.json"
    if path.exists():
        with open(path) as fh:
            return json.load(fh)["api_token"]
    raise SystemExit(
        "No Bright Data token. Set BRIGHTDATA_API_TOKEN or write "
        "~/.claude/secrets/brightdata.json as {\"api_token\": \"...\"}"
    )


def headers():
    return {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sentiment(text):
    words = re.findall(r"[a-z']+", (text or "").lower())
    p = sum(w in POS for w in words)
    n = sum(w in NEG for w in words)
    if not (p or n):
        return 0.0
    return round((p - n) / (p + n), 3)


# ------------------------------------------------------------ Web Unlocker

def unlock(url, fmt="raw"):
    """One request in, unblocked page out. Handles the anti-bot stuff for us."""
    r = requests.post(
        f"{API}/request",
        headers=headers(),
        json={"zone": UNLOCKER_ZONE, "url": url, "format": fmt},
        timeout=120,
    )
    r.raise_for_status()
    return r.text


def fights(sources):
    """Pull fight tables via Web Unlocker and cache the raw HTML.

    Parsing is deliberately separate (parse_fights) so a page-shape change
    is a parser fix, not a re-scrape — we already paid for the bytes.
    """
    raw = {}
    for name, url in sources.items():
        print(f"  unlocking {name} ...", flush=True)
        try:
            html = unlock(url)
            path = store.RAW / f"fights_{name}.html"
            path.write_text(html)
            raw[name] = {"url": url, "path": str(path), "bytes": len(html)}
            print(f"    {len(html):,} bytes -> {path.name}")
        except requests.RequestException as exc:
            print(f"    FAILED: {exc}")
    with open(store.RAW / "fights_sources.json", "w") as fh:
        json.dump({"fetched_at": now(), "sources": raw}, fh, indent=2)
    print("\nRaw HTML cached. Now write a parser into parse_fights() for the "
          "page shape you actually got, then run: python3 ingest/brightdata.py parse")
    return raw


def parse_fights():
    """Turn cached HTML into fight records.

    Fill this in against the real page once you see it. Every record MUST
    carry a real source_url — that is what flips the UI banner from red
    PLACEHOLDER to green, and it is checked in store.provenance().
    """
    out = []
    for path in sorted(store.RAW.glob("fights_*.html")):
        html = path.read_text()
        # TODO: parse `html` into records shaped like:
        #   {"id","season","episode","red","blue","winner","method","time",
        #    "source":"brightdata","source_url": <the page URL>}
        _ = html
    if not out:
        print("parse_fights() has no parser yet — inspect data/raw/fights_*.html "
              "and fill it in. Until then the app runs on PLACEHOLDER seed data.")
        return None
    store.write_cache("fights", {"fights": out, "ingested_at": now()})
    print(f"wrote {len(out)} real fight records")
    return out


# -------------------------------------------------------- Scraper Library

def trigger(dataset_id, inputs):
    """Async batch job. Returns a snapshot_id to poll later."""
    r = requests.post(
        f"{API}/datasets/v3/trigger",
        headers=headers(),
        params={"dataset_id": dataset_id, "include_errors": "true"},
        json=inputs,
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["snapshot_id"]


def progress(snapshot_id):
    r = requests.get(f"{API}/datasets/v3/progress/{snapshot_id}",
                     headers=headers(), timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_snapshot(snapshot_id):
    r = requests.get(f"{API}/datasets/v3/snapshot/{snapshot_id}",
                     headers=headers(), params={"format": "json"}, timeout=300)
    r.raise_for_status()
    return r.json()


def chatter_trigger():
    """Fire the batch jobs and walk away. Snapshots live 30 days."""
    bots = store.bots()
    if not bots:
        raise SystemExit("no bots — run ingest/seed.py first")

    snaps = {}
    for platform, dataset_id in DATASETS.items():
        if not dataset_id:
            print(f"  skip {platform}: no dataset id "
                  f"(set BRIGHTDATA_DATASET_{platform.upper()})")
            continue
        inputs = [{"keyword": f"BattleBots {b['name']}"} for b in bots]
        print(f"  triggering {platform}: {len(inputs)} keywords ...", flush=True)
        try:
            sid = trigger(dataset_id, inputs)
            snaps[platform] = sid
            print(f"    snapshot_id = {sid}")
        except requests.RequestException as exc:
            print(f"    FAILED: {exc}")

    if snaps:
        with open(SNAPSHOTS, "w") as fh:
            json.dump({"triggered_at": now(), "snapshots": snaps}, fh, indent=2)
        print(f"\n{len(snaps)} job(s) cooking. Go build. Collect with: "
              "python3 ingest/brightdata.py chatter-collect")
    return snaps


def chatter_collect(wait=False):
    if not SNAPSHOTS.exists():
        raise SystemExit("no snapshots — run chatter-trigger first")
    with open(SNAPSHOTS) as fh:
        snaps = json.load(fh)["snapshots"]

    posts = []
    index = {b["name"].lower(): b["slug"] for b in store.bots()}

    for platform, sid in snaps.items():
        while True:
            st = progress(sid).get("status")
            print(f"  {platform} [{sid}]: {st}")
            if st == "ready":
                break
            if st in ("failed", "canceled"):
                print(f"    giving up on {platform}")
                break
            if not wait:
                print("    not ready — re-run later, or pass --wait")
                break
            time.sleep(20)
        else:
            continue

        if progress(sid).get("status") != "ready":
            continue

        rows = fetch_snapshot(sid)
        (store.RAW / f"chatter_{platform}.json").write_text(json.dumps(rows, indent=2))
        print(f"    {len(rows)} rows -> data/raw/chatter_{platform}.json")

        for i, row in enumerate(rows):
            text = row.get("description") or row.get("comment") or row.get("title") or ""
            blob = f"{row.get('keyword','')} {text}".lower()
            slug = next((s for name, s in index.items() if name in blob), None)
            if not slug:
                continue
            posts.append({
                "id": f"{platform}-{i}",
                "platform": platform,
                "bot": slug,
                "text": text[:500],
                "score": int(row.get("num_upvotes") or row.get("likes") or 0),
                "sentiment": sentiment(text),
                "url": row.get("url") or row.get("post_url"),
                "source": "brightdata",
            })

    if posts:
        store.write_cache("chatter", {"posts": posts, "ingested_at": now()})
        print(f"\nwrote {len(posts)} real posts across "
              f"{len({p['bot'] for p in posts})} bots")
    else:
        print("\nno posts matched a bot — check keyword→slug matching above")
    return posts


def status():
    print("token:", "set" if os.environ.get("BRIGHTDATA_API_TOKEN") or
          (pathlib.Path.home() / ".claude/secrets/brightdata.json").exists() else "MISSING")
    print("unlocker zone:", UNLOCKER_ZONE)
    for k, v in DATASETS.items():
        print(f"dataset {k}:", v or "MISSING")
    print("provenance:", json.dumps(store.provenance(), indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fights", "parse", "chatter-trigger",
                                    "chatter-collect", "status"])
    ap.add_argument("--wait", action="store_true", help="block until snapshots are ready")
    ap.add_argument("--url", action="append", default=[],
                    help="fight-record page URL (repeatable)")
    args = ap.parse_args()

    if args.cmd == "status":
        status()
    elif args.cmd == "fights":
        if not args.url:
            raise SystemExit("give at least one --url (the official fight-record page)")
        fights({f"src{i}": u for i, u in enumerate(args.url)})
    elif args.cmd == "parse":
        parse_fights()
    elif args.cmd == "chatter-trigger":
        chatter_trigger()
    elif args.cmd == "chatter-collect":
        chatter_collect(wait=args.wait)


if __name__ == "__main__":
    main()
