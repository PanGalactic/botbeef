# BOT BEEF

Two BattleBots experiences in one app: data-grounded rap battles and a
playable, early-2000s-3D-fighter-inspired robot combat mode.

Pick two bots. The app writes a 16-bar battle where **every single bar cites a
real scraped fact** — win/loss record, KO count, strength of schedule, or a
real fan post — and speaks it aloud in two different voices. Bars that cite
nothing are rejected before they reach the screen.

Built for the [Bright Data BattleBots Hack Night](https://luma.com/battle-bots-hack-night-jul28-2026),
London, 28 Jul 2026.

Open <http://127.0.0.1:5050/> to choose a mode:

- **Rap Battle** at <http://127.0.0.1:5050/rap> turns scraped records and fan
  chatter into voiced, cited bars.
- **Robot Fight** at <http://127.0.0.1:5050/fight> runs the playable
  best-of-three combat loop. The existing `/arena` URL remains an alias.

Both modes run in the same Flask application and share robot identities,
assets, data APIs, and navigation. There is one combat engine, loaded only by
Fight mode; Rap mode uses the grounded-rap pipeline.

---

## The claim we're making

Everyone at a scraping hackathon ships a dashboard. The interesting thing in
this data isn't a leaderboard — it's the **residual**.

We compute two independent scores per bot:

- **Performance** — from the fight record: win rate weighted by margin (a KO
  counts more than a judges' decision), adjusted for strength of schedule, and
  shrunk toward the mean so a 1-0 bot doesn't top the chart.
- **Hype** — from scraped fan chatter: log mention volume plus sentiment.

Then we fit a line through all bots and take the residual. Bots **above** the
line are overrated — the crowd loves them more than the record earns. Bots
**below** are the quiet killers nobody talks about.

That residual is both the chart and the ammunition: it's what lets one bot
tell another *"your hype floats twelve points above what your results earn."*
It only works because two separately-scraped datasets are joined on bot name.

## Every bar is sourced

The model never freestyles. It receives a numbered list of facts derived from
the scraped data and must cite one per bar. Aggregate facts retain the HTTP(S)
URLs of their contributing fight or fan-comment records. `core/rap.py:validate()`
then drops any bar whose `fact_id` doesn't exist or whose fact has no usable
source URL. The citation is rendered under each bar on screen, so a judge can
check our working live.

```
TOMBSTONE
  "You've been knocked out three times off one single knockout win."
  F8  Hydra has 1 wins by knockout and has been knocked out 3 times.
```

---

## Six-robot fighter MVP

The verified MVP roster is Witch Doctor, Tombstone, HyperShock, Minotaur,
HUGE, and Cobalt. The arena is self-contained: Three.js, robot cutouts, arena
art, combat sound effects, and gameplay modules are served locally.

From this repository directory:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5050/fight>. The backwards-compatible `/arena` URL
serves the same Fight mode.

### Fighter controls

| Action | Keyboard |
| --- | --- |
| Move toward / away from opponent | `W` / `S` |
| Sidestep left / right | `A` / `D` |
| Light / medium / heavy / special attack | `J` / `K` / `L` / `I` |
| Block (hold) | `Space` |
| Restart the full best-of-three match | `R` |
| Pause / open match menu | `Esc` |
| Toggle combat sound | `M` |

On touch devices up to 1000 px wide, on-screen movement, attack, block, and
restart controls appear automatically. Hold movement and block buttons; tap
attack and restart buttons.

### Verification

```bash
node --test tests/test_gameplay.mjs
node --test tests/test_rap_audio.mjs
python -m pytest -q
```

`GET /health` is a fast local liveness check. It reports the configured
optional voice backend but deliberately does not contact Kokoro, Chatterbox,
or ElevenLabs. Those services are unrelated to fighter gameplay, and probing
their DNS/network state in the request path can stall a demo.

## Rap Battle mode

Open <http://127.0.0.1:5050/rap>.

The data view shows a **red PLACEHOLDER banner** until every record carries a
real `source_url` from an actual scrape. That's deliberate: it is not possible
to demo fake numbers by forgetting to swap the data, because the screen says
so.

### Real data (Bright Data)

```bash
export BRIGHTDATA_API_TOKEN=...

# One credit-capped bulk run. Defaults: 12 Reddit threads, 5 official
# YouTube videos, and at most 6,000 predicted comment records.
python3 ingest/brightdata.py bulk \
  --max-reddit-posts 12 \
  --max-youtube-videos 5 \
  --max-records 6000

# Resume only if Bright Data is still preparing an asynchronous snapshot.
python3 ingest/brightdata.py resume

# Inspect the durable corpus and exported-cache provenance.
python3 ingest/brightdata.py status
```

The one-time job discovers sources, applies the record cap before comment
collection, and writes deduplicated comments plus robot associations to
`data/botbeef.sqlite3`. It then exports `data/cache/chatter.json`, preserving
the JSON contract already consumed by Flask. The database deduplicates source
records and robot links during collection or resume. After a successful run,
another `bulk` command fails closed while `data/raw/snapshots.json` exists, so
an accidental rerun cannot spend more Bright Data credits.

There is deliberately **no scheduler, daemon, polling service, request-time
scrape, or background refresh**. Run `bulk` once (and `resume` only when an
already-triggered Bright Data snapshot is unfinished), then demo entirely from
SQLite-derived local caches.

### Battles and audio

```bash
python3 ingest/pregen.py --top 6        # generate + render the juiciest matchups
python3 ingest/pregen.py tombstone hydra
```

Everything is pre-rendered to disk. **Nothing hits the network during a demo** —
no scrape, no LLM call, no TTS render. Live scraping on stage is how demos die.

The voice manifest remains the timing authority: its intro and bar indices
decide which lyric is highlighted. The selected backing bed loops quietly
below the voices and stops when that sequence completes.

### Backing beats

Ten instrumental beds, one per rap style, in `static/beats/` with a manifest
in `beats.json` (style, BPM, duration, Suno id). Generated on Suno v5.5 under
a Pro subscription, which carries commercial rights.

```bash
python3 ingest/beats.py     # re-pull from the CDN if the files are missing
```

Grime · G-funk · boom bap · trap · drill · phonk · industrial · Miami bass ·
Detroit · cinematic orchestral. Every prompt asked for a seamless loop, so the
shorter beds are safe to loop under a battle that outruns them — a 16-bar
battle runs roughly 70–90 seconds including the announcer intro.

`industrial.mp3` (clanging metal, hydraulic hiss, servo grind) is the
thematic pick; `cinematic.mp3` is entrance music rather than a verse bed.
Known cached matchups have an explicit theme, other pairs receive a stable
theme derived from their canonical IDs, and the Rap header can override it.

### Arena art + fighter cutouts

```bash
python3 ingest/images.py fetch     # real bot photos
python3 ingest/images.py cutout    # background removal -> transparent PNGs
python3 ingest/images.py arena     # FLUX stage backdrop
```

Fighters are **real photographs**, not generated art — a text-to-image model
has never seen Tombstone, and the bots have to look like their real-world
counterparts. FLUX is used only for the arena backdrop, where there's no
likeness to preserve.

---

## Architecture

```
ingest/     scrape once, persist in SQLite, export cache, never scrape in Flask
  robots.py      battlebots.com/robot/<slug>/ -> records + photo URLs
  brightdata.py  credit-capped, one-time Reddit + YouTube bulk collection
  corpus.py      SQLite schema, normalization, dedupe, robot links, JSON export
  images.py      photos -> cutouts; FLUX -> arena
  pregen.py      battles + TTS, rendered ahead of the demo
  seed.py        PLACEHOLDER data so the app runs before any scrape

core/
  store.py    disk cache + the provenance check behind the red banner
  score.py    performance, hype, and the residual
  facts.py    scraped data -> numbered citable facts
  rap.py      generation + citation validation
  voice.py    Kokoro TTS, one WAV per bar

app.py      Flask, serves only from cache
static/     the stage UI
```

**Stack:** Flask · Claude Opus 5 (bars) · Cerebras (live fallback) ·
Kokoro MLX TTS · FLUX.2-klein · Bright Data.

## Dependencies that are not pip-installable

- **Kokoro TTS** on `127.0.0.1:8766` — for spoken bars.
- **mflux** on `garage.wg:8005` — for arena art only.

Both are optional: without them you lose audio and backdrop, not the app.
This is why **GitHub Codespaces won't run the full stack** — those two services
are on the local network. Codespaces is fine for the Flask app, scoring,
scraping, and the UI.

## License

MIT.
