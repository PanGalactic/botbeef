import * as THREE from "../vendor/three.module.js";
import { RapAudioController, selectBeatForMatchup } from "./rap-audio.js";

const $ = (selector) => document.querySelector(selector);
const INLINE_SOURCE_LIMIT = 3;
const FACT_KIND_PRIORITY = Object.freeze({
  matchup: 0,
  rivalry: 0,
  comparison: 0,
  overrated: 1,
  underrated: 1,
  record: 2,
  ko: 3,
  sos: 4,
  hype: 5,
  weapon: 6,
  quote: 9,
});

export function battleIsPlayable(battle) {
  if (!battle || typeof battle !== "object" || !battle.a || !battle.b || battle.a === battle.b) {
    return false;
  }
  if ("playable" in battle && battle.playable !== true) return false;
  if ("ready" in battle && battle.ready !== true) return false;
  if (battle.stale === true || battle.available === false) return false;
  if (battle.validation && battle.validation.valid === false) return false;
  return true;
}

export function safeSourceUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

export function indexedMatchups(battles, allowedSlugs) {
  const allowed = new Set(allowedSlugs);
  const pairs = [];
  const seen = new Set();
  for (const battle of Array.isArray(battles) ? battles : []) {
    if (!battleIsPlayable(battle) || !allowed.has(battle.a) || !allowed.has(battle.b)) continue;
    const key = [battle.a, battle.b].sort().join("__");
    if (seen.has(key)) continue;
    seen.add(key);
    pairs.push({ ...battle, key });
  }
  return pairs;
}

export function selectPreviewFact(facts) {
  const candidates = (Array.isArray(facts) ? facts : [])
    .filter((fact) => {
      if (!fact || typeof fact.text !== "string" || !fact.text.trim()) return false;
      const urls = [fact.source_url, ...(Array.isArray(fact.source_urls) ? fact.source_urls : [])];
      return urls.some((url) => safeSourceUrl(url));
    })
    .map((fact, index) => ({
      fact,
      index,
      priority: FACT_KIND_PRIORITY[fact.kind] ?? 8,
      concise: fact.text.trim().length <= 160 ? 0 : 1,
    }))
    .sort((left, right) =>
      left.priority - right.priority
      || left.concise - right.concise
      || left.fact.text.length - right.fact.text.length
      || left.index - right.index
    );
  return candidates[0]?.fact || null;
}

function metric(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(1) : "—";
}

export function formatScoreComparison(rowA, rowB) {
  if (!rowA || !rowB) return "Record comparison unavailable";
  return `${rowA.name || rowA.slug}: ${metric(rowA.hype)} hype / ${metric(rowA.performance)} record · `
    + `${rowB.name || rowB.slug}: ${metric(rowB.hype)} hype / ${metric(rowB.performance)} record`;
}

export function formatAudioState(matchup) {
  const audio = matchup?.audio;
  if (audio?.complete === true) {
    const present = Number.isFinite(Number(audio.clips_present)) ? Number(audio.clips_present) : null;
    const expected = Number.isFinite(Number(audio.clips_expected)) ? Number(audio.clips_expected) : null;
    return present === null || expected === null
      ? "Voice + intro verified"
      : `${present}/${expected} bars + intro verified`;
  }
  if (audio?.manifest === true) return "Audio incomplete · caption fallback";
  return "Caption fallback only";
}

export function formatBeat(beat) {
  if (!beat) return "Instrumental unavailable";
  const style = beat.style || beat.title || beat.slug || "Instrumental";
  const bpm = Number(beat.bpm);
  return Number.isFinite(bpm) ? `${style} · ${bpm} BPM` : style;
}

