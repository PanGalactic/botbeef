import test from "node:test";
import assert from "node:assert/strict";

import {
  RapAudioController,
  buildVoiceSequence,
  selectBeatForMatchup,
  selectCachedMatchup,
} from "../static/js/rap-audio.js";

class FakeAudio {
  constructor({ fail = false } = {}) {
    this.fail = fail;
    this.src = "";
    this.paused = false;
  }
  removeAttribute(name) {
    if (name === "src") this.src = "";
  }
  load() {}
  pause() {
    this.paused = true;
  }
  play() {
    return this.fail ? Promise.reject(new Error("missing")) : Promise.resolve();
  }
}

test("known matchups receive an explicit themed beat and unknown pairs stay deterministic", () => {
  const beats = ["grime", "industrial", "boombap", "trap"].map((slug) => ({ slug, file: `${slug}.mp3` }));
  assert.equal(selectBeatForMatchup("tombstone", "hydra", beats).slug, "industrial");
  assert.equal(
    selectBeatForMatchup("unknown-a", "unknown-b", beats).slug,
    selectBeatForMatchup("unknown-b", "unknown-a", beats).slug,
  );
});

test("default battle ignores cached matchups whose bots are not selectable", () => {
  const rows = [
    { slug: "cobalt" },
    { slug: "malice" },
    { slug: "tombstone" },
  ];
  const battles = [
    { a: "bite-force", b: "black-dragon" },
    { a: "cobalt", b: "malice" },
  ];
  assert.deepEqual(selectCachedMatchup(rows, battles), { a: "cobalt", b: "malice" });
  assert.deepEqual(
    selectCachedMatchup(rows, [{ a: "missing-a", b: "missing-b" }]),
    { a: "cobalt", b: "malice" },
  );
});

test("voice sequence order and bar indices come only from the manifest", () => {
  assert.deepEqual(
    buildVoiceSequence({
      intro: "intro.wav",
      bars: [
        { index: 8, file: "eight.wav" },
        { index: 2, file: "two.wav", duration_ms: 2500 },
      ],
    }),
    [
      { index: -1, file: "intro.wav", kind: "intro" },
      { index: 8, file: "eight.wav", kind: "bar", durationMs: null },
      { index: 2, file: "two.wav", kind: "bar", durationMs: 2500 },
    ],
  );
});

test("a missing voice clip holds its manifest bar instead of rapidly advancing", async () => {
  const scheduled = [];
  const steps = [];
  const voice = new FakeAudio({ fail: true });
  const beat = new FakeAudio();
  const controller = new RapAudioController({
    voiceElement: voice,
    beatElement: beat,
    onStep: (step) => steps.push(step.index),
    schedule: (callback, delay) => {
      scheduled.push({ callback, delay });
      return scheduled.length;
    },
    cancel: () => {},
  });

  await controller.play({
    manifest: { bars: [{ index: 4, file: "missing.wav" }, { index: 9, file: "also-missing.wav" }] },
    bars: Array.from({ length: 10 }, (_, index) => ({ text: `bar ${index} has enough words to read` })),
    beat: { file: "industrial.mp3" },
  });
  await Promise.resolve();

  assert.deepEqual(steps, [4]);
  assert.equal(scheduled.length, 1);
  assert.ok(scheduled[0].delay >= 1800);
  scheduled[0].callback();
  assert.deepEqual(steps, [4, 9]);
});
