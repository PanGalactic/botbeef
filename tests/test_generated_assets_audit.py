import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core import rap, voice
from ingest import audit_generated_assets as audit


def write(path: Path, content: bytes = b"audio"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_audio_inventory_counts_manifest_code_and_archive_candidates(tmp_path):
    write(tmp_path / "audio" / "1111111111111111.mp3")
    write(tmp_path / "audio" / "2222222222222222.mp3")
    write(tmp_path / "audio" / "3333333333333333.mp3")
    write(tmp_path / "audio" / "friendly-name.mp3")
    (tmp_path / "audio" / "manifest__alpha__beta.json").write_text(
        json.dumps({
            "intro": "1111111111111111.mp3",
            "bars": [{"index": 0, "file": "1111111111111111.mp3"}],
        }),
        encoding="utf-8",
    )
    write(
        tmp_path / "static" / "reference.js",
        b'const clip = "2222222222222222.mp3";',
    )

    result = audit.audio_inventory(tmp_path)

    assert result["counts"] == {
        "root_mp3_files": 4,
        "manifest_references": 1,
        "code_references": 1,
        "referenced_existing": 2,
        "missing_references": 0,
        "unreferenced": 2,
        "archiveable_generated": 1,
        "other_unreferenced": 1,
    }
    assert result["archive_plan"] == [{
        "source": "audio/3333333333333333.mp3",
        "archive": "audio/archive/unreferenced/3333333333333333.mp3",
    }]
    assert result["other_unreferenced"] == ["friendly-name.mp3"]


def test_archive_is_reversible_and_never_moves_friendly_names(tmp_path):
    generated = tmp_path / "audio" / "aaaaaaaaaaaaaaaa.mp3"
    friendly = tmp_path / "audio" / "keep-me.mp3"
    write(generated)
    write(friendly)

    assert audit._apply_archive(tmp_path) == 1
    assert not generated.exists()
    assert friendly.exists()
    assert (tmp_path / "audio" / "archive" / "unreferenced" / generated.name).exists()

    assert audit._restore_archive(tmp_path) == 1
    assert generated.exists()
    assert friendly.exists()


def test_archive_collision_leaves_every_source_unmoved(tmp_path):
    first = tmp_path / "audio" / "1111111111111111.mp3"
    second = tmp_path / "audio" / "2222222222222222.mp3"
    collision = (
        tmp_path / "audio" / "archive" / "unreferenced" / second.name
    )
    write(first)
    write(second)
    write(collision, b"existing archive")

    with pytest.raises(RuntimeError, match="destination already exists"):
        audit._apply_archive(tmp_path)

    assert first.read_bytes() == b"audio"
    assert second.read_bytes() == b"audio"
    assert collision.read_bytes() == b"existing archive"
    assert not (
        tmp_path / "audio" / "archive" / "unreferenced" / first.name
    ).exists()


def test_archive_rejects_source_path_that_resolves_outside_audio(tmp_path):
    filename = "aaaaaaaaaaaaaaaa.mp3"
    outside = tmp_path / "outside" / filename
    write(outside)
    malicious_plan = {
        "archive_plan": [{
            "source": f"outside/{filename}",
            "archive": f"audio/archive/unreferenced/{filename}",
        }]
    }

    with patch.object(audit, "audio_inventory", return_value=malicious_plan):
        with pytest.raises(RuntimeError, match="unsafe archive candidate"):
            audit._apply_archive(tmp_path)

    assert outside.exists()


def test_transactional_regeneration_restores_battle_and_manifest_on_failure(tmp_path):
    battles = tmp_path / "data" / "battles"
    audio = tmp_path / "audio"
    battle_path = battles / "alpha__beta.json"
    manifest_path = audio / "manifest__alpha__beta.json"
    write(battle_path, b'{"old":"battle"}')
    write(manifest_path, b'{"old":"manifest"}')

    def failing_generation(*_args, **_kwargs):
        battle_path.write_text('{"new":"battle"}', encoding="utf-8")
        manifest_path.write_text('{"new":"manifest"}', encoding="utf-8")
        raise RuntimeError("tts failed")

    ready_preflight = {
        "text_generation": {
            "anthropic": {"package": True, "api_key": True, "ready": True}
        },
        "voice": {
            "backend": "elevenlabs",
            "ffmpeg": None,
            "elevenlabs_api_key": True,
        },
    }
    with patch.object(rap, "BATTLES", battles), patch.object(
        voice, "AUDIO", audio
    ), patch.object(audit, "regeneration_preflight", return_value=ready_preflight), patch.object(
        audit.pregen, "one", side_effect=failing_generation
    ):
        with pytest.raises(RuntimeError, match="tts failed"):
            audit.regenerate("alpha", "beta", "anthropic")

    assert battle_path.read_bytes() == b'{"old":"battle"}'
    assert manifest_path.read_bytes() == b'{"old":"manifest"}'


def test_archive_requires_explicit_confirmation():
    parser_source = Path(audit.__file__).read_text(encoding="utf-8")
    assert "--apply-archive requires --confirm" in parser_source
    assert audit.ARCHIVE_CONFIRMATION == "ARCHIVE_UNREFERENCED_GENERATED_AUDIO"


def test_regeneration_stops_before_writes_when_backend_is_not_configured():
    blocked = {
        "text_generation": {
            "anthropic": {"package": False, "api_key": False, "ready": False}
        },
        "voice": {
            "backend": "chatterbox",
            "ffmpeg": None,
            "elevenlabs_api_key": False,
        },
    }
    with patch.object(audit, "regeneration_preflight", return_value=blocked), patch.object(
        audit.pregen, "one"
    ) as generate:
        with pytest.raises(RuntimeError, match="text-generation preflight failed"):
            audit.regenerate("alpha", "beta", "anthropic")

    generate.assert_not_called()
