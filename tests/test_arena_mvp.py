import re
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app


class ArenaMVPTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.static_root = Path(app.root_path) / app.static_folder

    def get_text(self, path):
        response = self.client.get(path)
        try:
            return response.status_code, response.get_data(as_text=True)
        finally:
            response.close()

    def get_json(self, path):
        response = self.client.get(path)
        try:
            return response.status_code, response.get_json()
        finally:
            response.close()

    def test_fight_mode_loads_the_one_combat_engine_not_rap_battle_api(self):
        status, html = self.get_text("/fight")
        self.assertEqual(status, 200)
        self.assertIn('from "/js/combat-engine.js"', html)
        self.assertIn('from "/js/roster.js"', html)
        self.assertIn('from "/js/fighter-select.js"', html)
        self.assertIn("new CombatEngine", html)
        self.assertIn('href="/rap"', html)
        self.assertIn('href="/fight" aria-current="page"', html)
        self.assertNotIn("new THREE.MeshBasicMaterial({color,", html)
        self.assertNotIn("new THREE.MeshStandardMaterial({color,", html)
        self.assertNotIn("/api/battle/", html)

    def test_modes_are_two_controllers_on_one_shared_app(self):
        gateway_status, gateway_html = self.get_text("/")
        rap_status, rap_html = self.get_text("/rap")
        fight_status, fight_html = self.get_text("/fight")
        legacy_status, legacy_html = self.get_text("/arena")

        self.assertEqual(gateway_status, 200)
        self.assertEqual(rap_status, 200)
        self.assertEqual(fight_status, 200)
        self.assertEqual(legacy_status, 200)

        self.assertIn('href="/rap"', gateway_html)
        self.assertIn('href="/fight"', gateway_html)
        self.assertIn('href="/fight"', rap_html)
        self.assertIn('href="/rap"', fight_html)
        self.assertIn("fetch('/api/table')", rap_html)
        self.assertIn("fetch('/api/battles')", rap_html)
        self.assertIn("/api/battle/", rap_html)
        self.assertNotIn("CombatEngine", rap_html)
        self.assertIn("new CombatEngine", fight_html)
        self.assertEqual(fight_html, legacy_html)

        fight_rules = {
            rule.rule: rule.endpoint
            for rule in app.url_map.iter_rules()
            if rule.rule in {"/fight", "/arena"}
        }
        self.assertEqual(
            fight_rules,
            {"/fight": "fight_mode", "/arena": "fight_mode"},
        )
        self.assertIn("fight_mode", app.view_functions)

    def test_fight_roster_uses_shared_canonical_robot_identities(self):
        roster_source = (self.static_root / "js" / "roster.js").read_text(encoding="utf-8")
        canonical_ids = set(re.findall(r'^    id: "([^"]+)",$', roster_source, re.MULTILINE))
        self.assertEqual(len(canonical_ids), 6)

        bots_status, bots_payload = self.get_json("/api/bots")
        table_status, table_payload = self.get_json("/api/table")
        self.assertEqual(bots_status, 200)
        self.assertEqual(table_status, 200)

        bots = bots_payload["bots"]
        backend_ids = {bot["slug"] for bot in bots}
        rap_table_ids = {row["slug"] for row in table_payload["rows"]}
        self.assertTrue(canonical_ids <= backend_ids)
        self.assertTrue(canonical_ids <= rap_table_ids)

        for slug in canonical_ids:
            with self.subTest(slug=slug):
                self.assertIn(f'image: "/bots/{slug}.png"', roster_source)
                response = self.client.get(f"/bots/{slug}.png")
                try:
                    self.assertEqual(response.status_code, 200)
                finally:
                    response.close()

    def test_repository_has_one_fight_only_combat_engine(self):
        production_sources = [
            path
            for pattern in ("*.html", "*.js")
            for path in self.static_root.rglob(pattern)
        ]
        definitions = [
            path.relative_to(self.static_root).as_posix()
            for path in production_sources
            if re.search(
                r"\bexport\s+class\s+CombatEngine\b",
                path.read_text(encoding="utf-8"),
            )
        ]
        engine_imports = [
            (
                path.relative_to(self.static_root).as_posix(),
                match.group(1),
            )
            for path in production_sources
            for match in re.finditer(
                r'\bfrom\s+["\']([^"\']*combat-engine\.js)["\']',
                path.read_text(encoding="utf-8"),
            )
        ]

        self.assertEqual(definitions, ["js/combat-engine.js"])
        self.assertEqual(engine_imports, [("arena.html", "/js/combat-engine.js")])

        _, rap_html = self.get_text("/rap")
        self.assertNotIn("CombatEngine", rap_html)
        self.assertNotIn("combat-engine.js", rap_html)

    def test_game_assets_are_served_locally(self):
        assets = [
            "/css/mode-switcher.css",
            "/js/combat-engine.js",
            "/js/roster.js",
            "/js/fighter-select.js",
            "/css/arena-fighter.css",
            "/vendor/three.module.js",
            "/arena/floor.png",
            "/arena/arena.png",
            "/bots/witch-doctor.png",
            "/bots/tombstone.png",
            "/bots/hypershock.png",
            "/bots/minotaur.png",
            "/bots/huge.png",
            "/bots/cobalt.png",
        ]
        for path in assets:
            with self.subTest(path=path):
                response = self.client.get(path)
                try:
                    self.assertEqual(response.status_code, 200)
                finally:
                    response.close()

    def test_presentation_has_no_external_runtime_dependency(self):
        _, css = self.get_text("/css/arena-fighter.css")
        self.assertNotIn("@import", css)
        self.assertNotIn("https://", css)
        self.assertNotIn("http://", css)

    def test_health_is_local_and_does_not_probe_optional_voice_services(self):
        with patch("core.voice.requests.get", side_effect=AssertionError("network probe")):
            status, payload = self.get_json("/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["voice"]["availability"], "not-probed")


if __name__ == "__main__":
    unittest.main()
