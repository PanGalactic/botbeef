"""Voice — one audio file per bar, pre-rendered to disk.

Per-bar files are deliberate: the stage UI plays them in sequence and
highlights the matching bar as each one starts. That gives perfect
text/audio sync for free, with no timing code and nothing to drift.

Three backends:
  chatterbox — default. The ElevenLabs voices, CLONED onto garage's GPU by
               ingest/clone_voices.py. Same delivery, zero per-character
               cost. Needs the wg mesh (garage.wg:8093).
  elevenlabs — the paid original. Used once to buy the reference audio the
               clones are built from, and as a quality fallback.
  kokoro     — local MLX on 127.0.0.1:8766. Free, instant, offline, and
               works with no network at all. The last line of defence.

Both write into audio/ and are addressed through the same manifest, so the
front end never knows or cares which one produced a file.
"""
import hashlib
import json
import os
import shutil
import subprocess

import requests

from . import store

AUDIO = store.ROOT / "audio"
AUDIO.mkdir(parents=True, exist_ok=True)

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

BACKEND = os.environ.get("BOTBEEF_VOICE", "chatterbox")

# ---------------------------------------------------------------- kokoro
KOKORO = "http://127.0.0.1:8766"
KOKORO_VOICES = ["am_michael", "bm_george"]
KOKORO_ANNOUNCER = "bm_fable"

# ------------------------------------------------------------ chatterbox
# Clones of the ElevenLabs voices, living on garage's GPU. Names match the
# reference WAVs uploaded by ingest/clone_voices.py.
# NB: garage.local does NOT resolve from the venue — only the wg mesh does.
CHATTERBOX = "http://garage.wg:8093"
CHATTERBOX_VOICES = ["botbeef-a", "botbeef-b"]
CHATTERBOX_ANNOUNCER = "botbeef-announcer"

# ------------------------------------------------------------ elevenlabs
ELEVEN = "https://api.elevenlabs.io/v1"
# Two aggressive, clearly-distinguishable male voices, plus a deep
# ring-announcer. Picked for contrast: the crowd must hear who's spitting.
ELEVEN_VOICES = [
    ("SOYHLrjzK2X1ezoPC6cr", "Harry — fierce warrior"),
    ("N2lVS1w4EtoT3dr4eOWO", "Callum — husky trickster"),
]
ELEVEN_ANNOUNCER = ("nPczCjzI2devNBz1zQrb", "Brian — deep, resonant")
ELEVEN_MODEL = "eleven_multilingual_v2"

# Low stability = more expressive, which is what battle rap needs; high
# stability reads the bars like a train announcement.
ELEVEN_SETTINGS = {
    "stability": 0.32,
    "similarity_boost": 0.8,
    "style": 0.55,
    "use_speaker_boost": True,
}


def _eleven_key():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY not set. Either export it, or run with "
            "BOTBEEF_VOICE=kokoro for the local voices."
        )
    return key


VOICE_SETS = {
    "chatterbox": (CHATTERBOX_VOICES, CHATTERBOX_ANNOUNCER),
    "elevenlabs": ([v[0] for v in ELEVEN_VOICES], ELEVEN_ANNOUNCER[0]),
    "kokoro": (KOKORO_VOICES, KOKORO_ANNOUNCER),
}


def voice_for(slug, battle, backend=None):
    """Bot A always gets the first voice, bot B the second. Stable per battle."""
    voices, _ = VOICE_SETS[backend or BACKEND]
    return voices[0] if slug == battle["a"] else voices[1]


def announcer(backend=None):
    return VOICE_SETS[backend or BACKEND][1]


def _key(text, voice, speed, backend):
    return hashlib.sha1(f"{backend}|{voice}|{speed}|{text}".encode()).hexdigest()[:16]


def _render_kokoro(text, voice, speed, path):
    r = requests.post(f"{KOKORO}/generate",
                      json={"text": text, "voice": voice, "speed": speed},
                      timeout=120)
    r.raise_for_status()
    if not r.content.startswith(b"RIFF"):
        raise RuntimeError(f"kokoro returned {r.headers.get('content-type')}, not WAV")
    return r.content


