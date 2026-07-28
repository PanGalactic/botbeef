"""SQLite-backed corpus for one-time fan-chatter ingestion.

The database is the durable source of truth.  The existing Flask application
continues to consume ``data/cache/chatter.json``; ``export_chatter`` creates
that deterministic demo artifact from the database after a bulk scrape.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sqlite3
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from core import store

DB_PATH = store.ROOT / "data" / "botbeef.sqlite3"

# The 2026 Pro League field. Slugs intentionally follow the canonical slugs
# already used by the fight cache where one exists.
PRO_LEAGUE_ROSTER = {
    "bloodsport": ("Bloodsport", ("bloodsport",)),
    "cobalt": ("Cobalt", ("cobalt",)),
    "copperhead": ("Copperhead", ("copperhead",)),
    "death-roll": ("Death Roll", ("death roll", "deathroll")),
    "disarray": ("Disarray", ("disarray",)),
    "end-game": ("End Game", ("end game",)),
    "golden-fury": ("Golden Fury", ("golden fury",)),
    "huge": ("HUGE", ("HUGE",)),
    "hypershock": ("HyperShock", ("hypershock", "hyper shock")),
    "jackpot": ("Jackpot", ("jackpot",)),
    "mad-catter": ("MadCatter", ("mad catter", "madcatter")),
    "magnitude": ("Magnitude", ("magnitude",)),
    "malice": ("Malice", ("malice",)),
    "manta": ("Manta", ("manta",)),
    "minotaur": ("Minotaur", ("minotaur",)),
    "orbitron": ("Orbitron", ("orbitron",)),
    "ribbot": ("Ribbot", ("ribbot",)),
    "skorpios": ("Skorpios", ("skorpios",)),
    "switchback": ("Switchback", ("switchback",)),
    "terror-tops": ("TerrorTops", ("terrortops", "terror tops")),
    "the-twins": ("The Twins", ("the twins", "twins")),
    "tombstone": ("Tombstone", ("tombstone",)),
    "valkyrie": ("Valkyrie", ("valkyrie",)),
    "witch-doctor": ("Witch Doctor", ("witch doctor",)),
}

THEMES = {
    "weapon_reliability": (
        "weapon", "spinner", "drum", "bar", "blade", "spin up", "spinning",
        "stopped", "jammed", "failed", "reliability",
    ),
    "durability": (
        "durable", "tank", "tanky", "armor", "armour", "survive", "broke",
        "broken", "damage", "destroyed",
    ),
    "driving": (
        "drive", "driver", "driving", "control", "steer", "wheels", "wheel",
        "mobility", "stuck",
    ),
    "strategy": (
        "strategy", "matchup", "configuration", "wedge", "fork", "ground game",
        "aggressive", "aggression",
    ),
    "hype": (
        "overrated", "underrated", "hype", "favorite", "favourite", "goat",
        "legend", "washed", "mid",
    ),
    "result": (
        "win", "won", "lose", "lost", "loss", "knockout", "ko", "decision",
        "tap out", "tapout",
    ),
}

POS = {
    "amazing", "beast", "best", "brilliant", "clean", "dominant", "elite",
    "favorite", "favourite", "goat", "great", "impressive", "incredible",
    "legend", "legendary", "love", "loved", "perfect", "underrated",
}
NEG = {
    "awful", "bad", "boring", "broken", "disappointing", "embarrassing",
    "lucky", "mid", "overhyped", "overrated", "predictable", "sloppy",
    "slow", "trash", "useless", "washed", "weak", "worst",
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def connect(path: pathlib.Path | str = DB_PATH) -> sqlite3.Connection:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA busy_timeout = 5000")
    return db


def init_db(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS robots (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            source_url TEXT
        );

        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            external_id TEXT,
            url TEXT NOT NULL,
            title TEXT,
            description TEXT,
            published_at TEXT,
            engagement INTEGER NOT NULL DEFAULT 0,
            comment_count INTEGER NOT NULL DEFAULT 0,
            context_bots_json TEXT NOT NULL DEFAULT '[]',
            raw_json TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(platform, url)
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            external_id TEXT NOT NULL,
            source_id INTEGER REFERENCES sources(id),
            url TEXT NOT NULL,
            text TEXT NOT NULL,
            author_hash TEXT,
            score INTEGER NOT NULL DEFAULT 0,
            replies INTEGER NOT NULL DEFAULT 0,
            published_at TEXT,
            sentiment REAL NOT NULL DEFAULT 0,
            theme TEXT NOT NULL DEFAULT 'general',
            stance TEXT NOT NULL DEFAULT 'neutral',
            claim_type TEXT NOT NULL DEFAULT 'fan_opinion',
            raw_json TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(platform, external_id)
        );

        CREATE TABLE IF NOT EXISTS comment_robots (
            comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
            robot_slug TEXT NOT NULL REFERENCES robots(slug),
            confidence REAL NOT NULL,
            match_basis TEXT NOT NULL,
            PRIMARY KEY(comment_id, robot_slug)
        );

        CREATE TABLE IF NOT EXISTS ingest_runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            delivered_records INTEGER NOT NULL DEFAULT 0,
            details_json TEXT
        );

        CREATE INDEX IF NOT EXISTS comments_source_idx ON comments(source_id);
        CREATE INDEX IF NOT EXISTS comment_robots_robot_idx
            ON comment_robots(robot_slug);
        """
    )
    seed_robots(db)
    db.commit()


