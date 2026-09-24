// Tests du module partagé de l'interface : node --test "tests/web/*.test.mjs"
import assert from "node:assert/strict";
import { test } from "node:test";

import { arrondi2, fmtScore, messageSansClause } from "../../app/web/commun.js";

// Mêmes valeurs que Python ``round(v, 2)`` (construire_warnings) et ``f"{v:.2f}"`` (_fr).
const PYTHON = { 0.595: 0.59, 0.605: 0.6, 0.745: 0.74, 0.755: 0.76, 0.6: 0.6, 0.42: 0.42 };

test("arrondi2 arrondit comme Python round(v, 2)", () => {
  for (const [v, attendu] of Object.entries(PYTHON)) assert.equal(arrondi2(Number(v)), attendu, v);
});

test("fmtScore affiche la valeur que le serveur cite dans ses warnings", () => {
  assert.equal(fmtScore(0.595), "0,59");
  assert.equal(fmtScore(null), "—");
});

// Réponses d'appeler() : null = en cours ; { ok: true, corps } ; { ok: false, statut, detail }.
const OK_VIDE = { ok: true, corps: { clauses: [] } };
const ECHEC = { ok: false, statut: 503, detail: "fournisseur LLM indisponible" };

test("messageSansClause : chargement tant qu'une analyse est en cours", () => {
  assert.equal(messageSansClause([null]), "Chargement…");
  assert.equal(messageSansClause([OK_VIDE, null]), "Chargement…");
  assert.equal(messageSansClause([ECHEC, null]), "Chargement…");
});

test("messageSansClause : échec quand toutes les analyses ont échoué", () => {
  assert.equal(messageSansClause([ECHEC]), "Aucune clause : analyse en échec");
  assert.equal(messageSansClause([ECHEC, ECHEC]), "Aucune clause : analyse en échec");
});

test("messageSansClause : aucune clause détectée seulement si une analyse a abouti", () => {
  assert.equal(messageSansClause([OK_VIDE]), "Aucune clause détectée.");
  assert.equal(messageSansClause([OK_VIDE, ECHEC]), "Aucune clause détectée.");
});
