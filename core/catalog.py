"""Canonical, read-only robot presentation catalog.

The project accumulated several overlapping identity sources during the
hackathon: scraped bot metadata, score rows, battle caches, two sprite sets,
and a hand-authored JavaScript combat roster. The shared identity authority is
``static/data/robot-identities.json``; this module joins it with the remaining
operational sources and makes capability checks explicit.

Combat mechanics intentionally remain owned by ``static/js/roster.js``. That
module joins its mechanics profiles to the same JSON registry and fails loudly
if their combat IDs drift. Python never parses or mirrors the JavaScript IDs.
"""

from __future__ import annotations

import json
import pathlib
import threading
from collections import defaultdict
from typing import Iterable, Mapping

from . import rap, score, store


REGISTRY_RELATIVE_PATH = pathlib.Path("static/data/robot-identities.json")
_SNAPSHOTS: dict[str, tuple[tuple, dict]] = {}
_SNAPSHOT_LOCK = threading.Lock()


def _read_json(path: pathlib.Path, default):
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _slug(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value or None


def _registry_records(root: pathlib.Path) -> list[dict]:
    payload = _read_json(root / REGISTRY_RELATIVE_PATH, None)
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("robots"), list)
    ):
        raise ValueError("robot identity registry is missing or has an unsupported schema")

    records = []
    seen = set()
    for item in payload["robots"]:
        slug = _slug(item.get("id")) if isinstance(item, dict) else None
        if (
            not slug
            or slug in seen
            or not isinstance(item.get("name"), str)
            or not item["name"].strip()
        ):
            raise ValueError("robot identity registry has an invalid or duplicate id")
        seen.add(slug)
        records.append({**item, "id": slug})
    return records


def _asset_ids(root: pathlib.Path, directory: str, manifest: str) -> set[str]:
    folder = root / "static" / directory
    payload = _read_json(folder / manifest, [])
    declared = {
        slug
        for item in payload
        if isinstance(item, dict) and (slug := _slug(item.get("slug")))
    }
    present = {path.stem for path in folder.glob("*.png") if path.is_file()}
    # An asset is usable only when the manifest and filesystem agree.
    return declared & present


def _battle_records(
    root: pathlib.Path, current_provenance: Mapping | None
) -> list[dict]:
    records = []
    for path in sorted((root / "data" / "battles").glob("*.json")):
        payload = _read_json(path, None)
        if not isinstance(payload, dict):
            records.append(
                {
                    "key": path.stem,
                    "a": None,
                    "b": None,
                    "bar_count": 0,
                    "is_sourced": False,
                    "has_complete_audio": False,
                    "audio_issue": "invalid_battle_json",
                    "names": {},
                }
            )
            continue
        a, b = _slug(payload.get("a")), _slug(payload.get("b"))
        summary = rap.cached_summary(
            payload,
            current_provenance=current_provenance,
            audio_dir=root / "audio",
        )
        provenance_status = summary["provenance"]["status"]
        content_is_current_and_valid = (
            summary["valid"] and provenance_status == "current"
        )
        names = payload.get("names")
        records.append(
            {
                "key": path.stem,
                "a": a,
                "b": b,
                "bar_count": summary["bars"],
                "is_sourced": content_is_current_and_valid,
                "has_complete_audio": summary["audio"]["complete"],
                "audio_issue": (
                    None
                    if summary["audio"]["complete"]
                    else summary["audio"]["reason"]
                ),
                "provenance_status": provenance_status,
                "validation_errors": summary["errors"],
                "is_ready": summary["ready"],
                "names": names if isinstance(names, dict) else {},
            }
        )
    return records