def seed_robots(db: sqlite3.Connection) -> None:
    cached = {b.get("slug"): b for b in store.bots()}
    for slug, (name, aliases) in PRO_LEAGUE_ROSTER.items():
        bot = cached.get(slug, {})
        db.execute(
            """
            INSERT INTO robots(slug, name, aliases_json, source_url)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name,
                aliases_json=excluded.aliases_json,
                source_url=COALESCE(excluded.source_url, robots.source_url)
            """,
            (slug, name, json.dumps(aliases), bot.get("source_url")),
        )


def sentiment(text: str) -> float:
    words = re.findall(r"[a-z']+", (text or "").lower())
    positive = sum(word in POS for word in words)
    negative = sum(word in NEG for word in words)
    return round((positive - negative) / (positive + negative), 3) if positive + negative else 0.0


def classify_theme(text: str) -> str:
    lowered = (text or "").lower()
    scored = {
        theme: sum(
            1
            for token in tokens
            if re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", lowered)
        )
        for theme, tokens in THEMES.items()
    }
    theme, hits = max(scored.items(), key=lambda item: item[1])
    return theme if hits else "general"


def stance_for(value: float) -> str:
    if value >= 0.25:
        return "praise"
    if value <= -0.25:
        return "critical"
    return "neutral"


def _word_match(text: str, alias: str) -> bool:
    return bool(re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", text, re.I))


def match_robots(text: str, context: str = "") -> list[tuple[str, float, str]]:
    """Return explicit robot mentions, avoiding the ordinary adjective HUGE."""
    found: dict[str, tuple[float, str]] = {}
    for slug, (_, aliases) in PRO_LEAGUE_ROSTER.items():
        for alias in aliases:
            if slug == "huge":
                matched = bool(re.search(r"(?<![\w])HUGE(?![\w])", text or ""))
            else:
                matched = _word_match(text or "", alias)
            if matched:
                found[slug] = (0.98, f"explicit:{alias}")
                break

    if not found:
        context_hits = []
        for slug, (_, aliases) in PRO_LEAGUE_ROSTER.items():
            if any(
                (slug == "huge" and re.search(r"(?<![\w])HUGE(?![\w])", context or ""))
                or (slug != "huge" and _word_match(context or "", alias))
                for alias in aliases
            ):
                context_hits.append(slug)
        if len(context_hits) == 1:
            found[context_hits[0]] = (0.55, "single-source-context")

    return [(slug, confidence, basis) for slug, (confidence, basis) in found.items()]


def _safe_raw(row: dict) -> str:
    """Serialize provider data without retaining commenter identity fields."""
    hidden = {
        "author", "author_id", "author_name", "author_url", "avatar",
        "channel_id", "channel_name", "channel_url", "profile_image",
        "profile_picture", "profile_url", "user_channel", "user_id",
        "user_name", "user_posted", "user_url", "username",
    }
    hidden_compact = {key.replace("_", "") for key in hidden}

    def redact(value):
        if isinstance(value, dict):
            return {
                key: redact(child)
                for key, child in value.items()
                if re.sub(r"[^a-z0-9]", "", key.lower()) not in hidden_compact
            }
        if isinstance(value, list):
            return [redact(child) for child in value]
        return value

    return json.dumps(redact(row), ensure_ascii=False, sort_keys=True)


def _first(row: dict, *keys: str):
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _integer(value) -> int:
    """Parse common scraper counters such as ``1,234`` and ``1.2K``."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    cleaned = str(value).strip().replace(",", "").lower()
    match = re.search(r"[-+]?\d+(?:\.\d+)?\s*[km]?", cleaned)
    if not match:
        return 0
    cleaned = match.group(0).replace(" ", "")
    multiplier = 1
    if cleaned.endswith("k"):
        cleaned, multiplier = cleaned[:-1], 1_000
    elif cleaned.endswith("m"):
        cleaned, multiplier = cleaned[:-1], 1_000_000
    try:
        return int(float(cleaned) * multiplier)
    except ValueError:
        return 0


def _canonical_source_url(platform: str, value: str) -> str:
    """Remove tracking/comment fragments while retaining the source identity."""
    value = str(value or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        return value
    parsed = urlsplit(value)
    host = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    query = parse_qs(parsed.query)

    if platform == "youtube":
        if host == "youtu.be":
            video_id = path.strip("/").split("/")[0]
        else:
            video_id = (query.get("v") or [""])[0]
        if video_id:
            return f"https://www.youtube.com/watch?{urlencode({'v': video_id})}"
    elif platform == "reddit":
        parts = [part for part in path.split("/") if part]
        if "comments" in parts:
            index = parts.index("comments")
            # Keep /comments/<post-id>/<slug>, dropping a trailing comment id.
            path = "/" + "/".join(parts[: index + 3])
        return urlunsplit(("https", host, path, "", ""))

    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def _external_id(
    platform: str,
    row: dict,
    parent_url: str,
    text: str,
) -> str:
    explicit = _first(row, "comment_id", "commentId", "cid", "id")
    if explicit:
        return str(explicit)
    author = _first(
        row, "username", "user_name", "user_posted", "author", "author_name"
    )
    digest = hashlib.sha256(
        f"{platform}\0{parent_url}\0{author or ''}\0{text}".encode("utf-8")
    ).hexdigest()
    return digest[:32]


def _author_hash(platform: str, row: dict) -> str | None:
    author = _first(
        row, "username", "user_name", "user_posted", "author", "author_name"
    )
    if not author:
        return None
    return hashlib.sha256(f"{platform}:{author}".encode("utf-8")).hexdigest()[:16]


def normalize_comment(platform: str, row: dict) -> dict | None:
    platform = platform.strip().lower()
    if platform == "youtube":
        text = _first(row, "comment_text", "comment", "text", "content") or ""
        parent_url = _first(row, "video_url", "input_url", "url") or ""
        url = _first(row, "comment_url", "permalink") or parent_url
        score = _first(row, "likes", "like_count", "num_likes")
        replies = _first(row, "replies", "reply_count", "num_replies")
        published = _first(
            row, "comment_date", "date", "date_posted", "published_at"
        )
    elif platform == "reddit":
        text = _first(row, "comment", "body", "comment_text", "description") or ""
        parent_url = _first(row, "post_url", "input_url", "url") or ""
        url = _first(row, "comment_url", "permalink", "url") or parent_url
        score = _first(row, "num_upvotes", "score", "ups", "likes")
        replies = _first(row, "num_replies", "replies", "reply_count")
        published = _first(
            row, "date_posted", "comment_date", "created_utc", "published_at"
        )
    else:
        raise ValueError(f"unsupported platform: {platform}")

    text = " ".join(str(text).split())
    parent_url = _canonical_source_url(platform, parent_url)
    if (
        not text
        or text.lower() in {"[deleted]", "[removed]"}
        or not parent_url.startswith(("http://", "https://"))
    ):
        return None
    url = str(url).strip()
    if url.startswith("/"):
        url = urljoin(parent_url + "/", url)
    elif not url.startswith(("http://", "https://")):
        url = parent_url
    sent = sentiment(text)
    return {
        "platform": platform,
        "external_id": _external_id(platform, row, parent_url, text),
        "parent_url": parent_url,
        "url": url,
        "text": text[:2000],
        "author_hash": _author_hash(platform, row),
        "score": _integer(score),
        "replies": _integer(replies),
        "published_at": published,
        "sentiment": sent,
        "theme": classify_theme(text),
        "stance": stance_for(sent),
        "raw_json": _safe_raw(row),
    }


def source_from_row(platform: str, row: dict) -> dict | None:
    platform = platform.strip().lower()
    # Bright Data's YouTube video record contains both `url` (the canonical
    # watch page) and `video_url` (a short-lived googlevideo.com stream).
    # Only the former is stable enough to cite and to join to comment rows.
    if platform == "youtube":
        url = _first(row, "url", "input_url", "video_url")
    else:
        url = _first(row, "post_url", "url", "input_url")
    if not url:
        return None
    url = _canonical_source_url(platform, url)
    if not url.startswith(("http://", "https://")):
        return None
    title = row.get("title") or ""
    description = row.get("description") or ""
    context = [slug for slug, _, _ in match_robots(f"{title} {description}")]
    return {
        "platform": platform,
        "external_id": str(
            _first(row, "post_id", "video_id", "id", "shortcode") or ""
        ),
        "url": url,
        "title": title,
        "description": description,
        "published_at": row.get("date_posted") or row.get("date"),
        "engagement": _integer(
            _first(row, "num_upvotes", "likes", "views", "view_count")
        ),
        "comment_count": _integer(
            _first(row, "num_comments", "comments", "comment_count")
        ),
        "context_bots_json": json.dumps(context),
        "raw_json": _safe_raw(row),
    }


def ingest_sources(db: sqlite3.Connection, platform: str, rows: Iterable[dict]) -> int:
    platform = platform.strip().lower()
    count = 0
    for row in rows:
        src = source_from_row(platform, row)
        if not src:
            continue
        db.execute(
            """
            INSERT INTO sources(
                platform, external_id, url, title, description, published_at,
                engagement, comment_count, context_bots_json, raw_json, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, url) DO UPDATE SET
                external_id=excluded.external_id,
                title=excluded.title,
                description=excluded.description,
                published_at=excluded.published_at,
                engagement=excluded.engagement,
                comment_count=excluded.comment_count,
                context_bots_json=excluded.context_bots_json,
                raw_json=excluded.raw_json,
                scraped_at=excluded.scraped_at
            """,
            (*src.values(), now()),
        )
        canonical = db.execute(
            "SELECT id FROM sources WHERE platform=? AND url=?",
            (platform, src["url"]),
        ).fetchone()
        # Earlier versions could store YouTube's transient video_url rather
        # than its watch-page URL. Merge any same-entity row into the stable
        # source so comment foreign keys and metadata stay together.
        if canonical and src["external_id"]:
            duplicates = db.execute(
                """
                SELECT id FROM sources
                WHERE platform=? AND external_id=? AND id<>?
                """,
                (platform, src["external_id"], canonical["id"]),
            ).fetchall()
            for duplicate in duplicates:
                db.execute(
                    "UPDATE comments SET source_id=? WHERE source_id=?",
                    (canonical["id"], duplicate["id"]),
                )
                db.execute(
                    "DELETE FROM sources WHERE id=?",
                    (duplicate["id"],),
                )
            _reindex_source_comments(db, canonical["id"])
        count += 1
    db.commit()
    return count


def _reindex_source_comments(db: sqlite3.Connection, source_id: int) -> None:
    source = db.execute(
        "SELECT title, description FROM sources WHERE id=?",
        (source_id,),
    ).fetchone()
    if not source:
        return
    context = f"{source['title'] or ''} {source['description'] or ''}"
    comments = db.execute(
        "SELECT id, text FROM comments WHERE source_id=?",
        (source_id,),
    ).fetchall()
    for comment in comments:
        db.execute(
            "DELETE FROM comment_robots WHERE comment_id=?",
            (comment["id"],),
        )
        for slug, confidence, basis in match_robots(
            comment["text"], context=context
        ):
            db.execute(
                """
                INSERT INTO comment_robots(
                    comment_id, robot_slug, confidence, match_basis
                ) VALUES (?, ?, ?, ?)
                """,
                (comment["id"], slug, confidence, basis),
            )


def _source_for(db: sqlite3.Connection, platform: str, parent_url: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM sources WHERE platform=? AND url=?",
        (platform, parent_url),
    ).fetchone()
    if row:
        return row
    db.execute(
        """
        INSERT OR IGNORE INTO sources(platform, url, scraped_at)
        VALUES (?, ?, ?)
        """,
        (platform, parent_url, now()),
    )
    return db.execute(
        "SELECT * FROM sources WHERE platform=? AND url=?",
        (platform, parent_url),
    ).fetchone()


def ingest_comments(db: sqlite3.Connection, platform: str, rows: Iterable[dict]) -> dict:
    platform = platform.strip().lower()
    processed = inserted = updated = linked = ignored = 0
    for raw in rows:
        item = normalize_comment(platform, raw)
        if not item:
            ignored += 1
            continue
        source = _source_for(db, platform, item.pop("parent_url"))
        existed = db.execute(
            "SELECT id FROM comments WHERE platform=? AND external_id=?",
            (platform, item["external_id"]),
        ).fetchone()
        db.execute(
            """
            INSERT INTO comments(
                platform, external_id, source_id, url, text, author_hash, score,
                replies, published_at, sentiment, theme, stance, claim_type,
                raw_json, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fan_opinion', ?, ?)
            ON CONFLICT(platform, external_id) DO UPDATE SET
                source_id=excluded.source_id,
                url=excluded.url,
                text=excluded.text,
                author_hash=excluded.author_hash,
                score=excluded.score,
                replies=excluded.replies,
                published_at=excluded.published_at,
                sentiment=excluded.sentiment,
                theme=excluded.theme,
                stance=excluded.stance,
                raw_json=excluded.raw_json,
                scraped_at=excluded.scraped_at
            """,
            (
                item["platform"], item["external_id"], source["id"], item["url"],
                item["text"], item["author_hash"], item["score"], item["replies"],
                item["published_at"], item["sentiment"], item["theme"],
                item["stance"], item["raw_json"], now(),
            ),
        )
        comment = db.execute(
            "SELECT id FROM comments WHERE platform=? AND external_id=?",
            (platform, item["external_id"]),
        ).fetchone()
        # The provider can amend a comment between snapshots. Rebuild its
        # associations so removed/edited robot mentions do not leave stale links.
        db.execute(
            "DELETE FROM comment_robots WHERE comment_id=?",
            (comment["id"],),
        )
        context = f"{source['title'] or ''} {source['description'] or ''}"
        matches = match_robots(item["text"], context=context)
        for slug, confidence, basis in matches:
            db.execute(
                """
                INSERT INTO comment_robots(comment_id, robot_slug, confidence, match_basis)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(comment_id, robot_slug) DO UPDATE SET
                    confidence=excluded.confidence,
                    match_basis=excluded.match_basis
                """,
                (comment["id"], slug, confidence, basis),
            )
            linked += 1
        processed += 1
        if existed:
            updated += 1
        else:
            inserted += 1
    db.commit()
    return {
        "comments": processed,
        "inserted": inserted,
        "updated": updated,
        "links": linked,
        "ignored": ignored,
    }


def export_chatter(db: sqlite3.Connection, path: pathlib.Path | None = None) -> pathlib.Path:
    path = pathlib.Path(path or (store.CACHE / "chatter.json"))
    rows = db.execute(
        """
        SELECT c.platform, c.external_id, c.url, c.text, c.score, c.replies,
               c.sentiment, c.theme, c.stance, c.published_at,
               cr.robot_slug, cr.confidence, cr.match_basis,
               s.url AS context_url
        FROM comments c
        JOIN comment_robots cr ON cr.comment_id = c.id
        JOIN sources s ON s.id = c.source_id
        WHERE c.url LIKE 'http%'
        ORDER BY c.score DESC, c.id, cr.robot_slug
        """
    ).fetchall()
    posts = [
        {
            "id": f"{row['platform']}-{row['external_id']}-{row['robot_slug']}",
            "platform": row["platform"],
            "bot": row["robot_slug"],
            "text": row["text"],
            "score": row["score"],
            "replies": row["replies"],
            "sentiment": row["sentiment"],
            "theme": row["theme"],
            "stance": row["stance"],
            "confidence": row["confidence"],
            "match_basis": row["match_basis"],
            "published_at": row["published_at"],
            "url": row["url"],
            "context_url": row["context_url"],
            "source": "brightdata",
        }
        for row in rows
    ]
    database_row = db.execute("PRAGMA database_list").fetchone()
    database = pathlib.Path(database_row["file"]).name if database_row["file"] else ":memory:"
    payload = {"posts": posts, "ingested_at": now(), "database": database}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def stats(db: sqlite3.Connection) -> dict:
    return {
        "robots": db.execute("SELECT COUNT(*) FROM robots").fetchone()[0],
        "sources": db.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "comments": db.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
        "linked_comments": db.execute(
            "SELECT COUNT(DISTINCT comment_id) FROM comment_robots"
        ).fetchone()[0],
        "robot_links": db.execute("SELECT COUNT(*) FROM comment_robots").fetchone()[0],
        "platforms": {
            row["platform"]: row["n"]
            for row in db.execute(
                "SELECT platform, COUNT(*) n FROM comments GROUP BY platform"
            )
        },
    }