export function resolveDisplayName(slug, { identity, battleName, tableName } = {}) {
  return identity?.name
    || battleName
    || tableName
    || String(slug || "").replaceAll("-", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function resolveSpritePath(slug, identity, standardSlugs, tekkenSlugs) {
  const standard = standardSlugs instanceof Set ? standardSlugs : new Set(standardSlugs || []);
  const tekken = tekkenSlugs instanceof Set ? tekkenSlugs : new Set(tekkenSlugs || []);
  const expectedStandard = `/bots/${slug}.png`;
  const expectedTekken = `/bots_tekken/${slug}.png`;
  if (standard.has(slug) && identity?.assets?.standard === expectedStandard) {
    return identity.assets.standard;
  }
  if (tekken.has(slug) && identity?.assets?.tekken === expectedTekken) {
    return identity.assets.tekken;
  }
  if (standard.has(slug)) return expectedStandard;
  if (tekken.has(slug)) return expectedTekken;
  return null;
}

let table = null;
let battle = null;
let previewBattle = null;
let previewRequestId = 0;
let beats = [];
let matchups = [];
let identities = new Map();
let spritePaths = new Map();
let scene;
let camera;
let renderer;
let clock;
let textureLoader;
let fighters = {};
let speaking = null;
let cameraTarget;
let cameraPosition;
let audioController;
let graphicsMode = "3d";
let webglAvailable = false;
const reducedMotion = typeof matchMedia === "function"
  && matchMedia("(prefers-reduced-motion: reduce)").matches;

function setStatus(message, error = false) {
  const element = $("#availability");
  element.textContent = message;
  element.classList.toggle("error", error);
}

function setMeter(selector, value) {
  const numeric = Number(value);
  $(selector).style.width = `${Math.max(4, Math.min(100, Number.isFinite(numeric) ? numeric : 4))}%`;
}

function updateGraphicsToggle() {
  const toggle = $("#graphics-toggle");
  toggle.setAttribute("aria-pressed", String(graphicsMode === "2d"));
  toggle.textContent = graphicsMode === "2d"
    ? (webglAvailable ? "Use 3D graphics" : "Reduced graphics active")
    : "Use reduced graphics";
  toggle.disabled = graphicsMode === "2d" && !webglAvailable;
}

function activateFallback(message, { permanent = false } = {}) {
  graphicsMode = "2d";
  if (permanent) webglAvailable = false;
  $("#scene").hidden = true;
  $("#fallback-scene").hidden = false;
  const notice = $("#graphics-notice");
  notice.textContent = message || "Reduced graphics mode is active.";
  notice.hidden = false;
  updateGraphicsToggle();
}

function activateWebGL() {
  if (!webglAvailable) return;
  graphicsMode = "3d";
  $("#scene").hidden = false;
  $("#fallback-scene").hidden = true;
  $("#graphics-notice").hidden = true;
  updateGraphicsToggle();
}

function initScene() {
  scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x05060a, 0.031);
  camera = new THREE.PerspectiveCamera(46, innerWidth / innerHeight, 0.1, 260);
  camera.position.set(0, 3.1, 15.5);
  cameraTarget = new THREE.Vector3(0, 2.4, 0);
  cameraPosition = new THREE.Vector3(0, 3.1, 15.5);

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.domElement.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    activateFallback(
      "3D graphics stopped responding. The battle is continuing in reduced graphics mode.",
      { permanent: true },
    );
  });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(innerWidth, innerHeight);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.18;
  $("#scene").append(renderer.domElement);

  textureLoader = new THREE.TextureLoader();
  const floorTexture = textureLoader.load("/arena/floor.png", (loaded) => {
    loaded.wrapS = loaded.wrapT = THREE.RepeatWrapping;
    loaded.repeat.set(7, 7);
  });
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(150, 150),
    new THREE.MeshStandardMaterial({
      map: floorTexture,
      roughness: 0.72,
      metalness: 0.45,
      color: 0x8892a4,
    }),
  );
  floor.rotation.x = -Math.PI / 2;
  scene.add(floor);

  textureLoader.load("/arena/arena.png", (loaded) => {
    loaded.colorSpace = THREE.SRGBColorSpace;
    loaded.wrapS = THREE.RepeatWrapping;
    loaded.repeat.x = 2;
    const backdrop = new THREE.Mesh(
      new THREE.CylinderGeometry(40, 40, 34, 64, 1, true),
      new THREE.MeshBasicMaterial({
        map: loaded,
        side: THREE.BackSide,
        color: 0x7f8ba6,
        fog: false,
      }),
    );
    backdrop.position.y = 15;
    scene.add(backdrop);
  });

  scene.add(new THREE.AmbientLight(0x35406a, 1.5));
  const key = new THREE.SpotLight(0xffffff, 320, 60, 0.44, 0.55, 1.6);
  key.position.set(0, 21, 9);
  scene.add(key);
  const rimA = new THREE.PointLight(0xff6b35, 190, 40, 2);
  rimA.position.set(-9, 4.2, 3);
  scene.add(rimA);
  const rimB = new THREE.PointLight(0x35c8ff, 190, 40, 2);
  rimB.position.set(9, 4.2, 3);
  scene.add(rimB);
  for (let i = -2; i <= 2; i += 1) {
    const light = new THREE.PointLight(0x6f7fbf, 46, 46, 2);
    light.position.set(i * 11, 15, -6);
    scene.add(light);
  }

  clock = new THREE.Clock();
  webglAvailable = true;
  addEventListener("resize", () => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });
  animate();
}

