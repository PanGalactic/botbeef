# Generated battle and audio remediation

The committed cache contains both verified stage assets and older hackathon
drafts. Never change provenance flags or source URLs by hand. A battle becomes
playable only after new lyrics are generated from the current sourced corpus,
all 16 bars pass citation validation, and a complete voice manifest is
rendered.

## Read-only audit

From the repository root:

```powershell
python ingest/audit_generated_assets.py
```

The JSON report lists every battle, its provenance state, its exact
regeneration command, generation preflight results, and every root-level MP3
that no manifest or code file references. The audit does not contact an API,
probe a voice service, rewrite a cache, or move media.

## Regenerate an untrusted pair

First resolve every failed item under `preflight`:

- Anthropic requires the `anthropic` package and `ANTHROPIC_API_KEY`.
- Cerebras requires `~/.claude/secrets/cerebras.json` containing `api_key`.
- `BOTBEEF_VOICE` defaults to `chatterbox`; that service must be reachable and
  have the cloned voices. `kokoro` also requires its local service.
- Chatterbox and Kokoro return WAV data, so `ffmpeg` must be on `PATH`.
- ElevenLabs requires `ELEVENLABS_API_KEY`; it returns MP3 directly.

Then run the command emitted for the pair, for example:

```powershell
python ingest/audit_generated_assets.py --regenerate tombstone cobalt --backend anthropic
```

Regeneration is transactional for the battle JSON and authoritative manifest.
If generation, citation validation, TTS, or the final readiness audit fails,
the previous battle and manifest are restored. Newly content-addressed clips
created before a failure can remain unreferenced; the next audit reports them.

Do not use `ingest/pregen.py --no-audio` for remediation. It cannot produce a
stage-ready battle.

## Reversible audio archive

The audit classifies a root-level MP3 as archiveable only when:

1. its filename is a 16-character lowercase hexadecimal content hash;
2. no current voice manifest references it;
3. no repository text/code file outside `audio/` references it.

Review `audio.archive_plan`, then archive exactly that recalculated set:

```powershell
python ingest/audit_generated_assets.py --apply-archive `
  --confirm ARCHIVE_UNREFERENCED_GENERATED_AUDIO
```

Files move to `audio/archive/unreferenced/`; they are not deleted. Restore them
with:

```powershell
python ingest/audit_generated_assets.py --restore-archive
```

Both operations fail before overwriting an existing destination. Commit or
back up the audit result before applying an archive in a shared branch.

The archive action resolves every path before doing any move. Every resolved
source must be a direct child of `audio/`, and every resolved destination must
be a direct child of `audio/archive/unreferenced/`. This also rejects a
symlink or Windows reparse point that resolves outside those directories.
The complete plan, including every destination collision, is validated first;
if one entry fails, no candidate is moved.
