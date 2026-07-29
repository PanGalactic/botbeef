import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from core import facts, rap, store


HTTPS_SOURCE = "https://example.test/source/1"


class StoreEncodingTests(unittest.TestCase):
    def test_reads_and_writes_utf8_cache_on_windows(self):
        with tempfile.TemporaryDirectory(dir=store.ROOT) as directory:
            cache = pathlib.Path(directory)
            with patch.object(store, "CACHE", cache):
                store.write_cache("unicode", {"text": "🤖 café"})
                self.assertEqual(
                    store._read(cache / "unicode.json", {}),
                    {"text": "🤖 café"},
                )


class ProvenanceTests(unittest.TestCase):
    def test_requires_http_urls_for_every_fight_and_comment(self):
        fights = [
            {"source": "battlebots", "source_url": HTTPS_SOURCE},
            {"source": "battlebots", "source_url": "not-a-url"},
        ]
        comments = [
            {"source": "brightdata", "url": "https://reddit.com/r/battlebots/1"},
            {"source": "brightdata", "url": None},
        ]
        payloads = {
            "fights": {"fights": fights, "ingested_at": "fight-time"},
            "chatter": {"posts": comments, "ingested_at": "comment-time"},
        }

        with patch.object(store, "fights", return_value=fights), patch.object(
            store, "chatter", return_value=comments
        ), patch.object(store, "load", side_effect=lambda name: payloads[name]):
            result = store.provenance()

        self.assertFalse(result["is_real"])
        self.assertEqual(result["fights_real"], 1)
        self.assertEqual(result["posts_real"], 1)
        self.assertEqual(result["fights_missing_source_url"], 1)
        self.assertEqual(result["posts_missing_source_url"], 1)

    def test_accepts_complete_http_sourced_corpus(self):
        fights = [{"source": "battlebots", "source_url": HTTPS_SOURCE}]
        comments = [{"source": "brightdata", "url": "http://example.test/comment"}]
        payloads = {
            "fights": {"fights": fights},
            "chatter": {"posts": comments},
        }

        with patch.object(store, "fights", return_value=fights), patch.object(
            store, "chatter", return_value=comments
        ), patch.object(store, "load", side_effect=lambda name: payloads[name]):
            self.assertTrue(store.provenance()["is_real"])


class FactGroundingTests(unittest.TestCase):
    def _table(self):
        base = {
            "performance": 50.0,
            "hype": 50.0,
            "expected_hype": 50.0,
            "residual": 0.0,
            "wins": 1,
            "losses": 0,
            "ko_wins": 1,
            "ko_losses": 0,
            "fights": 1,
            "win_rate": 100.0,
            "strength_of_schedule": 50.0,
            "mentions": 1,
            "sentiment": 0.5,
            "engagement": 10,
        }
        return {
            "rows": [
                {**base, "slug": "alpha", "name": "Alpha", "weapon": "hammer"},
                {**base, "slug": "beta", "name": "Beta", "weapon": "drum"},
            ],
            "fit": {},
            "provenance": {},
        }

    def test_aggregate_facts_keep_contributor_urls(self):
        fight = {
            "red": "alpha",
            "blue": "beta",
            "winner": "alpha",
            "method": "KO",
            "source_url": "https://battlebots.com/matches/alpha-beta/",
        }
        comments = [
            {
                "bot": "alpha",
                "text": "Alpha hits hard",
                "score": 10,
                "url": "https://reddit.com/r/battlebots/comments/alpha",
            },
            {
                "bot": "beta",
                "text": "Beta spins fast",
                "score": 9,
                "url": "https://youtube.com/watch?v=beta",
            },
        ]
        records = {
            "alpha": {"fights": [fight]},
            "beta": {"fights": [fight]},
        }
        bots = {
            "alpha": {
                "name": "Alpha",
                "source_url": "https://battlebots.com/robot/alpha/",
            },
            "beta": {
                "name": "Beta",
                "source_url": "https://battlebots.com/robot/beta/",
            },
        }
        hype = {
            "alpha": {"top_post": comments[0]},
            "beta": {"top_post": comments[1]},
        }

        with patch.object(facts.score, "table", return_value=self._table()), patch.object(
            facts.score, "raw_records", return_value=records
        ), patch.object(facts.score, "head_to_head", return_value=[fight]), patch.object(
            facts.score, "hype", return_value=hype
        ), patch.object(facts.store, "bot_index", return_value=bots), patch.object(
            facts.store, "chatter", return_value=comments
        ):
            fact_list, _ = facts.for_matchup("alpha", "beta")

        self.assertTrue(fact_list)
        for fact in fact_list:
            self.assertTrue(store.is_http_url(fact["source_url"]))
            self.assertTrue(fact["source_urls"])
            self.assertTrue(all(store.is_http_url(url) for url in fact["source_urls"]))

        record = next(
            fact for fact in fact_list
            if fact["kind"] == "record" and fact["side"] == "alpha"
        )
        self.assertIn(fight["source_url"], record["source_urls"])

    def test_unsourced_aggregate_facts_are_not_emitted(self):
        fight = {
            "red": "alpha",
            "blue": "beta",
            "winner": "alpha",
            "method": "KO",
            "source_url": None,
        }
        comments = [
            {"bot": "alpha", "text": "Alpha", "score": 1, "url": None},
            {"bot": "beta", "text": "Beta", "score": 1, "url": None},
        ]
        records = {
            "alpha": {"fights": [fight]},
            "beta": {"fights": [fight]},
        }

        with patch.object(facts.score, "table", return_value=self._table()), patch.object(
            facts.score, "raw_records", return_value=records
        ), patch.object(facts.score, "head_to_head", return_value=[fight]), patch.object(
            facts.score, "hype", return_value={}
        ), patch.object(facts.store, "bot_index", return_value={}), patch.object(
            facts.store, "chatter", return_value=comments
        ):
            fact_list, _ = facts.for_matchup("alpha", "beta")

        self.assertEqual(fact_list, [])