function initVisuals() {
  try {
    initScene();
    activateWebGL();
  } catch {
    activateFallback(
      "3D graphics are unavailable on this device. The full rap, subtitles, and sources remain available.",
      { permanent: true },
    );
  }
}

function makeFighter(slug, side) {
  const group = new THREE.Group();
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(6.2, 4.2),
    new THREE.MeshBasicMaterial({
      transparent: true,
      opacity: 0,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  plane.position.y = 2.1;
  group.add(plane);

  const addPlaceholder = () => {
    plane.material.opacity = 0;
    const box = new THREE.Mesh(
      new THREE.BoxGeometry(3.1, 2, 2.4),
      new THREE.MeshStandardMaterial({
        color: side < 0 ? 0x8a4a24 : 0x1f6f92,
        emissive: side < 0 ? 0x2a1206 : 0x06202a,
        emissiveIntensity: 0.9,
        roughness: 0.55,
        metalness: 0.1,
      }),
    );
    box.position.y = 1;
    group.add(box);
  };

  const spritePath = spritePaths.get(slug);
  if (spritePath) {
    textureLoader.load(spritePath, (loaded) => {
      loaded.colorSpace = THREE.SRGBColorSpace;
      const aspect = loaded.image.width / loaded.image.height;
      const boxWidth = 6.4;
      const boxHeight = 4;
      const width = aspect > boxWidth / boxHeight ? boxWidth : boxHeight * aspect;
      const height = aspect > boxWidth / boxHeight ? boxWidth / aspect : boxHeight;
      plane.geometry.dispose();
      plane.geometry = new THREE.PlaneGeometry(width, height);
      plane.position.y = height / 2 * 0.98;
      plane.material.map = loaded;
      plane.material.opacity = 1;
      plane.material.needsUpdate = true;
    }, undefined, addPlaceholder);
  } else {
    addPlaceholder();
  }

  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(2.5, 32),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.5 }),
  );
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = 0.02;
  group.add(shadow);
  group.position.set(side * 5, 0, 0);
  group.rotation.y = side * -0.19;
  scene.add(group);
  return group;
}

