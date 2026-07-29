#!/usr/bin/env python3
"""Audit generated battles/audio and provide reversible remediation actions.

The default command is read-only:

    python ingest/audit_generated_assets.py

Archive and restore are explicit operations. Only unreferenced, root-level,
16-character content-hash MP3s are eligible:

    python ingest/audit_generated_assets.py --apply-archive
    python ingest/audit_generated_assets.py --restore-archive

Regeneration is transactional for the authoritative battle and manifest:

    python ingest/audit_generated_assets.py --regenerate BOT_A BOT_B
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import rap, store, voice  # noqa: E402
from ingest import pregen  # noqa: E402


HASHED_MP3 = re.compile(r"^[0-9a-f]{16}\.mp3$")
TEXT_SUFFIXES = {
    ".cjs", ".css", ".html", ".js", ".json", ".jsx", ".md", ".mjs", ".py",
    ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
SKIP_SCAN_DIRS = {".git", ".pytest_cache", "__pycache__", "audio", "node_modules"}
ARCHIVE_CONFIRMATION = "ARCHIVE_UNREFERENCED_GENERATED_AUDIO"


def _read_json(path, default):
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _manifest_references(audio_dir):
    references = set()
    invalid = []
    for path in sorted(audio_dir.glob("manifest__*.json")):
        payload = _read_json(path, None)
        if not isinstance(payload, dict):
            invalid.append(path.name)
            continue
        values = [payload.get("intro")]
        values.extend(
            entry.get("file")
            for entry in payload.get("bars", [])
            if isinstance(entry, dict)
        )
        for value in values:
            if isinstance(value, str) and pathlib.Path(value).name == value:
                references.add(value)
    return references, invalid


def _code_references(root, candidates):
    references = set()
    token = re.compile(r"(?<![0-9a-f])([0-9a-f]{16}\.mp3)(?![0-9a-f])")
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in TEXT_SUFFIXES
            or any(part in SKIP_SCAN_DIRS for part in path.relative_to(root).parts)
        ):
            continue
        try:
            found = token.findall(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        references.update(name for name in found if name in candidates)
    return references


def audio_inventory(root=store.ROOT):
    """Return a deterministic inventory; never mutate the audio directory."""
    root = pathlib.Path(root)
    audio_dir = root / "audio"
    files = {path.name: path for path in audio_dir.glob("*.mp3") if path.is_file()}
    manifest_refs, invalid_manifests = _manifest_references(audio_dir)
    code_refs = _code_references(root, set(files))
    referenced = manifest_refs | code_refs
    missing = sorted(name for name in referenced if name not in files)
    unreferenced = sorted(name for name in files if name not in referenced)
    archiveable = [name for name in unreferenced if HASHED_MP3.fullmatch(name)]
    other = [name for name in unreferenced if name not in archiveable]

    return {
        "counts": {
            "root_mp3_files": len(files),
            "manifest_references": len(manifest_refs),
            "code_references": len(code_refs),
            "referenced_existing": sum(name in files for name in referenced),
            "missing_references": len(missing),
            "unreferenced": len(unreferenced),
            "archiveable_generated": len(archiveable),
            "other_unreferenced": len(other),
        },
        "bytes": {
            "root_mp3_files": sum(path.stat().st_size for path in files.values()),
            "unreferenced": sum(files[name].stat().st_size for name in unreferenced),
        },
        "invalid_manifests": invalid_manifests,
        "missing_references": missing,
        "unreferenced": unreferenced,
        "archive_plan": [
            {
                "source": f"audio/{name}",
                "archive": f"audio/archive/unreferenced/{name}",
            }
            for name in archiveable
        ],
        "other_unreferenced": other,
    }


def battle_inventory(root=store.ROOT, current_provenance=None):
    root = pathlib.Path(root)
    current_provenance = (
        store.provenance() if current_provenance is None else current_provenance
    )
    battles = []
    for path in sorted((root / "data" / "battles").glob("*.json")):
        payload = _read_json(path, None)
        if not isinstance(payload, dict):
            battles.append({
                "key": path.stem,
                "a": None,
                "b": None,
                "ready": False,
                "status": "unreadable",
                "reason": "battle cache file is unreadable",
                "command": None,
            })
            continue
        summary = rap.cached_summary(
            payload,
            current_provenance=current_provenance,
            audio_dir=root / "audio",
        )
        battles.append({
            "key": path.stem,
            "a": summary["a"],
            "b": summary["b"],
            "ready": summary["ready"],
            "status": summary["provenance"]["status"],
            "reason": (
                summary["provenance"]["reason"]
                if summary["provenance"]["status"] != "current"
                else (summary["errors"][0] if summary["errors"] else summary["audio"]["reason"])
            ),
            "command": (
                f"python ingest/audit_generated_assets.py --regenerate "
                f"{summary['a']} {summary['b']} --backend anthropic"
                if summary["a"] and summary["b"] and not summary["ready"]
                else None
            ),
        })
    return {
        "counts": {
            "total": len(battles),
            "ready": sum(item["ready"] for item in battles),
            "untrusted": sum(not item["ready"] for item in battles),
        },
        "battles": battles,
    }


def regeneration_preflight():
    cerebras_path = pathlib.Path.home() / ".claude" / "secrets" / "cerebras.json"
    cerebras_payload = _read_json(cerebras_path, {})
    voice_backend = os.environ.get("BOTBEEF_VOICE", "chatterbox")
    ffmpeg = shutil.which("ffmpeg")
    text = {
        "anthropic": {
            "package": importlib.util.find_spec("anthropic") is not None,
            "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        },
        "cerebras": {
            "secret_file": cerebras_path.is_file(),
            "api_key": bool(
                isinstance(cerebras_payload, dict)
                and cerebras_payload.get("api_key")
            ),
        },
    }
    text["anthropic"]["ready"] = all(text["anthropic"].values())
    text["cerebras"]["ready"] = all(text["cerebras"].values())
    return {
        "text_generation": text,
        "voice": {
            "backend": voice_backend,
            "ffmpeg": ffmpeg,
            "elevenlabs_api_key": bool(os.environ.get("ELEVENLABS_API_KEY")),
            "service_reachability": "not-probed",
            "ready": False,
            "reason": (
                "voice services are external and intentionally not contacted by "
                "this read-only audit; verify the configured service before regeneration"
            ),
        },
    }


def report(root=store.ROOT, current_provenance=None):
    return {
        "battles": battle_inventory(root, current_provenance),
        "audio": audio_inventory(root),
        "preflight": regeneration_preflight(),
    }


def _apply_archive(root):
    root = pathlib.Path(root)
    inventory = audio_inventory(root)
    audio_dir = (root / "audio").resolve()
    archive_dir = (audio_dir / "archive" / "unreferenced").resolve()
    if audio_dir not in archive_dir.parents:
        raise RuntimeError("archive directory escaped the audio root")
    archive_dir.mkdir(parents=True, exist_ok=True)

    moves = []
    sources = set()
    destinations = set()
    for item in inventory["archive_plan"]:
        source_path = root / item["source"]
        if source_path.is_symlink():
            raise RuntimeError(f"unsafe archive candidate: {source_path}")
        source = source_path.resolve()
        destination = (root / item["archive"]).resolve()
        if (
            source.parent != audio_dir
            or not source.is_file()
            or not HASHED_MP3.fullmatch(source.name)
        ):
            raise RuntimeError(f"unsafe archive candidate: {source}")
        if destination.parent != archive_dir:
            raise RuntimeError(f"archive destination escaped its directory: {destination}")
        if destination.exists():
            raise RuntimeError(f"archive destination already exists: {destination}")
        if source in sources or destination in destinations:
            raise RuntimeError("archive plan contains duplicate paths")
        sources.add(source)
        destinations.add(destination)
        moves.append((source, destination))

    # Validate the entire plan before moving anything. A late collision or
    # containment failure must leave every candidate in its original place.
    for source, destination in moves:
        source.replace(destination)
    return len(moves)


def _restore_archive(root):
    root = pathlib.Path(root)
    audio_dir = (root / "audio").resolve()
    archive_dir = (audio_dir / "archive" / "unreferenced").resolve()
    candidates = sorted(archive_dir.glob("*.mp3")) if archive_dir.exists() else []
    for source in candidates:
        if source.parent.resolve() != archive_dir or not HASHED_MP3.fullmatch(source.name):
            raise RuntimeError(f"unsafe restore candidate: {source}")
        destination = audio_dir / source.name
        if destination.exists():
            raise RuntimeError(f"restore destination already exists: {destination}")
    for source in candidates:
        source.replace(audio_dir / source.name)
    return len(candidates)


def regenerate(a, b, backend):
    """Regenerate one pair while preserving the last known files on failure."""
    preflight = regeneration_preflight()
    text_status = preflight["text_generation"][backend]
    if not text_status["ready"]:
        missing = [key for key, value in text_status.items() if key != "ready" and not value]
        raise RuntimeError(
            f"{backend} text-generation preflight failed: {', '.join(missing)}"
        )
    voice_status = preflight["voice"]
    if voice_status["backend"] in {"chatterbox", "kokoro"} and not voice_status["ffmpeg"]:
        raise RuntimeError(
            f"{voice_status['backend']} voice preflight failed: ffmpeg is not on PATH"
        )
    if voice_status["backend"] == "elevenlabs" and not voice_status["elevenlabs_api_key"]:
        raise RuntimeError(
            "elevenlabs voice preflight failed: ELEVENLABS_API_KEY is missing"
        )

    battle_path = rap.cache_path(a, b)
    manifest_path = voice.AUDIO / f"manifest__{'__'.join(sorted([a, b]))}.json"
    previous_battle = battle_path.read_bytes() if battle_path.exists() else None
    previous_manifest = manifest_path.read_bytes() if manifest_path.exists() else None

    def restore(path, previous):
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)

    try:
        kept = pregen.one(a, b, backend, no_audio=False)
        payload = _read_json(battle_path, None)
        audit = rap.audit_cached(payload)
        if kept != rap.EXPECTED_BAR_COUNT or not audit["ready"]:
            raise RuntimeError("regenerated pair did not pass the complete readiness audit")
        return audit
    except Exception:
        restore(battle_path, previous_battle)
        restore(manifest_path, previous_manifest)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=store.ROOT)
    parser.add_argument("--apply-archive", action="store_true")
    parser.add_argument("--restore-archive", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--regenerate", nargs=2, metavar=("BOT_A", "BOT_B"))
    parser.add_argument("--backend", choices=("anthropic", "cerebras"), default="anthropic")
    args = parser.parse_args()

    actions = sum(bool(value) for value in (
        args.apply_archive, args.restore_archive, args.regenerate
    ))
    if actions > 1:
        parser.error("choose only one mutation action")
    if args.apply_archive:
        if args.confirm != ARCHIVE_CONFIRMATION:
            parser.error(f"--apply-archive requires --confirm {ARCHIVE_CONFIRMATION}")
        print(json.dumps({"archived": _apply_archive(args.root)}, indent=2))
    elif args.restore_archive:
        print(json.dumps({"restored": _restore_archive(args.root)}, indent=2))
    elif args.regenerate:
        if args.root.resolve() != store.ROOT.resolve():
            parser.error("--root is audit/archive-only and cannot be used with --regenerate")
        audit = regenerate(*args.regenerate, args.backend)
        print(json.dumps({"regenerated": args.regenerate, "audit": audit}, indent=2))
    else:
        print(json.dumps(report(args.root), indent=2))


if __name__ == "__main__":
    main()
