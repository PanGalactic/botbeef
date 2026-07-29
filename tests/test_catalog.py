import json
from pathlib import Path
from unittest.mock import patch

from core import catalog, store


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def touch_asset(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"asset")


def current_provenance():
    return {
        "is_real": True,
        "fights_total": 1,
        "fights_real": 1,
        "posts_total": 1,
        "posts_real": 1,
        "fights_ingested_at": "fight-time",
        "chatter_ingested_at": "chatter-time",
    }


def valid_battle(a="alpha", b="beta"):
    fact = {
        "id": "F1",
        "text": "Grounded fact",
        "source_url": "https://example.test/source",
    }
    return {
        "a": a,
        "b": b,
        "names": {a: a.title(), b: b.title()},
        "facts": [fact],
        "bars": [
            {
                "bot": a if index % 2 == 0 else b,
                "text": f"Grounded bar {index}",
                "fact_id": "F1",
                "fact": fact["text"],
                "source_url": fact["source_url"],
            }
            for index in range(16)
        ],
        "context": {"provenance": current_provenance()},
    }


def test_catalog_joins_identities_and_requires_sourced_audio_ready_battles(tmp_path):
    write_json(
        tmp_path / "static/bots/sprites.json",
        [{"slug": "alpha"}, {"slug": "beta"}, {"slug": "sprite-only"}],
    )
    for slug in ("alpha", "beta", "sprite-only"):
        touch_asset(tmp_path, f"static/bots/{slug}.png")
    write_json(
        tmp_path / "static/bots_tekken/index.json",
        [{"slug": "alpha"}, {"slug": "declared-but-missing"}],
    )
    touch_asset(tmp_path, "static/bots_tekken/alpha.png")

    write_json(
        tmp_path / "data/battles/alpha__beta.json",
        valid_battle(),
    )
    audio_entries = []
    for index in range(16):
        filename = f"bar-{index}.mp3"
        audio_entries.append({"index": index, "file": filename})
        touch_asset(tmp_path, f"audio/{filename}")
    touch_asset(tmp_path, "audio/intro.mp3")
    write_json(
        tmp_path / "audio/manifest__alpha__beta.json",
        {"intro": "intro.mp3", "bars": audio_entries},
    )

    write_json(
        tmp_path / "data/battles/beta__gamma.json",
        {
            "a": "beta",
            "b": "gamma",
            "names": {"gamma": "Gamma From Battle"},
            "bars": [{"bot": "beta", "text": "Unsourced"}],
        },
    )

    robots = catalog.build_catalog(
        tmp_path,
        registry_records=[
            {
                "id": "alpha",
                "name": "Alpha Canonical",
                "weapon": "hammer",
                "source_url": "https://example.test/robots/alpha",
                "assets": {
                    "standard": "/bots/alpha.png",
                    "tekken": "/bots_tekken/alpha.png",
                },
                "combat": True,
            },
            {
                "id": "beta",
                "name": "Beta Canonical",
                "weapon": "drum",
                "assets": {"standard": "/bots/beta.png"},
            },
            {"id": "registry-only", "name": "Registry Only", "assets": {}},
        ],
        bot_records=[
            {
                "slug": "alpha",
                "name": "Alpha Canonical",
                "weapon": "hammer",
                "source_url": "https://example.test/robots/alpha",
            },
            {"slug": "beta", "name": "Beta Canonical", "weapon": "drum"},
        ],
        score_rows=[
            {"slug": "beta", "name": "Beta Score", "weapon": "spinner"},
            {"slug": "score-only", "name": "Score Only"},
        ],
        current_provenance=current_provenance(),
    )

    assert set(robots) == {
        "alpha",
        "beta",
        "gamma",
        "registry-only",
        "score-only",
        "sprite-only",
    }
    assert robots["alpha"]["name"] == "Alpha Canonical"
    assert robots["alpha"]["assets"] == {
        "standard_sprite": "/bots/alpha.png",
        "tekken_sprite": "/bots_tekken/alpha.png",
    }
    assert robots["alpha"]["capabilities"]["has_ready_sourced_battle"]
    assert robots["alpha"]["ready_sourced_battle_count"] == 1
    assert robots["beta"]["battle_count"] == 2
    assert robots["beta"]["ready_sourced_battle_count"] == 1
    assert robots["gamma"]["name"] == "Gamma From Battle"
    assert not robots["gamma"]["capabilities"]["has_metadata"]
    assert not robots["gamma"]["capabilities"]["has_ready_sourced_battle"]
    assert not robots["score-only"]["capabilities"]["has_metadata"]
    # A manifest declaration without its PNG is not a usable asset.
    assert "declared-but-missing" not in robots

    audit = catalog.audit_catalog(robots)
    assert audit["counts"]["cached_battles"] == 2
    assert audit["issues"]["score_without_metadata"] == ["score-only"]
    assert audit["issues"]["registry_without_store_metadata"] == ["registry-only"]
    assert audit["issues"]["combat_profile_without_metadata"] == []
    assert audit["issues"]["standard_sprite_without_metadata"] == ["sprite-only"]
    assert audit["issues"]["cached_battle_without_metadata"] == ["gamma"]
    assert audit["issues"]["battles_without_sources"] == ["beta__gamma"]
    assert audit["issues"]["battles_without_complete_audio"] == ["beta__gamma"]
    assert not audit["is_clean"]