function animate() {
  requestAnimationFrame(animate);
  if (!renderer || graphicsMode !== "3d") return;
  const elapsed = clock.getElapsedTime();
  for (const [slug, group] of Object.entries(fighters)) {
    const active = speaking === slug;
    group.position.y = (reducedMotion ? 0 : Math.sin(elapsed * 1.7 + group.position.x) * 0.09)
      + (active ? 0.3 : 0);
    const targetX = Math.sign(group.position.x) * (active ? 4.1 : 5);
    group.position.x += (targetX - group.position.x) * (reducedMotion ? 1 : 0.05);
    const scale = reducedMotion
      ? (active ? 1.1 : 1)
      : group.scale.x + ((active ? 1.1 : 1) - group.scale.x) * 0.07;
    group.scale.setScalar(scale);
  }
  if (reducedMotion) camera.position.copy(cameraPosition);
  else camera.position.lerp(cameraPosition, 0.035);
  camera.lookAt(cameraTarget);
  if (!reducedMotion) camera.position.y += Math.sin(elapsed * 0.7) * 0.006;
  renderer.render(scene, camera);
}

function wideShot() {
  speaking = null;
  $("#fallback-a").classList.remove("active");
  $("#fallback-b").classList.remove("active");
  cameraPosition?.set(0, 3.6, 15);
  cameraTarget?.set(0, 2.5, 0);
}

function cutTo(slug) {
  speaking = slug;
  $("#fallback-a").classList.toggle("active", slug === $("#select-a").value);
  $("#fallback-b").classList.toggle("active", slug === $("#select-b").value);
  if (graphicsMode === "2d") return;
  const fighter = fighters[slug];
  if (!fighter) {
    wideShot();
    return;
  }
  const side = Math.sign(fighter.position.x);
  cameraPosition.set(side * 6.4, 3, 8.4);
  cameraTarget.set(side * 3.2, 2.3, 0);
}

function appendCitation(bar) {
  const citation = $("#citation");
  citation.replaceChildren();
  const factId = document.createElement("strong");
  factId.textContent = bar.fact_id || "SOURCE";
  citation.append(factId, document.createTextNode(` ${bar.fact || "Source details unavailable."}`));
  const sources = [...new Set(
    [bar.source_url, ...(Array.isArray(bar.source_urls) ? bar.source_urls : [])]
      .map(safeSourceUrl)
      .filter(Boolean),
  )];
  const inlineSources = sources.slice(0, INLINE_SOURCE_LIMIT);
  inlineSources.forEach((source, index) => {
    citation.append(document.createTextNode(" · "));
    const link = document.createElement("a");
    link.href = source;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = index === 0 ? "view source" : `source ${index + 1}`;
    citation.append(link);
  });

  const remainingSources = sources.slice(INLINE_SOURCE_LIMIT);
  if (remainingSources.length) {
    citation.append(document.createTextNode(" · "));
    const disclosure = document.createElement("details");
    disclosure.className = "citation-more";
    const summary = document.createElement("summary");
    summary.textContent = `+${remainingSources.length} more source${remainingSources.length === 1 ? "" : "s"}`;
    disclosure.append(summary);
    const list = document.createElement("ul");
    remainingSources.forEach((source, index) => {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = source;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = `source ${INLINE_SOURCE_LIMIT + index + 1}`;
      item.append(link);
      list.append(item);
    });
    disclosure.append(list);
    citation.append(disclosure);
  }
}

function showAudioStep(step) {
  if (step.kind === "intro" || step.index < 0) {
    wideShot();
    $("#subtitle").classList.remove("on");
    $("#round").textContent = "INTRO";
    return;
  }
  const bar = battle?.bars?.[step.index];
  if (!bar) return;
  const isA = bar.bot === $("#select-a").value;
  cutTo(bar.bot);
  $("#round").textContent = `ROUND ${step.index < Math.ceil(battle.bars.length / 2) ? 1 : 2}`;
  $("#speaker").className = `speaker ${isA ? "a" : "b"}`;
  $("#speaker").textContent = battle.names?.[bar.bot] || bar.bot;
  $("#bar").textContent = bar.text || "";
  appendCitation(bar);
  $("#subtitle").classList.add("on");
}

function finishBattle() {
  wideShot();
  $("#subtitle").classList.remove("on");
  $("#round").textContent = "K.O.";
  $("#start").disabled = false;
  $("#replay").hidden = false;
  setStatus("Battle complete. Replay it or choose another matchup.");
  window.setTimeout(() => $("#splash").classList.remove("gone"), reducedMotion ? 0 : 1800);
}

