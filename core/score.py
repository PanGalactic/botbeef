"""Performance vs hype, and the residual that makes the whole app work.

performance : what the fight record actually says
hype        : what the internet says
residual    : hype - (fitted hype | performance)

Positive residual  -> overrated, the crowd loves it more than the record earns.
Negative residual  -> a quiet killer nobody talks about.

The residual is both the one chart AND the ammunition for the rap battle:
a bot with a big positive residual gets bodied for being all mouth.
"""
import math
import statistics
from collections import defaultdict

from . import store

# A KO is a wider margin than a judges' decision. Used to weight wins.
METHOD_MARGIN = {
    "ko": 1.0,
    "knockout": 1.0,
    "tapout": 0.95,
    "submission": 0.9,
    "jd": 0.55,
    "judges": 0.55,
    "judges_decision": 0.55,
    "split": 0.5,
    "unanimous": 0.65,
}


def _margin(method):
    return METHOD_MARGIN.get((method or "").strip().lower().replace(" ", "_"), 0.6)


def raw_records():
    """Win/loss tallies per bot from the fight table."""
    rec = defaultdict(lambda: {"w": 0, "l": 0, "ko_w": 0, "ko_l": 0,
                               "margin_for": 0.0, "opponents": [], "fights": []})
    for f in store.fights():
        red, blue, winner = f.get("red"), f.get("blue"), f.get("winner")
        if not (red and blue and winner):
            continue
        loser = blue if winner == red else red
        m = _margin(f.get("method"))
        rec[winner]["w"] += 1
        rec[winner]["margin_for"] += m
        rec[loser]["l"] += 1
        if m >= 0.9:
            rec[winner]["ko_w"] += 1
            rec[loser]["ko_l"] += 1
        rec[winner]["opponents"].append(loser)
        rec[loser]["opponents"].append(winner)
        rec[winner]["fights"].append(f)
        rec[loser]["fights"].append(f)
    return rec


def performance():
    """0-100 performance score, opponent-strength adjusted.

    Two passes: naive win rate first, then re-weight each bot's wins by how
    good its opponents turned out to be. Beating Tombstone counts for more
    than beating a rookie.
    """
    rec = raw_records()
    naive = {}
    for slug, r in rec.items():
        n = r["w"] + r["l"]
        naive[slug] = (r["w"] / n) if n else 0.0

    out = {}
    for slug, r in rec.items():
        n = r["w"] + r["l"]
        if not n:
            continue
        sos = statistics.mean([naive.get(o, 0.5) for o in r["opponents"]]) if r["opponents"] else 0.5
        # margin-weighted win rate, then nudged by strength of schedule
        mwr = r["margin_for"] / n
        adjusted = mwr * (0.75 + 0.5 * sos)
        # small-sample shrinkage toward 0.5 so a 1-0 bot doesn't top the chart
        k = 4.0
        shrunk = (adjusted * n + 0.5 * k) / (n + k)
        out[slug] = {
            "score": round(100 * min(max(shrunk, 0.0), 1.0), 1),
            "wins": r["w"],
            "losses": r["l"],
            "ko_wins": r["ko_w"],
            "ko_losses": r["ko_l"],
            "fights": n,
            "win_rate": round(100 * naive[slug], 1),
            "strength_of_schedule": round(100 * sos, 1),
        }
    return out


def hype():
    """0-100 hype score from mention volume and sentiment."""
    posts = store.chatter()
    agg = defaultdict(lambda: {"n": 0, "sent": [], "engagement": 0, "top": None})
    for p in posts:
        slug = p.get("bot")
        if not slug:
            continue
        a = agg[slug]
        a["n"] += 1
        if p.get("sentiment") is not None:
            a["sent"].append(float(p["sentiment"]))
        a["engagement"] += int(p.get("score") or 0)
        if a["top"] is None or (p.get("score") or 0) > (a["top"].get("score") or 0):
            a["top"] = p

    if not agg:
        return {}

    # log volume so one viral thread doesn't own the axis
    vols = {s: math.log1p(a["n"] + 0.25 * a["engagement"]) for s, a in agg.items()}
    lo, hi = min(vols.values()), max(vols.values())
    span = (hi - lo) or 1.0

    out = {}
    for slug, a in agg.items():
        vol_n = (vols[slug] - lo) / span
        sent = statistics.mean(a["sent"]) if a["sent"] else 0.0
        # sentiment in [-1,1] -> [0,1]
        sent_n = (sent + 1) / 2
        out[slug] = {
            "score": round(100 * (0.65 * vol_n + 0.35 * sent_n), 1),
            "mentions": a["n"],
            "engagement": a["engagement"],
            "sentiment": round(sent, 3),
            "top_post": a["top"],
        }
    return out


def _fit(xs, ys):
    """Least-squares line. Returns (slope, intercept, r)."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0), 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else 0.0
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    r = (sxy / math.sqrt(sxx * syy)) if sxx and syy else 0.0
    return slope, intercept, r


def table():
    """The joined dataset: one row per bot, with the residual."""
    perf, hyp = performance(), hype()
    idx = store.bot_index()
    slugs = sorted(set(perf) & set(hyp))

    xs = [perf[s]["score"] for s in slugs]
    ys = [hyp[s]["score"] for s in slugs]
    slope, intercept, r = _fit(xs, ys)

    rows = []
    for s in slugs:
        p, h = perf[s]["score"], hyp[s]["score"]
        expected = slope * p + intercept
        rows.append({
            "slug": s,
            "name": idx.get(s, {}).get("name", s),
            "weapon": idx.get(s, {}).get("weapon"),
            "performance": p,
            "hype": h,
            "expected_hype": round(expected, 1),
            "residual": round(h - expected, 1),
            **{k: v for k, v in perf[s].items() if k != "score"},
            "mentions": hyp[s]["mentions"],
            "sentiment": hyp[s]["sentiment"],
            "engagement": hyp[s]["engagement"],
        })
    rows.sort(key=lambda r_: r_["residual"], reverse=True)
    return {
        "rows": rows,
        "fit": {"slope": round(slope, 4), "intercept": round(intercept, 2), "r": round(r, 3)},
        "provenance": store.provenance(),
    }


def head_to_head(a, b):
    """Every fight these two have had, most recent first."""
    out = []
    for f in store.fights():
        if {f.get("red"), f.get("blue")} == {a, b}:
            out.append(f)
    out.sort(key=lambda f: (f.get("season") or 0, f.get("episode") or 0), reverse=True)
    return out
