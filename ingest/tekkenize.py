#!/usr/bin/env python3
"""Restyle a real bot photo into a fighting-game character sprite.

Runs FLUX.2-klein img2img on garage, seeded with the ACTUAL photograph, then
cuts the result out. Image-to-image is the whole trick: text-to-image has
never seen these machines, so it invents a generic robot. The photo anchors
the silhouette; the prompt only restyles it.

Measured on Black Dragon:
    --image-strength 0.35  -> great game art, WRONG robot (invented a mech)
    --image-strength 0.55  -> unmistakably the real bot, but a flat product shot
    ~0.50 is the working compromise.

Do NOT ask the prompt for dramatic lighting or an arena background. The
three.js stage already rim-lights and stages the sprite; asking twice is what
pushes the model into inventing whole scenes instead of restyling the bot.

    python3 ingest/tekkenize.py black-dragon --prompt "..." --strength 0.5
    python3 ingest/tekkenize.py --all          # every bot, default prompt
"""
import argparse
import json
import pathlib
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import store  # noqa: E402

GARAGE = "garage.wg"
REMOTE = "botbeef"
MFP = "/opt/homebrew/anaconda3/envs/mflux/bin"

PHOTOS = store.RAW / "photos"
OUT = store.ROOT / "static" / "bots_tekken"
RAW_OUT = store.RAW / "tekken"
for d in (OUT, RAW_OUT):
    d.mkdir(parents=True, exist_ok=True)

# Style only — no lighting, no background, no scene. See module docstring.
STYLE_TAIL = (
    "rendered as a 3D fighting-game character asset: glossy game-engine render, "
    "crisp panel edges, saturated colours, clean specular highlights on metal, "
    "high contrast, plain white background, no text, no logo, no watermark"
)


def default_prompt(slug, name, weapon):
    w = (weapon or "combat robot").replace("_", " ")
    return f"{name}, a BattleBots {w} combat robot, {STYLE_TAIL}"


def run(cmd, timeout=1800):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-400:])
    return r.stdout


def tekkenize(slug, prompt, strength=0.5, seed=7000, steps=4):
    src = PHOTOS / f"{slug}.jpg"
    if not src.exists():
        raise FileNotFoundError(f"no source photo for {slug} ({src})")

    run(f"ssh -o ConnectTimeout=10 {GARAGE} 'mkdir -p ~/{REMOTE}/src ~/{REMOTE}/out'")
    run(f"scp -q {shlex.quote(str(src))} {GARAGE}:{REMOTE}/src/{slug}.jpg")

    # Two shells to get through: the local one, then the remote one. Quote the
    # prompt for the remote shell, then quote the WHOLE remote command for ssh —
    # quoting only the prompt lets the outer layer eat it and the model receives
    # the words as stray argv entries.
    remote = (
        f"{MFP}/mflux-generate-flux2 -m flux2-klein-4b -q 4 "
        f"--prompt {shlex.quote(prompt)} "
        f"--image-path ~/{REMOTE}/src/{slug}.jpg --image-strength {strength} "
        f"--width 1024 --height 768 --steps {steps} --seed {seed} "
        f"--output ~/{REMOTE}/out/{slug}.png"
    )
    run(f"ssh {GARAGE} {shlex.quote(remote)}")

    flat = RAW_OUT / f"{slug}.png"
    run(f"scp -q {GARAGE}:{REMOTE}/out/{slug}.png {shlex.quote(str(flat))}")

    # Cut it out so it drops straight onto the arena floor like the real sprites.
    from rembg import remove
    from PIL import Image

    img = Image.open(flat).convert("RGBA")
    cut = remove(img)
    bbox = cut.getbbox()
    if bbox:
        cut = cut.crop(bbox)
    dst = OUT / f"{slug}.png"
    cut.save(dst)
    return {"slug": slug, "file": str(dst), "size": cut.size,
            "strength": strength, "seed": seed, "prompt": prompt}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--prompt")
    ap.add_argument("--strength", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=7000)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    bots = {b["slug"]: b for b in store.bots()}
    targets = ([p.stem for p in sorted(PHOTOS.glob("*.jpg"))] if args.all
               else [args.slug])
    if not targets or targets == [None]:
        ap.error("give a slug or --all")

    done = []
    for slug in targets:
        b = bots.get(slug, {})
        prompt = args.prompt or default_prompt(
            slug, b.get("name", slug), b.get("weapon"))
        try:
            r = tekkenize(slug, prompt, args.strength, args.seed)
            print(f"  {slug:<15} {r['size'][0]}x{r['size'][1]}  ok")
            done.append(r)
        except Exception as exc:
            print(f"  {slug:<15} FAILED: {exc}")

    if done:
        idx = OUT / "index.json"
        prev = json.load(open(idx)) if idx.exists() else []
        merged = {d["slug"]: d for d in prev}
        merged.update({d["slug"]: d for d in done})
        json.dump(sorted(merged.values(), key=lambda d: d["slug"]),
                  open(idx, "w"), indent=2)
    print(f"\n{len(done)}/{len(targets)} styled -> {OUT}")


if __name__ == "__main__":
    main()
