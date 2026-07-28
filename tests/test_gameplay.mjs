import test from "node:test";
import assert from "node:assert/strict";

import { CombatEngine, DEFAULT_CONTROLS } from "../static/js/combat-engine.js";
import {
  ROBOT_IDS,
  ROBOT_LIST,
  getRobotById,
  resolveRobotId,
} from "../static/js/roster.js";

test("MVP roster has exactly the six canonical robots", () => {
  assert.deepEqual(ROBOT_IDS, [
    "witch-doctor",
    "tombstone",
    "hypershock",
    "minotaur",
    "huge",
    "cobalt",
  ]);
  assert.equal(ROBOT_LIST.length, 6);
  for (const robot of ROBOT_LIST) {
    assert.equal(robot.attacks.length, 4);
    assert.match(robot.image, /^\/bots\/[a-z-]+\.png$/);
  }
  assert.equal(resolveRobotId("WitchDoctor"), "witch-doctor");
  assert.equal(getRobotById("H.U.G.E.").id, "huge");
});

test("keyboard movement, sidestepping, and restart controls are stable", () => {
  assert.deepEqual(DEFAULT_CONTROLS.attacks, ["KeyJ", "KeyK", "KeyL", "KeyI"]);
  assert.equal(DEFAULT_CONTROLS.block, "Space");
  assert.equal(DEFAULT_CONTROLS.restart, "KeyR");

  const engine = new CombatEngine(
    getRobotById("hypershock"),
    getRobotById("cobalt"),
    { aiEnabled: false },
  );
  engine.start();
  const start = engine.getFighterState("p1").position;
  engine.handleKeyDown({ code: "KeyW", preventDefault() {} });
  engine.update(0.5);
  engine.handleKeyUp({ code: "KeyW", preventDefault() {} });
  const advanced = engine.getFighterState("p1").position;
  assert.ok(advanced.x > start.x, "W should move toward the opponent");

  engine.handleKeyDown({ code: "KeyD", preventDefault() {} });
  engine.update(0.5);
  engine.handleKeyUp({ code: "KeyD", preventDefault() {} });
  const sidestepped = engine.getFighterState("p1").position;
  assert.notEqual(sidestepped.z, advanced.z, "D should sidestep on the depth axis");

  engine.handleKeyDown({ code: "KeyR", repeat: false, preventDefault() {} });
  assert.equal(engine.getState().round, 1);
  assert.equal(engine.getFighterState("p1").health, 100);
});

test("attacks hit in range and blocking materially reduces damage", () => {
  const makeEngine = () => new CombatEngine(
    getRobotById("witch-doctor"),
    getRobotById("tombstone"),
    { aiEnabled: false, random: () => 0.5 },
  );

  const open = makeEngine();
  open.start();
  open.fighters.p1.position.x = -0.8;
  open.fighters.p2.position.x = 0.8;
  assert.equal(open.tryAttack("p1", 1), true);
  open.update(0.7);
  const openDamage = 100 - open.getFighterState("p2").health;
  assert.ok(openDamage > 0);

  const guarded = makeEngine();
  guarded.start();
  guarded.fighters.p1.position.x = -0.8;
  guarded.fighters.p2.position.x = 0.8;
  guarded.fighters.p2.input.block = true;
  guarded.update(0.02);
  assert.equal(guarded.tryAttack("p1", 1), true);
  guarded.update(0.7);
  const guardedDamage = 100 - guarded.getFighterState("p2").health;
  assert.ok(guardedDamage < openDamage / 2, "guard should reduce damage substantially");
});

test("KO, best-of-three rounds, timer, and full restart form a complete loop", () => {
  const events = [];
  const engine = new CombatEngine(
    getRobotById("cobalt"),
    getRobotById("minotaur"),
    {
      aiEnabled: false,
      roundBreakDuration: 0,
      onEvent: (event) => events.push(event.type),
    },
  );
  engine.start();
  engine.fighters.p1.position.x = -0.7;
  engine.fighters.p2.position.x = 0.7;
  engine.fighters.p2.health = 1;
  engine.tryAttack("p1", 0);
  engine.update(0.5);
  assert.ok(events.includes("ko"));
  assert.equal(engine.getState().phase, "round-over");
  assert.equal(engine.getFighterState("p1").roundsWon, 1);

  engine.update(0.02);
  assert.equal(engine.getState().round, 2);
  assert.equal(engine.getState().roundTime, 60);

  for (let second = 0; second < 60; second += 1) engine.update(1);
  assert.equal(engine.getState().phase, "round-over");
  assert.ok(events.includes("round-end"));

  engine.restart();
  const state = engine.getState();
  assert.equal(state.phase, "fighting");
  assert.equal(state.round, 1);
  assert.equal(state.fighters.p1.roundsWon, 0);
  assert.equal(state.fighters.p2.roundsWon, 0);
  assert.equal(state.roundTime, 60);
});