def build_catalog(
    root: pathlib.Path | str = store.ROOT,
    *,
    registry_records: Iterable[Mapping] | None = None,
    bot_records: Iterable[Mapping] | None = None,
    score_rows: Iterable[Mapping] | None = None,
    current_provenance: Mapping | None = None,
) -> dict[str, dict]:
    """Build the canonical catalog keyed by robot slug.

    Optional record arguments are dependency-injection seams for audits,
    migrations, and unit tests.  The default path is entirely read-only.
    """

    root = pathlib.Path(root)
    current_provenance = (
        store.provenance() if current_provenance is None else current_provenance
    )
    registry_records = list(
        _registry_records(root) if registry_records is None else registry_records
    )
    bot_records = list(store.bots() if bot_records is None else bot_records)
    score_rows = list(
        score.table().get("rows", []) if score_rows is None else score_rows
    )
    standard_ids = _asset_ids(root, "bots", "sprites.json")
    tekken_ids = _asset_ids(root, "bots_tekken", "index.json")
    battles = _battle_records(root, current_provenance)

    registry = {
        slug: dict(item)
        for item in registry_records
        if isinstance(item, Mapping) and (slug := _slug(item.get("id")))
    }
    store_metadata = {
        slug: dict(item)
        for item in bot_records
        if isinstance(item, Mapping) and (slug := _slug(item.get("slug")))
    }
    scores = {
        slug: dict(item)
        for item in score_rows
        if isinstance(item, Mapping) and (slug := _slug(item.get("slug")))
    }

    battle_names: dict[str, str] = {}
    battle_by_bot: dict[str, list[dict]] = defaultdict(list)
    battle_ids = set()
    for battle in battles:
        a, b = battle["a"], battle["b"]
        if not a or not b:
            continue
        battle_ids.update((a, b))
        for slug in (a, b):
            name = battle["names"].get(slug)
            if isinstance(name, str) and name.strip():
                battle_names.setdefault(slug, name.strip())
            opponent = b if slug == a else a
            battle_by_bot[slug].append(
                {
                    "key": battle["key"],
                    "opponent": opponent,
                    "bar_count": battle["bar_count"],
                    "is_sourced": battle["is_sourced"],
                    "has_complete_audio": battle["has_complete_audio"],
                    "is_ready_sourced": battle["is_ready"],
                    "audio_issue": battle["audio_issue"],
                    "provenance_status": battle["provenance_status"],
                    "validation_errors": battle["validation_errors"],
                }
            )

    all_ids = (
        set(registry)
        | set(store_metadata)
        | set(scores)
        | standard_ids
        | tekken_ids
        | battle_ids
    )
    catalog = {}
    for slug in sorted(all_ids):
        identity = registry.get(slug, {})
        legacy_meta = store_metadata.get(slug, {})
        row = scores.get(slug, {})
        cached_battles = sorted(battle_by_bot.get(slug, []), key=lambda item: item["key"])
        display_name = (
            identity.get("name")
            or legacy_meta.get("name")
            or row.get("name")
            or battle_names.get(slug)
            or slug.replace("-", " ").title()
        )
        has_ready_sourced_battle = any(
            battle["is_ready_sourced"] for battle in cached_battles
        )
        declared_assets = (
            identity.get("assets") if isinstance(identity.get("assets"), dict) else {}
        )
        expected_standard = f"/bots/{slug}.png"
        expected_tekken = f"/bots_tekken/{slug}.png"
        standard_asset = (
            expected_standard
            if slug in standard_ids and declared_assets.get("standard") == expected_standard
            else None
        )
        tekken_asset = (
            expected_tekken
            if slug in tekken_ids and declared_assets.get("tekken") == expected_tekken
            else None
        )
        has_any_sprite = bool(standard_asset or tekken_asset)
        catalog[slug] = {
            "slug": slug,
            "name": display_name,
            "weapon": (
                identity.get("weapon")
                or legacy_meta.get("weapon")
                or row.get("weapon")
            ),
            "source_url": (
                identity.get("source_url")
                if store.is_http_url(identity.get("source_url"))
                else legacy_meta.get("source_url")
                if store.is_http_url(legacy_meta.get("source_url"))
                else None
            ),
            "assets": {
                "standard_sprite": standard_asset,
                "tekken_sprite": tekken_asset,
            },
            "capabilities": {
                "has_metadata": slug in registry,
                "has_store_metadata": slug in store_metadata,
                "has_score_data": slug in scores,
                "has_standard_asset_file": slug in standard_ids,
                "has_tekken_asset_file": slug in tekken_ids,
                "has_standard_sprite": standard_asset is not None,
                "has_tekken_sprite": tekken_asset is not None,
                "has_any_sprite": has_any_sprite,
                "has_any_cached_battle": bool(cached_battles),
                "has_ready_sourced_battle": has_ready_sourced_battle,
                "has_combat_profile": identity.get("combat") is True,
                "is_rap_arena_ready": has_any_sprite and has_ready_sourced_battle,
            },
            "battle_count": len(cached_battles),
            "ready_sourced_battle_count": sum(
                battle["is_ready_sourced"] for battle in cached_battles
            ),
            "cached_battles": cached_battles,
        }
    return catalog


