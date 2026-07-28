#!/usr/bin/env python3
"""One-time Bright Data bulk ingestion for BOT BEEF.

This command deliberately has no scheduler or background refresh loop. It:

1. discovers current BattleBots Pro League Reddit posts and YouTube videos;
2. caps the selected source set before comments are requested;
3. collects those comment threads once;
4. deduplicates and indexes them in SQLite; and
5. exports the local JSON cache consumed by the demo.

The Bright Data token is read only from the process environment (or the
legacy local secret file). It is never written to the database or repository.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
from typing import Iterable
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import store  # noqa: E402
from ingest import corpus  # noqa: E402

API = "https://api.brightdata.com"
UNLOCKER_ZONE = os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "mcp_unlocker")
SNAPSHOTS = store.RAW / "snapshots.json"

DATASETS = {
    "reddit_posts": "gd_lvz8ah06191smkebj4",
    "reddit_comments": "gd_lvzdpsdlw09j6t702",
    "youtube_videos": "gd_lk56epmy2i5g7lzu0k",
    "youtube_comments": "gd_lk9q0ew71spt1mxywf",
}


def token() -> str:
    """Return the process-only credential.

    Deliberately do not support secret files: a one-time scrape should not
    persist a token, and the state file must remain safe to inspect or commit
    accidentally.
    """
    value = os.environ.get("BRIGHTDATA_API_TOKEN")
    if not value:
        raise SystemExit(
            "No Bright Data token. Set BRIGHTDATA_API_TOKEN in the current process."
        )
    return value


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
    }


def unlock(url: str, fmt: str = "raw") -> str:
    """Return one page through Web Unlocker; used by other ingest modules."""
    response = requests.post(
        f"{API}/request",
        headers=headers(),
        json={"zone": UNLOCKER_ZONE, "url": url, "format": fmt},
        timeout=120,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        # Bright Data returns useful schema/account diagnostics in the body.
        # Do not include request headers, which contain the bearer token.
        detail = response.text.strip()[:1000]
        raise RuntimeError(
            f"Bright Data trigger failed ({response.status_code}): {detail}"
        ) from exc
    return response.text


def trigger(
    dataset_id: str,
    inputs: list[dict],
    *,
    discover_by: str | None = None,
    limit_per_input: int | None = None,
) -> str:
    params = {
        "dataset_id": dataset_id,
        "include_errors": "true",
    }
    if discover_by:
        params.update({"type": "discover_new", "discover_by": discover_by})
    if limit_per_input is not None:
        if limit_per_input < 1:
            raise ValueError("limit_per_input must be positive")
        params["limit_per_input"] = limit_per_input
    response = requests.post(
        f"{API}/datasets/v3/trigger",
        headers=headers(),
        params=params,
        json=inputs,
        timeout=120,
    )
    response.raise_for_status()
    body = response.json()
    snapshot_id = body.get("snapshot_id")
    if not snapshot_id:
        raise RuntimeError("Bright Data did not return a snapshot id")
    return snapshot_id


def progress(snapshot_id: str) -> dict:
    response = requests.get(
        f"{API}/datasets/v3/progress/{snapshot_id}",
        headers=headers(),
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def fetch_snapshot(snapshot_id: str) -> list[dict]:
    response = requests.get(
        f"{API}/datasets/v3/snapshot/{snapshot_id}",
        headers=headers(),
        params={"format": "json"},
        timeout=300,
    )
    response.raise_for_status()
    body = response.json()
    if isinstance(body, dict):
        return body.get("data") or body.get("results") or [body]
    return body


def wait_snapshot(snapshot_id: str, deadline: float) -> list[dict]:
    while True:
        state = progress(snapshot_id)
        status = str(state.get("status") or "").lower()
        if status == "ready":
            return fetch_snapshot(snapshot_id)
        if status in {"failed", "canceled", "cancelled"}:
            raise RuntimeError(f"snapshot {snapshot_id} ended with status {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"snapshot {snapshot_id} is still {status or 'running'}; "
                "rerun `python ingest/brightdata.py resume` later"
            )
        time.sleep(10)


def _save_state(state: dict) -> None:
    # Snapshot state contains job ids, limits and source URLs only. Refuse an
    # accidental credential-shaped field before it reaches disk.
    forbidden = {"token", "api_token", "api_key", "authorization"}
    if any(str(key).lower() in forbidden for key in _walk_keys(state)):
        raise ValueError("refusing to persist credential-like state")
    store.RAW.mkdir(parents=True, exist_ok=True)
    temporary = SNAPSHOTS.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, SNAPSHOTS)


def _load_state() -> dict:
    if not SNAPSHOTS.exists():
        raise SystemExit("no bulk-ingest state found; run `brightdata.py bulk` first")
    return json.loads(SNAPSHOTS.read_text(encoding="utf-8"))


def _walk_keys(value) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _canonical_url(row: dict) -> str:
    return str(row.get("url") or row.get("post_url") or row.get("video_url") or "")


def _safe_host(parsed) -> str:
    return (parsed.hostname or "").lower().removeprefix("www.")


def _is_reddit_battlebots(row: dict) -> bool:
    parsed = urlparse(_canonical_url(row))
    if _safe_host(parsed) not in {"reddit.com", "old.reddit.com"}:
        return False
    if not re.match(r"^/r/battlebots/comments/[^/]+(?:/|$)", parsed.path, re.I):
        return False
    community = str(row.get("community_name") or "").strip().removeprefix("r/")
    community_url = urlparse(str(row.get("community_url") or ""))
    community_path_ok = bool(
        re.fullmatch(r"/r/battlebots/?", community_url.path, re.I)
    )
    return community.lower() == "battlebots" or community_path_ok


def _is_official_youtube(row: dict) -> bool:
    parsed = urlparse(_canonical_url(row))
    host = _safe_host(parsed)
    is_video = (
        host == "youtu.be" and bool(parsed.path.strip("/"))
    ) or (
        host in {"youtube.com", "m.youtube.com"}
        and parsed.path == "/watch"
        and bool(parse_qs(parsed.query).get("v"))
    )
    if not is_video:
        return False
    names = {
        str(row.get(field) or "").strip().lower().removeprefix("@")
        for field in ("youtuber", "handle_name", "channel_name")
    }
    channel = urlparse(str(row.get("channel_url") or ""))
    channel_is_official = (
        _safe_host(channel) in {"youtube.com", "m.youtube.com"}
        and channel.path.rstrip("/").lower() == "/@battlebots"
    )
    title = str(row.get("title") or "").lower()
    return (
        ("battlebots" in names or channel_is_official)
        and ("pro league" in title or "battlebots" in title)
    )


def _select_with_budget(
    rows: Iterable[dict],
    predicate,
    *,
    max_sources: int,
    remaining_records: int,
    default_comments: int,
) -> tuple[list[dict], int]:
    candidates = [row for row in rows if predicate(row)]
    candidates.sort(
        key=lambda row: (
            int(row.get("num_comments") or 0),
            int(row.get("num_upvotes") or row.get("views") or 0),
        ),
        reverse=True,
    )
    # The API-enforced limit_per_input controls spend. Use metadata estimates
    # for reporting/ranking only, rather than letting one huge thread eliminate
    # breadth from the source corpus.
    selected = candidates[:min(max_sources, remaining_records)]
    predicted = min(
        remaining_records,
        sum(max(1, int(row.get("num_comments") or default_comments)) for row in selected),
    )
    return selected, predicted


def _validate_limits(
    max_reddit_posts: int,
    max_youtube_videos: int,
    max_records: int,
) -> None:
    if max_reddit_posts < 0 or max_youtube_videos < 0:
        raise ValueError("source limits cannot be negative")
    if not max_reddit_posts and not max_youtube_videos:
        raise ValueError("at least one discovery source is required")
    # Discovery can deliver at most the requested number of posts/videos. Keep
    # that inside the same paid-record ceiling and reserve at least one comment.
    discovery_cap = max_reddit_posts + max_youtube_videos
    if max_records <= discovery_cap:
        raise ValueError(
            f"max_records must exceed the discovery cap ({discovery_cap})"
        )


def _trigger_missing_discovery(state: dict) -> dict:
    """Trigger only discovery jobs not already recorded in resumable state."""
    max_reddit_posts = state["limits"]["max_reddit_posts"]
    max_youtube_videos = state["limits"]["max_youtube_videos"]
    if max_reddit_posts:
        if "reddit" not in state["discover"]:
            state["discover"]["reddit"] = trigger(
                DATASETS["reddit_posts"],
                [{
                    "keyword": "BattleBots Pro League",
                    "date": "Past month",
                    "num_of_posts": max_reddit_posts,
                }],
                discover_by="keyword",
            )
            _save_state(state)
    if max_youtube_videos:
        if "youtube" not in state["discover"]:
            state["discover"]["youtube"] = trigger(
                DATASETS["youtube_videos"],
                [{
                    "keyword": "BattleBots Pro League powered by Bright Data",
                    "num_of_posts": max_youtube_videos,
                }],
                discover_by="keyword",
            )
            _save_state(state)
    state["stage"] = "discovery"
    _save_state(state)
    return state


def start_bulk(
    max_reddit_posts: int,
    max_youtube_videos: int,
    max_records: int,
) -> dict:
    """Trigger the cheap discovery stage and persist resumable snapshot ids."""
    _validate_limits(max_reddit_posts, max_youtube_videos, max_records)
    if SNAPSHOTS.exists():
        previous = _load_state()
        if previous.get("stage") != "complete":
            raise RuntimeError(
                "an unfinished bulk scrape exists; run `brightdata.py resume`"
            )
        raise RuntimeError(
            "the one-time bulk scrape is already complete; use the existing "
            "SQLite database/cache (remove data/raw/snapshots.json explicitly "
            "only if you intend to spend credits on a new collection)"
        )
    state = {
        "stage": "discovery_trigger",
        "discover": {},
        "limits": {
            "max_reddit_posts": max_reddit_posts,
            "max_youtube_videos": max_youtube_videos,
        },
        "max_records": max_records,
        "discovery_record_cap": max_reddit_posts + max_youtube_videos,
    }
    _save_state(state)
    return _trigger_missing_discovery(state)


def _trigger_missing_comments(state: dict) -> dict:
    """Trigger only comment jobs not already recorded in resumable state."""
    per_source_limit = state["limit_per_source"]
    reddit_urls = state["selected"]["reddit"]
    youtube_urls = state["selected"]["youtube"]
    if reddit_urls and "reddit" not in state["comments"]:
        state["comments"]["reddit"] = trigger(
            DATASETS["reddit_comments"],
            [{"url": url, "days_back": 60} for url in reddit_urls],
            limit_per_input=per_source_limit,
        )
        _save_state(state)
    if youtube_urls and "youtube" not in state["comments"]:
        state["comments"]["youtube"] = trigger(
            DATASETS["youtube_comments"],
            [{"url": url} for url in youtube_urls],
            limit_per_input=per_source_limit,
        )
        _save_state(state)
    state["stage"] = "comments"
    _save_state(state)
    return state


def finish_discovery(state: dict, deadline: float) -> dict:
    max_records = int(state["max_records"])
    reddit_rows = (
        wait_snapshot(state["discover"]["reddit"], deadline)
        if state["discover"].get("reddit")
        else []
    )
    youtube_rows = (
        wait_snapshot(state["discover"]["youtube"], deadline)
        if state["discover"].get("youtube")
        else []
    )
    discovery_delivered = len(reddit_rows) + len(youtube_rows)
    if discovery_delivered > state["discovery_record_cap"]:
        raise RuntimeError(
            "discovery exceeded its configured output cap; comments were not triggered"
        )
    comment_budget = max_records - discovery_delivered
    if comment_budget < 1:
        raise RuntimeError("no records remain for comments after discovery")

    reddit_selected, reddit_estimate = _select_with_budget(
        reddit_rows,
        _is_reddit_battlebots,
        max_sources=state["limits"]["max_reddit_posts"],
        remaining_records=comment_budget,
        default_comments=100,
    )
    remaining = max(0, comment_budget - len(reddit_selected))
    youtube_selected, youtube_estimate = _select_with_budget(
        youtube_rows,
        _is_official_youtube,
        max_sources=state["limits"]["max_youtube_videos"],
        remaining_records=max(1, remaining),
        default_comments=500,
    )
    if not reddit_selected and not youtube_selected:
        raise RuntimeError(
            "discovery returned no verified BattleBots sources; no comment credits spent"
        )

    db = corpus.connect()
    corpus.init_db(db)
    corpus.ingest_sources(db, "reddit", reddit_selected)
    corpus.ingest_sources(db, "youtube", youtube_selected)

    all_selected = len(reddit_selected) + len(youtube_selected)
    per_source_limit = comment_budget // all_selected
    if per_source_limit < 1:
        raise RuntimeError("record cap is too small for the selected source set")
    comment_cap = per_source_limit * all_selected

    state.update({
        "stage": "comment_trigger",
        "comments": {},
        "completed_comments": {},
        "selected": {
            "reddit": [_canonical_url(row) for row in reddit_selected],
            "youtube": [_canonical_url(row) for row in youtube_selected],
        },
        "predicted_comment_records": min(
            reddit_estimate + youtube_estimate, comment_cap
        ),
        "comment_record_cap": comment_cap,
        "limit_per_source": per_source_limit,
        "discovery_delivered_records": discovery_delivered,
    })
    _save_state(state)
    return _trigger_missing_comments(state)


def finish_comments(state: dict, deadline: float) -> dict:
    db = corpus.connect()
    corpus.init_db(db)
    completed = state.setdefault("completed_comments", {})
    for platform, snapshot_id in state.get("comments", {}).items():
        if platform in completed:
            continue
        rows = wait_snapshot(snapshot_id, deadline)
        delivered = len(rows)
        if delivered > state["limit_per_source"] * len(state["selected"][platform]):
            raise RuntimeError(
                f"{platform} comments exceeded the configured output cap"
            )
        completed[platform] = {
            "delivered_records": delivered,
            "ingest": corpus.ingest_comments(db, platform, rows),
        }
        _save_state(state)
    corpus.export_chatter(db)
    result = corpus.stats(db)
    delivered = sum(
        item["delivered_records"] for item in completed.values()
    )
    total_delivered = state.get("discovery_delivered_records", 0) + delivered
    if total_delivered > state["max_records"]:
        raise RuntimeError("Bright Data delivered more records than the global cap")
    result.update({
        "delivered_records": total_delivered,
        "delivered_comment_records": delivered,
        "delivered_discovery_records": state.get(
            "discovery_delivered_records", 0
        ),
        "total_brightdata_records": total_delivered,
        # Bright Data rates depend on the account, dataset and hack-night
        # credits. Report the enforceable record ceiling instead of inventing
        # a dollar estimate that may not match the user's plan.
        "configured_record_cap": state["max_records"],
        "ingest": {
            platform: item["ingest"] for platform, item in completed.items()
        },
    })
    state.update({"stage": "complete", "result": result})
    _save_state(state)
    return result


def resume(wait_seconds: int, max_records: int | None = None) -> dict:
    state = _load_state()
    if max_records is not None and max_records != state.get("max_records"):
        raise ValueError(
            "cannot change max_records after discovery has started"
        )
    deadline = time.monotonic() + wait_seconds
    if state["stage"] == "discovery_trigger":
        state = _trigger_missing_discovery(state)
    if state["stage"] == "discovery":
        state = finish_discovery(state, deadline)
    if state["stage"] == "comment_trigger":
        state = _trigger_missing_comments(state)
    if state["stage"] == "comments":
        return finish_comments(state, deadline)
    return state.get("result") or {}


def bulk(
    *,
    max_reddit_posts: int,
    max_youtube_videos: int,
    max_records: int,
    wait_seconds: int,
) -> dict:
    start_bulk(max_reddit_posts, max_youtube_videos, max_records)
    return resume(wait_seconds)


def status() -> dict:
    db = corpus.connect()
    corpus.init_db(db)
    result = {
        "token": "set" if os.environ.get("BRIGHTDATA_API_TOKEN") else "missing",
        "database": str(corpus.DB_PATH),
        "corpus": corpus.stats(db),
        "provenance": store.provenance(),
    }
    if SNAPSHOTS.exists():
        state = _load_state()
        result["bulk_stage"] = state.get("stage")
        result["selected"] = state.get("selected")
        result["predicted_comment_records"] = state.get("predicted_comment_records")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    bulk_parser = sub.add_parser("bulk", help="run the one-time bulk scrape")
    bulk_parser.add_argument("--max-reddit-posts", type=int, default=12)
    bulk_parser.add_argument("--max-youtube-videos", type=int, default=5)
    bulk_parser.add_argument(
        "--max-records",
        type=int,
        default=6000,
        help="preflight cap based on source comment counts",
    )
    bulk_parser.add_argument("--wait-seconds", type=int, default=900)

    resume_parser = sub.add_parser("resume", help="resume persisted snapshots")
    resume_parser.add_argument("--wait-seconds", type=int, default=900)
    resume_parser.add_argument("--max-records", type=int)

    sub.add_parser("status")

    args = parser.parse_args()
    if args.command == "bulk":
        result = bulk(
            max_reddit_posts=args.max_reddit_posts,
            max_youtube_videos=args.max_youtube_videos,
            max_records=args.max_records,
            wait_seconds=args.wait_seconds,
        )
    elif args.command == "resume":
        result = resume(args.wait_seconds, max_records=args.max_records)
    else:
        result = status()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
