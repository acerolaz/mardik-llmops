// Tests du module partagé de l'interface : node --test "tests/web/*.test.mjs"
import assert from "node:assert/strict";
import { test } from "node:test";

import { arrondi2, etatSansVerdict, fmtDate, fmtNombre, fmtScore, messageSansClause } from "../../app/web/commun.js";

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

test("etatSansVerdict : un canary connu sans verdict est « indisponible », jamais « aucun »", () => {
  assert.equal(etatSansVerdict({ canary: "v2.0.0", alertes: ["métriques illisibles : perm"] }), "indisponible");
  assert.equal(etatSansVerdict({ canary: "v2.0.0", alertes: ["seuils invalides : absent"] }), "indisponible");
});

test("etatSansVerdict : registre illisible → état inconnu", () => {
  assert.equal(etatSansVerdict({ canary: null, alertes: ["registre illisible : index.json"] }), "inconnu");
});

test("etatSansVerdict : aucun canary seulement si le registre a été lu", () => {
  assert.equal(etatSansVerdict({ canary: null, alertes: [] }), "aucun");
  assert.equal(etatSansVerdict({ canary: null, alertes: ["seuils invalides : absent"] }), "aucun");
});

test("fmtDate : date locale fr-FR, « — » si absente ou illisible", () => {
  const iso = "2026-09-23T14:08:35+00:00";
  assert.equal(fmtDate(iso), new Date(iso).toLocaleString("fr-FR"));
  assert.equal(fmtDate(null), "—");
  assert.equal(fmtDate(""), "—");
  assert.equal(fmtDate("pas une date"), "—");
});

test("fmtNombre : séparateur de milliers fr-FR, décimales au plus d", () => {
  assert.equal(fmtNombre(62186), (62186).toLocaleString("fr-FR"));
  assert.equal(fmtNombre(2.345, 1), "2,3");
  assert.equal(fmtNombre(2, 1), "2");
});