function option(label, value) {
  const element = document.createElement("option");
  element.value = value;
  element.textContent = label;
  return element;
}

function rowFor(slug) {
  return table?.rows?.find((row) => row.slug === slug);
}

function displayName(slug) {
  return resolveDisplayName(slug, {
    identity: identities.get(slug),
    battleName: battle?.names?.[slug] || previewBattle?.names?.[slug],
    tableName: rowFor(slug)?.name,
  });
}

function stageFallbackFighters(a, b) {
  const left = $("#fallback-a");
  const right = $("#fallback-b");
  left.src = spritePaths.get(a) || "";
  right.src = spritePaths.get(b) || "";
  left.alt = `${displayName(a)} robot`;
  right.alt = `${displayName(b)} robot`;
}

function stageFighters(a, b) {
  stageFallbackFighters(a, b);
  if (!webglAvailable || !scene || !textureLoader) {
    fighters = {};
    return;
  }
  for (const fighter of Object.values(fighters)) scene.remove(fighter);
  fighters = { [a]: makeFighter(a, -1), [b]: makeFighter(b, 1) };
}

function opponentsFor(slug) {
  const slugs = new Set();
  for (const pair of matchups) {
    if (pair.a === slug) slugs.add(pair.b);
    if (pair.b === slug) slugs.add(pair.a);
  }
  return [...slugs].sort((a, b) => displayName(a).localeCompare(displayName(b)));
}

function selectedMatchup() {
  const key = [$("#select-a").value, $("#select-b").value].sort().join("__");
  return matchups.find((pair) => pair.key === key) || null;
}

function setPreviewLoading() {
  const preview = $("#matchup-preview");
  preview.className = "matchup-preview loading";
  preview.setAttribute("aria-busy", "true");
  $("#preview-heading").textContent = "Preparing matchup…";
  $("#preview-beat").textContent = "Selecting beat…";
  $("#preview-score").textContent = "Comparing records…";
  $("#preview-audio").textContent = "Checking audio";
  $("#preview-audio").classList.remove("unavailable");
  $("#preview-fact").textContent = "Loading a verified rivalry fact…";
  $("#preview-source").hidden = true;
}

function setPreviewError(message) {
  const preview = $("#matchup-preview");
  preview.className = "matchup-preview error";
  preview.setAttribute("aria-busy", "false");
  $("#preview-heading").textContent = "Matchup unavailable";
  $("#preview-fact").textContent = message;
  $("#preview-source").hidden = true;
  $("#start").disabled = true;
}

function renderPreview(pair, payload) {
  const a = $("#select-a").value;
  const b = $("#select-b").value;
  const nameA = displayName(a);
  const nameB = displayName(b);
  const beat = selectBeatForMatchup(a, b, beats);
  const fact = selectPreviewFact(payload?.facts);
  const audioState = formatAudioState(pair);
  const preview = $("#matchup-preview");
  preview.className = "matchup-preview";
  preview.setAttribute("aria-busy", "false");
  $("#select-a").selectedOptions[0].textContent = nameA;
  $("#select-b").selectedOptions[0].textContent = nameB;
  $("#preview-heading").textContent = `${nameA} vs ${nameB}`;
  $("#preview-beat").textContent = formatBeat(beat);
  $("#preview-score").textContent = formatScoreComparison(
    { ...rowFor(a), name: nameA },
    { ...rowFor(b), name: nameB },
  );
  $("#preview-audio").textContent = audioState;
  $("#preview-audio").classList.toggle("unavailable", pair?.audio?.complete !== true);
  $("#preview-fact").textContent = fact?.text || "No concise sourced matchup fact is available.";

  const source = safeSourceUrl(
    fact?.source_url
      || (Array.isArray(fact?.source_urls) ? fact.source_urls.find(safeSourceUrl) : null),
  );
  const sourceLink = $("#preview-source");
  if (source) {
    sourceLink.href = source;
    sourceLink.hidden = false;
  } else {
    sourceLink.removeAttribute("href");
    sourceLink.hidden = true;
  }
  $("#start").disabled = false;
}

