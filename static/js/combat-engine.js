/**
 * Renderer-agnostic combat simulation for BotBeef.
 *
 * The engine deliberately stores only numbers, strings, booleans, and plain
 * objects. A Three.js integration can copy `fighter.position.x/z` and
 * `fighter.facing` onto meshes after every `update(dt)` call, then use events
 * to trigger animation, particles, camera shake, and sound.
 *
 * Robot definitions are injected rather than imported. A definition may keep
 * ratings under `stats` or at its top level and must provide four attacks:
 *
 * @example
 * const engine = new CombatEngine(playerDefinition, opponentDefinition, {
 *   onEvent: event => presentation.handleCombatEvent(event)
 * });
 * engine.bindKeyboard(window);
 * engine.start();
 *
 * function frame(now) {
 *   engine.update((now - previousNow) / 1000);
 *   const state = engine.getState();
 *   renderFighter(playerMesh, state.fighters.p1);
 *   renderFighter(aiMesh, state.fighters.p2);
 *   previousNow = now;
 *   requestAnimationFrame(frame);
 * }
 */

/** @typedef {'idle'|'fighting'|'round-over'|'match-over'} MatchPhase */

/**
 * @typedef {Object} AttackDefinition
 * @property {string} [id] Stable move identifier.
 * @property {string} [name] Display name.
 * @property {number} damage Base damage before the opponent's armour reduction.
 * @property {number} range Centre-to-centre hit range in world units.
 * @property {number} cooldown Seconds before the move can be used again.
 * @property {number} knockback Initial knockback velocity in world units/second.
 * @property {number} [startup=0.12] Seconds before the active hit window.
 * @property {number} [active=0.10] Duration of the active hit window.
 * @property {number} [recovery=0.24] Recovery after the active window.
 * @property {number} [stun=0.24] Hit-stun inflicted in seconds.
 * @property {number} [arc=100] Frontal hit arc in degrees.
 * @property {number} [chipDamageRatio=0.08] Damage ratio applied through block.
 */

/**
 * @typedef {Object} RobotDefinition
 * @property {string} [id] Canonical roster identifier.
 * @property {string} [canonicalId] Alias for `id`.
 * @property {string} [name] Display name.
 * @property {Object} [stats] Robot ratings.
 * @property {number} [speed] Speed rating (1-10 or 1-100).
 * @property {number} [armour] Armour rating (1-10 or 1-100).
 * @property {number} [handling] Handling rating (1-10 or 1-100).
 * @property {number} [movementSpeed] Explicit world-units/second override.
 * @property {number} [maxHealth=100] Starting health each round.
 * @property {number} [collisionRadius=0.72] Collision radius in world units.
 * @property {AttackDefinition[]|Object<string, AttackDefinition>} attacks
 */

/**
 * @typedef {Object} CombatEvent
 * @property {string} type Event name.
 * @property {number} round Current round number.
 * @property {number} roundTime Seconds remaining in the round.
 * @property {Object} [actor] Snapshot of the acting fighter.
 * @property {Object} [target] Snapshot of the target fighter.
 */

export const DEFAULT_CONTROLS = Object.freeze({
  forward: "KeyW",
  backward: "KeyS",
  sidestepLeft: "KeyA",
  sidestepRight: "KeyD",
  attacks: Object.freeze(["KeyJ", "KeyK", "KeyL", "KeyI"]),
  block: "Space",
  restart: "KeyR",
});

const DEFAULT_ATTACKS = Object.freeze([
  { id: "quick-strike", name: "Quick Strike", damage: 7, range: 1.75, cooldown: 0.45, knockback: 2.2, startup: 0.08, active: 0.09, recovery: 0.16, stun: 0.15, arc: 105 },
  { id: "power-strike", name: "Power Strike", damage: 13, range: 1.95, cooldown: 0.85, knockback: 4.0, startup: 0.18, active: 0.10, recovery: 0.30, stun: 0.28, arc: 95 },
  { id: "sweeping-strike", name: "Sweeping Strike", damage: 10, range: 2.35, cooldown: 1.05, knockback: 3.1, startup: 0.22, active: 0.16, recovery: 0.32, stun: 0.22, arc: 150 },
  { id: "signature-strike", name: "Signature Strike", damage: 18, range: 2.1, cooldown: 1.65, knockback: 5.7, startup: 0.32, active: 0.13, recovery: 0.46, stun: 0.42, arc: 80 },
]);

