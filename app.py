#!/usr/bin/env python3
"""BOT BEEF — the stage server.

Everything served here comes off disk. No scraping, no LLM call, no TTS
render happens during a demo unless you explicitly ask for it with
?live=1. Live scraping on stage is how demos die.
"""
import os

from flask import Flask, abort, jsonify, request, send_from_directory

from core import facts, rap, score, store, voice

app = Flask(__name__, static_folder="static", static_url_path="")

PORT = int(os.environ.get("BOTBEEF_PORT", "5050"))


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/arena")
def arena():
    """The Tekken-style stage. Same data, different room."""
    return send_from_directory("static", "arena.html")


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "voice": voice.health(),
        "bots": len(store.bots()),
        "battles_cached": len(rap.list_cached()),
        "provenance": store.provenance(),
    })


@app.route("/api/table")
def table():
    """The one chart: performance vs hype, with the residual per bot."""
    return jsonify(score.table())


@app.route("/api/bots")
def bots():
    return jsonify({"bots": store.bots()})


@app.route("/api/battles")
def battles():
    return jsonify({"battles": rap.list_cached()})


@app.route("/api/battle/<a>/<b>")
def battle(a, b):
    """Cached by default. ?live=cerebras generates on the spot (~1s)."""
    backend = request.args.get("live") or "cached"
    if backend == "1":
        backend = "cerebras"
    try:
        result = rap.battle(a, b, backend=backend)
    except FileNotFoundError as exc:
        abort(404, str(exc))
    except (ValueError, KeyError) as exc:
        abort(400, str(exc))
    result["audio"] = voice.manifest_for(a, b)
    return jsonify(result)


@app.route("/api/facts/<a>/<b>")
def matchup_facts(a, b):
    fact_list, ctx = facts.for_matchup(a, b)
    return jsonify({"facts": fact_list, "context": ctx})


@app.route("/audio/<path:filename>")
def audio(filename):
    return send_from_directory(voice.AUDIO, filename)


if __name__ == "__main__":
    print(f"BOT BEEF on http://127.0.0.1:{PORT}")
    print(f"  kokoro: {'up' if voice.health() else 'DOWN'}")
    print(f"  data:   {store.provenance()}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
