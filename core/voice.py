"""Kokoro TTS — one WAV per bar, pre-rendered to disk.

Per-bar files are deliberate: the stage UI plays them in sequence and
highlights the matching bar as each one starts. That gives perfect
text/audio sync for free, with no timing code and nothing to drift.

garage is unreachable from the venue, so this is the LOCAL Kokoro on
127.0.0.1:8766 (MLX, ~24x realtime on this machine).
"""
import hashlib
import json
import pathlib

import requests

from . import store

KOKORO = "http://127.0.0.1:8766"
AUDIO = store.ROOT / "audio"
AUDIO.mkdir(parents=True, exist_ok=True)

# Two clearly different voices so the crowd can tell who's spitting.
VOICES = ["am_michael", "bm_george"]
ANNOUNCER = "bm_fable"


def voice_for(slug, battle):
    """Bot A always gets the first voice, bot B the second. Stable per battle."""
    return VOICES[0] if slug == battle["a"] else VOICES[1]


def _key(text, voice, speed):
    raw = f"{voice}|{speed}|{text}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def render(text, voice, speed=1.0):
    """Render one line. Cached on disk by content hash — never re-renders."""
    path = AUDIO / f"{_key(text, voice, speed)}.wav"
    if path.exists() and path.stat().st_size > 44:
        return path

    r = requests.post(
        f"{KOKORO}/generate",
        json={"text": text, "voice": voice, "speed": speed},
        timeout=120,
    )
    r.raise_for_status()
    if not r.content.startswith(b"RIFF"):
        raise RuntimeError(f"kokoro returned {r.headers.get('content-type')}, not WAV")

    tmp = path.with_suffix(".wav.tmp")
    with open(tmp, "wb") as fh:
        fh.write(r.content)
    tmp.replace(path)
    return path


def render_battle(battle, speed=1.05):
    """Pre-render every bar plus the announcer intro. Run this before the demo."""
    names = battle.get("names", {})
    intro = (
        f"Tonight in the box: {names.get(battle['a'], battle['a'])} "
        f"versus {names.get(battle['b'], battle['b'])}. "
        "Every bar is sourced. Let's go."
    )
    manifest = {"intro": render(intro, ANNOUNCER, 1.0).name, "bars": []}

    for i, bar in enumerate(battle.get("bars", [])):
        path = render(bar["text"], voice_for(bar["bot"], battle), speed)
        manifest["bars"].append({"index": i, "file": path.name})

    out = AUDIO / f"manifest__{'__'.join(sorted([battle['a'], battle['b']]))}.json"
    with open(out, "w") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def manifest_for(a, b):
    path = AUDIO / f"manifest__{'__'.join(sorted([a, b]))}.json"
    if not path.exists():
        return None
    with open(path) as fh:
        return json.load(fh)


def health():
    try:
        r = requests.get(f"{KOKORO}/health", timeout=3)
        return r.status_code == 200 and r.json().get("model_loaded") is True
    except requests.RequestException:
        return False