def audit_catalog(catalog: Mapping[str, Mapping]) -> dict:
    """Return deterministic identity and readiness drift diagnostics."""

    issue_keys = (
        "score_without_metadata",
        "metadata_without_score",
        "registry_without_store_metadata",
        "store_metadata_without_registry",
        "standard_sprite_without_metadata",
        "tekken_sprite_without_metadata",
        "combat_profile_without_metadata",
        "cached_battle_without_metadata",
        "metadata_missing_standard_sprite",
        "metadata_missing_tekken_sprite",
        "battles_without_sources",
        "battles_without_complete_audio",
    )
    issues = {key: [] for key in issue_keys}
    battle_seen = set()
    ready_battle_seen = set()

    for slug in sorted(catalog):
        entry = catalog[slug]
        flags = entry.get("capabilities", {})
        has_metadata = bool(flags.get("has_metadata"))
        has_store_metadata = bool(flags.get("has_store_metadata"))
        if flags.get("has_score_data") and not has_metadata:
            issues["score_without_metadata"].append(slug)
        if has_metadata and not flags.get("has_score_data"):
            issues["metadata_without_score"].append(slug)
        if has_metadata and not has_store_metadata:
            issues["registry_without_store_metadata"].append(slug)
        if has_store_metadata and not has_metadata:
            issues["store_metadata_without_registry"].append(slug)
        if flags.get("has_standard_asset_file") and not has_metadata:
            issues["standard_sprite_without_metadata"].append(slug)
        if flags.get("has_tekken_asset_file") and not has_metadata:
            issues["tekken_sprite_without_metadata"].append(slug)
        if flags.get("has_combat_profile") and not has_metadata:
            issues["combat_profile_without_metadata"].append(slug)
        if flags.get("has_any_cached_battle") and not has_metadata:
            issues["cached_battle_without_metadata"].append(slug)
        if has_metadata and not flags.get("has_standard_sprite"):
            issues["metadata_missing_standard_sprite"].append(slug)
        if has_metadata and not flags.get("has_tekken_sprite"):
            issues["metadata_missing_tekken_sprite"].append(slug)

        for battle in entry.get("cached_battles", []):
            key = battle.get("key")
            if not key or key in battle_seen:
                continue
            battle_seen.add(key)
            if battle.get("is_ready_sourced"):
                ready_battle_seen.add(key)
            if not battle.get("is_sourced"):
                issues["battles_without_sources"].append(key)
            if not battle.get("has_complete_audio"):
                issues["battles_without_complete_audio"].append(key)

    return {
        "counts": {
            "robots": len(catalog),
            "with_metadata": sum(
                bool(item.get("capabilities", {}).get("has_metadata"))
                for item in catalog.values()
            ),
            "with_score_data": sum(
                bool(item.get("capabilities", {}).get("has_score_data"))
                for item in catalog.values()
            ),
            "with_store_metadata": sum(
                bool(item.get("capabilities", {}).get("has_store_metadata"))
                for item in catalog.values()
            ),
            "with_standard_sprite": sum(
                bool(item.get("capabilities", {}).get("has_standard_sprite"))
                for item in catalog.values()
            ),
            "with_tekken_sprite": sum(
                bool(item.get("capabilities", {}).get("has_tekken_sprite"))
                for item in catalog.values()
            ),
            "with_combat_profile": sum(
                bool(item.get("capabilities", {}).get("has_combat_profile"))
                for item in catalog.values()
            ),
            "with_cached_battle": sum(
                bool(item.get("capabilities", {}).get("has_any_cached_battle"))
                for item in catalog.values()
            ),
            "with_ready_sourced_battle": sum(
                bool(item.get("capabilities", {}).get("has_ready_sourced_battle"))
                for item in catalog.values()
            ),
            "rap_arena_ready": sum(
                bool(item.get("capabilities", {}).get("is_rap_arena_ready"))
                for item in catalog.values()
            ),
            "cached_battles": len(battle_seen),
            "ready_sourced_battles": len(ready_battle_seen),
        },
        "issues": issues,
        "is_clean": not any(issues.values()),
        "limitations": [],
    }


def _source_fingerprint(root: pathlib.Path) -> tuple:
    """Cheap-enough mtime snapshot of every file that can affect the catalog."""

    paths = [
        root / REGISTRY_RELATIVE_PATH,
        root / "data/cache",
        root / "data/cache/bots.json",
        root / "data/cache/fights.json",
        root / "data/cache/chatter.json",
        root / "data/seed/bots.json",
        root / "data/seed/fights.json",
        root / "data/seed/chatter.json",
        root / "audio",
        root / "static/bots",
        root / "static/bots_tekken",
        root / "static/bots/sprites.json",
        root / "static/bots_tekken/index.json",
    ]
    for pattern in (
        "data/battles/*.json",
        "audio/manifest__*.json",
    ):
        paths.extend(root.glob(pattern))

    fingerprint = []
    for path in sorted(set(paths)):
        try:
            stat = path.stat()
            fingerprint.append(
                (path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size)
            )
        except OSError:
            fingerprint.append((path.relative_to(root).as_posix(), None, None))
    return tuple(fingerprint)


def clear_catalog_cache(root: pathlib.Path | str | None = None) -> None:
    """Explicitly invalidate one catalog snapshot, or every snapshot."""

    with _SNAPSHOT_LOCK:
        if root is None:
            _SNAPSHOTS.clear()
        else:
            _SNAPSHOTS.pop(str(pathlib.Path(root).resolve()), None)


def catalog_with_audit(
    root: pathlib.Path | str = store.ROOT,
    **kwargs,
) -> dict:
    """Return an API-ready payload, cached until any input file changes.

    Dependency-injected builds intentionally bypass the process cache so tests
    and migration tools cannot accidentally reuse a production snapshot.
    """

    root = pathlib.Path(root).resolve()
    if kwargs:
        robots = build_catalog(root, **kwargs)
        return {"robots": robots, "audit": audit_catalog(robots)}

    cache_key = str(root)
    fingerprint = _source_fingerprint(root)
    with _SNAPSHOT_LOCK:
        cached = _SNAPSHOTS.get(cache_key)
        if cached and cached[0] == fingerprint:
            return cached[1]

        robots = build_catalog(root)
        payload = {"robots": robots, "audit": audit_catalog(robots)}
        # Store the pre-build fingerprint. If a writer raced the build, the
        # next request sees a different fingerprint and rebuilds safely.
        _SNAPSHOTS[cache_key] = (fingerprint, payload)
        return payload
