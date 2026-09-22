// Tests du module partagé de l'interface : node --test "tests/web/*.test.mjs"
import assert from "node:assert/strict";
import { test } from "node:test";

import { arrondi2, fmtScore } from "../../app/web/commun.js";

// Mêmes valeurs que Python ``round(v, 2)`` (construire_warnings) et ``f"{v:.2f}"`` (_fr).
const PYTHON = { 0.595: 0.59, 0.605: 0.6, 0.745: 0.74, 0.755: 0.76, 0.6: 0.6, 0.42: 0.42 };

test("arrondi2 arrondit comme Python round(v, 2)", () => {
  for (const [v, attendu] of Object.entries(PYTHON)) assert.equal(arrondi2(Number(v)), attendu, v);
});

test("fmtScore affiche la valeur que le serveur cite dans ses warnings", () => {
  assert.equal(fmtScore(0.595), "0,59");
  assert.equal(fmtScore(null), "—");
});