def _render_eleven(text, voice, speed, path):
    """MP3 out. The browser plays it natively, so no PCM/WAV wrapping —
    that conversion is where this integration usually goes wrong."""
    r = requests.post(
        f"{ELEVEN}/text-to-speech/{voice}",
        headers={"xi-api-key": _eleven_key(), "Content-Type": "application/json"},
        params={"output_format": "mp3_44100_128"},
        json={"text": text, "model_id": ELEVEN_MODEL,
              "voice_settings": ELEVEN_SETTINGS},
        timeout=180,
    )
    if r.status_code == 401:
        raise RuntimeError("ElevenLabs rejected the key (401)")
    if r.status_code == 429:
        raise RuntimeError("ElevenLabs quota/rate limit hit (429)")
    r.raise_for_status()
    if len(r.content) < 500:
        raise RuntimeError(f"ElevenLabs returned {len(r.content)}B — not audio")
    return r.content


def _render_chatterbox(text, voice, speed, path):
    """The cloned ElevenLabs voices, running free on garage's GPU."""
    r = requests.post(
        f"{CHATTERBOX}/v1/audio/speech",
        json={"model": "chatterbox", "input": text,
              "voice": voice, "response_format": "wav", "speed": speed},
        timeout=300,
    )
    if r.status_code == 404:
        raise RuntimeError(
            f"chatterbox has no voice '{voice}' — run ingest/clone_voices.py")
    r.raise_for_status()
    if not r.content.startswith(b"RIFF"):
        raise RuntimeError(f"chatterbox returned {r.headers.get('content-type')}")
    return r.content


RENDERERS = {
    "chatterbox": _render_chatterbox,
    "elevenlabs": _render_eleven,
    "kokoro": _render_kokoro,
}


def render(text, voice, speed=1.0, backend=None):
    """Render one line. Cached on disk by content hash — never re-renders,
    which matters when the backend bills per character.

    Always lands as MP3. Chatterbox and Kokoro both hand back 24 kHz mono
    PCM, which is ~4x the size for speech nobody can tell apart at 96 kbps —
    and since these filenames are content-hashed, WAVs accumulate in git
    forever rather than being replaced. Storing MP3 is what lets the audio
    ship in the repo at all.
    """
    backend = backend or BACKEND
    path = AUDIO / f"{_key(text, voice, speed, backend)}.mp3"
    if path.exists() and path.stat().st_size > 500:
        return path

    data = RENDERERS[backend](text, voice, speed, path)

    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)

    if data.startswith(b"RIFF"):          # PCM in, MP3 out
        _to_mp3(tmp, path)
        tmp.unlink(missing_ok=True)
    else:                                  # ElevenLabs already returns MP3
        tmp.replace(path)
    return path


def _to_mp3(src, dst):
    """96 kbps mono — inaudible on speech, a quarter of the bytes."""
    r = subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(src),
         "-c:a", "libmp3lame", "-b:a", "96k", "-ac", "1", str(dst)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError(f"mp3 encode failed: {r.stderr[-200:]}")


def render_battle(battle, speed=1.0, backend=None):
    """Pre-render every bar plus the announcer intro. Run before the demo.

    Falls back to Kokoro per-line if the paid backend fails, so a dead API
    key or an exhausted quota degrades the voices instead of the demo.
    """
    backend = backend or BACKEND
    names = battle.get("names", {})
    intro = (
        f"Tonight in the box: {names.get(battle['a'], battle['a'])}, "
        f"versus {names.get(battle['b'], battle['b'])}. "
        "Every bar is sourced. Let's go."
    )

    manifest = {"backend": backend, "bars": []}
    fallbacks = 0

    def _one(text, voice_id, fallback_voice):
        nonlocal fallbacks
        try:
            return render(text, voice_id, speed, backend).name
        except (requests.RequestException, RuntimeError) as exc:
            if backend == "kokoro":
                raise
            # Never let a dead mesh or an exhausted quota kill the demo —
            # degrade the voices, not the battle.
            fallbacks += 1
            print(f"    ! {exc}\n      falling back to Kokoro for this line")
            return render(text, fallback_voice, speed, "kokoro").name

    manifest["intro"] = _one(intro, announcer(backend), KOKORO_ANNOUNCER)

    for i, bar in enumerate(battle.get("bars", [])):
        vid = voice_for(bar["bot"], battle, backend)
        fb = KOKORO_VOICES[0] if bar["bot"] == battle["a"] else KOKORO_VOICES[1]
        manifest["bars"].append({"index": i, "file": _one(bar["text"], vid, fb)})

    if fallbacks:
        manifest["fallbacks"] = fallbacks

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
    """Return voice configuration without probing optional network services.

    Flask health checks must remain local and fast. In particular, Requests'
    timeout does not put a hard deadline on DNS resolution, so checking the
    optional ``garage.wg`` service here could stall the entire response.
    """
    return {
        "backend": BACKEND,
        "availability": "not-probed",
        "optional": True,
    }
