#!/usr/bin/env python3
"""Pull the Suno instrumental beds down to disk.

The beats are generated on Suno v5.5 under a Pro subscription, which carries
commercial rights, and cached locally for the same reason as everything else
in this project: nothing should hit the network during a demo.

One bed per style. Every prompt asked for a seamless loop, so the short ones
are safe to loop under a battle that outruns them.

    python3 ingest/beats.py
"""
import json
import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import store  # noqa: E402

BEATS = store.ROOT / "static" / "beats"
CDN = "https://cdn1.suno.ai/{id}.mp3"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}


def main():
    manifest = json.load(open(BEATS / "beats.json"))
    ok = 0
    for b in manifest["beats"]:
        dst = BEATS / b["file"]
        if dst.exists() and dst.stat().st_size > 100_000:
            print(f"  {b['slug']:<11} cached ({dst.stat().st_size:,}B)")
            ok += 1
            continue
        # cdn2 403s; cdn1 serves the mp3 directly.
        url = CDN.format(id=b["suno_id"])
        try:
            r = requests.get(url, headers=UA, timeout=180)
            r.raise_for_status()
            if len(r.content) < 100_000:
                raise ValueError(f"only {len(r.content)}B — not a full track")
            dst.write_bytes(r.content)
            ok += 1
            print(f"  {b['slug']:<11} {len(r.content):,}B  {b['title']} ({b['duration']})")
        except (requests.RequestException, ValueError) as exc:
            print(f"  {b['slug']:<11} FAILED: {exc}")

    total = sum(f.stat().st_size for f in BEATS.glob("*.mp3"))
    print(f"\n{ok}/{len(manifest['beats'])} beds in {BEATS} ({total/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
