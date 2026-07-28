const DEFAULT_STATS = ["speed", "power", "armour", "reach", "handling"];
const DEFAULT_KEYS = [
  ["Forward / back", "W / S"],
  ["Sidestep", "A / D"],
  ["Attacks 1–4", "J K L I"],
  ["Block", "Space"],
  ["Restart", "R"],
  ["Pause", "Esc"],
  ["Sound", "M"],
];

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const titleCase = (value = "") => String(value)
  .replace(/[_-]+/g, " ")
  .replace(/\b\w/g, (letter) => letter.toUpperCase());
const escapeHTML = (value = "") => String(value).replace(/[&<>"']/g, (character) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
}[character]));

function percentStat(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 50;
  return clamp(numeric <= 10 ? numeric * 10 : numeric, 0, 100);
}

function validColour(value, fallback) {
  if (typeof value !== "string") return fallback;
  if (typeof CSS === "undefined" || !CSS.supports || CSS.supports("color", value)) return value;
  return fallback;
}

function normalizeAttack(attack, index) {
  const source = typeof attack === "string" ? { name: attack } : (attack || {});
  return {
    name: source.name || source.label || `Attack ${index + 1}`,
    damage: Number(source.damage ?? source.power ?? 0),
    range: Number(source.range ?? source.reach ?? 0),
    cooldown: Number(source.cooldown ?? source.cooldownSeconds ?? 0),
    knockback: Number(source.knockback ?? source.force ?? 0),
    input: source.input || source.key || ["J", "K", "U", "I"][index] || "",
  };
}

function normalizeRobot(robot, index) {
  const id = String(robot.id || robot.canonicalId || robot.canonical_id || robot.slug || `fighter-${index + 1}`);
  const palette = robot.palette || robot.colours || robot.colors || {};
  const primary = Array.isArray(palette) ? palette[0] : palette.primary;
  const secondary = Array.isArray(palette) ? palette[1] : palette.secondary;
  const stats = robot.stats || robot.attributes || {};
  const weaponSource = robot.weaponType || robot.weapon_type || robot.weapon || "Custom weapon";
  const weapon = typeof weaponSource === "object"
    ? (weaponSource.name || weaponSource.type || "Custom weapon")
    : weaponSource;
  const attacks = (robot.attacks || robot.moves || []).slice(0, 4).map(normalizeAttack);

  while (attacks.length < 4) attacks.push(normalizeAttack(null, attacks.length));

  return {
    ...robot,
    id,
    name: robot.displayName || robot.display_name || robot.name || titleCase(id),
    weapon: titleCase(weapon),
    archetype: robot.archetype || robot.fighterArchetype || robot.fighter_archetype || "All-rounder",
    portrait: robot.portrait || robot.portraitUrl || robot.portrait_url || robot.image || `/bots/${id}.png`,
    palette: {
      primary: validColour(primary, index % 2 ? "#25d9ff" : "#ff3b2f"),
      secondary: validColour(secondary, "#f6c746"),
    },
    stats: Object.fromEntries(DEFAULT_STATS.map((stat) => [
      stat,
      percentStat(stats[stat] ?? robot[stat]),
    ])),
    attacks,
  };
}

