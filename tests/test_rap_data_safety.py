import json
from pathlib import Path

from core import score


def test_data_view_only_builds_selectors_from_audited_playable_pairs():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")

    assert "b.playable === true" in html
    assert "const readySlugs = new Set(PLAYABLE.flatMap" in html
    assert ".filter(row => readySlugs.has(row.slug))" in html
    assert "function playablePartners(slug)" in html
    assert "function syncOpponentOptions" in html
    assert "TABLE.rows.filter(row => partners.has(row.slug))" in html
    assert "$('#selA').onchange = () => {" in html
    assert "syncOpponentOptions();" in html


def test_data_view_has_empty_and_tamper_safe_states():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")

    assert "No verified rap battles are ready." in html
    assert "$('#selA').disabled = PLAYABLE.length === 0" in html
    assert "$('#play').disabled = true" in html
    assert "if (!isPlayablePair(a, b))" in html
    assert "That matchup is not verified for playback." in html


def test_canonical_identity_corrects_lowercase_table_label():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    identities = json.loads(
        (root / "static" / "data" / "robot-identities.json").read_text(
            encoding="utf-8"
        )
    )
    canonical = {robot["id"]: robot["name"] for robot in identities["robots"]}
    table_row = next(row for row in score.table()["rows"] if row["slug"] == "bloodsport")

    assert table_row["name"] == "bloodsport"
    assert canonical["bloodsport"] == "Bloodsport"
    assert 'fetch(\'/data/robot-identities.json\')' in html
    assert "canonicalName(row.slug, row.name)" in html
    assert "canonicalName(r.slug, r.name)" in html
    assert "canonicalName(x.bot, d.names?.[x.bot])" in html
