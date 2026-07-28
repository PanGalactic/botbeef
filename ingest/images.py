#!/usr/bin/env python3
"""Bot imagery for the Tekken-style arena.

Two very different jobs, and the split matters:

  BOTS  -> REAL PHOTOS, background-removed into transparent cutouts.
           A text-to-image model has never seen Tombstone. Ask FLUX for
           "Tombstone the battlebot" and you get a generic robot, which
           fails the one requirement that matters: they must look like
           their real-world counterparts. So the fighters are photographs.

  ARENA -> FLUX (garage.wg:8005, FLUX.2-klein). The backdrop is the one
           thing a generative model genuinely helps with — no likeness to
           preserve, and we want a stylised Tekken stage, not a photo.

    python3 ingest/images.py fetch      # download bot photos (Bright Data or direct)
    python3 ingest/images.py cutout     # background removal -> static/bots/*.png
    python3 ingest/images.py arena      # FLUX stage backdrops -> static/arena/*.png
"""
import argparse
import io
import json
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import store  # noqa: E402

PHOTOS = store.RAW / "photos"
BOTS_OUT = store.ROOT / "static" / "bots"
ARENA_OUT = store.ROOT / "static" / "arena"
for d in (PHOTOS, BOTS_OUT, ARENA_OUT):
    d.mkdir(parents=True, exist_ok=True)

MFLUX = "http://garage.wg:8005"
SOURCES = store.RAW / "photo_sources.json"

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}


def _brightdata_available():
    import os
    return bool(os.environ.get("BRIGHTDATA_API_TOKEN")) or (
        pathlib.Path.home() / ".claude" / "secrets" / "brightdata.json").exists()


def fetch(limit=None):
    """Download one photo per bot.

    Source URLs come from data/raw/photo_sources.json — a {slug: url} map.
    Populate it via Bright Data (SERP/Unlocker over the official bot pages)
    or by hand. Bright Data is preferred: it survives the hotlink blocking
    and rate limits that kill a naive requests.get loop.
    """
    if not SOURCES.exists():
        print(f"No {SOURCES.name}. Write it as {{\"tombstone\": \"https://...jpg\", ...}}")
        print("Populate with Bright Data:")
        print("  python3 ingest/brightdata.py fights --url <official bot roster page>")
        print("then pull the image srcs out of the cached HTML.")
        return {}

    with open(SOURCES) as fh:
        sources = json.load(fh)

    use_bd = _brightdata_available()
    print(f"fetching {len(sources)} photos via {'Bright Data' if use_bd else 'direct HTTP'}")

    got = {}
    for i, (slug, url) in enumerate(sources.items()):
        if limit and i >= limit:
            break
        out = PHOTOS / f"{slug}.jpg"
        if out.exists() and out.stat().st_size > 5000:
            got[slug] = str(out)
            continue
        try:
            if use_bd:
                from ingest import brightdata
                data = requests.post(
                    f"{brightdata.API}/request",
                    headers=brightdata.headers(),
                    json={"zone": brightdata.UNLOCKER_ZONE, "url": url, "format": "raw"},
                    timeout=90,
                ).content
            else:
                data = requests.get(url, headers=UA, timeout=45).content
            if len(data) < 5000:
                raise ValueError(f"suspiciously small ({len(data)}B)")
            out.write_bytes(data)
            got[slug] = str(out)
            print(f"  {slug}: {len(data):,}B")
        except (requests.RequestException, ValueError) as exc:
            print(f"  {slug}: FAILED {exc}")
        time.sleep(0.3)

    print(f"\n{len(got)}/{len(sources)} photos in {PHOTOS}")
    return got


def cutout():
    """Background-removal -> transparent PNG, sized for billboard planes."""
    try:
        from rembg import remove
    except ImportError:
        raise SystemExit("pip install rembg onnxruntime")
    from PIL import Image

    photos = sorted(PHOTOS.glob("*.jpg")) + sorted(PHOTOS.glob("*.png"))
    if not photos:
        raise SystemExit(f"no photos in {PHOTOS} — run `images.py fetch` first")

    for src in photos:
        dst = BOTS_OUT / f"{src.stem}.png"
        if dst.exists():
            continue
        img = Image.open(src).convert("RGBA")
        img.thumbnail((900, 900), Image.LANCZOS)
        out = remove(img)

        # Crop to the actual robot so every billboard sits on the arena floor
        # at a consistent scale — otherwise they float at random heights.
        bbox = out.getbbox()
        if bbox:
            out = out.crop(bbox)
        out.save(dst)
        print(f"  {dst.name}: {out.size[0]}x{out.size[1]}")

    print(f"\ncutouts in {BOTS_OUT}")


ARENA_PROMPTS = {
    "arena": (
        "wide empty robot combat arena interior, scratched steel floor with hazard "
        "chevrons, heavy lexan blast walls, industrial trusses overhead, volumetric "
        "spotlight shafts cutting through haze, deep blue and orange rim light, "
        "dark crowd silhouettes in the stands, cinematic fighting game stage, "
        "dramatic low angle, no characters, no text"
    ),
    "floor": (
        "top down seamless texture, scuffed steel arena floor plate, diamond tread, "
        "scorch marks, orange hazard stripes, worn metal, dark, high contrast"
    ),
}


def arena(steps=4, width=1344, height=768):
    """FLUX.2-klein on garage for the stage art. GPU is serialized up there."""
    try:
        # Generous: the GPU is serialized up there, so a busy box answers slowly.
        requests.get(f"{MFLUX}/health", timeout=90).raise_for_status()
    except requests.RequestException as exc:
        raise SystemExit(f"mflux unreachable at {MFLUX} ({exc}). "
                         "Is the wg mesh up? try: curl http://garage.wg:8005/health")

    for name, prompt in ARENA_PROMPTS.items():
        dst = ARENA_OUT / f"{name}.png"
        if dst.exists():
            print(f"  {name}: exists, skipping")
            continue
        w, h = (1024, 1024) if name == "floor" else (width, height)
        print(f"  {name}: generating {w}x{h} ...", flush=True)
        r = requests.post(
            f"{MFLUX}/generate",
            json={"prompt": prompt, "width": w, "height": h,
                  "steps": steps, "seed": 1939, "model": "flux2-klein-4b"},
            timeout=900,
        )
        r.raise_for_status()
        if not r.content.startswith(b"\x89PNG"):
            raise SystemExit(f"mflux returned {r.headers.get('content-type')}, not PNG")
        dst.write_bytes(r.content)
        print(f"    {len(r.content):,}B -> {dst.name}")

    print(f"\narena art in {ARENA_OUT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "cutout", "arena"])
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    if args.cmd == "fetch":
        fetch(args.limit)
    elif args.cmd == "cutout":
        cutout()
    else:
        arena()


if __name__ == "__main__":
    main()
