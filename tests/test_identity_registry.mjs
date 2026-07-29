import test from "node:test";
import assert from "node:assert/strict";

import {
  joinCombatProfiles,
  loadIdentityRegistry,
} from "../static/js/identity-registry.js";

const profile = (id) => ({ id, stats: {}, attacks: [] });

test("combat bootstrap rejects registry/profile ID drift in either direction", async () => {
  const identities = await loadIdentityRegistry();
  const combatIds = [...identities.values()]
    .filter((identity) => identity.combat)
    .map((identity) => identity.id);
  const profiles = combatIds.map(profile);

  assert.doesNotThrow(() => joinCombatProfiles(profiles, identities));
  assert.throws(
    () => joinCombatProfiles(profiles.slice(1), identities),
    /out of sync/,
    "a registry combat identity without mechanics must fail startup",
  );

  const extraProfiles = [...profiles, profile("not-in-registry")];
  assert.throws(
    () => joinCombatProfiles(extraProfiles, identities),
    /out of sync/,
    "a mechanics profile without a registry combat identity must fail startup",
  );
});
