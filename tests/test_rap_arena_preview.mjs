import assert from "node:assert/strict";

import {
  formatAudioState,
  formatBeat,
  formatScoreComparison,
  resolveDisplayName,
  resolveSpritePath,
  selectPreviewFact,
} from "../static/js/rap-arena.js";

const fact = selectPreviewFact([
  {
    kind: "quote",
    text: "A dramatic but less useful quotation.",
    source_url: "https://example.test/quote",
  },
  {
    kind: "record",
    text: "Alpha is 7-1 across eight recorded fights.",
    source_url: "https://example.test/record",
  },
  {
    kind: "overrated",
    text: "Beta's fan hype sits 19.6 points above what its record predicts.",
    source_url: "https://example.test/hype",
  },
  {
    kind: "matchup",
    text: "Alpha beat Beta in their last sourced meeting.",
    source_url: "javascript:alert(1)",
  },
]);

assert.equal(
  fact.text,
  "Beta's fan hype sits 19.6 points above what its record predicts.",
  "preview should prefer a sourced comparison and reject unsafe-source facts",
);

assert.equal(
  selectPreviewFact([{ kind: "record", text: "Unsourced claim" }]),
  null,
  "preview must not present an unsourced claim as verified",
);

assert.equal(
  formatScoreComparison(
    { slug: "alpha", name: "Alpha", hype: 74.45, performance: 46.24 },
    { slug: "beta", name: "Beta", hype: 22.91, performance: 66.82 },
  ),
  "Alpha: 74.5 hype / 46.2 record · Beta: 22.9 hype / 66.8 record",
);
assert.equal(formatScoreComparison(null, null), "Record comparison unavailable");

assert.equal(
  formatAudioState({
    audio: {
      complete: true,
      clips_present: 16,
      clips_expected: 16,
      intro_present: true,
    },
  }),
  "16/16 bars + intro verified",
);
assert.equal(
  formatAudioState({ audio: { manifest: true, complete: false } }),
  "Audio incomplete · caption fallback",
);
assert.equal(formatAudioState({}), "Caption fallback only");

assert.equal(formatBeat({ style: "UK grime", bpm: 140 }), "UK grime · 140 BPM");
assert.equal(formatBeat(null), "Instrumental unavailable");

assert.equal(
  resolveDisplayName("bloodsport", {
    identity: { id: "bloodsport", name: "Bloodsport" },
    battleName: "bloodsport",
    tableName: "bloodsport",
  }),
  "Bloodsport",
  "canonical registry name must outrank lowercase battle and table payloads",
);
assert.equal(
  resolveDisplayName("end-game", {
    battleName: "End Game",
    tableName: "end-game",
  }),
  "End Game",
  "battle names remain the compatibility fallback when the registry is unavailable",
);

assert.equal(
  resolveSpritePath(
    "bloodsport",
    { assets: { standard: "/bots/bloodsport.png" } },
    new Set(["bloodsport"]),
    new Set(),
  ),
  "/bots/bloodsport.png",
);
assert.equal(
  resolveSpritePath(
    "bloodsport",
    { assets: { standard: "/unverified/path.png" } },
    new Set(["bloodsport"]),
    new Set(),
  ),
  "/bots/bloodsport.png",
  "unverified registry paths must not bypass the known sprite manifest",
);

console.log("rap arena preview helpers: ok");