function ensureStylesheet(href) {
  if (!href || document.querySelector(`link[data-bf-styles="${CSS.escape(href)}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  link.dataset.bfStyles = href;
  document.head.append(link);
}

/**
 * Mount the complete fighter-select, match HUD, overlays, and touch controls.
 *
 * Required option:
 *   roster: Array of robot definitions.
 *
 * Useful callbacks:
 *   onStart({ playerId, opponentId, player, opponent })
 *   onPause(paused), onRestart(), onReturnToSelect(), onControl(action, pressed)
 *   onSelectionChange({ phase, playerId, opponentId })
 */
export function createFighterUI(options = {}) {
  if (typeof document === "undefined") {
    throw new Error("createFighterUI requires a browser document.");
  }

  const roster = (options.roster || []).map(normalizeRobot).slice(0, 6);
  if (roster.length < 2) {
    throw new Error("createFighterUI requires at least two roster entries.");
  }

  ensureStylesheet(options.stylesheetUrl === false
    ? null
    : (options.stylesheetUrl || "/css/arena-fighter.css"));

  let host = options.root;
  if (typeof host === "string") host = document.querySelector(host);
  let ownsHost = false;
  if (!host) {
    host = document.createElement("div");
    host.id = "fighter-ui-root";
    document.body.append(host);
    ownsHost = true;
  }

  host.classList.add("bf-ui");
  host.innerHTML = `
    <section class="bf-select" aria-labelledby="bf-select-title">
      <div class="bf-select__backdrop" aria-hidden="true"></div>
      <header class="bf-title-lockup">
        <p>BOT BEEF // WORLD COMBAT GRID</p>
        <h1 id="bf-select-title">SELECT <em>YOUR MACHINE</em></h1>
        <span>Six robots. One arena. No safe angle.</span>
      </header>

      <div class="bf-versus-preview">
        <button class="bf-pick-panel bf-pick-panel--p1 is-active" type="button" data-phase="player">
          <span class="bf-kicker">PLAYER 1</span>
          <strong data-pick-name="player">—</strong>
          <small data-pick-state="player">SELECTING</small>
        </button>
        <div class="bf-versus-preview__mark" aria-hidden="true">VS</div>
        <button class="bf-pick-panel bf-pick-panel--cpu" type="button" data-phase="opponent">
          <span class="bf-kicker">CPU RIVAL</span>
          <strong data-pick-name="opponent">—</strong>
          <small data-pick-state="opponent">STANDBY</small>
        </button>
      </div>

      <div class="bf-select__body">
        <div class="bf-roster" role="grid" aria-label="Robot roster"></div>
        <aside class="bf-dossier" aria-live="polite"></aside>
      </div>

      <footer class="bf-select__footer">
        <p class="bf-select__prompt" data-select-prompt>
          <span>◀ ▲ ▼ ▶</span> CHOOSE ROBOT <span>ENTER</span> LOCK IN
        </p>
        <div class="bf-select__actions">
          <button class="bf-button bf-button--ghost" type="button" data-random>RANDOM RIVAL</button>
          <button class="bf-button bf-button--confirm" type="button" data-confirm>LOCK P1</button>
        </div>
      </footer>
    </section>

    <section class="bf-match-ui" aria-hidden="true">
      <div class="bf-hud">
        <div class="bf-hud-fighter bf-hud-fighter--p1">
          <div class="bf-hud-fighter__identity">
            <span class="bf-hud-fighter__tag">P1</span>
            <strong data-hud-name="player">PLAYER</strong>
            <span class="bf-round-pips" data-round-pips="player" aria-label="Player rounds"></span>
          </div>
          <div class="bf-health-track" role="meter" aria-label="Player health" aria-valuemin="0" aria-valuemax="100">
            <i data-health-lag="player"></i><b data-health="player"></b>
          </div>
          <div class="bf-status bf-status--p1" data-status="player"></div>
        </div>
        <div class="bf-timer-block">
          <span>ROUND <b data-round>1</b></span>
          <strong data-timer>60</strong>
          <small data-match-state>FIGHT</small>
        </div>
        <div class="bf-hud-fighter bf-hud-fighter--cpu">
          <div class="bf-hud-fighter__identity">
            <span class="bf-round-pips" data-round-pips="opponent" aria-label="CPU rounds"></span>
            <strong data-hud-name="opponent">CPU</strong>
            <span class="bf-hud-fighter__tag">CPU</span>
          </div>
          <div class="bf-health-track" role="meter" aria-label="CPU health" aria-valuemin="0" aria-valuemax="100">
            <i data-health-lag="opponent"></i><b data-health="opponent"></b>
          </div>
          <div class="bf-status bf-status--cpu" data-status="opponent"></div>
        </div>
      </div>

      <div class="bf-combo bf-combo--p1" data-combo="player"></div>
      <div class="bf-combo bf-combo--cpu" data-combo="opponent"></div>

      <div class="bf-controls-legend" data-controls-legend>
        <button type="button" data-toggle-controls aria-expanded="true">CONTROLS</button>
        <div class="bf-controls-legend__grid"></div>
      </div>

      <div class="bf-touch-controls" aria-label="Touch combat controls">
        <div class="bf-touch-dpad">
          <button type="button" data-control="forward" aria-label="Move forward">▲</button>
          <button type="button" data-control="sidestepLeft" aria-label="Sidestep left">◀</button>
          <button type="button" data-control="backward" aria-label="Move backward">▼</button>
          <button type="button" data-control="sidestepRight" aria-label="Sidestep right">▶</button>
        </div>
        <div class="bf-touch-actions">
          <button type="button" data-control="attack0" aria-label="Attack one">J</button>
          <button type="button" data-control="attack1" aria-label="Attack two">K</button>
          <button type="button" data-control="attack2" aria-label="Attack three">L</button>
          <button type="button" data-control="attack3" aria-label="Attack four">I</button>
          <button type="button" data-control="block" class="bf-touch-actions__block" aria-label="Block">BLOCK</button>
          <button type="button" data-control="restart" class="bf-touch-actions__restart" aria-label="Restart match">R</button>
        </div>
      </div>
    </section>

    <div class="bf-announcer" aria-hidden="true">
      <p data-overlay-kicker></p>
      <strong data-overlay-title></strong>
      <span data-overlay-subtitle></span>
    </div>

    <div class="bf-pause" aria-hidden="true">
      <div class="bf-pause__panel">
        <p>MATCH INTERRUPT</p>
        <h2>PAUSED</h2>
        <button class="bf-button bf-button--confirm" type="button" data-resume>RESUME</button>
        <button class="bf-button bf-button--ghost" type="button" data-restart>RESTART ROUND</button>
        <button class="bf-button bf-button--ghost" type="button" data-return>FIGHTER SELECT</button>
      </div>
    </div>
    <div class="bf-sr-only" aria-live="assertive" data-live-region></div>
  `;

  const $ = (selector) => host.querySelector(selector);
  const $$ = (selector) => [...host.querySelectorAll(selector)];
  const listeners = [];
  const timeouts = new Set();
  const healthLagTimeouts = { player: null, opponent: null };
  let playerIndex = clamp(Number(options.initialPlayer) || 0, 0, roster.length - 1);
  let opponentIndex = clamp(Number(options.initialOpponent) || 1, 0, roster.length - 1);
  if (opponentIndex === playerIndex) opponentIndex = (playerIndex + 1) % roster.length;
  let cursorIndex = playerIndex;
  let phase = "player";
  let paused = false;
  let inMatch = false;
  let overlayTimeout = null;

  const listen = (target, event, handler, settings) => {
    target.addEventListener(event, handler, settings);
    listeners.push(() => target.removeEventListener(event, handler, settings));
  };

  const later = (handler, delay) => {
    const id = setTimeout(() => {
      timeouts.delete(id);
      handler();
    }, delay);
    timeouts.add(id);
    return id;
  };

  function robotAt(index) {
    return roster[clamp(index, 0, roster.length - 1)];
  }

  function currentRobot() {
    return robotAt(cursorIndex);
  }

  function notifySelection() {
    options.onSelectionChange?.({
      phase,
      playerId: robotAt(playerIndex).id,
      opponentId: robotAt(opponentIndex).id,
    });
  }

  function renderCards() {
    $(".bf-roster").innerHTML = roster.map((robot, index) => `
      <button
        class="bf-fighter-card"
        type="button"
        role="gridcell"
        data-fighter-index="${index}"
        aria-label="${escapeHTML(robot.name)}, ${escapeHTML(robot.archetype)}"
        style="--fighter:${escapeHTML(robot.palette.primary)};--fighter-alt:${escapeHTML(robot.palette.secondary)}"
      >
        <span class="bf-fighter-card__number">${String(index + 1).padStart(2, "0")}</span>
        <span class="bf-fighter-card__portrait">
          <img src="${escapeHTML(robot.portrait)}" alt="" draggable="false">
        </span>
        <span class="bf-fighter-card__name">${escapeHTML(robot.name)}</span>
        <span class="bf-fighter-card__class">${escapeHTML(robot.archetype)}</span>
        <span class="bf-fighter-card__badge bf-fighter-card__badge--p1">P1</span>
        <span class="bf-fighter-card__badge bf-fighter-card__badge--cpu">CPU</span>
      </button>
    `).join("");

    $$(".bf-fighter-card img").forEach((image) => {
      listen(image, "error", () => {
        image.hidden = true;
        image.closest(".bf-fighter-card__portrait")?.classList.add("is-missing");
      }, { once: true });
    });

    $$(".bf-fighter-card").forEach((card) => {
      listen(card, "click", () => {
        cursorIndex = Number(card.dataset.fighterIndex);
        assignCursorToPhase();
        renderSelection();
      });
      listen(card, "focus", () => {
        cursorIndex = Number(card.dataset.fighterIndex);
        renderSelection();
      });
    });
  }

  function attackMarkup(attack) {
    const cooldown = attack.cooldown ? `${attack.cooldown.toFixed(1)}s CD` : "READY";
    return `
      <li>
        <kbd>${escapeHTML(attack.input)}</kbd>
        <span><strong>${escapeHTML(attack.name)}</strong><small>${attack.damage} DMG · ${attack.range} RNG · ${cooldown}</small></span>
      </li>
    `;
  }

  function renderDossier(robot) {
    $(".bf-dossier").style.setProperty("--fighter", robot.palette.primary);
    $(".bf-dossier").style.setProperty("--fighter-alt", robot.palette.secondary);
    $(".bf-dossier").innerHTML = `
      <div class="bf-dossier__heading">
        <p>${escapeHTML(robot.archetype)}</p>
        <h2>${escapeHTML(robot.name)}</h2>
        <span>${escapeHTML(robot.weapon)}</span>
      </div>
      <div class="bf-stat-list">
        ${DEFAULT_STATS.map((stat) => `
          <div class="bf-stat">
            <span>${stat.toUpperCase()}</span>
            <i><b style="width:${robot.stats[stat]}%"></b></i>
            <em>${Math.round(robot.stats[stat])}</em>
          </div>
        `).join("")}
      </div>
      <div class="bf-move-list">
        <p>COMMAND ATTACKS</p>
        <ol>${robot.attacks.map(attackMarkup).join("")}</ol>
      </div>
    `;
  }

  function assignCursorToPhase() {
    if (phase === "player") {
      playerIndex = cursorIndex;
      if (opponentIndex === playerIndex) opponentIndex = (playerIndex + 1) % roster.length;
    } else {
      opponentIndex = cursorIndex;
      if (opponentIndex === playerIndex) {
        opponentIndex = (opponentIndex + 1) % roster.length;
        cursorIndex = opponentIndex;
      }
    }
    notifySelection();
  }

  function setPhase(nextPhase) {
    phase = nextPhase === "opponent" ? "opponent" : "player";
    cursorIndex = phase === "player" ? playerIndex : opponentIndex;
    renderSelection();
    $(".bf-fighter-card[data-fighter-index='" + cursorIndex + "']")?.focus({ preventScroll: true });
    notifySelection();
  }

  function renderSelection() {
    const player = robotAt(playerIndex);
    const opponent = robotAt(opponentIndex);
    const cards = $$(".bf-fighter-card");
    cards.forEach((card, index) => {
      card.classList.toggle("is-cursor", index === cursorIndex);
      card.classList.toggle("is-p1", index === playerIndex);
      card.classList.toggle("is-cpu", index === opponentIndex);
      card.setAttribute("aria-pressed", String(index === (phase === "player" ? playerIndex : opponentIndex)));
      card.tabIndex = index === cursorIndex ? 0 : -1;
    });
    $$("[data-phase]").forEach((panel) => panel.classList.toggle("is-active", panel.dataset.phase === phase));
    $('[data-pick-name="player"]').textContent = player.name;
    $('[data-pick-name="opponent"]').textContent = opponent.name;
    $('[data-pick-state="player"]').textContent = phase === "player" ? "SELECTING" : "LOCKED";
    $('[data-pick-state="opponent"]').textContent = phase === "opponent" ? "SELECTING" : "STANDBY";
    $("[data-confirm]").textContent = phase === "player" ? "LOCK P1" : "START FIGHT";
    $("[data-select-prompt]").innerHTML = phase === "player"
      ? "<span>◀ ▲ ▼ ▶</span> CHOOSE P1 <span>ENTER</span> LOCK IN"
      : "<span>◀ ▲ ▼ ▶</span> CHOOSE CPU <span>ENTER</span> START";
    renderDossier(currentRobot());
  }

  function confirmSelection() {
    assignCursorToPhase();
    if (phase === "player") {
      setPhase("opponent");
      return;
    }
    startMatch();
  }

  function startMatch() {
    const player = robotAt(playerIndex);
    const opponent = robotAt(opponentIndex);
    inMatch = true;
    paused = false;
    $(".bf-select").hidden = true;
    $(".bf-match-ui").setAttribute("aria-hidden", "false");
    $(".bf-match-ui").classList.add("is-visible");
    $('[data-hud-name="player"]').textContent = player.name;
    $('[data-hud-name="opponent"]').textContent = opponent.name;
    host.style.setProperty("--p1", player.palette.primary);
    host.style.setProperty("--cpu", opponent.palette.primary);
    updateHUD({ playerHealth: 100, opponentHealth: 100, timer: 60, round: 1, playerRounds: 0, opponentRounds: 0 });
    setStatus("player");
    setStatus("opponent");
    setCombo("player");
    setCombo("opponent");
    hideOverlay();
    options.onStart?.({
      playerId: player.id,
      opponentId: opponent.id,
      player,
      opponent,
    });
  }

  function showSelect() {
    inMatch = false;
    paused = false;
    $(".bf-pause").classList.remove("is-visible");
    $(".bf-pause").setAttribute("aria-hidden", "true");
    $(".bf-match-ui").classList.remove("is-visible");
    $(".bf-match-ui").setAttribute("aria-hidden", "true");
    $(".bf-select").hidden = false;
    setPhase("player");
  }

  function updateHealth(side, value, immediate = false) {
    if (!["player", "opponent"].includes(side)) return;
    const amount = clamp(Number(value) || 0, 0, 100);
    const health = $(`[data-health="${side}"]`);
    const lag = $(`[data-health-lag="${side}"]`);
    const meter = health.closest('[role="meter"]');
    health.style.width = `${amount}%`;
    meter.setAttribute("aria-valuenow", String(Math.round(amount)));
    meter.classList.toggle("is-critical", amount <= 20);
    if (healthLagTimeouts[side]) clearTimeout(healthLagTimeouts[side]);
    if (immediate) {
      lag.style.width = `${amount}%`;
    } else {
      healthLagTimeouts[side] = later(() => {
        lag.style.width = `${amount}%`;
        healthLagTimeouts[side] = null;
      }, 360);
    }
  }

  function renderPips(side, won = 0, needed = 2) {
    const container = $(`[data-round-pips="${side}"]`);
    container.innerHTML = Array.from({ length: needed }, (_, index) =>
      `<i class="${index < won ? "is-won" : ""}"></i>`).join("");
    container.setAttribute("aria-label", `${side === "player" ? "Player" : "CPU"} rounds: ${won} of ${needed}`);
  }

  function updateHUD(state = {}) {
    if (state.playerHealth != null) updateHealth("player", state.playerHealth, state.immediate);
    if (state.opponentHealth != null) updateHealth("opponent", state.opponentHealth, state.immediate);
    if (state.timer != null) {
      const timer = clamp(Math.ceil(Number(state.timer) || 0), 0, 99);
      $("[data-timer]").textContent = String(timer).padStart(2, "0");
      $(".bf-timer-block").classList.toggle("is-critical", timer <= 10);
    }
    if (state.round != null) $("[data-round]").textContent = state.round;
    if (state.matchState != null) $("[data-match-state]").textContent = String(state.matchState);
    if (state.playerRounds != null) renderPips("player", state.playerRounds, state.roundsToWin || 2);
    if (state.opponentRounds != null) renderPips("opponent", state.opponentRounds, state.roundsToWin || 2);
  }

  function showOverlay(kind, title, subtitle = "", settings = {}) {
    const overlay = $(".bf-announcer");
    if (overlayTimeout) clearTimeout(overlayTimeout);
    overlay.className = `bf-announcer is-visible bf-announcer--${kind || "notice"}`;
    $("[data-overlay-kicker]").textContent = settings.kicker || "";
    $("[data-overlay-title]").textContent = title;
    $("[data-overlay-subtitle]").textContent = subtitle;
    overlay.setAttribute("aria-hidden", "false");
    $("[data-live-region]").textContent = [settings.kicker, title, subtitle].filter(Boolean).join(". ");
    if (settings.duration !== 0) {
      overlayTimeout = later(() => hideOverlay(), settings.duration || 1300);
    }
  }

  function hideOverlay() {
    $(".bf-announcer").classList.remove("is-visible");
    $(".bf-announcer").setAttribute("aria-hidden", "true");
    overlayTimeout = null;
  }

  function showCountdown(value) {
    const text = Number(value) > 0 ? String(value) : "FIGHT!";
    showOverlay("countdown", text, "", { duration: Number(value) > 0 ? 850 : 1050, kicker: "GET READY" });
  }

  function showRound(round) {
    showOverlay("round", `ROUND ${round}`, "STEEL YOURSELF", { duration: 1250, kicker: "NEXT BOUT" });
  }

  function showKO(winnerName = "") {
    showOverlay("ko", "K.O.", winnerName ? `${winnerName} TAKES THE ROUND` : "", {
      duration: 2200,
      kicker: "DECISIVE IMPACT",
    });
  }

  function showMatchResult(winnerName = "") {
    showOverlay("result", winnerName || "MATCH COMPLETE", winnerName ? "WINS THE MATCH" : "", {
      duration: 0,
      kicker: "FINAL RESULT",
    });
  }

  function setCombo(side, count = 0, label = "HIT COMBO") {
    const combo = $(`[data-combo="${side}"]`);
    if (!combo) return;
    combo.innerHTML = count > 1 ? `<strong>${Math.floor(count)}</strong><span>${escapeHTML(label)}</span>` : "";
    combo.classList.toggle("is-visible", count > 1);
  }

  function setStatus(side, text = "", tone = "neutral", duration = 0) {
    const status = $(`[data-status="${side}"]`);
    if (!status) return;
    status.textContent = text;
    status.dataset.tone = tone;
    status.classList.toggle("is-visible", Boolean(text));
    if (duration && text) {
      later(() => {
        if (status.textContent === text) {
          status.textContent = "";
          status.classList.remove("is-visible");
        }
      }, duration);
    }
  }

  function setPaused(nextPaused, emit = true) {
    if (!inMatch) return;
    paused = Boolean(nextPaused);
    $(".bf-pause").classList.toggle("is-visible", paused);
    $(".bf-pause").setAttribute("aria-hidden", String(!paused));
    if (paused) $("[data-resume]").focus();
    if (emit) options.onPause?.(paused);
  }

  function returnToSelect() {
    showSelect();
    options.onReturnToSelect?.();
  }

  function randomOpponent() {
    const choices = roster.map((_, index) => index).filter((index) => index !== playerIndex);
    opponentIndex = choices[Math.floor(Math.random() * choices.length)];
    if (phase === "opponent") cursorIndex = opponentIndex;
    renderSelection();
    notifySelection();
  }

  function moveCursor(delta) {
    cursorIndex = (cursorIndex + delta + roster.length) % roster.length;
    assignCursorToPhase();
    renderSelection();
    $(`.bf-fighter-card[data-fighter-index="${cursorIndex}"]`)?.focus({ preventScroll: true });
  }

  function onKeydown(event) {
    if (event.key === "Escape" && inMatch) {
      event.preventDefault();
      setPaused(!paused);
      return;
    }
    if (inMatch || $(".bf-select").hidden) return;
    const columns = matchMedia("(max-width: 760px)").matches ? 2 : 3;
    const moves = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -columns,
      ArrowDown: columns,
    };
    if (event.key in moves) {
      event.preventDefault();
      moveCursor(moves[event.key]);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      confirmSelection();
    } else if (event.key === "Backspace" && phase === "opponent") {
      event.preventDefault();
      setPhase("player");
    }
  }

  function buildControlLegend() {
    const entries = options.controlLegend || DEFAULT_KEYS;
    $(".bf-controls-legend__grid").innerHTML = entries.map(([label, keys]) =>
      `<span>${escapeHTML(label)} <kbd>${escapeHTML(keys)}</kbd></span>`).join("");
  }

  renderCards();
  buildControlLegend();
  renderSelection();

  $$("[data-phase]").forEach((panel) => listen(panel, "click", () => setPhase(panel.dataset.phase)));
  listen($("[data-confirm]"), "click", confirmSelection);
  listen($("[data-random]"), "click", randomOpponent);
  listen($("[data-toggle-controls]"), "click", (event) => {
    const legend = $(".bf-controls-legend");
    const collapsed = legend.classList.toggle("is-collapsed");
    event.currentTarget.setAttribute("aria-expanded", String(!collapsed));
  });
  listen($("[data-resume]"), "click", () => setPaused(false));
  listen($("[data-restart]"), "click", () => {
    setPaused(false);
    options.onRestart?.();
  });
  listen($("[data-return]"), "click", returnToSelect);
  listen(document, "keydown", onKeydown);

  $$("[data-control]").forEach((button) => {
    const action = button.dataset.control;
    const release = () => {
      button.classList.remove("is-pressed");
      options.onControl?.(action, false);
    };
    listen(button, "pointerdown", (event) => {
      event.preventDefault();
      button.setPointerCapture?.(event.pointerId);
      button.classList.add("is-pressed");
      options.onControl?.(action, true);
    });
    listen(button, "pointerup", release);
    listen(button, "pointercancel", release);
    listen(button, "lostpointercapture", release);
  });

  return {
    root: host,
    roster: [...roster],
    startMatch,
    showSelect,
    getSelection: () => ({
      playerId: robotAt(playerIndex).id,
      opponentId: robotAt(opponentIndex).id,
    }),
    setSelection({ playerId, opponentId } = {}) {
      const nextPlayer = roster.findIndex((robot) => robot.id === playerId);
      const nextOpponent = roster.findIndex((robot) => robot.id === opponentId);
      if (nextPlayer >= 0) playerIndex = nextPlayer;
      if (nextOpponent >= 0 && nextOpponent !== playerIndex) opponentIndex = nextOpponent;
      if (opponentIndex === playerIndex) opponentIndex = (playerIndex + 1) % roster.length;
      cursorIndex = phase === "player" ? playerIndex : opponentIndex;
      renderSelection();
      notifySelection();
    },
    updateHUD,
    updateHealth,
    showOverlay,
    hideOverlay,
    showCountdown,
    showRound,
    showKO,
    showMatchResult,
    setCombo,
    setStatus,
    setPaused,
    returnToSelect,
    destroy() {
      listeners.splice(0).forEach((remove) => remove());
      timeouts.forEach(clearTimeout);
      timeouts.clear();
      if (ownsHost) host.remove();
      else {
        host.innerHTML = "";
        host.classList.remove("bf-ui");
      }
    },
  };
}

export default createFighterUI;
