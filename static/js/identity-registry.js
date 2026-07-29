const REGISTRY_URL = new URL("../data/robot-identities.json", import.meta.url);

async function readRegistry(url) {
  if (globalThis.process?.versions?.node) {
    const { readFile } = await import("node:fs/promises");
    return JSON.parse(await readFile(url, "utf8"));
  }

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Robot identity registry failed to load (${response.status})`);
  }
  return response.json();
}

function validateRegistry(payload) {
  if (!payload || payload.version !== 1 || !Array.isArray(payload.robots)) {
    throw new Error("Robot identity registry has an unsupported schema");
  }

  const identities = new Map();
  for (const identity of payload.robots) {
    if (
      !identity
      || typeof identity.id !== "string"
      || !identity.id
      || typeof identity.name !== "string"
      || !identity.name
      || identities.has(identity.id)
    ) {
      throw new Error("Robot identity registry contains an invalid or duplicate id");
    }
    identities.set(identity.id, identity);
  }
  return identities;
}

/**
 * Load the shared identity registry in browsers and Node.
 *
 * Browser module evaluation waits for this promise before evaluating modules
 * that import roster.js, so roster exports remain ordinary synchronous values
 * to every consumer. Node reads the same JSON from disk for deterministic tests.
 */
export async function loadIdentityRegistry(url = REGISTRY_URL) {
  return validateRegistry(await readRegistry(url));
}

/**
 * Join JS-owned mechanics to registry-owned identity fields.
 *
 * Exact bidirectional ID parity is required so neither a mechanics profile nor
 * a registry combat identity can silently disappear from the playable roster.
 */
export function joinCombatProfiles(combatProfiles, identities) {
  const profileIds = new Set(combatProfiles.map((profile) => profile.id));
  const registryCombatIds = [...identities.values()]
    .filter((identity) => identity.combat)
    .map((identity) => identity.id);
  if (
    registryCombatIds.length !== profileIds.size
    || registryCombatIds.some((id) => !profileIds.has(id))
  ) {
    throw new Error("Combat profiles and the robot identity registry are out of sync");
  }

  return combatProfiles.map((profile) => {
    const identity = identities.get(profile.id);
    if (
      !identity?.combat
      || !identity.assets?.standard
      || !Array.isArray(identity.aliases)
      || typeof identity.combat_weapon_type !== "string"
    ) {
      throw new Error(`Combat identity ${profile.id} is missing required registry fields`);
    }
    return {
      ...profile,
      name: identity.name,
      aliases: identity.aliases,
      image: identity.assets.standard,
      weaponType: identity.combat_weapon_type,
    };
  });
}