const EPSILON = 0.00001;

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function finite(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function clonePosition(position) {
  return { x: position.x, z: position.z };
}

function rating01(value, fallback = 5) {
  const rating = finite(value, fallback);
  return clamp(rating > 10 ? rating / 100 : rating / 10, 0, 1);
}

function attackEntries(definition) {
  if (Array.isArray(definition.attacks)) return definition.attacks;
  if (definition.attacks && typeof definition.attacks === "object") {
    return Object.entries(definition.attacks).map(([id, attack]) => ({ id, ...attack }));
  }
  return [];
}

function normalizeAttack(raw, fallback, index) {
  const source = raw && typeof raw === "object" ? raw : {};
  const startup = clamp(finite(source.startup ?? source.windup, fallback.startup), 0.01, 3);
  const active = clamp(finite(source.active ?? source.activeTime, fallback.active), 0.01, 2);
  const recovery = clamp(finite(source.recovery, fallback.recovery), 0.01, 4);
  return Object.freeze({
    id: String(source.id || source.slug || `attack-${index + 1}`),
    name: String(source.name || source.displayName || fallback.name),
    damage: clamp(finite(source.damage ?? source.baseDamage, fallback.damage), 0, 100),
    range: clamp(finite(source.range ?? source.reach, fallback.range), 0.2, 12),
    cooldown: clamp(finite(source.cooldown ?? source.cooldownSeconds, fallback.cooldown), 0.05, 15),
    knockback: clamp(finite(source.knockback ?? source.knockbackForce, fallback.knockback), 0, 30),
    startup,
    active,
    recovery,
    stun: clamp(finite(source.stun ?? source.hitStun, fallback.stun), 0, 5),
    arc: clamp(finite(source.arc ?? source.arcDegrees ?? source.hitArc, fallback.arc), 10, 360),
    chipDamageRatio: clamp(finite(source.chipDamageRatio ?? source.blockDamageRatio, 0.08), 0, 1),
    duration: startup + active + recovery,
  });
}

function normalizeRobot(definition, fallbackId) {
  if (!definition || typeof definition !== "object") {
    throw new TypeError(`CombatEngine requires a robot definition for ${fallbackId}.`);
  }

  const stats = definition.stats && typeof definition.stats === "object" ? definition.stats : definition;
  const attacks = attackEntries(definition);
  const normalizedAttacks = DEFAULT_ATTACKS.map((fallback, index) =>
    normalizeAttack(attacks[index], fallback, index)
  );
  const speed = rating01(stats.speed, 5);
  const handling = rating01(stats.handling, 5);
  const armour = rating01(stats.armour ?? stats.armor, 5);

  return Object.freeze({
    id: String(definition.canonicalId || definition.id || definition.slug || fallbackId),
    name: String(definition.name || definition.displayName || definition.id || fallbackId),
    maxHealth: clamp(finite(definition.maxHealth, 100), 1, 1000),
    collisionRadius: clamp(finite(definition.collisionRadius, 0.72), 0.2, 3),
    moveSpeed: clamp(finite(definition.movementSpeed, 4.2 + speed * 3.2), 1, 20),
    handling,
    armour,
    attacks: Object.freeze(normalizedAttacks),
  });
}

function makeFighter(side, definition, x) {
  return {
    side,
    id: definition.id,
    name: definition.name,
    definition,
    health: definition.maxHealth,
    maxHealth: definition.maxHealth,
    position: { x, z: 0 },
    velocity: { x: 0, z: 0 },
    facing: side === "p1" ? 0 : Math.PI,
    stun: 0,
    blocking: false,
    attack: null,
    cooldowns: definition.attacks.map(() => 0),
    input: { forward: false, backward: false, left: false, right: false, block: false },
    roundsWon: 0,
  };
}

/**
 * Reusable two-fighter combat simulation.
 *
 * @fires CombatEngine#attack-start
 * @fires CombatEngine#attack-end
 * @fires CombatEngine#hit
 * @fires CombatEngine#block
 * @fires CombatEngine#ko
 * @fires CombatEngine#round-start
 * @fires CombatEngine#round-end
 * @fires CombatEngine#match-start
 * @fires CombatEngine#match-end
 */
export class CombatEngine {
  /**
   * @param {RobotDefinition} playerDefinition Player-one robot data.
   * @param {RobotDefinition} opponentDefinition Player-two robot data.
   * @param {Object} [options]
   * @param {number} [options.roundDuration=60] Round length in seconds.
   * @param {number} [options.roundBreakDuration=2.25] Pause before the next round.
   * @param {boolean} [options.aiEnabled=true] Whether player two uses built-in AI.
   * @param {Object} [options.bounds] Arena half extents: `{x: 8, z: 5}`.
   * @param {number} [options.spawnDistance=4.5] Distance of each spawn from centre.
   * @param {(event: CombatEvent) => void} [options.onEvent] Catch-all event callback.
   * @param {() => number} [options.random=Math.random] Injectable RNG for deterministic tests.
   */
  constructor(playerDefinition, opponentDefinition, options = {}) {
    this.options = {
      roundDuration: clamp(finite(options.roundDuration, 60), 5, 600),
      roundBreakDuration: clamp(finite(options.roundBreakDuration, 2.25), 0, 20),
      aiEnabled: options.aiEnabled !== false,
      bounds: {
        x: clamp(finite(options.bounds?.x, 8), 2, 100),
        z: clamp(finite(options.bounds?.z, 5), 2, 100),
      },
      spawnDistance: clamp(finite(options.spawnDistance, 4.5), 1, 30),
      random: typeof options.random === "function" ? options.random : Math.random,
      onEvent: typeof options.onEvent === "function" ? options.onEvent : null,
    };

    const player = normalizeRobot(playerDefinition, "player-one");
    const opponent = normalizeRobot(opponentDefinition, "player-two");
    this.fighters = {
      p1: makeFighter("p1", player, -this.options.spawnDistance),
      p2: makeFighter("p2", opponent, this.options.spawnDistance),
    };

    /** @type {MatchPhase} */
    this.phase = "idle";
    this.round = 0;
    this.roundTime = this.options.roundDuration;
    this.roundBreakRemaining = 0;
    this.elapsed = 0;
    this.matchWinner = null;
    this.listeners = new Map();
    this.keyboardTarget = null;
    this.ai = { decisionIn: 0, preferredRange: 1.8, blockIn: 0 };

    this.handleKeyDown = this.handleKeyDown.bind(this);
    this.handleKeyUp = this.handleKeyUp.bind(this);
  }

  /**
   * Subscribe to a combat event. Subscribe to `"*"` for every event.
   * @param {string} type
   * @param {(event: CombatEvent) => void} listener
   * @returns {() => void} Unsubscribe function.
   */
  on(type, listener) {
    if (typeof listener !== "function") throw new TypeError("Combat event listener must be a function.");
    const bucket = this.listeners.get(type) || new Set();
    bucket.add(listener);
    this.listeners.set(type, bucket);
    return () => this.off(type, listener);
  }

  /**
   * Remove a previously registered listener.
   * @param {string} type
   * @param {(event: CombatEvent) => void} listener
   */
  off(type, listener) {
    const bucket = this.listeners.get(type);
    if (!bucket) return;
    bucket.delete(listener);
    if (!bucket.size) this.listeners.delete(type);
  }

  /**
   * Start a fresh best-of-three match and immediately begin round one.
   */
  start() {
    this._resetMatch();
    this._emit("match-start", {});
    this._startRound();
  }

  /**
   * Restart the entire match. This is the action bound to the R key.
   */
  restart() {
    this.start();
  }

  /**
   * Advance the simulation.
   *
   * Call once per render frame with time in seconds. Large frame gaps retain
   * accurate round timing but physics is limited to 0.25 seconds per call so a
   * backgrounded browser tab cannot catapult fighters across the arena.
   *
   * @param {number} dt Seconds since the previous update.
   */
  update(dt) {
    const elapsed = clamp(finite(dt, 0), 0, 5);
    if (elapsed <= 0 || this.phase === "idle" || this.phase === "match-over") return;
    this.elapsed += elapsed;

    if (this.phase === "round-over") {
      this.roundBreakRemaining -= elapsed;
      if (this.roundBreakRemaining <= 0) this._startRound();
      return;
    }

    this.roundTime = Math.max(0, this.roundTime - elapsed);
    const simulationTime = Math.min(elapsed, 0.25);
    let remaining = simulationTime;
    while (remaining > EPSILON && this.phase === "fighting") {
      const step = Math.min(remaining, 1 / 60);
      this._step(step);
      remaining -= step;
    }

    if (this.phase === "fighting" && this.roundTime <= 0) this._finishByTime();
  }

  /**
   * Return an immutable-by-convention snapshot safe for rendering/UI code.
   * Mutating this object never changes the live simulation.
   */
  getState() {
    return {
      phase: this.phase,
      round: this.round,
      roundTime: this.roundTime,
      roundDuration: this.options.roundDuration,
      bestOf: 3,
      winsNeeded: 2,
      elapsed: this.elapsed,
      matchWinner: this.matchWinner,
      fighters: {
        p1: this._snapshotFighter(this.fighters.p1),
        p2: this._snapshotFighter(this.fighters.p2),
      },
    };
  }

  /**
   * Return one fighter snapshot.
   * @param {'p1'|'p2'} side
   */
  getFighterState(side) {
    const fighter = this.fighters[side];
    if (!fighter) throw new RangeError(`Unknown fighter side: ${side}`);
    return this._snapshotFighter(fighter);
  }

  /**
   * Attach default keyboard controls to a Window, Document, or EventTarget.
   * Calling this again first detaches the previous target.
   * @param {EventTarget} [target=window]
   */
  bindKeyboard(target = globalThis.window) {
    if (!target?.addEventListener) throw new TypeError("Keyboard target must support addEventListener.");
    this.unbindKeyboard();
    this.keyboardTarget = target;
    target.addEventListener("keydown", this.handleKeyDown);
    target.addEventListener("keyup", this.handleKeyUp);
  }

  /**
   * Detach keyboard controls and clear held player inputs.
   */
  unbindKeyboard() {
    if (this.keyboardTarget) {
      this.keyboardTarget.removeEventListener("keydown", this.handleKeyDown);
      this.keyboardTarget.removeEventListener("keyup", this.handleKeyUp);
      this.keyboardTarget = null;
    }
    this._clearInput(this.fighters.p1);
  }

  /**
   * DOM keydown handler, exposed for integrations that manage listeners.
   * @param {KeyboardEvent|{code?: string, key?: string, repeat?: boolean, preventDefault?: Function}} event
   */
  handleKeyDown(event) {
    const code = this._keyCode(event);
    if (!this._isControl(code)) return;
    event.preventDefault?.();

    if (code === DEFAULT_CONTROLS.restart) {
      if (!event.repeat) this.restart();
      return;
    }
    if (this.phase !== "fighting") return;

    const input = this.fighters.p1.input;
    if (code === DEFAULT_CONTROLS.forward) input.forward = true;
    else if (code === DEFAULT_CONTROLS.backward) input.backward = true;
    else if (code === DEFAULT_CONTROLS.sidestepLeft) input.left = true;
    else if (code === DEFAULT_CONTROLS.sidestepRight) input.right = true;
    else if (code === DEFAULT_CONTROLS.block) input.block = true;
    else if (!event.repeat) {
      const attackIndex = DEFAULT_CONTROLS.attacks.indexOf(code);
      if (attackIndex >= 0) this.tryAttack("p1", attackIndex);
    }
  }

  /**
   * DOM keyup handler, exposed for integrations that manage listeners.
   * @param {KeyboardEvent|{code?: string, key?: string, preventDefault?: Function}} event
   */
  handleKeyUp(event) {
    const code = this._keyCode(event);
    if (!this._isControl(code)) return;
    event.preventDefault?.();
    const input = this.fighters.p1.input;
    if (code === DEFAULT_CONTROLS.forward) input.forward = false;
    else if (code === DEFAULT_CONTROLS.backward) input.backward = false;
    else if (code === DEFAULT_CONTROLS.sidestepLeft) input.left = false;
    else if (code === DEFAULT_CONTROLS.sidestepRight) input.right = false;
    else if (code === DEFAULT_CONTROLS.block) input.block = false;
  }

  /**
   * Attempt one of a fighter's four attacks.
   *
   * Useful for touch/gamepad controls and for disabling the built-in AI in
   * favour of a remote or custom controller.
   *
   * @param {'p1'|'p2'} side
   * @param {number} attackIndex Index from 0 through 3.
   * @returns {boolean} Whether the attack started.
   */
  tryAttack(side, attackIndex) {
    const fighter = this.fighters[side];
    if (!fighter) throw new RangeError(`Unknown fighter side: ${side}`);
    const index = Math.trunc(attackIndex);
    if (
      this.phase !== "fighting" ||
      index < 0 ||
      index >= 4 ||
      fighter.health <= 0 ||
      fighter.stun > 0 ||
      fighter.attack ||
      fighter.blocking ||
      fighter.cooldowns[index] > 0
    ) {
      return false;
    }

    const move = fighter.definition.attacks[index];
    fighter.attack = { index, move, elapsed: 0, connected: false };
    fighter.cooldowns[index] = move.cooldown;
    fighter.input.block = false;
    this._emit("attack-start", { actor: fighter, target: this._opponent(fighter), attack: this._snapshotAttack(move, index) });
    return true;
  }

  /**
   * Release listeners and keyboard ownership.
   */
  destroy() {
    this.unbindKeyboard();
    this.listeners.clear();
  }

  _step(dt) {
    const p1 = this.fighters.p1;
    const p2 = this.fighters.p2;
    this._updateFacing(p1, p2);
    this._updateFacing(p2, p1);
    if (this.options.aiEnabled) this._updateAI(dt);
    this._updateFighter(p1, p2, dt);
    this._updateFighter(p2, p1, dt);
    this._separateFighters();
    this._constrain(p1);
    this._constrain(p2);
  }

  _updateFighter(fighter, opponent, dt) {
    fighter.stun = Math.max(0, fighter.stun - dt);
    fighter.cooldowns = fighter.cooldowns.map(value => Math.max(0, value - dt));
    fighter.blocking =
      fighter.input.block &&
      fighter.stun <= 0 &&
      !fighter.attack &&
      fighter.health > 0;

    if (fighter.attack) {
      const action = fighter.attack;
      action.elapsed += dt;
      const inActiveWindow =
        action.elapsed >= action.move.startup &&
        action.elapsed <= action.move.startup + action.move.active;
      if (inActiveWindow && !action.connected) {
        action.connected = this._resolveAttack(fighter, opponent, action.move, action.index);
      }
      if (action.elapsed >= action.move.duration) {
        this._emit("attack-end", { actor: fighter, target: opponent, attack: this._snapshotAttack(action.move, action.index), connected: action.connected });
        fighter.attack = null;
      }
    }

    const canMove = fighter.stun <= 0 && fighter.health > 0;
    if (canMove) this._applyMovement(fighter, opponent, dt);

    const damping = Math.exp(-7 * dt);
    fighter.velocity.x *= damping;
    fighter.velocity.z *= damping;
    fighter.position.x += fighter.velocity.x * dt;
    fighter.position.z += fighter.velocity.z * dt;
  }

  _applyMovement(fighter, opponent, dt) {
    const dx = opponent.position.x - fighter.position.x;
    const dz = opponent.position.z - fighter.position.z;
    const distance = Math.hypot(dx, dz) || 1;
    const forwardX = dx / distance;
    const forwardZ = dz / distance;
    const rightX = -forwardZ;
    const rightZ = forwardX;
    const forwardInput = Number(fighter.input.forward) - Number(fighter.input.backward);
    const sideInput = Number(fighter.input.right) - Number(fighter.input.left);
    let moveX = forwardX * forwardInput + rightX * sideInput;
    let moveZ = forwardZ * forwardInput + rightZ * sideInput;
    const magnitude = Math.hypot(moveX, moveZ);
    if (magnitude <= EPSILON) return;
    moveX /= magnitude;
    moveZ /= magnitude;

    const reverseMultiplier = forwardInput < 0 ? 0.72 : 1;
    const strafeMultiplier = sideInput ? 0.72 + fighter.definition.handling * 0.22 : 1;
    const actionMultiplier = fighter.attack ? 0.28 : fighter.blocking ? 0.43 : 1;
    const speed = fighter.definition.moveSpeed * reverseMultiplier * strafeMultiplier * actionMultiplier;
    const acceleration = 10 + fighter.definition.handling * 14;
    fighter.velocity.x += (moveX * speed - fighter.velocity.x) * Math.min(1, acceleration * dt);
    fighter.velocity.z += (moveZ * speed - fighter.velocity.z) * Math.min(1, acceleration * dt);
  }

  _resolveAttack(attacker, defender, move, index) {
    if (defender.health <= 0) return false;
    const dx = defender.position.x - attacker.position.x;
    const dz = defender.position.z - attacker.position.z;
    const distance = Math.hypot(dx, dz);
    const hitRange = move.range + attacker.definition.collisionRadius + defender.definition.collisionRadius;
    if (distance > hitRange) return false;

    const directionX = distance > EPSILON ? dx / distance : Math.cos(attacker.facing);
    const directionZ = distance > EPSILON ? dz / distance : Math.sin(attacker.facing);
    const facingX = Math.cos(attacker.facing);
    const facingZ = Math.sin(attacker.facing);
    const minimumDot = Math.cos((move.arc * Math.PI) / 360);
    if (facingX * directionX + facingZ * directionZ < minimumDot) return false;

    const blockDot =
      Math.cos(defender.facing) * -directionX +
      Math.sin(defender.facing) * -directionZ;
    const blocked = defender.blocking && defender.stun <= 0 && blockDot >= 0.25;
    const armourReduction = 1 - defender.definition.armour * 0.22;
    const rawDamage = move.damage * armourReduction;
    const damage = blocked
      ? Math.max(0, rawDamage * move.chipDamageRatio)
      : Math.max(1, rawDamage);
    const knockback = move.knockback * (blocked ? 0.22 : 1);

    defender.health = Math.max(0, defender.health - damage);
    defender.velocity.x += directionX * knockback;
    defender.velocity.z += directionZ * knockback;
    if (!blocked) {
      defender.stun = Math.max(defender.stun, move.stun);
      defender.attack = null;
      defender.blocking = false;
    } else {
      defender.stun = Math.max(defender.stun, Math.min(0.16, move.stun * 0.3));
    }

    const payload = {
      actor: attacker,
      target: defender,
      attack: this._snapshotAttack(move, index),
      damage,
      knockback,
      position: {
        x: (attacker.position.x + defender.position.x) / 2,
        z: (attacker.position.z + defender.position.z) / 2,
      },
    };
    this._emit(blocked ? "block" : "hit", payload);

    if (defender.health <= 0) {
      this._emit("ko", { actor: attacker, target: defender, attack: this._snapshotAttack(move, index) });
      this._endRound(attacker, "ko");
    }
    return true;
  }

  _updateAI(dt) {
    const ai = this.fighters.p2;
    const player = this.fighters.p1;
    if (ai.health <= 0 || ai.stun > 0) {
      this._clearInput(ai);
      return;
    }

    this.ai.decisionIn -= dt;
    this.ai.blockIn = Math.max(0, this.ai.blockIn - dt);
    if (this.ai.decisionIn > 0) {
      ai.input.block = this.ai.blockIn > 0;
      return;
    }
    this.ai.decisionIn = 0.14 + this.options.random() * 0.2;

    const distance = Math.hypot(
      player.position.x - ai.position.x,
      player.position.z - ai.position.z
    );
    const incoming = player.attack;
    const imminent =
      incoming &&
      incoming.elapsed >= Math.max(0, incoming.move.startup - 0.16) &&
      distance <= incoming.move.range + 1.7;
    if (imminent && this.options.random() < 0.48) {
      this.ai.blockIn = 0.18 + this.options.random() * 0.32;
    }

    ai.input.block = this.ai.blockIn > 0;
    ai.input.forward = !ai.input.block && distance > this.ai.preferredRange + 0.45;
    ai.input.backward = !ai.input.block && distance < 1.25;
    ai.input.left = !ai.input.block && !ai.input.forward && this.options.random() < 0.24;
    ai.input.right = !ai.input.block && !ai.input.forward && !ai.input.left && this.options.random() < 0.24;

    if (!ai.input.block && !ai.attack && distance < 3.2) {
      const available = ai.cooldowns
        .map((cooldown, index) => ({ cooldown, index, move: ai.definition.attacks[index] }))
        .filter(option => option.cooldown <= 0 && distance <= option.move.range + 1.55);
      if (available.length && this.options.random() < 0.28) {
        const weightedIndex = Math.floor(Math.pow(this.options.random(), 1.45) * available.length);
        this.tryAttack("p2", available[Math.min(weightedIndex, available.length - 1)].index);
      }
    }
  }

  _finishByTime() {
    const p1 = this.fighters.p1;
    const p2 = this.fighters.p2;
    let winner = null;
    if (p1.health > p2.health + EPSILON) winner = p1;
    else if (p2.health > p1.health + EPSILON) winner = p2;
    this._endRound(winner, "time");
  }

  _endRound(winner, reason) {
    if (this.phase !== "fighting") return;
    this.phase = "round-over";
    this._clearInput(this.fighters.p1);
    this._clearInput(this.fighters.p2);
    if (winner) winner.roundsWon += 1;

    this._emit("round-end", {
      winner: winner ? this._snapshotFighter(winner) : null,
      winnerSide: winner?.side || null,
      reason,
      score: {
        p1: this.fighters.p1.roundsWon,
        p2: this.fighters.p2.roundsWon,
      },
    });

    const matchComplete =
      this.fighters.p1.roundsWon >= 2 ||
      this.fighters.p2.roundsWon >= 2 ||
      this.round >= 3;
    if (matchComplete) {
      this.phase = "match-over";
      this.matchWinner =
        this.fighters.p1.roundsWon === this.fighters.p2.roundsWon
          ? null
          : this.fighters.p1.roundsWon > this.fighters.p2.roundsWon
            ? "p1"
            : "p2";
      this._emit("match-end", {
        winner: this.matchWinner ? this._snapshotFighter(this.fighters[this.matchWinner]) : null,
        winnerSide: this.matchWinner,
        score: {
          p1: this.fighters.p1.roundsWon,
          p2: this.fighters.p2.roundsWon,
        },
      });
    } else {
      this.roundBreakRemaining = this.options.roundBreakDuration;
    }
  }

  _startRound() {
    this.round += 1;
    this.roundTime = this.options.roundDuration;
    this.phase = "fighting";
    this.matchWinner = null;
    this._resetFighterForRound(this.fighters.p1, -this.options.spawnDistance);
    this._resetFighterForRound(this.fighters.p2, this.options.spawnDistance);
    this.ai.decisionIn = 0;
    this.ai.blockIn = 0;
    this._emit("round-start", {
      score: {
        p1: this.fighters.p1.roundsWon,
        p2: this.fighters.p2.roundsWon,
      },
    });
  }

  _resetMatch() {
    this.phase = "idle";
    this.round = 0;
    this.roundTime = this.options.roundDuration;
    this.roundBreakRemaining = 0;
    this.elapsed = 0;
    this.matchWinner = null;
    this.fighters.p1.roundsWon = 0;
    this.fighters.p2.roundsWon = 0;
  }

  _resetFighterForRound(fighter, x) {
    fighter.health = fighter.maxHealth;
    fighter.position.x = x;
    fighter.position.z = 0;
    fighter.velocity.x = 0;
    fighter.velocity.z = 0;
    fighter.facing = fighter.side === "p1" ? 0 : Math.PI;
    fighter.stun = 0;
    fighter.blocking = false;
    fighter.attack = null;
    fighter.cooldowns = fighter.definition.attacks.map(() => 0);
    this._clearInput(fighter);
  }

  _clearInput(fighter) {
    fighter.input.forward = false;
    fighter.input.backward = false;
    fighter.input.left = false;
    fighter.input.right = false;
    fighter.input.block = false;
    fighter.blocking = false;
  }

  _updateFacing(fighter, opponent) {
    const dx = opponent.position.x - fighter.position.x;
    const dz = opponent.position.z - fighter.position.z;
    if (Math.abs(dx) + Math.abs(dz) > EPSILON) fighter.facing = Math.atan2(dz, dx);
  }

  _opponent(fighter) {
    return fighter.side === "p1" ? this.fighters.p2 : this.fighters.p1;
  }

  _separateFighters() {
    const p1 = this.fighters.p1;
    const p2 = this.fighters.p2;
    const dx = p2.position.x - p1.position.x;
    const dz = p2.position.z - p1.position.z;
    const distance = Math.hypot(dx, dz);
    const minimum = p1.definition.collisionRadius + p2.definition.collisionRadius;
    if (distance >= minimum) return;
    const nx = distance > EPSILON ? dx / distance : 1;
    const nz = distance > EPSILON ? dz / distance : 0;
    const correction = (minimum - distance) / 2;
    p1.position.x -= nx * correction;
    p1.position.z -= nz * correction;
    p2.position.x += nx * correction;
    p2.position.z += nz * correction;
  }

  _constrain(fighter) {
    const radius = fighter.definition.collisionRadius;
    const xLimit = Math.max(0, this.options.bounds.x - radius);
    const zLimit = Math.max(0, this.options.bounds.z - radius);
    const beforeX = fighter.position.x;
    const beforeZ = fighter.position.z;
    fighter.position.x = clamp(fighter.position.x, -xLimit, xLimit);
    fighter.position.z = clamp(fighter.position.z, -zLimit, zLimit);
    if (fighter.position.x !== beforeX) fighter.velocity.x = 0;
    if (fighter.position.z !== beforeZ) fighter.velocity.z = 0;
  }

  _snapshotAttack(move, index) {
    return {
      index,
      id: move.id,
      name: move.name,
      damage: move.damage,
      range: move.range,
      cooldown: move.cooldown,
      knockback: move.knockback,
      startup: move.startup,
      active: move.active,
      recovery: move.recovery,
    };
  }

  _snapshotFighter(fighter) {
    return {
      side: fighter.side,
      id: fighter.id,
      name: fighter.name,
      health: fighter.health,
      maxHealth: fighter.maxHealth,
      healthRatio: fighter.health / fighter.maxHealth,
      position: clonePosition(fighter.position),
      velocity: clonePosition(fighter.velocity),
      facing: fighter.facing,
      blocking: fighter.blocking,
      stunned: fighter.stun > 0,
      stunRemaining: fighter.stun,
      roundsWon: fighter.roundsWon,
      cooldowns: [...fighter.cooldowns],
      attack: fighter.attack
        ? {
            ...this._snapshotAttack(fighter.attack.move, fighter.attack.index),
            elapsed: fighter.attack.elapsed,
            connected: fighter.attack.connected,
          }
        : null,
    };
  }

  _emit(type, details) {
    const event = {
      type,
      round: this.round,
      roundTime: this.roundTime,
      ...details,
    };
    if (event.actor?.definition) event.actor = this._snapshotFighter(event.actor);
    if (event.target?.definition) event.target = this._snapshotFighter(event.target);
    this.options.onEvent?.(event);
    for (const listener of this.listeners.get(type) || []) listener(event);
    for (const listener of this.listeners.get("*") || []) listener(event);
  }

  _keyCode(event) {
    if (event.code) return event.code;
    const key = String(event.key || "").toLowerCase();
    const aliases = {
      w: "KeyW",
      s: "KeyS",
      a: "KeyA",
      d: "KeyD",
      j: "KeyJ",
      k: "KeyK",
      l: "KeyL",
      i: "KeyI",
      r: "KeyR",
      " ": "Space",
      spacebar: "Space",
    };
    return aliases[key] || "";
  }

  _isControl(code) {
    return (
      code === DEFAULT_CONTROLS.forward ||
      code === DEFAULT_CONTROLS.backward ||
      code === DEFAULT_CONTROLS.sidestepLeft ||
      code === DEFAULT_CONTROLS.sidestepRight ||
      code === DEFAULT_CONTROLS.block ||
      code === DEFAULT_CONTROLS.restart ||
      DEFAULT_CONTROLS.attacks.includes(code)
    );
  }
}

export default CombatEngine;
