const MATCHUP_BEAT_THEMES = Object.freeze({
  "bite-force__black-dragon": "boombap",
  "black-dragon__blip": "gfunk",
  "black-dragon__malice": "trap",
  "black-dragon__ribbot": "grime",
  "black-dragon__sawblaze": "industrial",
  "cobalt__malice": "drill",
  "hydra__tombstone": "industrial",
});

function matchupKey(a, b) {
  return [String(a || ""), String(b || "")].sort().join("__");
}

function stableIndex(value, length) {
  let hash = 2166136261;
  for (const char of value) {
    hash ^= char.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return length ? (hash >>> 0) % length : -1;
}

export function selectBeatForMatchup(a, b, beats) {
  if (!Array.isArray(beats) || !beats.length) return null;
  const themedSlug = MATCHUP_BEAT_THEMES[matchupKey(a, b)];
  const themed = beats.find((beat) => beat.slug === themedSlug);
  return themed || beats[stableIndex(matchupKey(a, b), beats.length)];
}

export function selectCachedMatchup(rows, battles) {
  const availableRows = Array.isArray(rows) ? rows : [];
  const selectable = new Set(availableRows.map((row) => row?.slug).filter(Boolean));
  const cached = (Array.isArray(battles) ? battles : []).find(
    (battle) =>
      battle?.a &&
      battle?.b &&
      battle.a !== battle.b &&
      selectable.has(battle.a) &&
      selectable.has(battle.b)
  );
  if (cached) return { a: cached.a, b: cached.b };

  const a = availableRows.find((row) => row?.slug)?.slug || "";
  const b = availableRows.find((row) => row?.slug && row.slug !== a)?.slug || "";
  return { a, b };
}

export function buildVoiceSequence(manifest) {
  if (!manifest || typeof manifest !== "object") return [];
  const sequence = [];
  if (typeof manifest.intro === "string" && manifest.intro) {
    sequence.push({ index: -1, file: manifest.intro, kind: "intro" });
  }
  if (Array.isArray(manifest.bars)) {
    for (const entry of manifest.bars) {
      if (!entry || typeof entry.file !== "string" || !entry.file) continue;
      const index = Number(entry.index);
      if (!Number.isInteger(index)) continue;
      sequence.push({
        index,
        file: entry.file,
        kind: "bar",
        durationMs: Number(entry.duration_ms) || null,
      });
    }
  }
  return sequence;
}

function readableFallbackMs(step, bars) {
  if (step.durationMs && step.durationMs > 0) {
    return Math.max(900, Math.min(step.durationMs, 12000));
  }
  if (step.kind === "intro") return 1400;
  const text = bars?.[step.index]?.text || "";
  const words = String(text).trim().split(/\s+/).filter(Boolean).length;
  return Math.max(1800, Math.min(6500, 850 + words * 310));
}

export class RapAudioController {
  constructor({
    voiceElement,
    beatElement,
    audioRoot = "/audio/",
    beatRoot = "/beats/",
    onStep = () => {},
    onComplete = () => {},
    onStatus = () => {},
    schedule = (callback, delay) => window.setTimeout(callback, delay),
    cancel = (timer) => window.clearTimeout(timer),
  }) {
    this.voice = voiceElement;
    this.beat = beatElement;
    this.audioRoot = audioRoot;
    this.beatRoot = beatRoot;
    this.onStep = onStep;
    this.onComplete = onComplete;
    this.onStatus = onStatus;
    this.schedule = schedule;
    this.cancel = cancel;
    this.runId = 0;
    this.timer = null;
    this.sequence = [];
    this.position = 0;
    this.bars = [];

    if (this.beat) {
      this.beat.loop = true;
      this.beat.volume = 0.22;
      this.beat.preload = "metadata";
    }
    if (this.voice) {
      this.voice.volume = 1;
      this.voice.preload = "metadata";
    }
  }

  setBeat(beat) {
    if (!this.beat) return;
    this.beat.pause();
    this.beat.removeAttribute("src");
    if (beat?.file) {
      this.beat.src = this.beatRoot + encodeURIComponent(beat.file);
      this.beat.load?.();
    }
  }

  stop({ complete = false } = {}) {
    this.runId += 1;
    if (this.timer !== null) {
      this.cancel(this.timer);
      this.timer = null;
    }
    for (const audio of [this.voice, this.beat]) {
      if (!audio) continue;
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
    }
    if (complete) this.onComplete();
  }

  async play({ manifest, bars = [], beat = null }) {
    this.stop();
    this.sequence = buildVoiceSequence(manifest);
    this.position = 0;
    this.bars = bars;
    this.setBeat(beat);

    if (!this.sequence.length || !this.voice) {
      this.onStatus("Voice manifest unavailable; showing the sourced bars without audio.");
      this.onComplete();
      return false;
    }

    const runId = this.runId;
    if (this.beat?.src) {
      this.beat.currentTime = 0;
      this.beat.play().catch(() => {
        if (runId === this.runId) {
          this.onStatus("Beat unavailable; continuing with the voice track.");
        }
      });
    }
    this.#playNext(runId);
    return true;
  }

  #playNext(runId) {
    if (runId !== this.runId) return;
    if (this.position >= this.sequence.length) {
      this.stop({ complete: true });
      return;
    }

    const step = this.sequence[this.position++];
    this.onStep(step);
    const advance = () => {
      if (runId !== this.runId) return;
      if (this.timer !== null) {
        this.cancel(this.timer);
        this.timer = null;
      }
      this.#playNext(runId);
    };
    const holdThenAdvance = () => {
      if (runId !== this.runId || this.timer !== null) return;
      const delay = readableFallbackMs(step, this.bars);
      this.onStatus(`Voice clip unavailable; holding this ${step.kind} for ${Math.round(delay / 100) / 10}s.`);
      this.timer = this.schedule(advance, delay);
    };

    this.voice.onended = advance;
    this.voice.onerror = holdThenAdvance;
    this.voice.src = this.audioRoot + encodeURIComponent(step.file);
    this.voice.load?.();
    const playResult = this.voice.play();
    if (playResult?.catch) playResult.catch(holdThenAdvance);
  }
}
