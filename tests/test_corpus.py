import json

from ingest import corpus


def database(tmp_path):
    db = corpus.connect(tmp_path / "corpus.sqlite3")
    corpus.init_db(db)
    return db


def test_match_robots_uses_boundaries_and_avoids_lowercase_huge():
    assert corpus.match_robots("That was a huge hit!") == []
    assert corpus.match_robots("The gap is huge, but Minotaur can close it.") == [
        ("minotaur", 0.98, "explicit:minotaur")
    ]
    assert corpus.match_robots("HUGE versus Witch Doctor will be wild") == [
        ("huge", 0.98, "explicit:HUGE"),
        ("witch-doctor", 0.98, "explicit:witch doctor"),
    ]
    assert corpus.match_robots("The end game plan needs work.") == [
        ("end-game", 0.98, "explicit:end game")
    ]


def test_normalize_youtube_schema_hashes_author_and_parses_counts():
    row = {
        "commentId": "UC123",
        "comment_text": "Minotaur is an amazing beast",
        "url": "https://www.youtube.com/watch?v=abc123&utm_source=x",
        "comment_url": "https://www.youtube.com/watch?v=abc123&lc=UC123",
        "user_name": "Real Person",
        "likes": "1.2K",
        "reply_count": "4",
        "comment_date": "2026-07-28",
        "nested": {"authorName": "Nested Person", "safe": "kept"},
    }

    item = corpus.normalize_comment("YouTube", row)

    assert item["external_id"] == "UC123"
    assert item["parent_url"] == "https://www.youtube.com/watch?v=abc123"
    assert item["url"].endswith("lc=UC123")
    assert item["score"] == 1200
    assert item["replies"] == 4
    assert item["author_hash"]
    assert "Real Person" not in item["raw_json"]
    assert "Nested Person" not in item["raw_json"]
    assert json.loads(item["raw_json"])["nested"]["safe"] == "kept"


def test_normalize_reddit_schema_and_fallback_id_are_stable():
    base = {
        "body": "Cobalt's weapon looked broken",
        "permalink": "/r/battlebots/comments/post/title/comment/",
        "username": "fan",
        "score": "2,345",
        "created_utc": 1785280000,
    }
    first = corpus.normalize_comment(
        "reddit",
        {
            **base,
            "post_url": "https://www.reddit.com/r/battlebots/comments/post/title/?utm_source=x",
        },
    )
    second = corpus.normalize_comment(
        "reddit",
        {
            **base,
            "post_url": "https://reddit.com/r/battlebots/comments/post/title/",
        },
    )

    assert first["parent_url"] == "https://reddit.com/r/battlebots/comments/post/title"
    assert first["url"] == "https://reddit.com/r/battlebots/comments/post/title/comment/"
    assert first["external_id"] == second["external_id"]
    assert first["score"] == 2345
    assert first["theme"] in {"durability", "weapon_reliability"}
    assert first["stance"] == "critical"


def test_invalid_or_unsupported_comments_are_rejected():
    assert corpus.normalize_comment(
        "reddit", {"comment": "Minotaur", "url": ""}
    ) is None
    assert corpus.normalize_comment(
        "youtube", {"comment_text": "", "url": "https://youtube.com/watch?v=x"}
    ) is None
    try:
        corpus.normalize_comment("x", {})
    except ValueError as error:
        assert "unsupported platform" in str(error)
    else:
        raise AssertionError("unsupported platform should fail")


def test_ingest_deduplicates_and_rebuilds_robot_links(tmp_path):
    db = database(tmp_path)
    corpus.ingest_sources(
        db,
        "youtube",
        [{
            "video_id": "vid",
            "url": "https://youtube.com/watch?v=vid",
            "title": "BattleBots Pro League",
            "comments": "10",
        }],
    )
    initial = {
        "comment_id": "same-comment",
        "comment_text": "Minotaur is amazing",
        "url": "https://youtube.com/watch?v=vid",
        "likes": 10,
    }
    result = corpus.ingest_comments(db, "youtube", [initial, initial])

    assert result == {
        "comments": 2,
        "inserted": 1,
        "updated": 1,
        "links": 2,
        "ignored": 0,
    }
    assert corpus.stats(db)["comments"] == 1
    assert [
        row["robot_slug"]
        for row in db.execute("SELECT robot_slug FROM comment_robots")
    ] == ["minotaur"]

    changed = {
        **initial,
        "comment_text": "Cobalt is amazing",
        "likes": 12,
    }
    corpus.ingest_comments(db, "youtube", [changed])

    assert corpus.stats(db)["comments"] == 1
    assert [
        row["robot_slug"]
        for row in db.execute("SELECT robot_slug FROM comment_robots")
    ] == ["cobalt"]
    stored = db.execute("SELECT text, score FROM comments").fetchone()
    assert (stored["text"], stored["score"]) == ("Cobalt is amazing", 12)


