import unittest
from pathlib import Path


class RapArenaRestorationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.html = (cls.root / "static" / "rap-arena.html").read_text(encoding="utf-8")
        cls.script = (cls.root / "static" / "js" / "rap-arena.js").read_text(encoding="utf-8")
        cls.styles = (cls.root / "static" / "css" / "rap-arena.css").read_text(encoding="utf-8")

    def test_restored_arena_is_separate_from_combat(self):
        self.assertIn('src="/js/rap-arena.js"', self.html)
        self.assertIn('href="/fight"', self.html)
        self.assertNotIn("CombatEngine", self.html)
        self.assertNotIn("combat-engine.js", self.script)

    def test_historical_presentation_features_remain(self):
        for feature in (
            "makeFighter",
            "wideShot",
            "cutTo",
            "ROUND",
            "Fan hype",
            "Record",
            'id="subtitle"',
            'id="citation"',
        ):
            with self.subTest(feature=feature):
                self.assertIn(feature, self.html + self.script)

    def test_modern_audio_controller_and_local_assets_are_used(self):
        self.assertIn("RapAudioController", self.script)
        self.assertIn("selectBeatForMatchup", self.script)
        self.assertIn("manifest: payload.audio || silentManifest", self.script)
        self.assertIn('$("#round").textContent = "INTRO"', self.script)
        self.assertIn('$("#round").textContent = "K.O."', self.script)
        self.assertIn(
            'window.setTimeout(() => $("#splash").classList.remove("gone"), reducedMotion ? 0 : 1800)',
            self.script,
        )
        self.assertIn('"/beats/beats.json"', self.script)
        self.assertIn('"../vendor/three.module.js"', self.script)
        self.assertNotIn("https://", self.html)
        self.assertNotIn("http://", self.html)
        self.assertNotIn("@import", self.styles)

    def test_data_and_citation_handling_are_defensive(self):
        self.assertIn('loadJson("/api/battles")', self.script)
        self.assertIn('loadJson("/api/table")', self.script)
        self.assertIn("battleIsPlayable", self.script)
        self.assertIn("safeSourceUrl", self.script)
        self.assertIn("bar.source_urls", self.script)
        self.assertIn('link.rel = "noopener noreferrer"', self.script)
        self.assertIn("replaceChildren", self.script)
        self.assertNotIn("innerHTML", self.script)

    def test_citations_cap_inline_links_and_disclose_the_rest(self):
        self.assertIn("const INLINE_SOURCE_LIMIT = 3", self.script)
        self.assertIn("sources.slice(0, INLINE_SOURCE_LIMIT)", self.script)
        self.assertIn("sources.slice(INLINE_SOURCE_LIMIT)", self.script)
        self.assertIn('document.createElement("details")', self.script)
        self.assertIn('document.createElement("summary")', self.script)
        self.assertIn("more source", self.script)
        self.assertIn(".citation-more ul", self.styles)
        self.assertIn("max-height: 180px", self.styles)

    def test_controls_have_accessible_names_and_live_status(self):
        self.assertIn("<label>", self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('role="status"', self.html)
        self.assertIn("@media (max-width: 680px)", self.styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.styles)

    def test_matchup_preview_exposes_verified_story_fields(self):
        for identifier in (
            'id="preview-heading"',
            'id="preview-beat"',
            'id="preview-score"',
            'id="preview-audio"',
            'id="preview-fact"',
            'id="preview-source"',
        ):
            with self.subTest(identifier=identifier):
                self.assertIn(identifier, self.html)
        self.assertIn("selectPreviewFact", self.script)
        self.assertIn("formatScoreComparison", self.script)
        self.assertIn("formatAudioState", self.script)
        self.assertIn("formatBeat", self.script)
        self.assertIn('aria-busy="true"', self.html)

    def test_canonical_registry_owns_names_and_verified_asset_paths(self):
        self.assertIn('loadJson("/data/robot-identities.json"', self.script)
        self.assertIn("resolveDisplayName", self.script)
        self.assertIn("identity: identities.get(slug)", self.script)
        self.assertIn("resolveSpritePath", self.script)
        self.assertIn("identity?.assets?.standard === expectedStandard", self.script)
        self.assertIn("identity?.assets?.tekken === expectedTekken", self.script)

    def test_webgl_failure_has_functional_reduced_graphics_fallback(self):
        self.assertIn('id="fallback-scene"', self.html)
        self.assertIn('id="fallback-a"', self.html)
        self.assertIn('id="fallback-b"', self.html)
        self.assertIn('id="graphics-notice"', self.html)
        self.assertIn('id="graphics-toggle"', self.html)
        self.assertIn('"webglcontextlost"', self.script)
        self.assertIn("activateFallback", self.script)
        self.assertIn("stageFallbackFighters", self.script)
        self.assertIn("3D graphics are unavailable", self.script)
        self.assertIn(".fallback-scene:not([hidden])", self.styles)

    def test_projector_and_mobile_landscape_layout_hooks_exist(self):
        self.assertIn("@media (orientation: landscape) and (max-height: 560px)", self.styles)
        self.assertIn("@media (min-width: 1600px)", self.styles)
        self.assertIn("reducedMotion", self.script)


if __name__ == "__main__":
    unittest.main()
