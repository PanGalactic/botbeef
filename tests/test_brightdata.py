import json
from unittest.mock import Mock

import pytest

from ingest import brightdata


class Response:
    def __init__(self, body):
        self._body = body
        self.text = json.dumps(body)

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def test_token_is_environment_only(monkeypatch):
    monkeypatch.delenv("BRIGHTDATA_API_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="BRIGHTDATA_API_TOKEN"):
        brightdata.token()
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "temporary-secret")
    assert brightdata.token() == "temporary-secret"


def test_trigger_applies_output_limit_without_logging_token(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "temporary-secret")
    post = Mock(return_value=Response({"snapshot_id": "s_123"}))
    monkeypatch.setattr(brightdata.requests, "post", post)

    assert brightdata.trigger(
        "dataset",
        [{"url": "https://example.test"}],
        limit_per_input=25,
    ) == "s_123"
    call = post.call_args
    assert call.kwargs["params"]["limit_per_input"] == 25
    assert call.kwargs["headers"]["Authorization"] == "Bearer temporary-secret"


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({
            "url": "https://www.reddit.com/r/battlebots/comments/abc/thread/",
            "community_name": "battlebots",
        }, True),
        ({
            "url": "https://www.reddit.com/r/battlebots_fake/comments/abc/thread/",
            "community_name": "battlebots_fake",
        }, False),
        ({
            "url": "https://evil.test/r/battlebots/comments/abc/thread/",
            "community_name": "battlebots",
        }, False),
    ],
)
def test_reddit_filter_requires_exact_public_community(row, expected):
    assert brightdata._is_reddit_battlebots(row) is expected


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({
            "url": "https://www.youtube.com/watch?v=abc",
            "channel_url": "https://www.youtube.com/@BattleBots",
            "title": "BattleBots Pro League Episode 1",
        }, True),
        ({
            "url": "https://www.youtube.com/watch?v=abc",
            "youtuber": "Unofficial BattleBots Clips",
            "title": "BattleBots Pro League Episode 1",
        }, False),
        ({
            "url": "https://evil.test/watch?v=abc",
            "youtuber": "BattleBots",
            "title": "BattleBots Pro League Episode 1",
        }, False),
    ],
)
def test_youtube_filter_requires_official_channel_and_video_url(row, expected):
    assert brightdata._is_official_youtube(row) is expected


def test_start_bulk_reserves_discovery_inside_global_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(brightdata, "SNAPSHOTS", tmp_path / "snapshots.json")
    snapshots = iter(["reddit-snapshot", "youtube-snapshot"])
    trigger = Mock(side_effect=lambda *args, **kwargs: next(snapshots))
    monkeypatch.setattr(brightdata, "trigger", trigger)

    state = brightdata.start_bulk(3, 2, 20)

    assert state["discovery_record_cap"] == 5
    assert state["max_records"] == 20
    assert json.loads(brightdata.SNAPSHOTS.read_text())["discover"] == {
        "reddit": "reddit-snapshot",
        "youtube": "youtube-snapshot",
    }
    # Each discovery input already carries its dataset-specific output limit
    # (`num_of_posts`). Adding the generic `limit_per_input` query parameter
    # causes Reddit discovery to reject the request with HTTP 400.
    assert "limit_per_input" not in trigger.call_args_list[0].kwargs
    assert "limit_per_input" not in trigger.call_args_list[1].kwargs


def test_start_bulk_rejects_cap_that_cannot_cover_discovery(monkeypatch, tmp_path):
    monkeypatch.setattr(brightdata, "SNAPSHOTS", tmp_path / "snapshots.json")
    monkeypatch.setattr(brightdata, "trigger", Mock())
    with pytest.raises(ValueError, match="discovery cap"):
        brightdata.start_bulk(3, 2, 5)
    brightdata.trigger.assert_not_called()


def test_start_bulk_refuses_to_repeat_completed_paid_run(monkeypatch, tmp_path):
    monkeypatch.setattr(brightdata, "SNAPSHOTS", tmp_path / "snapshots.json")
    brightdata._save_state({"stage": "complete", "result": {"comments": 10}})
    monkeypatch.setattr(brightdata, "trigger", Mock())

    with pytest.raises(RuntimeError, match="already complete"):
        brightdata.start_bulk(3, 2, 20)

    brightdata.trigger.assert_not_called()


