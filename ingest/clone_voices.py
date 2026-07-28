#!/usr/bin/env python3
"""Clone the ElevenLabs voices onto garage Chatterbox — pay once, not per bar.

ElevenLabs bills per character. A 16-bar battle is ~1.5k characters, and we
re-render on every prompt tweak, so the API cost is recurring for the exact
same three voices. Chatterbox on garage clones from a reference WAV and then
runs free on the GPU forever.

So: buy ~15 seconds of each voice from ElevenLabs ONCE, clone it, and never
call the paid API again.

    python3 ingest/clone_voices.py           # generate refs, upload, verify
    python3 ingest/clone_voices.py --verify  # just re-test the clones

Then run everything with BOTBEEF_VOICE=chatterbox.
"""
import argparse
import pathlib
import subprocess
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import voice as V  # noqa: E402

GARAGE = "garage.wg"
CHATTERBOX = f"http://{GARAGE}:8093"
REMOTE_DIR = "pannyflow/data/voices"
REFS = V.AUDIO / "refs"
REFS.mkdir(parents=True, exist_ok=True)

FFMPEG = "/opt/homebrew/bin/ffmpeg"

# ~15s of in-character speech each. Prosody matters more than content: the
# clone copies delivery, so the reference has to actually sound like battle
# rap, not like someone reading a manual.
ROSTER = {
    "botbeef-a": {
        "eleven_id": V.ELEVEN_VOICES[0][0],
        "text": "Step in the box and check the tape, I don't guess — I know. "
                "Four and one, three of them knockouts, and the record backs "
                "every word I say. You want smoke? Bring receipts, because "
                "I brought mine and they're stamped and sourced.",
    },
    "botbeef-b": {
        "eleven_id": V.ELEVEN_VOICES[1][0],
        "text": "Nobody tweets my name and that's exactly how I like it. "
                "While you were trending I was winning, quiet, clean, and "
                "twelve points better than the crowd ever gave me. Talk all "
                "you want. The numbers already answered for me.",
    },
    "botbeef-announcer": {
        "eleven_id": V.ELEVEN_ANNOUNCER[0],
        "text": "Ladies and gentlemen, welcome to the box. Tonight, two "
                "machines, sixteen bars, and every single line sourced from "
                "the record. This is BOT BEEF. Let's go.",
    },
}


def eleven_ref(key, spec):
    """Buy the reference audio. WAV out — Chatterbox wants a WAV to clone."""
    mp3 = REFS / f"{key}.mp3"
    if not mp3.exists() or mp3.stat().st_size < 5000:
        print(f"  {key}: generating reference from ElevenLabs ...", flush=True)
        r = requests.post(
            f"{V.ELEVEN}/text-to-speech/{spec['eleven_id']}",
            headers={"xi-api-key": V._eleven_key(), "Content-Type": "application/json"},
            params={"output_format": "mp3_44100_128"},
            json={"text": spec["text"], "model_id": V.ELEVEN_MODEL,
                  "voice_settings": V.ELEVEN_SETTINGS},
            timeout=180,
        )
        r.raise_for_status()
        mp3.write_bytes(r.content)
        print(f"    {len(r.content):,}B")
    else:
        print(f"  {key}: reference mp3 cached")

    # Chatterbox expects mono 24 kHz. 10 seconds is the house convention and
    # is plenty for a clone — longer references don't improve it.
    wav = REFS / f"{key}.wav"
    if not wav.exists() or wav.stat().st_size < 10000:
        subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-i", str(mp3),
             "-t", "10", "-ac", "1", "-ar", "24000", str(wav)],
            check=True,
        )
        print(f"    trimmed -> {wav.name} ({wav.stat().st_size:,}B)")
    return wav


def upload(key, wav):
    subprocess.run(
        ["scp", "-q", str(wav), f"{GARAGE}:{REMOTE_DIR}/{key}.wav"], check=True
    )
    print(f"    uploaded to {GARAGE}:{REMOTE_DIR}/{key}.wav")


def verify(key):
    """Synth a line through the clone and prove we got real audio back."""
    r = requests.post(
        f"{CHATTERBOX}/v1/audio/speech",
        json={"model": "chatterbox", "input":
              "Check the tape. Every bar is sourced.",
              "voice": key, "response_format": "wav"},
        timeout=300,
    )
    if r.status_code != 200:
        print(f"    VERIFY FAILED {r.status_code}: {r.text[:160]}")
        return False
    out = REFS / f"cloned-{key}.wav"
    out.write_bytes(r.content)
    ok = r.content.startswith(b"RIFF") and len(r.content) > 20000
    print(f"    clone test: {len(r.content):,}B {'OK' if ok else 'SUSPICIOUS'}"
          f" -> {out.name}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="skip generation/upload")
    args = ap.parse_args()

    try:
        h = requests.get(f"{CHATTERBOX}/health", timeout=10).json()
        print(f"chatterbox: {h.get('backend')} loaded={h.get('loaded')}\n")
    except requests.RequestException as exc:
        raise SystemExit(f"chatterbox unreachable at {CHATTERBOX}: {exc}\n"
                         "Is the wg mesh up? (garage.local won't work — use garage.wg)")

    ok = 0
    for key, spec in ROSTER.items():
        print(f"{key}:")
        if not args.verify:
            wav = eleven_ref(key, spec)
            upload(key, wav)
        if verify(key):
            ok += 1
        print()

    print(f"{ok}/{len(ROSTER)} voices cloned and verified on garage")
    if ok == len(ROSTER):
        print("\nNow run everything free:")
        print("  export BOTBEEF_VOICE=chatterbox")
        print("  python3 ingest/pregen.py --top 6")


if __name__ == "__main__":
    main()
