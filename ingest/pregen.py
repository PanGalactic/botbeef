#!/usr/bin/env python3
"""Pre-generate battles + audio to disk, so the stage never waits.

    python3 ingest/pregen.py tombstone hydra          # one matchup
    python3 ingest/pregen.py --top 6                  # the 6 juiciest pairs
    python3 ingest/pregen.py --backend cerebras a b   # fast, lower quality

"Juiciest" = the biggest hype-residual gap. A bot the crowd overrates
against one it underrates is where the data gives the sharpest bars.
"""
import argparse
import itertools
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import rap, score, voice  # noqa: E402


def top_pairs(n):
    rows = score.table()["rows"]
    scored = []
    for x, y in itertools.combinations(rows, 2):
        gap = abs(x["residual"] - y["residual"])
        volume = x["mentions"] + y["mentions"]
        scored.append((gap + volume / 50.0, x["slug"], y["slug"]))
    scored.sort(reverse=True)
    return [(a, b) for _, a, b in scored[:n]]


def one(a, b, backend, no_audio):
    print(f"\n=== {a} vs {b} [{backend}] ===")
    battle = rap.battle(a, b, backend=backend, force=True)
    kept, rej = len(battle["bars"]), len(battle["rejected"])
    print(f"  {kept} bars kept, {rej} rejected as unsourced")
    for bar in battle["bars"][:3]:
        print(f"  [{bar['fact_id']}] {bar['bot']}: {bar['text']}")
    if kept < 8:
        print(f"  WARNING: only {kept} sourced bars — consider re-running")
    if not no_audio:
        m = voice.render_battle(battle)
        print(f"  audio: intro + {len(m['bars'])} bars rendered")
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pair", nargs="*", help="two bot slugs")
    ap.add_argument("--top", type=int, help="pre-generate the N juiciest matchups")
    ap.add_argument("--backend", default="anthropic", choices=["anthropic", "cerebras"])
    ap.add_argument("--no-audio", action="store_true")
    args = ap.parse_args()

    if args.top:
        pairs = top_pairs(args.top)
    elif len(args.pair) == 2:
        pairs = [tuple(args.pair)]
    else:
        ap.error("give two slugs or --top N")

    ok = 0
    for a, b in pairs:
        try:
            if one(a, b, args.backend, args.no_audio) >= 8:
                ok += 1
        except Exception:
            traceback.print_exc()
            print(f"  FAILED: {a} vs {b}")

    print(f"\n{ok}/{len(pairs)} battles usable on stage")


if __name__ == "__main__":
    main()