def test_single_robot_source_context_links_implicit_comment(tmp_path):
    db = database(tmp_path)
    corpus.ingest_sources(
        db,
        "reddit",
        [{
            "post_id": "post",
            "url": "https://reddit.com/r/battlebots/comments/post/minotaur/",
            "title": "Minotaur post-fight discussion",
        }],
    )
    corpus.ingest_comments(
        db,
        "reddit",
        [{
            "comment_id": "implicit",
            "comment": "Its driving was brilliant.",
            "post_url": "https://reddit.com/r/battlebots/comments/post/minotaur/",
        }],
    )

    link = db.execute(
        "SELECT robot_slug, confidence, match_basis FROM comment_robots"
    ).fetchone()
    assert dict(link) == {
        "robot_slug": "minotaur",
        "confidence": 0.55,
        "match_basis": "single-source-context",
    }


def test_youtube_source_prefers_watch_url_and_merges_legacy_stream_row(tmp_path):
    db = database(tmp_path)
    watch_url = "https://www.youtube.com/watch?v=vid"
    db.execute(
        """
        INSERT INTO sources(
            platform, external_id, url, title, scraped_at
        ) VALUES ('youtube', 'vid', ?, 'Legacy title', ?)
        """,
        ("https://rr1.googlevideo.com/videoplayback", corpus.now()),
    )
    legacy_id = db.execute(
        "SELECT id FROM sources WHERE title='Legacy title'"
    ).fetchone()["id"]
    db.execute(
        """
        INSERT INTO comments(
            platform, external_id, source_id, url, text, score, replies,
            sentiment, theme, stance, claim_type, scraped_at
        ) VALUES ('youtube', 'comment', ?, ?, 'It was brilliant', 0, 0,
                  1, 'general', 'praise', 'fan_opinion', ?)
        """,
        (legacy_id, watch_url, corpus.now()),
    )
    db.commit()

    corpus.ingest_sources(
        db,
        "youtube",
        [{
            "video_id": "vid",
            "url": watch_url,
            "video_url": "https://rr2.googlevideo.com/videoplayback",
            "title": "Minotaur highlights",
        }],
    )

    sources = db.execute(
        "SELECT id, url FROM sources WHERE platform='youtube'"
    ).fetchall()
    assert [row["url"] for row in sources] == [watch_url]
    assert db.execute(
        "SELECT source_id FROM comments WHERE external_id='comment'"
    ).fetchone()["source_id"] == sources[0]["id"]
    assert dict(db.execute(
        "SELECT robot_slug, match_basis FROM comment_robots"
    ).fetchone()) == {
        "robot_slug": "minotaur",
        "match_basis": "single-source-context",
    }


def test_export_writes_requested_cache_shape_without_duplicates(tmp_path):
    db = database(tmp_path)
    source = "https://www.youtube.com/watch?v=vid"
    corpus.ingest_sources(
        db,
        "youtube",
        [{"video_id": "vid", "url": source, "title": "Minotaur highlights"}],
    )
    corpus.ingest_comments(
        db,
        "youtube",
        [{
            "comment_id": "one",
            "comment_text": "Minotaur is underrated",
            "url": source,
            "comment_url": f"{source}&lc=one",
            "likes": 7,
        }],
    )

    target = tmp_path / "nested" / "chatter.json"
    returned = corpus.export_chatter(db, target)
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert returned == target
    assert payload["database"] == "corpus.sqlite3"
    assert len(payload["posts"]) == 1
    assert payload["posts"][0] == {
        "id": "youtube-one-minotaur",
        "platform": "youtube",
        "bot": "minotaur",
        "text": "Minotaur is underrated",
        "score": 7,
        "replies": 0,
        "sentiment": 1.0,
        "theme": "hype",
        "stance": "praise",
        "confidence": 0.98,
        "match_basis": "explicit:minotaur",
        "published_at": None,
        "url": f"{source}&lc=one",
        "context_url": source,
        "source": "brightdata",
    }
    assert payload["ingested_at"]