async function updatePreview() {
  const pair = selectedMatchup();
  previewBattle = null;
  const requestId = ++previewRequestId;
  if (!pair) {
    setPreviewError("Choose a verified opponent to continue.");
    return;
  }
  setPreviewLoading();
  $("#start").disabled = true;
  const a = $("#select-a").value;
  const b = $("#select-b").value;
  const payload = await loadJson(`/api/battle/${encodeURIComponent(a)}/${encodeURIComponent(b)}`);
  if (requestId !== previewRequestId) return;
  if (!payload || !Array.isArray(payload.bars) || !payload.bars.length) {
    setPreviewError("This battle is stale or its sourced bars could not be loaded.");
    return;
  }
  previewBattle = payload;
  renderPreview(pair, payload);
}

function populateFirstSelector() {
  const first = $("#select-a");
  const slugs = [...new Set(matchups.flatMap((pair) => [pair.a, pair.b]))]
    .sort((a, b) => displayName(a).localeCompare(displayName(b)));
  first.replaceChildren(...slugs.map((slug) => option(displayName(slug), slug)));
}

function populateSecondSelector(preferred = "") {
  const second = $("#select-b");
  const opponents = opponentsFor($("#select-a").value);
  second.replaceChildren(...opponents.map((slug) => option(displayName(slug), slug)));
  if (opponents.includes(preferred)) second.value = preferred;
  $("#start").disabled = true;
}

