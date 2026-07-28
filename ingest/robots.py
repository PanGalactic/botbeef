#!/usr/bin/env python3
"""Scrape battlebots.com/robot/<slug>/ — the official per-bot pages.

These pages carry BOTH things the app needs:
  * the official career record (e.g. "25-10, 71%") -> real performance data,
    which is what turns the red PLACEHOLDER banner green
  * the official bot photo -> the fighter cutout for the arena

Goes through Bright Data Web Unlocker when a token is present (the sponsor
path, and it survives the rate limiting that kills a naive loop); falls back
to direct HTTP so the pipeline is testable before the token lands.

    python3 ingest/robots.py discover        # find each bot's page URL
    python3 ingest/robots.py scrape          # pull pages -> data/raw/robot_*.html
    python3 ingest/robots.py parse           # -> data/cache/{bots,fights}.json + photo_sources
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

PAGES = store.RAW / "robots"
PAGES.mkdir(parents=True, exist_ok=True)
URLS = store.RAW / "robot_urls.json"

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}

# battlebots.com slugs don't always match ours; season suffixes vary.
# These are the candidates we try, most-recent first.
SUFFIXES = ["-wcvii", "-2021", "-2020", ""]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _bd():
    """Bright Data Web Unlocker, if configured."""
    have = os.environ.get("BRIGHTDATA_API_TOKEN") or (
        pathlib.Path.home() / ".claude" / "secrets" / "brightdata.json").exists()
    if not have:
        return None
    from ingest import brightdata
    return brightdata


def get(url, timeout=60):
    bd = _bd()
    if bd:
        return bd.unlock(url)
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.text


def discover():
    """Resolve each roster bot to a live battlebots.com robot page."""
    found, missing = {}, []
    for bot in store.bots():
        slug = bot["slug"]
        for suffix in SUFFIXES:
            url = f"https://battlebots.com/robot/{slug}{suffix}/"
            try:
                bd = _bd()
                if bd:
                    html = bd.unlock(url)
                    ok = "robot" in html.lower() and len(html) > 5000
                else:
                    r = requests.get(url, headers=UA, timeout=30)
                    ok = r.status_code == 200 and len(r.text) > 5000
                    html = r.text
                if ok:
                    found[slug] = url
                    print(f"  {slug} -> {url}")
                    break
            except requests.RequestException:
                pass
            time.sleep(0.25)
        else:
            missing.append(slug)
            print(f"  {slug}: NOT FOUND")

    with open(URLS, "w") as fh:
        json.dump({"found": found, "missing": missing, "at": now()}, fh, indent=2)
    print(f"\n{len(found)}/{len(found) + len(missing)} resolved -> {URLS.name}")
    return found


def scrape():
    if not URLS.exists():
        raise SystemExit("run `robots.py discover` first")
    with open(URLS) as fh:
        found = json.load(fh)["found"]

    print(f"scraping {len(found)} pages via "
          f"{'Bright Data Web Unlocker' if _bd() else 'direct HTTP'}")
    ok = 0
    for slug, url in found.items():
        dst = PAGES / f"{slug}.html"
        if dst.exists() and dst.stat().st_size > 5000:
            ok += 1
            continue
        try:
            dst.write_text(get(url))
            ok += 1
            print(f"  {slug}: {dst.stat().st_size:,}B")
        except requests.RequestException as exc:
            print(f"  {slug}: FAILED {exc}")
        time.sleep(0.4)
    print(f"\n{ok}/{len(found)} pages cached in {PAGES}")


RE_IMG = re.compile(
    r'https://battlebots\.com/wp-content/uploads/[^"\'\s]+\.(?:jpg|jpeg|png)', re.I)
RE_RECORD = re.compile(r'(\d+)\s*(?:wins?|W)\D{0,12}?(\d+)\s*(?:loss(?:es)?|L)', re.I)
RE_WEAPON = re.compile(r'weapon[^<]{0,40}</[^>]+>\s*<[^>]+>([^<]{3,60})', re.I)
RE_WPSIZE = re.compile(r'-\d{2,4}x\d{2,4}\.(jpg|jpeg|png)$', re.I)

# Words that appear in filenames but carry no identity.
_STOP = {"the", "bb", "bot", "team", "jpg", "jpeg", "png", "wcvii", "wcvi"}


def _tokens(s):
    return {t for t in re.findall(r"[a-z]+", s.lower()) if t not in _STOP and len(t) > 2}


def base_name(slug):
    return slug.replace("-", " ")


def _names_bot(url, slug, name):
    """Does this filename actually name this bot?

    Compared on tokens AND on the de-hyphenated run, because the site is
    inconsistent: 'lock-jaw' ships as 'BB2022-lockjaw-bot.jpg'.
    """
    fn = url.split("/")[-1]
    want = _tokens(slug) | _tokens(name)
    if _tokens(fn) & want:
        return True
    flat = re.sub(r"[^a-z]", "", fn.lower())
    return any(re.sub(r"[^a-z]", "", w) in flat for w in (slug, name) if len(w) > 3)


def parse():
    """Turn cached pages into real records + photo sources."""
    pages = sorted(PAGES.glob("*.html"))
    if not pages:
        raise SystemExit(f"no pages in {PAGES} — run `robots.py scrape` first")

    with open(URLS) as fh:
        urls = json.load(fh)["found"]

    bots, photos, records = [], {}, []
    existing = {b["slug"]: b for b in store.bots()}

    for path in pages:
        slug = path.stem
        html = path.read_text()
        src_url = urls.get(slug)

        # Site furniture is never the fighter. Drop it before anything else —
        # a page background or favicon sails straight through rembg and ends
        # up on stage as a fighter.
        JUNK = ("logo", "favicon", "spoiler", "video-bg", "header",
                "placeholder", "sponsor", "banner")
        imgs = [u for u in RE_IMG.findall(html)
                if not any(j in u.lower() for j in JUNK)]

        # Prefer a shot of the machine alone. Team photos are a last resort:
        # rembg keeps the humans, so a crew shot puts five people on stage.
        solo = [u for u in imgs
                if not any(t in u.lower() for t in ("-team", "team-", "crew"))]
        # A robot page also links OTHER robots (related bots, brackets), so
        # "first non-junk image" silently assigns the wrong machine — that's
        # how bite-force ended up showing Captain Shrederator. Require the
        # filename to actually name this bot.
        named = [u for u in solo if _names_bot(u, slug, base_name(slug))]
        if solo and not named:
            print(f"  {slug}: no image matches its own name — skipping photo")
        solo = named

        if not solo and imgs:
            print(f"  {slug}: only a team photo available — will include people")
        imgs = solo or []
        if imgs:
            # WordPress serves resized variants (foo-300x200.jpg). Strip the
            # suffix to get the full-size original — a 200px thumbnail is
            # mush on a projector.
            imgs = [RE_WPSIZE.sub(r".\1", u) for u in imgs]
            # prefer a filename that looks like the bot itself, not the team
            bot_imgs = [u for u in imgs if "-bot-" in u.lower()] or imgs
            photos[slug] = bot_imgs[0]

        weapon = None
        m = RE_WEAPON.search(html)
        if m:
            weapon = m.group(1).strip().lower().replace(" ", "_")

        base = existing.get(slug, {"slug": slug, "name": slug})
        bots.append({**base, "weapon": weapon or base.get("weapon"),
                     "source_url": src_url, "provisional": False})

        # Career record → a single aggregate "fight" record per bot.
        # Honest about what it is: an aggregate, not a per-fight table.
        m = RE_RECORD.search(re.sub(r"<[^>]+>", " ", html))
        if m:
            records.append({"slug": slug, "wins": int(m.group(1)),
                            "losses": int(m.group(2)), "source_url": src_url})

    if photos:
        with open(store.RAW / "photo_sources.json", "w") as fh:
            json.dump(photos, fh, indent=2)
        print(f"  {len(photos)} photo URLs -> data/raw/photo_sources.json")

    if bots:
        store.write_cache("bots", {"bots": bots, "ingested_at": now()})
        print(f"  {len(bots)} bots -> data/cache/bots.json")

    if records:
        with open(store.RAW / "career_records.json", "w") as fh:
            json.dump({"records": records, "at": now()}, fh, indent=2)
        print(f"  {len(records)} career records -> data/raw/career_records.json")
        print("\nNOTE: these are aggregate W-L, not a per-fight table. To flip the "
              "provenance banner green you still need per-fight records — scrape the "
              "season/fight pages next.")

    print(f"\nnext: python3 ingest/images.py fetch && python3 ingest/images.py cutout")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["discover", "scrape", "parse"])
    args = ap.parse_args()
    {"discover": discover, "scrape": scrape, "parse": parse}[args.cmd]()


if __name__ == "__main__":
    main()
