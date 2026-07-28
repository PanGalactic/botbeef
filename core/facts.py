"""Turn the joined dataset into citable facts.

This is the spine of the whole app. The rap generator is never given free
rein — it is handed a numbered list of facts and every bar it writes must
cite one by id. Bars that cite nothing get rejected before they reach the
stage. That is what makes it a data project and not a party trick.
"""
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


def _fmt_fight(f, idx):
    red = idx.get(f.get("red"), {}).get("name", f.get("red"))
    blue = idx.get(f.get("blue"), {}).get("name", f.get("blue"))
    win = idx.get(f.get("winner"), {}).get("name", f.get("winner"))
    where = f"S{f.get('season')}E{f.get('episode')}" if f.get("season") else f.get("event", "")
    method = (f.get("method") or "").upper()
    t = f" at {f['time']}" if f.get("time") else ""
    return f"{red} vs {blue} ({where}): {win} won by {method}{t}".strip()


def for_matchup(a_slug, b_slug):
    """Facts available to both sides of one battle.

    Returns (facts, context) where facts is a list of dicts with stable ids
    the model must cite: F1, F2, ...
    """
    tbl = score.table()
    rows = {r["slug"]: r for r in tbl["rows"]}
    idx = store.bot_index()
    facts = []

    def add(kind, side, text, source_url=None, value=None):
        facts.append({
            "id": f"F{len(facts) + 1}",
            "kind": kind,
            "side": side,          # which bot the fact is ABOUT
            "text": text,
            "value": value,
            "source_url": source_url,
        })

    for slug in (a_slug, b_slug):
        r = rows.get(slug)
        if not r:
            continue
        name = r["name"]
        add("record", slug,
            f"{name} is {r['wins']}-{r['losses']} across {r['fights']} recorded fights "
            f"({r['win_rate']}% win rate).",
            value=r["win_rate"])
        if r["fights"]:
            add("ko", slug,
                f"{name} has {r['ko_wins']} wins by knockout and has been knocked out "
                f"{r['ko_losses']} times.",
                value=r["ko_wins"])
        if r.get("weapon"):
            add("weapon", slug,
                f"{name} is a {WEAPON_LABEL.get(r['weapon'], r['weapon'])}.")
        add("hype", slug,
            f"{name} has {r['mentions']} scraped fan mentions with average sentiment "
            f"{r['sentiment']:+.2f}.",
            value=r["mentions"])
        resid = r["residual"]
        if resid >= 8:
            add("overrated", slug,
                f"{name} is the crowd's darling and the record does not back it up: "
                f"fan hype sits {resid:+.1f} points above what its results predict.",
                value=resid)
        elif resid <= -8:
            add("underrated", slug,
                f"{name} is a quiet killer — nobody talks about it, but it is "
                f"{abs(resid):.1f} points better than its hype implies.",
                value=resid)
        add("sos", slug,
            f"{name}'s opponents average a {r['strength_of_schedule']}% win rate "
            f"(strength of schedule).",
            value=r["strength_of_schedule"])

    for f in score.head_to_head(a_slug, b_slug):
        loser = f["blue"] if f["winner"] == f["red"] else f["red"]
        add("h2h", f["winner"], _fmt_fight(f, idx), source_url=f.get("source_url"))
        _ = loser

    # a real fan quote is the sharpest bar material there is
    for slug in (a_slug, b_slug):
        h = score.hype().get(slug, {})
        top = h.get("top_post")
        if top and top.get("text"):
            snippet = " ".join(str(top["text"]).split())[:180]
            add("quote", slug,
                f"Top fan post about {idx.get(slug, {}).get('name', slug)} "
                f"({top.get('platform', 'web')}): \"{snippet}\"",
                source_url=top.get("url"))

    context = {
        "a": rows.get(a_slug, {"slug": a_slug, "name": idx.get(a_slug, {}).get("name", a_slug)}),
        "b": rows.get(b_slug, {"slug": b_slug, "name": idx.get(b_slug, {}).get("name", b_slug)}),
        "fit": tbl["fit"],
        "provenance": tbl["provenance"],
    }
    return facts, context
