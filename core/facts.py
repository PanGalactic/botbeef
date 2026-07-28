"""Turn the joined dataset into source-backed facts for rap generation."""
from . import score, store

WEAPON_LABEL = {
    "vertical_spinner": "vertical spinner",
    "horizontal_spinner": "horizontal spinner",
    "drum": "drum",
    "flipper": "flipper",
    "hammer": "hammer",
    "crusher": "crusher",
    "grabber": "grabber",
    "control": "control bot",
    "multibot": "multibot",
}


def _fmt_fight(fight, index):
    red = index.get(fight.get("red"), {}).get("name", fight.get("red"))
    blue = index.get(fight.get("blue"), {}).get("name", fight.get("blue"))
    winner = index.get(fight.get("winner"), {}).get("name", fight.get("winner"))
    where = (
        f"S{fight.get('season')}E{fight.get('episode')}"
        if fight.get("season")
        else fight.get("event", "")
    )
    method = (fight.get("method") or "").upper()
    clock = f" at {fight['time']}" if fight.get("time") else ""
    return f"{red} vs {blue} ({where}): {winner} won by {method}{clock}".strip()


def _urls(values):
    return list(dict.fromkeys(
        value.strip() for value in values
        if store.is_http_url(value)
    ))


def _complete_record_urls(records):
    """Return every record URL, or none if the aggregate is not fully sourced."""
    records = list(records)
    if not records:
        return []
    urls = [store.record_source_url(record) for record in records]
    if any(url is None for url in urls):
        return []
    return _urls(urls)


def for_matchup(a_slug, b_slug):
    """Return citable facts and matchup context for two robots."""
    table = score.table()
    rows = {row["slug"]: row for row in table["rows"]}
    index = store.bot_index()
    records = score.raw_records()
    chatter = store.chatter()
    facts = []

    def add(kind, side, text, *, source_url=None, source_urls=None, value=None):
        urls = _urls([source_url, *(source_urls or [])])
        if not urls:
            return
        facts.append({
            "id": f"F{len(facts) + 1}",
            "kind": kind,
            "side": side,
            "text": text,
            "value": value,
            "source_url": urls[0] if urls else None,
            "source_urls": urls,
        })

    for slug in (a_slug, b_slug):
        row = rows.get(slug)
        if not row:
            continue
        name = row["name"]
        bot_fights = records.get(slug, {}).get("fights", [])
        bot_chatter = [post for post in chatter if post.get("bot") == slug]
        fight_urls = _complete_record_urls(bot_fights)
        chatter_urls = _complete_record_urls(bot_chatter)
        bot_url = store.record_source_url(index.get(slug, {}))

        add(
            "record",
            slug,
            f"{name} is {row['wins']}-{row['losses']} across "
            f"{row['fights']} recorded fights ({row['win_rate']}% win rate).",
            source_urls=fight_urls,
            value=row["win_rate"],
        )
        if row["fights"]:
            add(
                "ko",
                slug,
                f"{name} has {row['ko_wins']} wins by knockout and has been "
                f"knocked out {row['ko_losses']} times.",
                source_urls=fight_urls,
                value=row["ko_wins"],
            )
        if row.get("weapon"):
            add(
                "weapon",
                slug,
                f"{name} is a {WEAPON_LABEL.get(row['weapon'], row['weapon'])}.",
                source_url=bot_url,
            )
        add(
            "hype",
            slug,
            f"{name} has {row['mentions']} scraped fan mentions with average "
            f"sentiment {row['sentiment']:+.2f}.",
            source_urls=chatter_urls,
            value=row["mentions"],
        )
        residual = row["residual"]
        residual_urls = (
            chatter_urls + fight_urls
            if chatter_urls and fight_urls
            else []
        )
        if residual >= 8:
            add(
                "overrated",
                slug,
                f"{name}'s fan hype sits {residual:+.1f} points above what its "
                "record predicts.",
                source_urls=residual_urls,
                value=residual,
            )
        elif residual <= -8:
            add(
                "underrated",
                slug,
                f"{name}'s results sit {abs(residual):.1f} points above its fan hype.",
                source_urls=residual_urls,
                value=residual,
            )
        add(
            "sos",
            slug,
            f"{name}'s opponents average a {row['strength_of_schedule']}% win "
            "rate (strength of schedule).",
            source_urls=fight_urls,
            value=row["strength_of_schedule"],
        )

    for fight in score.head_to_head(a_slug, b_slug):
        add(
            "h2h",
            fight["winner"],
            _fmt_fight(fight, index),
            source_url=fight.get("source_url"),
        )

    for slug in (a_slug, b_slug):
        hype = score.hype().get(slug, {})
        top = hype.get("top_post")
        top_url = store.record_source_url(top or {})
        if top and top.get("text") and top_url:
            snippet = " ".join(str(top["text"]).split())[:180]
            add(
                "quote",
                slug,
                f"Top fan post about {index.get(slug, {}).get('name', slug)} "
                f"({top.get('platform', 'web')}): \"{snippet}\"",
                source_url=top_url,
            )

    context = {
        "a": rows.get(
            a_slug,
            {"slug": a_slug, "name": index.get(a_slug, {}).get("name", a_slug)},
        ),
        "b": rows.get(
            b_slug,
            {"slug": b_slug, "name": index.get(b_slug, {}).get("name", b_slug)},
        ),
        "fit": table["fit"],
        "provenance": table["provenance"],
    }
    return facts, context