def test_incomplete_or_missing_audio_files_prevent_ready_status(tmp_path):
    write_json(tmp_path / "static/bots/sprites.json", [])
    write_json(tmp_path / "static/bots_tekken/index.json", [])
    write_json(
        tmp_path / "data/battles/alpha__beta.json",
        valid_battle(),
    )
    for index in range(1, 16):
        touch_asset(tmp_path, f"audio/bar-{index}.mp3")
    touch_asset(tmp_path, "outside.mp3")
    write_json(
        tmp_path / "audio/manifest__alpha__beta.json",
        {
            "intro": "../outside.mp3",
            "bars": [
                {"index": 0, "file": "../outside.mp3"},
                *[
                    {"index": index, "file": f"bar-{index}.mp3"}
                    for index in range(1, 16)
                ],
            ],
        },
    )

    robots = catalog.build_catalog(
        tmp_path,
        registry_records=[
            {"id": "alpha", "name": "Alpha", "assets": {}},
            {"id": "beta", "name": "Beta", "assets": {}},
        ],
        bot_records=[{"slug": "alpha"}, {"slug": "beta"}],
        score_rows=[],
        current_provenance=current_provenance(),
    )

    battle = robots["alpha"]["cached_battles"][0]
    assert battle["is_sourced"]
    assert not battle["has_complete_audio"]
    assert battle["audio_issue"] == "manifest has missing or invalid audio files"
    assert battle["validation_errors"] == []
    assert battle["provenance_status"] == "current"
    assert not battle["is_ready_sourced"]
    assert not robots["alpha"]["capabilities"]["has_ready_sourced_battle"]


def test_repository_catalog_exposes_known_cross_source_drift():
    robots = catalog.build_catalog(store.ROOT)
    audit = catalog.audit_catalog(robots)

    assert robots["bloodsport"]["capabilities"]["has_score_data"]
    assert robots["bloodsport"]["capabilities"]["has_metadata"]
    assert not robots["bloodsport"]["capabilities"]["has_store_metadata"]
    assert robots["bloodsport"]["capabilities"]["has_standard_sprite"]
    assert robots["bloodsport"]["capabilities"]["is_rap_arena_ready"]
    assert robots["cobalt"]["capabilities"]["has_combat_profile"]
    assert robots["cobalt"]["capabilities"]["has_ready_sourced_battle"]
    assert audit["issues"]["score_without_metadata"] == []
    assert "bloodsport" in audit["issues"]["registry_without_store_metadata"]
    assert audit["issues"]["cached_battle_without_metadata"] == []
    assert "bite-force__black-dragon" in audit["issues"]["battles_without_sources"]
    assert audit["counts"]["cached_battles"] == 16
    assert not audit["is_clean"]


def test_catalog_snapshot_reuses_build_and_invalidates_on_registry_change(tmp_path):
    write_json(
        tmp_path / "static/data/robot-identities.json",
        {"version": 1, "robots": []},
    )
    write_json(tmp_path / "static/bots/sprites.json", [])
    write_json(tmp_path / "static/bots_tekken/index.json", [])
    catalog.clear_catalog_cache(tmp_path)

    with patch.object(catalog.store, "bots", return_value=[]), patch.object(
        catalog.store, "provenance", return_value=current_provenance()
    ), patch.object(
        catalog.score,
        "table",
        return_value={"rows": [], "provenance": current_provenance()},
    ), patch.object(
        catalog, "build_catalog", wraps=catalog.build_catalog
    ) as build:
        first = catalog.catalog_with_audit(tmp_path)
        second = catalog.catalog_with_audit(tmp_path)
        assert first is second
        assert build.call_count == 1

        write_json(
            tmp_path / "static/data/robot-identities.json",
            {
                "version": 1,
                "robots": [{"id": "new-bot", "name": "New Bot", "assets": {}}],
            },
        )
        third = catalog.catalog_with_audit(tmp_path)

    assert build.call_count == 2
    assert "new-bot" in third["robots"]
    assert third is not first
    catalog.clear_catalog_cache(tmp_path)