async function loadJson(url, fallback = null) {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status}`);
    return await response.json();
  } catch {
    return fallback;
  }
}

async function boot() {
  initVisuals();
  const [
    tablePayload,
    battlePayload,
    standardSprites,
    tekkenSprites,
    beatPayload,
    identityPayload,
  ] = await Promise.all([
    loadJson("/api/table"),
    loadJson("/api/battles"),
    loadJson("/bots/sprites.json", []),
    loadJson("/bots_tekken/index.json", []),
    loadJson("/beats/beats.json", { beats: [] }),
    loadJson("/data/robot-identities.json", { robots: [] }),
  ]);

  table = tablePayload;
  beats = Array.isArray(beatPayload?.beats) ? beatPayload.beats : [];
  identities = new Map(
    (Array.isArray(identityPayload?.robots) ? identityPayload.robots : [])
      .filter((identity) =>
        identity
        && typeof identity.id === "string"
        && typeof identity.name === "string"
        && identity.id
        && identity.name
      )
      .map((identity) => [identity.id, identity]),
  );
  if (!table?.rows || !Array.isArray(battlePayload?.battles)) {
    setStatus("The rap battle roster is unavailable. Fight mode is still available.", true);
    $("#start").disabled = true;
    return;
  }

  const standard = new Set((standardSprites || []).map((sprite) => sprite?.slug).filter(Boolean));
  const tekken = new Set((tekkenSprites || []).map((sprite) => sprite?.slug).filter(Boolean));
  const visible = new Set([...standard, ...tekken]);
  for (const slug of visible) {
    const path = resolveSpritePath(slug, identities.get(slug), standard, tekken);
    if (path) spritePaths.set(slug, path);
  }

  const rowSlugs = table.rows.map((row) => row?.slug).filter(Boolean);
  matchups = indexedMatchups(battlePayload.battles, rowSlugs.filter((slug) => visible.has(slug)));
  if (!matchups.length) {
    setStatus("No verified rap matchup currently has both battle data and robot artwork.", true);
    $("#start").disabled = true;
    return;
  }

  populateFirstSelector();
  const firstPair = matchups[0];
  $("#select-a").value = firstPair.a;
  populateSecondSelector(firstPair.b);
  const sourceState = table.provenance?.is_real ? "sourced data" : "demo data";
  const audioState = matchups.every((pair) => pair.audio?.complete === true)
    ? "verified voice + caption fallback"
    : "caption fallback available";
  const identityState = identities.size ? "" : " · canonical names unavailable";
  setStatus(`${matchups.length} battle${matchups.length === 1 ? "" : "s"} ready · ${sourceState} · ${audioState}${identityState}`);
  await updatePreview();
}

async function startBattle() {
  audioController?.stop();
  const a = $("#select-a").value;
  const b = $("#select-b").value;
  const known = matchups.some((pair) => pair.key === [a, b].sort().join("__"));
  if (!known || !a || !b || a === b) {
    setStatus("That matchup is not marked ready. Choose an available opponent.", true);
    return;
  }

  $("#start").disabled = true;
  $("#replay").hidden = true;
  setStatus("Loading the battle…");
  const payload = previewBattle || await loadJson(
    `/api/battle/${encodeURIComponent(a)}/${encodeURIComponent(b)}`,
  );
  if (!payload || !Array.isArray(payload.bars) || !payload.bars.length) {
    $("#start").disabled = false;
    setStatus("This battle has no verified bars to perform.", true);
    return;
  }
  battle = payload;
  const rowA = rowFor(a);
  const rowB = rowFor(b);
  $("#name-a").textContent = displayName(a);
  $("#name-b").textContent = displayName(b);
  setMeter("#hype-a", rowA?.hype);
  setMeter("#performance-a", rowA?.performance);
  setMeter("#hype-b", rowB?.hype);
  setMeter("#performance-b", rowB?.performance);

  stageFighters(a, b);
  $("#splash").classList.add("gone");
  $("#round").textContent = "INTRO";
  $("#subtitle").classList.remove("on");
  wideShot();
  setStatus("Battle in progress.");

  const beat = selectBeatForMatchup(a, b, beats);
  const silentManifest = {
    bars: payload.bars.map((_, index) => ({
      index,
      file: `unavailable-${index}.mp3`,
    })),
  };
  const started = await audioController.play({
    manifest: payload.audio || silentManifest,
    bars: payload.bars,
    beat,
  });
  if (!started) {
    $("#splash").classList.remove("gone");
    $("#start").disabled = false;
    $("#replay").hidden = false;
  }
}

function initializeApp() {
  audioController = new RapAudioController({
    voiceElement: $("#voice"),
    beatElement: $("#beat"),
    onStep: showAudioStep,
    onComplete: finishBattle,
    onStatus: (message) => setStatus(message),
  });

  $("#select-a").addEventListener("change", () => {
    populateSecondSelector();
    updatePreview();
  });
  $("#select-b").addEventListener("change", () => updatePreview());
  $("#graphics-toggle").addEventListener("click", () => {
    if (graphicsMode === "3d") {
      activateFallback("Reduced graphics mode is active. Audio, subtitles, and sources are unchanged.");
    } else {
      activateWebGL();
    }
  });
  $("#matchup-form").addEventListener("submit", (event) => {
    event.preventDefault();
    startBattle().catch(() => {
      $("#start").disabled = false;
      $("#splash").classList.remove("gone");
      setStatus("The battle could not start. Please choose another matchup.", true);
    });
  });
  $("#replay").addEventListener("click", () => {
    startBattle().catch(() => {
      $("#start").disabled = false;
      $("#splash").classList.remove("gone");
      setStatus("The battle could not restart. Please choose another matchup.", true);
    });
  });
  addEventListener("pagehide", () => audioController.stop());

  boot().catch(() => {
    if (!renderer && graphicsMode !== "2d") {
      activateFallback(
        "The 3D stage could not load. Reduced graphics mode remains available.",
        { permanent: true },
      );
    }
    setPreviewError("The rap arena could not load its local battle data.");
    setStatus("The rap arena could not load its local data.", true);
    $("#start").disabled = true;
  });
}

if (typeof document !== "undefined") initializeApp();
