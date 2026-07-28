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


if __name__ == "__main__":
    unittest.main()
