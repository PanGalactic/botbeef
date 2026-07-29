# BOT BEEF

BOT BEEF turns sourced BattleBots records and fan discussion into two arena
experiences:

- a voiced, cited robot rap battle; and
- a playable, early-2000s-3D-fighter-inspired combat game.

A separate evidence view explains the data behind the rap battles. All three
interfaces run from one Flask application and share a canonical robot identity
registry, local assets, and read-only APIs.

Built for the
[Bright Data BattleBots Hack Night](https://luma.com/battle-bots-hack-night-jul28-2026)
in London on 28 July 2026.

## Run locally

From the repository root:

```bash
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS or Linux
source .venv/bin/activate
```

Install the Python dependencies and start the app:

```bash
python -m pip install -r requirements.txt
python app.py
```

The server listens on <http://127.0.0.1:5050> by default. Set
`BOTBEEF_PORT` to use another port.

The demo runtime is local-first: cached battles, audio, backing beats, robot
images, arena art, and Three.js are served from this repository. Starting the
Flask app does not scrape data, call an LLM, render speech, or probe an
optional voice service.

## Interfaces and routes

| Route | Purpose |
| --- | --- |
| `/` | Mode chooser |
| `/rap` | Restored 3D rap-performance arena |
| `/rap/data` | Evidence, scoring, citations, and cached-battle data view |
| `/fight` | Playable best-of-three robot combat |
| `/stage` | Compatibility alias for `/rap` |
| `/data` | Compatibility alias for `/rap/data` |
| `/arena` | Compatibility alias for `/fight` |
| `/sprites` | Robot cutout contact sheet for asset QA |
| `/health` | Local liveness, cache, provenance, and voice configuration |
| `/api/catalog` | Canonical robot capabilities and cross-source drift audit |

## Rap arena

The restored `/rap` experience presents two robots facing off in a 3D arena.
It includes:

- verified-matchup selection rather than arbitrary unavailable pairs;
- a preview of the opponent, beat, strongest sourced rivalry fact, hype and
  performance comparison, and audio readiness;
- voiced 16-bar battles with timed lyric highlighting;
- inline citations and access to the remaining sources;
- ten locally served backing-beat styles;
- responsive projector and mobile-landscape layouts; and
- a functional reduced-graphics mode if WebGL is unavailable.

The `/rap/data` view keeps the analytical experience separate. It exposes the
performance-versus-hype chart, matchup facts, provenance status, citations,
and playable cached battles without replacing the performance arena.

### Fail-closed battle validation

A cached battle is playable only when all of the following are true:

1. it contains exactly 16 valid bars;
2. every bar references a fact available to that matchup;
3. every referenced fact has a usable HTTP(S) source;
4. the battle provenance matches the current sourced corpus;
5. the voice manifest has the expected intro and bar sequence; and
6. every referenced audio file exists inside the audio directory.

Malformed, stale, placeholder, unsourced, or incomplete battles are rejected
by the backend and excluded from both selectors. The repository currently
contains 16 cached battle files: six pass the complete readiness audit and ten
older placeholder or untrusted battles remain deliberately unavailable.

No provenance flag or source URL should be edited merely to make a battle
appear ready. The untrusted battles must be regenerated from the current
sourced corpus and pass the complete audit.

### Why the data is interesting

The app computes two independent values for each robot:

- **Performance** comes from fight results. Win rate is weighted by margin,
  adjusted for strength of schedule, and shrunk toward the mean.
- **Hype** comes from scraped fan discussion, combining log mention volume and
  sentiment.

A fitted performance-to-hype line provides the residual. Robots above it have
more hype than their results predict; robots below it are comparatively
under-discussed. That residual powers both the chart and the grounded battle
material.

The rap generator receives numbered matchup facts. `core/rap.py` rejects bars
whose `fact_id` is unknown or whose fact lacks a valid source URL. Aggregate
facts retain the URLs of their contributing fight or fan records.

### Backing beats

The ten instrumental beds in `static/beats/` are described by
`static/beats/beats.json`:

Grime, G-funk, boom bap, trap, drill, phonk, industrial, Miami bass, Detroit,
and cinematic orchestral.

Known matchups have an explicit theme. Other pairs receive a stable theme
derived from their canonical IDs, and the arena header can override it. The
voice manifest remains the timing authority while the selected beat loops
under the battle.

## Robot fight

The `/fight` mode is a separate controller and presentation layer built on one
shared combat engine. Its verified six-robot roster is:

- Cobalt
- HUGE
- HyperShock
- Minotaur
- Tombstone
- Witch Doctor

Robot names, aliases, source URLs, standard assets, and combat participation
come from `static/data/robot-identities.json`. JavaScript-owned mechanics are
joined to that registry at startup; roster drift fails loudly instead of
silently hiding or inventing a fighter.

### Controls

| Action | Keyboard |
| --- | --- |
| Move toward or away from the opponent | `W` / `S` |
| Sidestep left or right | `A` / `D` |
| Light, medium, heavy, or special attack | `J` / `K` / `L` / `I` |
| Block while held | `Space` |
| Restart the full match | `R` |
| Pause or open the match menu | `Esc` |
| Toggle combat sound | `M` |

Touch controls appear automatically on supported narrower screens. Movement
and block are hold controls; attacks and restart are tap controls.

## Repository layout

```text
app.py                         Flask routes and local APIs
core/
  catalog.py                   cached cross-source robot catalog and drift audit
  facts.py                     sourced records converted into citable facts
  rap.py                       generation, validation, cache, and readiness audit
  score.py                     performance, hype, and residual scoring
  store.py                     disk-cache access and corpus provenance
  voice.py                     voice backends, manifests, and audio rendering
data/
  battles/                     cached battle JSON
  cache/                       exported robot, fight, and chatter data
  raw/                         durable ingestion metadata and records
ingest/
  brightdata.py                capped, resumable Reddit and YouTube collection
  corpus.py                    SQLite normalization, deduplication, and export
  pregen.py                    explicit battle and voice pre-generation
  audit_generated_assets.py    read-only audit plus explicit remediation actions
static/
  data/robot-identities.json   canonical presentation identity registry
  rap-arena.html               restored rap-performance page
  index.html                   evidence and scoring page
  arena.html                   playable combat page
  js/                          rap, audio, identity, roster, and combat modules
  bots/                        real robot photo cutouts
  bots_tekken/                 stylized presentation assets
  beats/                       local instrumental beds and manifest
tests/                         Python and Node test suites
docs/generated-assets-remediation.md
                                regeneration and reversible archival runbook
```

## Data ingestion

The checked-in cache allows the application to run without Bright Data. To
perform a new, explicitly authorized ingestion:

```bash
export BRIGHTDATA_API_TOKEN=...

python ingest/brightdata.py bulk \
  --max-reddit-posts 12 \
  --max-youtube-videos 5 \
  --max-records 6000

python ingest/brightdata.py resume
python ingest/brightdata.py status
```

Use `resume` only for an already-triggered asynchronous snapshot. The ingestion
pipeline applies its record cap before comment collection, persists normalized
and deduplicated data to `data/botbeef.sqlite3`, and exports the JSON cache
consumed by Flask. A completed paid run is recorded so another `bulk` command
fails closed rather than spending credits accidentally.

There is no request-time scrape, scheduler, daemon, or automatic refresh.

## Generated battle and audio audit

The default audit is safe and read-only:

```bash
python ingest/audit_generated_assets.py
```

It reports battle readiness, exact regeneration commands, generation
preflight status, and root-level MP3 files not referenced by a manifest or
repository code. The default command does not contact a generation API, probe
a voice service, rewrite battle data, or move media.

Mutation requires an explicit regeneration or archive command. Regeneration is
transactional for battle JSON and the authoritative voice manifest. Audio
archival uses a reviewed plan, an explicit confirmation token, collision
checks, path-containment checks, and a reversible archive directory.

See
[`docs/generated-assets-remediation.md`](docs/generated-assets-remediation.md)
before performing either operation.

### Deferred remediation

Stale-battle regeneration and unused-audio cleanup are intentionally outside
the rap-arena restoration change. A separate implementation must cover:

- Anthropic or Cerebras generation credentials;
- voice-service configuration and verification;
- `ffmpeg` installation and verification;
- transactional regeneration of all ten untrusted battles;
- validation of 16 bars, citations, current provenance, manifests, and audio
  for every regenerated battle; and
- reviewed, reversible archival of the 263 currently unreferenced MP3 files.

Until that work is complete, the ten untrusted battles remain fail-closed and
the unreferenced MP3 files remain untouched.

## Optional generation and voice services

These services are not required to run the checked-in demo:

- **Anthropic**: `ANTHROPIC_API_KEY` and the installed `anthropic` package.
- **Cerebras**: `~/.claude/secrets/cerebras.json` containing `api_key`.
- **Chatterbox**: the default `BOTBEEF_VOICE` backend, with cloned voices
  available from the configured local service.
- **Kokoro**: an alternative local voice backend.
- **ElevenLabs**: `BOTBEEF_VOICE=elevenlabs` and
  `ELEVENLABS_API_KEY`.
- **ffmpeg**: required to convert WAV output from Chatterbox or Kokoro to MP3.
- **mflux**: used only to generate arena artwork, not to run the application.

The `/health` endpoint reports local configuration without making network
requests to these services.

## Tests

Install `pytest` if it is not already available, then run:

```bash
python -m pip install pytest
python -m pytest -q
node --test tests/test_gameplay.mjs tests/test_identity_registry.mjs tests/test_rap_arena_preview.mjs tests/test_rap_audio.mjs
```

The browser JavaScript is framework-free and uses the vendored Three.js module
at runtime. Node is needed only for the JavaScript test suite.

## License

MIT.