class RapValidationTests(unittest.TestCase):
    def test_rejects_unknown_or_unsourced_fact_references(self):
        fact_list = [
            {"id": "F1", "text": "Grounded", "source_url": HTTPS_SOURCE},
            {"id": "F2", "text": "Missing", "source_url": None},
            {"id": "F3", "text": "Bad", "source_url": "javascript:alert(1)"},
        ]
        bars = [
            {"bot": "alpha", "text": "Grounded bar", "fact_id": "f1"},
            {"bot": "alpha", "text": "No source", "fact_id": "F2"},
            {"bot": "beta", "text": "Unsafe source", "fact_id": "F3"},
            {"bot": "beta", "text": "Unknown", "fact_id": "F99"},
        ]

        kept, rejected = rap.validate(bars, fact_list, "alpha", "beta")

        self.assertEqual([bar["fact_id"] for bar in kept], ["F1"])
        self.assertEqual(len(rejected), 3)
        self.assertEqual(kept[0]["source_url"], HTTPS_SOURCE)


class CachedBattleAuditTests(unittest.TestCase):
    def _provenance(self, *, is_real=True, ingested_at="now"):
        return {
            "fights_total": 2,
            "fights_real": 2 if is_real else 0,
            "posts_total": 2,
            "posts_real": 2 if is_real else 0,
            "fights_ingested_at": ingested_at,
            "chatter_ingested_at": ingested_at,
            "is_real": is_real,
        }

    def _battle(self, provenance=None):
        fact = {
            "id": "F1",
            "text": "Alpha beat Beta.",
            "source_url": HTTPS_SOURCE,
            "source_urls": [HTTPS_SOURCE],
        }
        bars = [
            {
                "bot": "alpha" if index % 2 == 0 else "beta",
                "text": f"Grounded bar {index}",
                "fact_id": "F1",
                "fact": fact["text"],
                "source_url": HTTPS_SOURCE,
                "source_urls": [HTTPS_SOURCE],
            }
            for index in range(rap.EXPECTED_BAR_COUNT)
        ]
        return {
            "a": "alpha",
            "b": "beta",
            "bars": bars,
            "facts": [fact],
            "context": {"provenance": provenance or self._provenance()},
        }

    def _write_audio(self, audio_dir):
        intro = audio_dir / "intro.mp3"
        intro.write_bytes(b"audio")
        entries = []
        for index in range(rap.EXPECTED_BAR_COUNT):
            filename = f"{index}.mp3"
            (audio_dir / filename).write_bytes(b"audio")
            entries.append({"index": index, "file": filename})
        (audio_dir / "manifest__alpha__beta.json").write_text(
            json.dumps({"intro": intro.name, "bars": entries}),
            encoding="utf-8",
        )

    def test_current_sourced_battle_with_complete_audio_is_ready(self):
        with tempfile.TemporaryDirectory(dir=store.ROOT) as directory:
            audio_dir = pathlib.Path(directory)
            self._write_audio(audio_dir)
            result = rap.cached_summary(
                self._battle(),
                current_provenance=self._provenance(),
                audio_dir=audio_dir,
            )

        self.assertTrue(result["valid"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["provenance"]["status"], "current")
        self.assertTrue(result["audio"]["complete"])
        self.assertEqual(result["bars"], rap.EXPECTED_BAR_COUNT)

    def test_stale_or_placeholder_battle_is_flagged_and_rejected_on_read(self):
        with tempfile.TemporaryDirectory(dir=store.ROOT) as directory:
            battles_dir = pathlib.Path(directory)
            payload = self._battle(self._provenance(is_real=False, ingested_at="old"))
            (battles_dir / "alpha__beta.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            summary = rap.cached_summary(
                payload, current_provenance=self._provenance(), audio_dir=battles_dir
            )
            with patch.object(rap, "BATTLES", battles_dir), patch.object(
                store, "provenance", return_value=self._provenance()
            ):
                with self.assertRaisesRegex(ValueError, "not trusted"):
                    rap.battle("alpha", "beta")

        self.assertFalse(summary["ready"])
        self.assertEqual(summary["provenance"]["status"], "placeholder")

    def test_real_looking_battle_from_an_older_corpus_is_stale(self):
        old_provenance = self._provenance(ingested_at="old")
        payload = self._battle(old_provenance)

        result = rap.audit_cached(
            payload,
            current_provenance=self._provenance(ingested_at="new"),
            audio_dir=store.ROOT / "does-not-exist",
        )

        self.assertTrue(result["valid"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["provenance"]["status"], "stale")

    def test_malformed_and_unsourced_bars_fail_closed(self):
        payload = self._battle()
        payload["bars"][0]["source_url"] = None
        payload["bars"][0]["source_urls"] = []
        payload["bars"][1]["fact_id"] = "UNKNOWN"

        result = rap.audit_cached(
            payload,
            current_provenance=self._provenance(),
            audio_dir=store.ROOT / "does-not-exist",
        )

        self.assertFalse(result["valid"])
        self.assertFalse(result["ready"])
        self.assertTrue(any("no citation" in error for error in result["errors"]))
        self.assertTrue(any("unknown fact" in error for error in result["errors"]))

    def test_audio_manifest_rejects_paths_outside_audio_directory(self):
        with tempfile.TemporaryDirectory(dir=store.ROOT) as directory:
            audio_dir = pathlib.Path(directory)
            outside = audio_dir.parent / "outside.mp3"
            outside.write_bytes(b"audio")
            manifest = {
                "intro": "../outside.mp3",
                "bars": [
                    {"index": index, "file": "../outside.mp3"}
                    for index in range(rap.EXPECTED_BAR_COUNT)
                ],
            }
            (audio_dir / "manifest__alpha__beta.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            try:
                result = rap.audit_cached(
                    self._battle(),
                    current_provenance=self._provenance(),
                    audio_dir=audio_dir,
                )
            finally:
                outside.unlink()

        self.assertFalse(result["audio"]["complete"])
        self.assertEqual(result["audio"]["clips_present"], 0)
        self.assertFalse(result["audio"]["intro_present"])

    def test_reversed_request_keeps_cached_orientation_explicit(self):
        with tempfile.TemporaryDirectory(dir=store.ROOT) as directory:
            battles_dir = pathlib.Path(directory)
            payload = self._battle()
            self._write_audio(battles_dir)
            (battles_dir / "alpha__beta.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with patch.object(rap, "BATTLES", battles_dir), patch.object(
                rap, "AUDIO", battles_dir
            ), patch.object(store, "provenance", return_value=self._provenance()):
                result = rap.battle("beta", "alpha")

        self.assertEqual((result["a"], result["b"]), ("alpha", "beta"))
        self.assertTrue(result["orientation"]["normalized"])
        self.assertEqual(result["orientation"]["requested_a"], "beta")

    def test_cached_read_rejects_battle_with_missing_audio(self):
        with tempfile.TemporaryDirectory(dir=store.ROOT) as directory:
            battles_dir = pathlib.Path(directory)
            (battles_dir / "alpha__beta.json").write_text(
                json.dumps(self._battle()), encoding="utf-8"
            )
            with patch.object(rap, "BATTLES", battles_dir), patch.object(
                rap, "AUDIO", battles_dir
            ), patch.object(store, "provenance", return_value=self._provenance()):
                with self.assertRaisesRegex(ValueError, "audio manifest is missing"):
                    rap.battle("alpha", "beta")

    def test_list_cached_does_not_expose_entries_without_robot_ids(self):
        with tempfile.TemporaryDirectory(dir=store.ROOT) as directory:
            battles_dir = pathlib.Path(directory)
            (battles_dir / "broken.json").write_text("{", encoding="utf-8")
            (battles_dir / "missing-ids.json").write_text(
                json.dumps({"bars": []}), encoding="utf-8"
            )
            with patch.object(rap, "BATTLES", battles_dir), patch.object(
                store, "provenance", return_value=self._provenance()
            ):
                result = rap.list_cached()

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
