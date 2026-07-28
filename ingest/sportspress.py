#!/usr/bin/env python3
"""The real fight table.

battlebots.com runs SportsPress Pro, and its WordPress REST API is open.
The fights table on each robot page is loaded client-side from it, which is
why a plain HTML scrape returns only CSS — the rows were never in the markup.

That API gives us the thing the whole app is built on:

    /wp-json/sportspress/v2/teams   -> every bot, with its /robot/<slug>/ link
    /wp-json/sportspress/v2/events  -> every fight, with per-team results
                                       shaped {"<team_id>": {"judgesscore": "W (KO)"}}

So the winner AND the method come straight from the official record. This is
what flips the provenance banner from red PLACEHOLDER to green.

Routes through Bright Data Web Unlocker when a token is configured (it
handles the rate limiting once you're pulling a few hundred records); falls
back to direct HTTP otherwise.

    python3 ingest/sportspress.py
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

BASE = "https://battlebots.com/wp-json/sportspress/v2"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}

# "W (KO)" / "L" / "W (JD)" — the letter is the outcome, the bracket the method.
RE_RESULT = re.compile(r'^\s*([WL])\s*(?:\(([^)]+)\))?', re.I)

# The bracket is free text entered by hand over ten seasons, so it arrives as
# "KO 2:08", "KO 1M9S", "JD 3-0", "JD: 3-0", "0:57"... Normalise to a method
# the scoring model understands plus the fight time, or the margin weighting
# silently falls through to its default for every single fight.
RE_TIME = re.compile(r'(\d{1,2})\s*[:M]\s*(\d{1,2})\s*S?', re.I)
RE_SECS = re.compile(r'^\s*(\d{1,3})\s*S\s*$', re.I)


def normalise_method(raw):
    """-> (method, time_str). method is 'KO' | 'JD' | 'UNKNOWN'."""
    s = (raw or "").strip().upper()

    t = None
    m = RE_TIME.search(s)
    if m:
        t = f"{int(m.group(1))}:{int(m.group(2)):02d}"
    else:
        m = RE_SECS.search(re.sub(r'^(KO|JD)[:\s]*', '', s))
        if m:
            t = f"0:{int(m.group(1)):02d}"

    if "KO" in s:
        return "KO", t
    if "JD" in s or re.search(r'\d\s*-\s*\d', s):
        return "JD", t
    # a bare time with no letter is a knockout — a judges' decision has no clock
    return ("KO", t) if t else ("UNKNOWN", None)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _bd():
    have = os.environ.get("BRIGHTDATA_API_TOKEN") or (
        pathlib.Path.home() / ".claude" / "secrets" / "brightdata.json").exists()
    if not have:
        return None
    from ingest import brightdata
    return brightdata


def fetch(url):
    bd = _bd()
    if bd:
        return json.loads(bd.unlock(url))
    r = requests.get(url, headers=UA, timeout=45)
    r.raise_for_status()
    return r.json()


def paged(endpoint, max_pages=12):
    """SportsPress 400s past the last page rather than returning []."""
    out = []
    for page in range(1, max_pages + 1):
        url = f"{BASE}/{endpoint}?per_page=100&page={page}"
        try:
            batch = fetch(url)
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            break
        if not batch:
            break
        out += batch
        print(f"    page {page}: +{len(batch)} (total {len(out)})", flush=True)
        if len(batch) < 100:
            break
        time.sleep(0.3)
    return out


def clean_name(raw):
    """Titles arrive as 'MaDCatTer (22)' — the number is a bracket seed."""
    return re.sub(r"\s*\(\d+\)\s*$", "", raw or "").strip()


def slug_from_link(link):
    m = re.search(r"/robot/([^/]+)/", link or "")
    if not m:
        return None
    # strip season suffixes so tombstone-2021 and tombstone-wcvii unify
    return re.sub(r"-(wcvii|wcvi|20\d\d|s\d)$", "", m.group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-roster", action="store_true",
                    help="don't replace bots.json, only add fights")
    args = ap.parse_args()

    print(f"source: {'Bright Data Web Unlocker' if _bd() else 'direct HTTP'}\n")

    print("teams:")
    teams = paged("teams")
    by_id, bots = {}, []
    for t in teams:
        name = clean_name((t.get("title") or {}).get("rendered"))
        slug = slug_from_link(t.get("link"))
        if not (name and slug):
            continue
        by_id[t["id"]] = {"slug": slug, "name": name}
        if slug not in {b["slug"] for b in bots}:
            bots.append({"slug": slug, "name": name,
                         "source_url": t.get("link"), "provisional": False})
    print(f"  {len(by_id)} team records -> {len(bots)} unique bots\n")

    print("events:")
    events = paged("events")
    print(f"  {len(events)} events\n")

    fights, skipped = [], 0
    for e in events:
        results = e.get("results") or {}
        rows = {k: v for k, v in results.items()
                if k != "0" and isinstance(v, dict) and v.get("judgesscore")}
        if len(rows) != 2:
            skipped += 1
            continue

        parsed = {}
        for tid, val in rows.items():
            m = RE_RESULT.match(str(val.get("judgesscore", "")))
            if not m:
                continue
            parsed[tid] = (m.group(1).upper(), (m.group(2) or "JD").strip().upper())
        if len(parsed) != 2 or sorted(v[0] for v in parsed.values()) != ["L", "W"]:
            skipped += 1
            continue

        win_id = next(t for t, v in parsed.items() if v[0] == "W")
        lose_id = next(t for t, v in parsed.items() if v[0] == "L")
        win, lose = by_id.get(int(win_id)), by_id.get(int(lose_id))
        if not (win and lose):
            skipped += 1
            continue

        method, fight_time = normalise_method(parsed[win_id][1])

        fights.append({
            "id": e.get("slug") or str(e["id"]),
            "season": (e.get("seasons") or [None])[0],
            "episode": None,
            "date": e.get("date"),
            "red": win["slug"],
            "blue": lose["slug"],
            "winner": win["slug"],
            "method": method,
            "time": fight_time,
            "source": "battlebots-sportspress",
            "source_url": e.get("link"),
        })

    print(f"  {len(fights)} real fights parsed, {skipped} skipped (no clean result)")

    if not fights:
        raise SystemExit("no fights parsed — aborting rather than wiping good data")

    if not args.keep_roster:
        # keep weapon classes we already know from the robot pages
        known = {b["slug"]: b for b in store.bots()}
        merged = []
        for b in bots:
            prev = known.get(b["slug"], {})
            merged.append({**prev, **b})
        store.write_cache("bots", {"bots": merged, "ingested_at": now()})
        print(f"  {len(merged)} bots -> data/cache/bots.json")

    store.write_cache("fights", {"fights": fights, "ingested_at": now()})
    print(f"  {len(fights)} fights -> data/cache/fights.json")

    methods = {}
    for f in fights:
        methods[f["method"]] = methods.get(f["method"], 0) + 1
    print(f"\n  methods: {dict(sorted(methods.items(), key=lambda x: -x[1]))}")
    print(f"\nprovenance now: {json.dumps(store.provenance(), indent=2)}")


if __name__ == "__main__":
    main()
