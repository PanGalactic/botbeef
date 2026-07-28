import unittest
from unittest.mock import patch

from app import app


class ArenaMVPTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_arena_loads_game_modules_not_rap_battle_api(self):
        response = self.client.get("/arena")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('from "/js/combat-engine.js"', html)
        self.assertIn('from "/js/roster.js"', html)
        self.assertIn('from "/js/fighter-select.js"', html)
        self.assertIn("new CombatEngine", html)
        self.assertNotIn("new THREE.MeshBasicMaterial({color,", html)
        self.assertNotIn("new THREE.MeshStandardMaterial({color,", html)
        self.assertNotIn("/api/battle/", html)

    def test_game_assets_are_served_locally(self):
        assets = [
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
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_presentation_has_no_external_runtime_dependency(self):
        css = self.client.get("/css/arena-fighter.css").get_data(as_text=True)
        self.assertNotIn("@import", css)
        self.assertNotIn("https://", css)
        self.assertNotIn("http://", css)

    def test_health_is_local_and_does_not_probe_optional_voice_services(self):
        with patch("core.voice.requests.get", side_effect=AssertionError("network probe")):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["voice"]["availability"], "not-probed")


if __name__ == "__main__":
    unittest.main()
