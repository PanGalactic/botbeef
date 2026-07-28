#!/usr/bin/env python3
"""Generate PLACEHOLDER seed data so the app runs end-to-end before ingest.

Bot names and weapon classes are real and stable public knowledge. The
fight records and fan posts are SYNTHETIC and every record says so in its
`source` field — the UI renders a loud red banner until every record
carries a real source_url from an actual Bright Data pull.

That banner is the safety catch: it is not possible to demo fake numbers
by forgetting to swap the data, because the screen says PLACEHOLDER.

    python3 ingest/seed.py
"""
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import store  # noqa: E402

ROSTER = [
    ("tombstone", "Tombstone", "horizontal_spinner"),
    ("hydra", "Hydra", "flipper"),
    ("minotaur", "Minotaur", "drum"),
    ("witch-doctor", "Witch Doctor", "vertical_spinner"),
    ("bite-force", "Bite Force", "vertical_spinner"),
    ("end-game", "End Game", "vertical_spinner"),
    ("riptide", "Riptide", "vertical_spinner"),
    ("copperhead", "Copperhead", "drum"),
    ("whiplash", "Whiplash", "vertical_spinner"),
    ("huge", "HUGE", "vertical_spinner"),
    ("black-dragon", "Black Dragon", "drum"),
    ("sawblaze", "SawBlaze", "hammer"),
    ("lock-jaw", "Lock-Jaw", "vertical_spinner"),
    ("valkyrie", "Valkyrie", "horizontal_spinner"),
    ("ribbot", "Ribbot", "vertical_spinner"),
    ("skorpios", "Skorpios", "hammer"),
    ("hypershock", "HyperShock", "horizontal_spinner"),
    ("rotator", "Rotator", "horizontal_spinner"),
    ("gigabyte", "Gigabyte", "horizontal_spinner"),
    ("shatter", "Shatter", "hammer"),
    ("duck", "Duck!", "control"),
    ("blip", "Blip", "flipper"),
    ("malice", "Malice", "horizontal_spinner"),
    ("cobalt", "Cobalt", "vertical_spinner"),
]

PLACEHOLDER_POSTS = [
    "that KO was the loudest thing I've heard all season",
    "still the scariest bot in the box and it isn't close",
    "criminally underrated, the record speaks for itself",
    "the drivers deserve more credit than the weapon does",
    "every time I pick against them they prove me wrong",
    "overhyped honestly, they lose whenever it matters",
    "watching that bar spin up gives me anxiety every time",
    "the meta has moved on and they haven't adapted",
]


def main():
    rng = random.Random(1939)
    slugs = [r[0] for r in ROSTER]

    bots = [{"slug": s, "name": n, "weapon": w, "provisional": True}
            for s, n, w in ROSTER]

    fights = []
    for season in (7, 8, 9):
        for episode in range(1, 9):
            pool = rng.sample(slugs, 6)
            for i in range(0, 6, 2):
                red, blue = pool[i], pool[i + 1]
                winner = rng.choice([red, blue])
                method = rng.choice(["KO", "KO", "JD", "JD", "JD", "tapout"])
                fights.append({
                    "id": f"s{season}e{episode}-{i//2}",
                    "season": season,
                    "episode": episode,
                    "red": red,
                    "blue": blue,
                    "winner": winner,
                    "method": method,
                    "time": f"{rng.randint(0,2)}:{rng.randint(10,59)}",
                    "source": "PLACEHOLDER",
                    "source_url": None,
                })

    posts = []
    for slug in slugs:
        for i in range(rng.randint(4, 40)):
            posts.append({
                "id": f"{slug}-{i}",
                "platform": rng.choice(["reddit", "youtube", "x"]),
                "bot": slug,
                "text": rng.choice(PLACEHOLDER_POSTS),
                "score": rng.randint(0, 900),
                "sentiment": round(rng.uniform(-0.6, 0.9), 3),
                "url": None,
                "source": "PLACEHOLDER",
            })

    store.SEED.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("bots", {"bots": bots}),
        ("fights", {"fights": fights, "ingested_at": None}),
        ("chatter", {"posts": posts, "ingested_at": None}),
    ):
        with open(store.SEED / f"{name}.json", "w") as fh:
            json.dump(payload, fh, indent=2)

    print(f"seed written: {len(bots)} bots, {len(fights)} fights, {len(posts)} posts")
    print("ALL RECORDS TAGGED PLACEHOLDER — swap via Bright Data ingest before demoing numbers")


if __name__ == "__main__":
    main()