def test_resume_triggers_only_missing_discovery_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(brightdata, "SNAPSHOTS", tmp_path / "snapshots.json")
    state = {
        "stage": "discovery_trigger",
        "discover": {"reddit": "already-paid"},
        "limits": {"max_reddit_posts": 3, "max_youtube_videos": 2},
        "max_records": 20,
        "discovery_record_cap": 5,
    }
    brightdata._save_state(state)
    trigger = Mock(return_value="new-youtube")
    monkeypatch.setattr(brightdata, "trigger", trigger)
    # Stop after verifying trigger recovery; snapshot polling is independent.
    monkeypatch.setattr(
        brightdata, "finish_discovery",
        Mock(side_effect=TimeoutError("still running")),
    )

    with pytest.raises(TimeoutError, match="still running"):
        brightdata.resume(1)

    trigger.assert_called_once()
    assert trigger.call_args.args[0] == brightdata.DATASETS["youtube_videos"]
    persisted = json.loads(brightdata.SNAPSHOTS.read_text())
    assert persisted["discover"] == {
        "reddit": "already-paid",
        "youtube": "new-youtube",
    }


def test_finish_discovery_caps_comment_outputs_and_hands_sources_to_corpus(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(brightdata, "SNAPSHOTS", tmp_path / "snapshots.json")
    reddit = [{
        "url": "https://reddit.com/r/battlebots/comments/a/thread/",
        "community_name": "battlebots",
        "title": "Episode",
        "num_comments": 500,
    }]
    youtube = [{
        "url": "https://youtube.com/watch?v=b",
        "channel_url": "https://youtube.com/@battlebots",
        "title": "BattleBots Pro League",
        "num_comments": 900,
    }]
    monkeypatch.setattr(
        brightdata, "wait_snapshot",
        lambda snapshot, deadline: reddit if snapshot == "r" else youtube,
    )
    trigger = Mock(side_effect=["rc", "yc"])
    monkeypatch.setattr(brightdata, "trigger", trigger)
    monkeypatch.setattr(brightdata.corpus, "connect", Mock(return_value=object()))
    monkeypatch.setattr(brightdata.corpus, "init_db", Mock())
    ingest_sources = Mock()
    monkeypatch.setattr(brightdata.corpus, "ingest_sources", ingest_sources)
    state = {
        "stage": "discovery",
        "discover": {"reddit": "r", "youtube": "y"},
        "limits": {"max_reddit_posts": 3, "max_youtube_videos": 2},
        "max_records": 20,
        "discovery_record_cap": 5,
    }

    result = brightdata.finish_discovery(state, 100.0)

    # Two discovery records leave 18 records. Two selected sources receive a
    # hard API limit of 9 each, keeping the global upper bound at 20.
    assert result["limit_per_source"] == 9
    assert result["comment_record_cap"] == 18
    assert result["comments"] == {"reddit": "rc", "youtube": "yc"}
    assert all(call.kwargs["limit_per_input"] == 9 for call in trigger.call_args_list)
    assert ingest_sources.call_count == 2


def test_finish_comments_is_resumable_without_reingesting_completed_platform(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(brightdata, "SNAPSHOTS", tmp_path / "snapshots.json")
    db = object()
    monkeypatch.setattr(brightdata.corpus, "connect", Mock(return_value=db))
    monkeypatch.setattr(brightdata.corpus, "init_db", Mock())
    ingest = Mock(return_value={"comments": 1, "links": 1, "ignored": 0})
    monkeypatch.setattr(brightdata.corpus, "ingest_comments", ingest)
    monkeypatch.setattr(brightdata.corpus, "export_chatter", Mock())
    monkeypatch.setattr(
        brightdata.corpus, "stats", Mock(return_value={"comments": 2})
    )
    wait = Mock(return_value=[{"comment_id": "2"}])
    monkeypatch.setattr(brightdata, "wait_snapshot", wait)
    state = {
        "stage": "comments",
        "comments": {"reddit": "r", "youtube": "y"},
        "completed_comments": {
            "reddit": {
                "delivered_records": 1,
                "ingest": {"comments": 1, "links": 1, "ignored": 0},
            }
        },
        "selected": {
            "reddit": ["https://reddit.com/r/battlebots/comments/a/x/"],
            "youtube": ["https://youtube.com/watch?v=b"],
        },
        "limit_per_source": 5,
        "max_records": 12,
        "discovery_delivered_records": 2,
    }

    result = brightdata.finish_comments(state, 100.0)

    wait.assert_called_once_with("y", 100.0)
    ingest.assert_called_once_with(db, "youtube", [{"comment_id": "2"}])
    assert result["delivered_records"] == 4
    assert result["delivered_comment_records"] == 2
    assert result["delivered_discovery_records"] == 2
    assert result["total_brightdata_records"] == 4
    assert state["stage"] == "complete"


def test_state_refuses_credential_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(brightdata, "SNAPSHOTS", tmp_path / "snapshots.json")
    with pytest.raises(ValueError, match="credential"):
        brightdata._save_state({"stage": "discovery", "api_token": "secret"})
    assert not brightdata.SNAPSHOTS.exists()
