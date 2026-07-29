import {
  joinCombatProfiles,
  loadIdentityRegistry,
} from "./identity-registry.js";

/**
 * Canonical fighter data for the six-robot gameplay roster.
 *
 * Scale and units:
 * - stats: 1 (weakest) to 10 (strongest)
 * - attack timing: seconds
 * - range and knockback: arena world units
 * - hitArc: degrees centred on the fighter's forward vector
 *
 * KeyboardEvent.code values are supplied alongside display keys so the
 * combat engine can remain keyboard-layout independent.
 */

const deepFreeze = (value) => {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    Object.values(value).forEach(deepFreeze);
  }
  return value;
};

const combatProfiles = [
  {
    id: "witch-doctor",
    stats: { speed: 8, power: 8, armour: 6, reach: 6, handling: 8 },
    palette: {
      primary: "#5a247d",
      secondary: "#22d3a6",
      accent: "#ff7a1a",
      ink: "#130b1c",
    },
    archetype: "Rushdown all-rounder",
    tagline: "Fast pressure, sharp angles, and a vicious finishing disc.",
    attacks: [
      {
        id: "voodoo-jab",
        name: "Voodoo Jab",
        slot: "light",
        key: "J",
        code: "KeyJ",
        damage: 7,
        range: 1.65,
        cooldown: 0.3,
        knockback: 0.35,
        stun: 0.12,
        windup: 0.07,
        duration: 0.13,
        hitArc: 72,
      },
      {
        id: "hex-drive",
        name: "Hex Drive",
        slot: "medium",
        key: "K",
        code: "KeyK",
        damage: 12,
        range: 2.05,
        cooldown: 0.62,
        knockback: 0.8,
        stun: 0.22,
        windup: 0.14,
        duration: 0.2,
        hitArc: 88,
      },
      {
        id: "disc-exorcism",
        name: "Disc Exorcism",
        slot: "heavy",
        key: "L",
        code: "KeyL",
        damage: 18,
        range: 2.35,
        cooldown: 1.18,
        knockback: 1.45,
        stun: 0.36,
        windup: 0.27,
        duration: 0.26,
        hitArc: 108,
      },
      {
        id: "black-magic-blitz",
        name: "Black Magic Blitz",
        slot: "special",
        key: "I",
        code: "KeyI",
        damage: 23,
        range: 2.9,
        cooldown: 2.45,
        knockback: 2.05,
        stun: 0.53,
        windup: 0.34,
        duration: 0.4,
        hitArc: 126,
      },
    ],
  },
  {
    id: "tombstone",
    stats: { speed: 5, power: 10, armour: 6, reach: 8, handling: 4 },
    palette: {
      primary: "#b80f1f",
      secondary: "#292929",
      accent: "#f4f4f4",
      ink: "#090909",
    },
    archetype: "Heavy hitter",
    tagline: "Slow commitment, enormous reach, catastrophic punishment.",
    attacks: [
      {
        id: "grave-tap",
        name: "Grave Tap",
        slot: "light",
        key: "J",
        code: "KeyJ",
        damage: 8,
        range: 1.8,
        cooldown: 0.38,
        knockback: 0.5,
        stun: 0.14,
        windup: 0.1,
        duration: 0.15,
        hitArc: 82,
      },
      {
        id: "headstone-sweep",
        name: "Headstone Sweep",
        slot: "medium",
        key: "K",
        code: "KeyK",
        damage: 14,
        range: 2.45,
        cooldown: 0.8,
        knockback: 1.05,
        stun: 0.27,
        windup: 0.2,
        duration: 0.26,
        hitArc: 132,
      },
      {
        id: "six-feet-under",
        name: "Six Feet Under",
        slot: "heavy",
        key: "L",
        code: "KeyL",
        damage: 21,
        range: 2.85,
        cooldown: 1.45,
        knockback: 1.85,
        stun: 0.45,
        windup: 0.38,
        duration: 0.34,
        hitArc: 156,
      },
      {
        id: "last-rites",
        name: "Last Rites",
        slot: "special",
        key: "I",
        code: "KeyI",
        damage: 28,
        range: 3.25,
        cooldown: 3.0,
        knockback: 2.65,
        stun: 0.66,
        windup: 0.52,
        duration: 0.48,
        hitArc: 174,
      },
    ],
  },
  {
    id: "hypershock",
    stats: { speed: 10, power: 7, armour: 5, reach: 5, handling: 9 },
    palette: {
      primary: "#f3df00",
      secondary: "#121212",
      accent: "#e5398f",
      ink: "#080808",
    },
    archetype: "Speedster",
    tagline: "Explosive movement turns every opening into a drive-by hit.",
    attacks: [
      {
        id: "quick-charge",
        name: "Quick Charge",
        slot: "light",
        key: "J",
        code: "KeyJ",
        damage: 6,
        range: 1.7,
        cooldown: 0.24,
        knockback: 0.3,
        stun: 0.1,
        windup: 0.05,
        duration: 0.11,
        hitArc: 68,
      },
      {
        id: "neon-drift",
        name: "Neon Drift",
        slot: "medium",
        key: "K",
        code: "KeyK",
        damage: 10,
        range: 2.25,
        cooldown: 0.52,
        knockback: 0.65,
        stun: 0.18,
        windup: 0.1,
        duration: 0.2,
        hitArc: 104,
      },
      {
        id: "hyperdrive",
        name: "Hyperdrive",
        slot: "heavy",
        key: "L",
        code: "KeyL",
        damage: 16,
        range: 2.65,
        cooldown: 1.02,
        knockback: 1.25,
        stun: 0.3,
        windup: 0.2,
        duration: 0.29,
        hitArc: 92,
      },
      {
        id: "maximum-oversteer",
        name: "Maximum Oversteer",
        slot: "special",
        key: "I",
        code: "KeyI",
        damage: 21,
        range: 3.2,
        cooldown: 2.25,
        knockback: 1.9,
        stun: 0.46,
        windup: 0.26,
        duration: 0.42,
        hitArc: 122,
      },
    ],
  },
  {
    id: "minotaur",
    stats: { speed: 8, power: 8, armour: 8, reach: 4, handling: 7 },
    palette: {
      primary: "#d8d8d8",
      secondary: "#202020",
      accent: "#e02424",
      ink: "#090909",
    },
    archetype: "Close-range bruiser",
    tagline: "Armoured pressure and drum recoil rule the pocket.",
    attacks: [
      {
        id: "bull-nudge",
        name: "Bull Nudge",
        slot: "light",
        key: "J",
        code: "KeyJ",
        damage: 7,
        range: 1.5,
        cooldown: 0.28,
        knockback: 0.38,
        stun: 0.13,
        windup: 0.06,
        duration: 0.14,
        hitArc: 76,
      },
      {
        id: "drum-roll",
        name: "Drum Roll",
        slot: "medium",
        key: "K",
        code: "KeyK",
        damage: 13,
        range: 1.85,
        cooldown: 0.58,
        knockback: 0.72,
        stun: 0.24,
        windup: 0.12,
        duration: 0.24,
        hitArc: 88,
      },
      {
        id: "labyrinth-charge",
        name: "Labyrinth Charge",
        slot: "heavy",
        key: "L",
        code: "KeyL",
        damage: 18,
        range: 2.2,
        cooldown: 1.06,
        knockback: 1.4,
        stun: 0.37,
        windup: 0.22,
        duration: 0.31,
        hitArc: 80,
      },
      {
        id: "rage-of-the-bull",
        name: "Rage of the Bull",
        slot: "special",
        key: "I",
        code: "KeyI",
        damage: 24,
        range: 2.45,
        cooldown: 2.4,
        knockback: 2.15,
        stun: 0.56,
        windup: 0.31,
        duration: 0.43,
        hitArc: 112,
      },
    ],
  },
  {
    id: "huge",
    stats: { speed: 5, power: 7, armour: 6, reach: 10, handling: 6 },
    palette: {
      primary: "#ededed",
      secondary: "#343434",
      accent: "#42b9ff",
      ink: "#101010",
    },
    archetype: "Long-range zoner",
    tagline: "Towering wheels and an overhead bar keep threats at a distance.",
    attacks: [
      {
        id: "wheel-check",
        name: "Wheel Check",
        slot: "light",
        key: "J",
        code: "KeyJ",
        damage: 6,
        range: 2.0,
        cooldown: 0.34,
        knockback: 0.45,
        stun: 0.11,
        windup: 0.09,
        duration: 0.16,
        hitArc: 96,
      },
      {
        id: "high-ground",
        name: "High Ground",
        slot: "medium",
        key: "K",
        code: "KeyK",
        damage: 11,
        range: 2.65,
        cooldown: 0.7,
        knockback: 0.9,
        stun: 0.21,
        windup: 0.17,
        duration: 0.24,
        hitArc: 118,
      },
      {
        id: "towering-blade",
        name: "Towering Blade",
        slot: "heavy",
        key: "L",
        code: "KeyL",
        damage: 17,
        range: 3.25,
        cooldown: 1.22,
        knockback: 1.5,
        stun: 0.35,
        windup: 0.31,
        duration: 0.31,
        hitArc: 134,
      },
      {
        id: "huge-reach",
        name: "HUGE Reach",
        slot: "special",
        key: "I",
        code: "KeyI",
        damage: 22,
        range: 3.8,
        cooldown: 2.6,
        knockback: 2.15,
        stun: 0.52,
        windup: 0.4,
        duration: 0.46,
        hitArc: 148,
      },
    ],
  },
  {
    id: "cobalt",
    stats: { speed: 6, power: 9, armour: 7, reach: 6, handling: 6 },
    palette: {
      primary: "#164d94",
      secondary: "#d5d9df",
      accent: "#ff8a1c",
      ink: "#071426",
    },
    archetype: "Counter-puncher",
    tagline: "Disciplined spacing converts one mistake into a brutal launch.",
    attacks: [
      {
        id: "blue-steel",
        name: "Blue Steel",
        slot: "light",
        key: "J",
        code: "KeyJ",
        damage: 7,
        range: 1.7,
        cooldown: 0.32,
        knockback: 0.4,
        stun: 0.12,
        windup: 0.08,
        duration: 0.13,
        hitArc: 70,
      },
      {
        id: "wedge-punish",
        name: "Wedge Punish",
        slot: "medium",
        key: "K",
        code: "KeyK",
        damage: 12,
        range: 2.15,
        cooldown: 0.66,
        knockback: 0.85,
        stun: 0.23,
        windup: 0.15,
        duration: 0.21,
        hitArc: 86,
      },
      {
        id: "carbide-cut",
        name: "Carbide Cut",
        slot: "heavy",
        key: "L",
        code: "KeyL",
        damage: 20,
        range: 2.55,
        cooldown: 1.3,
        knockback: 1.7,
        stun: 0.42,
        windup: 0.34,
        duration: 0.28,
        hitArc: 104,
      },
      {
        id: "critical-launch",
        name: "Critical Launch",
        slot: "special",
        key: "I",
        code: "KeyI",
        damage: 26,
        range: 2.9,
        cooldown: 2.75,
        knockback: 2.5,
        stun: 0.62,
        windup: 0.44,
        duration: 0.42,
        hitArc: 120,
      },
    ],
  },
];

const identities = await loadIdentityRegistry();
const roster = joinCombatProfiles(combatProfiles, identities);

export const ROBOT_LIST = deepFreeze(roster);

export const ROBOTS = deepFreeze(
  Object.fromEntries(ROBOT_LIST.map((robot) => [robot.id, robot])),
);

export const ROBOT_IDS = deepFreeze(ROBOT_LIST.map((robot) => robot.id));

const normaliseId = (value) =>
  String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "");

const aliasToId = new Map();
for (const robot of ROBOT_LIST) {
  for (const alias of [robot.id, robot.name, ...robot.aliases]) {
    aliasToId.set(normaliseId(alias), robot.id);
  }
}

/**
 * Resolve display names, canonical slugs, and known historical slugs to one
 * canonical roster id. Returns null rather than guessing for unknown input.
 */
export function resolveRobotId(value) {
  return aliasToId.get(normaliseId(value)) ?? null;
}

/**
 * Look up an immutable robot definition from any accepted id/name alias.
 */
export function getRobotById(value) {
  const id = resolveRobotId(value);
  return id ? ROBOTS[id] : null;
}
