#!/usr/bin/env python3
"""Convert rendered speech WAVs to MP3 so the audio can live in git.

The bars are 24 kHz mono speech. As PCM WAV that's ~100 MB across 439 files,
which is too much to commit — and because filenames are content-hashed, every
re-render ADDS files rather than replacing them, so history would grow forever.

At 96 kbps mono the difference is inaudible through a laptop or a PA, and the
whole set drops to roughly a quarter of the size. That's small enough to
commit, which means a fresh clone — a Codespace, a teammate's laptop — gets
the actual cloned voices instead of the silent timed-subtitle fallback.

The reference WAVs in audio/refs/ are deliberately left alone: those are the
voice-clone sources and want to stay lossless.

    python3 ingest/compress_audio.py           # convert + rewrite manifests
    python3 ingest/compress_audio.py --keep    # don't delete the WAVs
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import store  # noqa: E402

AUDIO = store.ROOT / "audio"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
BITRATE = "96k"


def convert(wav):
    mp3 = wav.with_suffix(".mp3")
    if mp3.exists() and mp3.stat().st_size > 1000:
        return mp3, 0
    r = subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(wav),
         "-c:a", "libmp3lame", "-b:a", BITRATE, "-ac", "1", str(mp3)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not mp3.exists():
        raise RuntimeError(f"{wav.name}: {r.stderr[-200:]}")
    return mp3, 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the source WAVs")
    args = ap.parse_args()

    if not pathlib.Path(FFMPEG).exists():
        raise SystemExit(f"ffmpeg not found at {FFMPEG}")

    wavs = sorted(AUDIO.glob("*.wav"))          # NB: not refs/, those stay WAV
    if not wavs:
        print("no WAVs to convert")
        return

    before = sum(w.stat().st_size for w in wavs)
    made = 0
    for w in wavs:
        try:
            _, n = convert(w)
            made += n
        except RuntimeError as exc:
            print(f"  FAILED {exc}")

    # Point every manifest at the new extension. The front end reads these
    # filenames verbatim, so converting without rewriting them would leave
    # the whole app pointing at files that no longer exist.
    rewritten = 0
    for man in AUDIO.glob("manifest__*.json"):
        d = json.load(open(man))
        d["intro"] = str(pathlib.Path(d["intro"]).with_suffix(".mp3"))
        for b in d.get("bars", []):
            b["file"] = str(pathlib.Path(b["file"]).with_suffix(".mp3"))
        d["format"] = "mp3"
        json.dump(d, open(man, "w"), indent=2)
        rewritten += 1

    after = sum(p.stat().st_size for p in AUDIO.glob("*.mp3"))
    print(f"  converted   {made} new ({len(wavs)} total)")
    print(f"  manifests   {rewritten} rewritten -> .mp3")
    print(f"  size        {before/1e6:.0f} MB WAV -> {after/1e6:.0f} MB MP3")

    if not args.keep:
        for w in wavs:
            w.unlink()
        print(f"  removed     {len(wavs)} WAVs (regenerate with ingest/pregen.py)")


if __name__ == "__main__":
    main()
