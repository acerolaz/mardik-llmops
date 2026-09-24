# Refonte frontend — Analyse, Pilotage, Observabilité — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trois pages dédiées — Analyse (v1 / v2 / comparer, timer par endpoint), Pilotage (verdict promouvoir / attendre / revenir en arrière, en lecture seule), Observabilité (métriques par route + Sentry optionnel).

**Architecture:** Frontend HTML/JS vanilla existant (`app/web/`), système visuel UI UX PRO MAX déjà en place dans `styles.css`. Le backend expose en plus le verdict du pilote (`decision`) et les seuils dans `/pilotage/resume`, en réutilisant `detecter_derive` / `evaluer_palier` (aucune logique de décision nouvelle). Sentry est branché sur le `TracerProvider` OpenTelemetry existant via `SentrySpanProcessor`, uniquement si `SENTRY_DSN` est défini.

**Tech Stack:** FastAPI, Pydantic v2, pydantic-settings, sentry-sdk 2.x (`instrumenter="otel"`), OpenTelemetry SDK, pytest, JS modules ES natifs, SVG inline.

**Spec:** `docs/superpowers/specs/2026-09-23-refonte-frontend-design.md`

## Global Constraints

- Point de départ : `origin/main` @ `62140b8` — **344 tests verts** (`MOCK=on uv run pytest -q`) ; ils doivent rester verts à chaque commit.
- Contrats `/v1/analyse` et `/v2/analyse` **inchangés** (protégés par `tests/acceptance/test_chaine.py`).
- `ResumePilotage` : champs **ajoutés** uniquement, aucun champ existant modifié ni retiré.
- Pilotage en **lecture seule** : aucun bouton, aucun appel qui promeut ou revient en arrière.
- Aucune dépendance frontend (pas de CDN JS, pas de framework) ; icônes = tracés Lucide inline de `commun.js`, jamais d'emoji.
- Aucun état porté par la seule couleur : icône SVG + texte ; contraste ≥ 4,5:1 (tokens existants de `styles.css`) ; cibles ≥ 44 px ; `prefers-reduced-motion` respecté (règle globale déjà présente).
- Le texte d'un contrat ne part **jamais** vers Sentry (`send_default_pii=False` + `filtrer_evenement`).
- Configuration Sentry via **pydantic-settings** (`SentrySettings`, préfixe `SENTRY_`) ; tout optionnel ; sans `SENTRY_DSN` le comportement actuel (console) est strictement conservé et aucun appel réseau n'a lieu.
- Routeurs minces, schémas Pydantic en réponse (jamais de dict brut), pas d'import `*`, pas d'`except Exception`.
- Libellés d'interface en français, formats `fr-FR` (helpers `fmt*` de `commun.js`).

## Écarts assumés à la spec (découverts en écrivant le plan)

1. **Composants CSS** : `.badge`, `.statut`, `.table-defilante` + `table` existent déjà dans `styles.css` → réutilisés au lieu de créer `.tableau` (DRY). Seul `.json-brut` est nouveau.
2. **Journal** : `resume()` renvoyait les 5 derniers événements ; passé à **20** pour que la traçabilité filtrable et « dernière occurrence » des rétroactions aient du contenu. Ajout sans changement de forme.
3. **`decision.active`** : ajouté au schéma (nom de la version active) — nécessaire pour le tableau canary vs active, l'index n'étant pas exposé.
4. **Logs structlog → Sentry** : structlog écrit sur stdout (pas via `logging`), donc pas de breadcrumbs Sentry sans refondre la chaîne de logs — hors périmètre. Sentry reçoit les **traces** et les **exceptions**.
5. **Liens Sentry** : liens génériques Issues / Traces / Performance filtrés par environnement ; pas de lien « traces de la version X » (seule la v2 porte `mardik.model_version`). Les transactions portent les tags `mardik.version` / `mardik.model_version` pour filtrer dans Sentry.

## File Structure

| Fichier | Rôle | Tâches |
|---|---|---|
| `app/main.py` | route page `/observabilite`, routeur observabilité, init Sentry | 1, 5, 6 |
| `app/web/index.html` | page Analyse | 1, 2 |
| `app/web/analyse.js` | logique Analyse (réécrit) | 2 |
| `app/web/pilotage.html` / `pilotage.js` | page Pilotage (réécrits) | 1, 4 |
| `app/web/observabilite.html` / `observabilite.js` | page Observabilité (nouveaux) | 1, 7 |
| `app/web/commun.js` | helpers partagés : + `demarrerTimer`, `surveillerResume({url})`, − `rendreP95` | 2, 4, 7 |
| `app/web/styles.css` | + composants Analyse / Pilotage / Observabilité, − règles mortes | 2, 4, 7 |
| `ops/dashboard.py` | `_decision()`, `seuils`, journal 20 | 3 |
| `app/pilotage.py` | schémas `ConstatResponse`, `DecisionResponse`, `SeuilsResponse` | 3 |
| `app/config.py` | `SentrySettings` (pydantic-settings) | 5 |
| `app/sentry.py` | `init_sentry`, `filtrer_evenement` | 5 |
| `app/telemetry.py` | `Telemetry.provider` | 5 |
| `ops/observabilite.py` | service `resume_observabilite`, `liens_sentry` | 6 |
| `app/observabilite.py` | routeur + schémas `ResumeObservabilite` | 6 |
| `pyproject.toml`, `.env.example` | dépendances, variables Sentry | 5 |
| `tests/conftest.py` | neutralise `SENTRY_DSN` | 5 |
| `tests/integration/test_interface.py` | pages, nav, résumé pilotage | 1, 2, 3, 4, 7 |
| `tests/integration/test_dashboard.py` | verdicts | 3 |
| `tests/unit/test_sentry.py` | Sentry | 5 |
| `tests/integration/test_observabilite.py` | résumé observabilité | 6 |

---

### Task 1: Socle — navigation à trois onglets et page `/observabilite`

**Files:**
- Create: `app/web/observabilite.html`, `app/web/observabilite.js`
- Modify: `app/main.py` (après `page_pilotage`), `app/web/index.html` (nav), `app/web/pilotage.html` (nav)
- Test: `tests/integration/test_interface.py`

**Interfaces:**
- Produces: route `GET /observabilite` (HTML, `<main id="observabilite">`), fichier `/static/observabilite.js` (étendu en Task 7).

- [ ] **Step 1: Write the failing tests**

Dans `tests/integration/test_interface.py`, remplacer le `parametrize` de `test_pages_servies` et ajouter un test de navigation :

```python
@pytest.mark.parametrize("chemin, marqueur", [
    ("/", 'id="form-analyse"'),
    ("/pilotage", 'id="versions"'),
    ("/observabilite", 'id="observabilite"'),
])
def test_pages_servies(client, chemin, marqueur):
    r = client.get(chemin)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert marqueur in r.text
    assert "/static/styles.css" in r.text


@pytest.mark.parametrize("chemin", ["/", "/pilotage", "/observabilite"])
def test_navigation_trois_onglets(client, chemin):
    page = client.get(chemin).text

    for cible in ('href="/"', 'href="/pilotage"', 'href="/observabilite"'):
        assert cible in page
    assert page.count('aria-current="page"') == 1
    assert f'href="{chemin}" aria-current="page"' in page
```

Et ajouter `"/static/observabilite.js"` à la liste de `test_fichiers_statiques` :

```python
@pytest.mark.parametrize("chemin", ["/static/styles.css", "/static/observabilite.js",
                                    "/exemples/c02.txt", "/exemples/c07.txt"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MOCK=on uv run pytest -q tests/integration/test_interface.py`
Expected: FAIL — `/observabilite` → 404, `href="/observabilite"` absent.

- [ ] **Step 3: Add the route**

Dans `app/main.py`, juste après `page_pilotage` :

```python
    @app.get("/observabilite", include_in_schema=False)
    def page_observabilite() -> FileResponse:
        return FileResponse(WEB / "observabilite.html")
```

- [ ] **Step 4: Update the nav in both existing pages**

`app/web/index.html` — remplacer le bloc `<nav>` par :

```html
    <nav class="nav" aria-label="Navigation principale">
      <a href="/" aria-current="page">Analyse</a>
      <a href="/pilotage">Pilotage</a>
      <a href="/observabilite">Observabilité</a>
    </nav>
```

`app/web/pilotage.html` — remplacer le bloc `<nav>` par :

```html
    <nav class="nav" aria-label="Navigation principale">
      <a href="/">Analyse</a>
      <a href="/pilotage" aria-current="page">Pilotage</a>
      <a href="/observabilite">Observabilité</a>
    </nav>
```

- [ ] **Step 5: Create the placeholder page**

`app/web/observabilite.html` :

```html
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mardik — Observabilité</title>
  <meta name="description" content="Observabilité Mardik : métriques par route, erreurs récentes, traces et logs dans Sentry.">
  <link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/static/styles.css">
  <script type="module" src="/static/observabilite.js"></script>
</head>
<body>
  <header class="entete">
    <a class="marque" href="/"><span id="logo"></span>Mardik</a>
    <nav class="nav" aria-label="Navigation principale">
      <a href="/">Analyse</a>
      <a href="/pilotage">Pilotage</a>
      <a href="/observabilite" aria-current="page">Observabilité</a>
    </nav>
    <button id="theme" class="bouton bouton-fantome" type="button" aria-label="Changer de thème"></button>
  </header>
  <main id="observabilite">
    <h1 class="titre-page">Observabilité</h1>
    <p class="carte vide">Page en construction : métriques par route et liens Sentry.</p>
  </main>
</body>
</html>
```

`app/web/observabilite.js` :

```js
import { icone, initTheme } from "/static/commun.js";

document.querySelector("#logo").innerHTML = icone("scale");
initTheme(document.querySelector("#theme"));
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `MOCK=on uv run pytest -q tests/integration/test_interface.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/web/index.html app/web/pilotage.html app/web/observabilite.html app/web/observabilite.js tests/integration/test_interface.py
git commit -m "feat(web): navigation à trois onglets et page Observabilité provisoire"
```

---

### Task 2: Page Analyse — v1 / v2 / comparer, timers, tableaux

**Files:**
- Modify: `app/web/index.html` (tout le `<main>` et la `meta description`), `app/web/commun.js`, `app/web/styles.css`
- Rewrite: `app/web/analyse.js`
- Test: `tests/integration/test_interface.py`

**Interfaces:**
- Consumes: `POST /v1/analyse` → `{clauses: string[], modele, version, tronque}` ; `POST /v2/analyse` → `{clauses: [{type, extrait, confiance, sections}], confiance_globale, modele, version, model_version, sections, appels_llm, latence_ms, cout_eur, warnings}`.
- Produces: `demarrerTimer(el: HTMLElement) → () => number` dans `commun.js` (arrête le chrono, fige l'affichage, renvoie la durée en ms). `rendreP95` et `SLO_P95_MS` sont **supprimés** de `commun.js` (seule l'ancienne page Analyse les utilisait ; `pilotage.js` n'importe que `rendrePalier`, `rendreTrafic`, `surveillerResume`, helpers de format).

- [ ] **Step 1: Write the failing test**

Ajouter dans `tests/integration/test_interface.py` :

```python
def test_page_analyse_modes_et_sans_kpi(client):
    page = client.get("/").text

    for mode in ('value="v1"', 'value="v2"', 'value="comparer"'):
        assert mode in page
    assert 'value="gateway"' not in page      # la gateway n'est plus proposée ici
    assert 'id="kpi-p95"' not in page and 'id="kpi-canary"' not in page
    assert 'id="suivi"' in page                # zone des timers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest -q tests/integration/test_interface.py::test_page_analyse_modes_et_sans_kpi`
Expected: FAIL — `value="v1"` absent.

- [ ] **Step 3: Rewrite the page markup**

Dans `app/web/index.html`, remplacer la balise `meta description` par :

```html
  <meta name="description" content="Analyse de contrats Mardik : v1, v2 ou les deux en comparaison, avec clauses clés, fiabilité, alertes et garantie de lecture complète.">
```

Puis remplacer **tout** le contenu de `<main>…</main>` par :

```html
  <main>
    <h1 class="titre-page">Analyse de contrat</h1>

    <form id="form-analyse" class="carte" novalidate>
      <div class="champ">
        <div class="champ-tete">
          <label for="texte">Texte du contrat</label>
          <div>
            <button class="bouton" type="button" data-exemple="c02">Exemple court</button>
            <button class="bouton" type="button" data-exemple="c07">Exemple long</button>
          </div>
        </div>
        <textarea id="texte" name="texte" aria-describedby="compteur erreur-texte"
                  placeholder="Collez ici le texte intégral du contrat…"></textarea>
        <p id="compteur" class="aide">0 caractère</p>
        <p id="erreur-texte" class="erreur-champ" role="alert"></p>
      </div>
      <div class="actions">
        <fieldset class="modes">
          <legend>Traitement</legend>
          <label><input type="radio" name="mode" value="v1"> v1</label>
          <label><input type="radio" name="mode" value="v2" checked> v2</label>
          <label><input type="radio" name="mode" value="comparer"> Comparer v1 + v2</label>
        </fieldset>
        <button id="analyser" class="bouton bouton-primaire" type="submit">Analyser</button>
      </div>
    </form>

    <section id="suivi" class="timers" aria-label="Temps de traitement par endpoint" hidden></section>
    <div id="resultats" class="resultats-analyse"></div>
  </main>
```

- [ ] **Step 4: Add the timer to `commun.js` and remove the dead P95 code**

Dans `app/web/commun.js` :
1. Mettre à jour le commentaire d'en-tête : `// Partagé par les pages Analyse, Pilotage et Observabilité : échappement, formatage, icônes,` / `// thème, rafraîchissement d'un résumé, timer et petits graphiques SVG.`
2. Supprimer la ligne `export const SLO_P95_MS = 8000;        // docs/besoin_client.md : P95 < 8 s`.
3. Supprimer toute la fonction `export function rendreP95(parVersion) { … }`.
4. Ajouter, juste après `fmtDuree` :

```js
const fmtSecondes = (ms) => `${nombre(1).format(ms / 1000)} s`;

// Chronomètre d'un appel : affiche le temps écoulé tous les 100 ms dans `el`
// (qui doit être aria-live="off" : on n'annonce pas chaque tick) ; la fonction
// renvoyée l'arrête, fige la valeur finale et renvoie la durée en ms.
export function demarrerTimer(el) {
  const debut = performance.now();
  const afficher = () => { el.textContent = fmtSecondes(performance.now() - debut); };
  afficher();
  const minuterie = setInterval(afficher, 100);
  return () => {
    clearInterval(minuterie);
    const duree = performance.now() - debut;
    el.textContent = fmtSecondes(duree);
    return duree;
  };
}
```

- [ ] **Step 5: Rewrite `analyse.js`**

Remplacer tout `app/web/analyse.js` par :

```js
import { arrondi2, demarrerTimer, esc, fmtScore, icone, initTheme } from "/static/commun.js";

const LIMITE_V1 = 16000;      // models/v1/config.yaml : contexte_max_caracteres (au-delà, v1 coupe)
const SEUIL_RELECTURE = 0.6;  // models/v2/config.yaml : seuil_relecture
const MIN_CARACTERES = 20;    // RequeteAnalyseV1/V2 : min_length=20
const ENDPOINTS = {
  v1: { chemin: "/v1/analyse", titre: "v1 · historique" },
  v2: { chemin: "/v2/analyse", titre: "v2 · nouvelle version" },
};
const MODES = { v1: ["v1"], v2: ["v2"], comparer: ["v1", "v2"] };

const $ = (s) => document.querySelector(s);
const texte = $("#texte");
const nombre = (n) => n.toLocaleString("fr-FR");

// --- Saisie -----------------------------------------------------------------
function majCompteur() {
  const n = texte.value.length;
  const alerte = n > LIMITE_V1
    ? ` · <span class="warn">${icone("warn")}au-delà de ${nombre(LIMITE_V1)}, v1 tronque le contrat</span>`
    : "";
  $("#compteur").innerHTML = `${nombre(n)} caractère${n > 1 ? "s" : ""}${alerte}`;
}
texte.addEventListener("input", majCompteur);

document.querySelectorAll("[data-exemple]").forEach((bouton) => {
  bouton.addEventListener("click", async () => {
    bouton.disabled = true;
    try {
      const r = await fetch(`/exemples/${bouton.dataset.exemple}.txt`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      texte.value = await r.text();
      $("#erreur-texte").textContent = "";
      majCompteur();
    } catch {
      $("#erreur-texte").textContent = "Exemple introuvable.";
    } finally {
      bouton.disabled = false;
    }
  });
});

// --- Appels API -------------------------------------------------------------
function detailErreur(corps) {
  const d = corps?.detail;
  if (Array.isArray(d)) return d.map((e) => e.msg).join(" ; ");
  return d ?? "réponse illisible";
}

async function appeler(chemin, contenu) {
  let r;
  try {
    r = await fetch(chemin, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ texte: contenu }),
    });
  } catch {
    return { ok: false, statut: null, detail: "API injoignable" };
  }
  const corps = await r.json().catch(() => null);
  if (!r.ok) return { ok: false, statut: r.status, detail: detailErreur(corps) };
  return { ok: true, corps };
}

// --- Suivi (timers) ---------------------------------------------------------
function preparerSuivi(versions) {
  const suivi = $("#suivi");
  suivi.hidden = false;
  suivi.innerHTML = versions.map((v) => `
    <p class="timer" id="suivi-${v}">
      <span class="timer-nom">${esc(ENDPOINTS[v].chemin)}</span>
      <span class="timer-duree" id="duree-${v}" aria-live="off">0,0 s</span>
      <span class="timer-etat" id="etat-${v}" aria-live="polite">en cours…</span>
    </p>`).join("");
}

function terminerSuivi(v, r, arreter) {
  arreter();
  $(`#etat-${v}`).innerHTML = r.ok
    ? `<span class="statut ok">${icone("ok")}terminé</span>`
    : `<span class="statut danger">${icone("danger")}${r.statut ? `Erreur ${r.statut}` : "Erreur réseau"} : ${esc(r.detail)}</span>`;
}

// --- Rendu des résultats ----------------------------------------------------
const EN_COURS = '<span class="vide">…</span>';
const INDISPONIBLE = '<span class="vide">—</span>';

function cellule(v, r, rendre) {
  if (!r) return EN_COURS;
  if (!r.ok) return INDISPONIBLE;
  return rendre(v, r.corps);
}

function fiabiliteDocument(v, corps) {
  if (v === "v1") return '<span class="vide">non mesuré</span>';
  const fiable = arrondi2(corps.confiance_globale) >= SEUIL_RELECTURE;
  return `<span class="chiffre">${fmtScore(corps.confiance_globale)}</span> ${fiable
    ? `<span class="statut ok">${icone("ok")}fiable</span>`
    : `<span class="statut danger">${icone("danger")}non fiable</span>`}`;
}

function lectureComplete(v, corps) {
  if (v === "v2") return `<span class="statut ok">${icone("ok")}oui (${esc(corps.sections)} section(s))</span>`;
  return corps.tronque
    ? `<span class="statut warn">${icone("warn")}non, tronqué à ${nombre(LIMITE_V1)} caractères</span>`
    : `<span class="statut ok">${icone("ok")}oui</span>`;
}

function auteur(v, corps) {
  return `${esc(corps.modele)} · ${esc(v === "v1" ? corps.version : corps.model_version)}`;
}

function tableauDocument(versions, etat) {
  const lignes = [
    ["Indice de fiabilité", fiabiliteDocument],
    ["Lecture complète", lectureComplete],
    ["Auteur de l'analyse", auteur],
  ];
  return `<section class="carte" aria-labelledby="t-document">
    <h2 id="t-document">Document</h2>
    <div class="table-defilante"><table>
      <thead><tr><th scope="col">Critère</th>${versions.map((v) => `<th scope="col">${esc(ENDPOINTS[v].titre)}</th>`).join("")}</tr></thead>
      <tbody>${lignes.map(([libelle, rendre]) => `<tr><th scope="row">${libelle}</th>${
        versions.map((v) => `<td>${cellule(v, etat[v], rendre)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
  </section>`;
}

const cleType = (t) => String(t).trim().toLowerCase();

function tableauClauses(versions, etat) {
  const types = new Map();   // clé normalisée → libellé affiché
  for (const v of versions) {
    const r = etat[v];
    if (!r?.ok) continue;
    for (const c of r.corps.clauses) {
      const libelle = v === "v1" ? c : c.type;
      if (!types.has(cleType(libelle))) types.set(cleType(libelle), libelle);
    }
  }
  if (!types.size && versions.every((v) => etat[v])) {
    return '<section class="carte"><h2>Clauses clés</h2><p class="vide">Aucune clause détectée.</p></section>';
  }
  const tries = [...types.entries()].sort((a, b) => a[1].localeCompare(b[1], "fr"));
  const celluleClause = (v, cle) => cellule(v, etat[v], (version, corps) => {
    if (version === "v1") {
      return corps.clauses.some((c) => cleType(c) === cle)
        ? `<span class="statut ok">${icone("ok")}détectée</span>` : INDISPONIBLE;
    }
    const c = corps.clauses.find((x) => cleType(x.type) === cle);
    if (!c) return INDISPONIBLE;
    const relire = arrondi2(c.confiance) < SEUIL_RELECTURE
      ? ` <span class="statut warn">${icone("warn")}à relire</span>` : "";
    return `<span class="chiffre">${fmtScore(c.confiance)}</span>${relire}
      <span class="extrait">« ${esc(c.extrait)} » · § ${c.sections.map(esc).join(", ")}</span>`;
  });
  return `<section class="carte" aria-labelledby="t-clauses">
    <h2 id="t-clauses">Clauses clés et fiabilité par clause</h2>
    <div class="table-defilante"><table>
      <thead><tr><th scope="col">Type</th>${versions.map((v) => `<th scope="col">${esc(ENDPOINTS[v].titre)}</th>`).join("")}</tr></thead>
      <tbody>${tries.map(([cle, libelle]) => `<tr><th scope="row">${esc(libelle)}</th>${
        versions.map((v) => `<td>${celluleClause(v, cle)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
  </section>`;
}

function alertes(versions, etat, longueur) {
  const items = [];
  for (const v of versions) {
    const r = etat[v];
    if (!r?.ok) continue;
    if (v === "v1" && r.corps.tronque) {
      items.push(`v1 · contrat tronqué : ${nombre(longueur - LIMITE_V1)} caractères non lus — préférez la v2`);
    }
    if (v === "v2") items.push(...r.corps.warnings.map((w) => `v2 · ${w}`));
  }
  if (!versions.every((v) => etat[v])) return "";
  const contenu = items.length
    ? `<ul class="warnings">${items.map((w) => `<li>${icone("warn")}<span>${esc(w)}</span></li>`).join("")}</ul>`
    : `<p class="statut ok">${icone("ok")}Aucune alerte : l'analyse peut être validée.</p>`;
  return `<section class="carte" aria-labelledby="t-alertes"><h2 id="t-alertes">Alertes et préconisations</h2>${contenu}</section>`;
}

function jsonBrut(versions, etat) {
  return versions.filter((v) => etat[v]?.ok).map((v) => `
    <details class="json-brut"><summary>JSON brut ${esc(v)}</summary>
      <pre>${esc(JSON.stringify(etat[v].corps, null, 2))}</pre></details>`).join("");
}

function rendre(versions, etat, longueur) {
  $("#resultats").innerHTML = tableauDocument(versions, etat) + tableauClauses(versions, etat)
    + alertes(versions, etat, longueur) + jsonBrut(versions, etat);
}

// --- Soumission -------------------------------------------------------------
$("#form-analyse").addEventListener("submit", async (evenement) => {
  evenement.preventDefault();
  const contenu = texte.value;
  if (contenu.length < MIN_CARACTERES) {
    $("#erreur-texte").textContent = `Le contrat doit contenir au moins ${MIN_CARACTERES} caractères.`;
    texte.focus();
    return;
  }
  $("#erreur-texte").textContent = "";
  const versions = MODES[new FormData(evenement.target).get("mode")] ?? MODES.v2;
  const etat = Object.fromEntries(versions.map((v) => [v, null]));
  const bouton = $("#analyser");
  bouton.disabled = true;
  bouton.textContent = "Analyse…";
  preparerSuivi(versions);
  rendre(versions, etat, contenu.length);
  try {
    await Promise.allSettled(versions.map(async (v) => {
      const arreter = demarrerTimer($(`#duree-${v}`));
      const r = await appeler(ENDPOINTS[v].chemin, contenu);
      terminerSuivi(v, r, arreter);
      etat[v] = r;
      rendre(versions, etat, contenu.length);
    }));
  } finally {
    bouton.disabled = false;
    bouton.textContent = "Analyser";
  }
});

$("#logo").innerHTML = icone("scale");
initTheme($("#theme"));
majCompteur();
```

- [ ] **Step 6: Update `styles.css`**

Supprimer ces règles devenues mortes (l'ancienne page Analyse) :
- `.grille-kpi { … }`
- `.barre-maj-kpi { margin-top: 12px; }`
- tout le bloc `/* Résultats */` (de `.resultats {` jusqu'à `.erreur p { … }` inclus) **sauf** les trois règles `.warnings`, `.warnings li`, `.erreur`, `.erreur p` qui sont conservées ;
- dans `@media (max-width: 767px)`, la ligne `.resultats { grid-template-columns: minmax(0, 1fr); }`.

Ajouter, avant `/* Squelette de chargement et apparition */` :

```css
/* Page Analyse */
.timers { display: flex; flex-wrap: wrap; gap: 12px; }
.timers[hidden] { display: none; }
.timer {
  display: inline-flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 0;
  padding: 8px 14px; min-height: 44px; border: 1px solid var(--border); border-radius: 999px; background: var(--card);
}
.timer-nom { font-weight: 700; color: var(--muted-fg); }
.timer-duree { font: 700 1.1rem var(--police-texte); font-variant-numeric: tabular-nums; min-width: 5ch; text-align: right; }
.resultats-analyse { display: grid; gap: 16px; }
.resultats-analyse:empty { display: none; }
.extrait { display: block; margin-top: 4px; font-size: .875rem; color: var(--muted-fg); white-space: pre-wrap; }
tbody th { color: var(--fg); font-size: 1rem; text-transform: none; letter-spacing: 0; }
.json-brut { border: 1px solid var(--border); border-radius: var(--rayon); background: var(--card); padding: 0 16px; }
.json-brut summary { cursor: pointer; min-height: 44px; display: flex; align-items: center; font-weight: 700; }
.json-brut pre { margin: 0 0 16px; padding: 12px; background: var(--muted); border-radius: 8px; overflow-x: auto; font-size: .8rem; }
```

- [ ] **Step 7: Run the test suite**

Run: `MOCK=on uv run pytest -q`
Expected: PASS (les 344 de départ + ceux des Tasks 1 et 2).

- [ ] **Step 8: Visual check**

Run: `MOCK=on uv run uvicorn app.main:app --port 8000` puis ouvrir `http://localhost:8000/`.
Vérifier : mode v2 par défaut ; « Exemple long » + Comparer → deux timers qui défilent, v1 finit avant v2 et sa colonne se remplit sans attendre ; ligne « Lecture complète » v1 = « non, tronqué » ; alerte « préférez la v2 » ; « JSON brut » dépliable ; à 375 px les tableaux défilent horizontalement sans scroll de page ; thèmes clair et sombre lisibles ; navigation clavier (Tab) avec focus visible. Aucune erreur dans la console du navigateur.

- [ ] **Step 9: Commit**

```bash
git add app/web/index.html app/web/analyse.js app/web/commun.js app/web/styles.css tests/integration/test_interface.py
git commit -m "feat(web): page Analyse — v1, v2 ou comparaison, timer par endpoint, tableaux"
```

---

### Task 3: Verdict du pilote et seuils exposés dans `/pilotage/resume`

**Files:**
- Modify: `ops/dashboard.py` (docstring de contrat, imports, `resume()`, nouvelle `_decision()`), `app/pilotage.py`
- Test: `tests/integration/test_dashboard.py`, `tests/integration/test_interface.py`

**Interfaces:**
- Consumes: `ops.pilotage.detecter_derive(version, mesures, seuils.derive, minimum=…) -> Derive` (`.version`, `.critique`, `.motif`, `.constats: tuple[Constat, …]`) ; `ops.pilotage.evaluer_palier(version, mesures_canary, mesures_active, seuils, *, depuis_s, pourcentage) -> DecisionPalier` (`.action ∈ {"attendre","progresser","promouvoir"}`, `.pourcentage_suivant`, `.motif`, `.constats`) ; `Constat.to_dict() -> {"signal","valeur","seuil","ok"}` ; `SeuilsPilotage.to_dict()`.
- Produces: `resume()` renvoie en plus
  - `"decision": {"action": "rollback"|"promouvoir"|"progresser"|"attendre", "version": str, "active": str | None, "motif": str, "pourcentage_suivant": int | None, "constats": [{"signal", "valeur", "seuil", "ok"}]} | None`
  - `"seuils": SeuilsPilotage.to_dict() | None`
  - `"journal"` : 20 derniers événements (au lieu de 5).
  - Schémas `ConstatResponse`, `DecisionResponse`, `SeuilsResponse` dans `app/pilotage.py`, champs `decision` et `seuils` dans `ResumePilotage`.

- [ ] **Step 1: Write the failing tests (service)**

Ajouter à la fin de `tests/integration/test_dashboard.py` (les helpers `_mesures` et `_canary` existent déjà en tête du fichier) :

```python
def _resume_apres(metriques, registry, tmp_path, entree, secondes):
    return resume(metriques, registry=registry, candidats=tmp_path / "absent.jsonl",
                  maintenant=entree["ts"] + secondes)


def test_decision_attendre_faute_de_volume(metriques, registry, tmp_path):
    entree = _canary(registry)
    _mesures(metriques, "v2.0.0", 12, score=0.9)

    d = _resume_apres(metriques, registry, tmp_path, entree, 42)["decision"]

    assert (d["action"], d["version"], d["active"]) == ("attendre", "v2.0.0", "v1.0.0")
    assert d["motif"] == "palier 10 % : 12/20 requêtes, 42/60 s"
    assert d["constats"] == [] and d["pourcentage_suivant"] is None


def test_decision_attendre_critere_non_tenu(metriques, registry, tmp_path):
    entree = _canary(registry)
    _mesures(metriques, "v2.0.0", 25, score=0.72)    # ≥ 0,70 : pas de dérive ; < 0,75 : palier non tenu

    d = _resume_apres(metriques, registry, tmp_path, entree, 120)["decision"]

    assert d["action"] == "attendre"
    assert d["motif"].startswith("critère non tenu : score_moyen")
    assert {"signal": "score_moyen", "valeur": 0.72, "seuil": 0.75, "ok": False} in d["constats"]


def test_decision_progresser(metriques, registry, tmp_path):
    entree = _canary(registry, 10)
    _mesures(metriques, "v2.0.0", 25, score=0.9)

    d = _resume_apres(metriques, registry, tmp_path, entree, 120)["decision"]

    assert (d["action"], d["pourcentage_suivant"]) == ("progresser", 50)
    assert d["constats"] and all(c["ok"] for c in d["constats"])


def test_decision_promouvoir(metriques, registry, tmp_path):
    entree = _canary(registry, 50)
    _mesures(metriques, "v2.0.0", 25, score=0.9)

    d = _resume_apres(metriques, registry, tmp_path, entree, 120)["decision"]

    assert (d["action"], d["pourcentage_suivant"]) == ("promouvoir", 100)


def test_decision_rollback_prioritaire_sur_le_palier(metriques, registry, tmp_path):
    entree = _canary(registry)
    _mesures(metriques, "v2.0.0", 25, score=0.5)     # dérive critique : score < 0,70

    d = _resume_apres(metriques, registry, tmp_path, entree, 120)["decision"]

    assert d["action"] == "rollback" and d["pourcentage_suivant"] is None
    assert any(c["signal"] == "score_moyen" and not c["ok"] for c in d["constats"])


def test_sans_canary_pas_de_verdict_mais_seuils_exposes(metriques, registry, tmp_path):
    r = resume(metriques, registry=registry, candidats=tmp_path / "absent.jsonl")

    assert r["decision"] is None
    assert r["seuils"]["derive"]["score_min"] == 0.70
    assert r["seuils"]["promotion"]["paliers"] == [10, 50, 100]
    assert r["seuils"]["motif"]
```

Et dans le test existant `test_seuils_invalides_signales_sans_bloquer`, ajouter à la fin :

```python
    assert r["seuils"] is None and r["decision"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MOCK=on uv run pytest -q tests/integration/test_dashboard.py`
Expected: FAIL — `KeyError: 'decision'`.

- [ ] **Step 3: Implement `_decision` and extend `resume()`**

Dans `ops/dashboard.py` :

1. Remplacer l'import `from ops.pilotage import debut_palier, detecter_derive, resume_metier, version_surveillee` par :

```python
from ops.pilotage import (
    Derive,
    debut_palier,
    detecter_derive,
    evaluer_palier,
    resume_metier,
    version_surveillee,
)
```

2. Ajouter, juste avant `def resume(` :

```python
def _decision(
    index: dict[str, Any],
    journal: list[dict[str, Any]],
    metriques: MetricsStore,
    seuils: SeuilsPilotage | None,
    derive: Derive | None,
    maintenant: float,
) -> dict[str, Any] | None:
    """Le verdict du pilote, avec ses propres fonctions et sur le même compte que
    ``ops.deploy.tour`` : l'écran montre ce que le pilote décidera. Une dérive
    critique du canary l'emporte sur le palier."""
    canary = index.get("canary")
    if canary is None or seuils is None:
        return None
    active = index.get("active")
    if derive is not None and derive.version == canary and derive.critique:
        return {"action": "rollback", "version": canary, "active": active, "motif": derive.motif,
                "pourcentage_suivant": None, "constats": [c.to_dict() for c in derive.constats]}
    debut = debut_palier(journal, canary)
    if debut is None:
        return None
    depuis_s = maintenant - debut
    mesures = metriques.lire(depuis_s=max(depuis_s, 0) + 1)
    d = evaluer_palier(
        canary,
        filtrer(mesures, canary, depuis_ts=debut),
        filtrer(mesures, active or "", depuis_ts=debut),
        seuils,
        depuis_s=depuis_s,
        pourcentage=int(index.get("canary_percent") or 0),
    )
    return {"action": d.action, "version": canary, "active": active, "motif": d.motif,
            "pourcentage_suivant": d.pourcentage_suivant,
            "constats": [c.to_dict() for c in d.constats]}
```

3. Dans `resume()`, juste avant `surveillee = version_surveillee(index)`, ajouter `derive: Derive | None = None`.

4. Dans `resume()`, après la ligne `candidats_n = _lu(…)`, ajouter :

```python
    decision = _lu(lambda: _decision(index, journal, metriques, seuils, derive, maintenant), None,
                   alertes, "métriques illisibles")
```

5. Remplacer le `return { … }` final de `resume()` par :

```python
    return {
        "fenetre_s": fenetre,
        "total": len(mesures),
        "par_version": _par_version(mesures),
        "palier": palier,
        "decision": decision,
        "seuils": seuils.to_dict() if seuils else None,
        "alertes": alertes,
        "candidats": candidats_n,
        "journal": journal[-20:],
    }
```

6. Dans la docstring de module (contrat), remplacer la ligne `"journal": [ …5 derniers événements de déploiement… ]` par :

```
          "decision": {"action": "rollback|promouvoir|progresser|attendre",
                       "version": …, "active": …, "motif": …,
                       "pourcentage_suivant": …, "constats": […]} | None,
          "seuils": {… ops/seuils_pilotage.yaml …} | None,
          "journal": [ …20 derniers événements de déploiement… ]
```

- [ ] **Step 4: Run service tests**

Run: `MOCK=on uv run pytest -q tests/integration/test_dashboard.py`
Expected: PASS.

- [ ] **Step 5: Write the failing API test**

Dans `tests/integration/test_interface.py`, à la fin de `test_resume_avec_canary`, ajouter :

```python
    assert corps.decision is not None
    assert (corps.decision.action, corps.decision.version) == ("attendre", "v2.0.0")
    assert corps.seuils is not None and corps.seuils.promotion["paliers"] == [10, 50, 100]
```

Run: `MOCK=on uv run pytest -q tests/integration/test_interface.py::test_resume_avec_canary`
Expected: FAIL — `AttributeError: 'ResumePilotage' object has no attribute 'decision'`.

- [ ] **Step 6: Add the schemas**

Dans `app/pilotage.py` : remplacer `from typing import Any` par `from typing import Any, Literal` (ajouter l'import s'il n'existe pas sous cette forme), puis ajouter avant `class ResumePilotage` :

```python
class ConstatResponse(BaseModel):
    signal: str
    valeur: float | None
    seuil: float
    ok: bool


class DecisionResponse(BaseModel):
    """Le verdict du pilote pour le canary en cours (lecture seule)."""

    action: Literal["rollback", "promouvoir", "progresser", "attendre"]
    version: str
    active: str | None
    motif: str
    pourcentage_suivant: int | None
    constats: list[ConstatResponse]


class SeuilsResponse(BaseModel):
    """Miroir de ``ops/seuils_pilotage.yaml``."""

    fenetre_s: float
    minimum: int
    intervalle_s: float
    derive: dict[str, float]
    promotion: dict[str, Any]
    capture: dict[str, float]
    motif: str
```

et dans `ResumePilotage`, après `palier: Palier | None` :

```python
    decision: DecisionResponse | None
    seuils: SeuilsResponse | None
```

- [ ] **Step 7: Run the full suite**

Run: `MOCK=on uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add ops/dashboard.py app/pilotage.py tests/integration/test_dashboard.py tests/integration/test_interface.py
git commit -m "feat(pilotage): verdict du pilote et seuils exposés dans /pilotage/resume"
```

---

### Task 4: Page Pilotage — verdict, canary vs active, rétroactions, seuils, traçabilité

**Files:**
- Rewrite: `app/web/pilotage.js`
- Modify: `app/web/pilotage.html` (tout le `<main>` et la `meta description`), `app/web/styles.css`
- Test: `tests/integration/test_interface.py`

**Interfaces:**
- Consumes: `/pilotage/resume` (Task 3) ; `commun.js` : `esc`, `fmtDuree`, `fmtEur`, `fmtMs`, `fmtPct`, `fmtScore`, `icone`, `initTheme`, `peindre`, `rendrePalier`, `rendreTrafic`, `surveillerResume`.
- Produces: `<section id="verdict">` (marqueur de page).

- [ ] **Step 1: Update the page marker test**

Dans `tests/integration/test_interface.py`, dans le `parametrize` de `test_pages_servies`, remplacer `("/pilotage", 'id="versions"'),` par `("/pilotage", 'id="verdict"'),` et ajouter :

```python
def test_page_pilotage_lecture_seule(client):
    page = client.get("/pilotage").text

    for zone in ('id="verdict"', 'id="comparaison"', 'id="retroactions"', 'id="seuils"', 'id="journal"'):
        assert zone in page
    assert "<form" not in page             # aucune action possible depuis la page
```

Run: `MOCK=on uv run pytest -q tests/integration/test_interface.py`
Expected: FAIL — `id="verdict"` absent.

- [ ] **Step 2: Rewrite the page markup**

Dans `app/web/pilotage.html`, remplacer la `meta description` par :

```html
  <meta name="description" content="Pilotage Mardik : faut-il promouvoir, attendre ou revenir en arrière ? Canary vs active, seuils, rétroactions, coût et traçabilité.">
```

Puis remplacer **tout** le contenu de `<main>…</main>` par :

```html
  <main>
    <div class="barre-maj">
      <h1 class="titre-page">Pilotage</h1>
      <span id="fenetre" class="chiffre"></span>
      <span id="maj" aria-live="off">en attente…</span>
      <button id="pause" class="bouton" type="button" aria-pressed="false">Pause</button>
    </div>

    <section class="carte" aria-labelledby="t-verdict">
      <h2 id="t-verdict">Verdict : promouvoir, attendre ou revenir en arrière ?</h2>
      <div id="verdict" aria-live="polite"><p class="vide">Chargement…</p></div>
      <p class="legende">Lecture seule : promotion et rollback s'exécutent via la chaîne
        (<code>python -m ops.deploy piloter</code>), jamais depuis cette page.</p>
    </section>

    <section class="carte" aria-labelledby="t-alertes">
      <h2 id="t-alertes">Alertes</h2>
      <div id="alertes" aria-live="polite"></div>
    </section>

    <section class="carte" aria-labelledby="t-comparaison">
      <h2 id="t-comparaison">Canary vs active : est-elle plus efficace ?</h2>
      <div id="comparaison"></div>
    </section>

    <section class="carte" aria-labelledby="t-retroactions">
      <h2 id="t-retroactions">Les 3 rétroactions</h2>
      <div id="retroactions"></div>
      <details id="seuils" class="json-brut"><summary>Seuils en vigueur</summary><div id="seuils-contenu"></div></details>
    </section>

    <section class="carte" aria-labelledby="t-journal">
      <h2 id="t-journal">Traçabilité</h2>
      <div id="filtres" class="modes" role="group" aria-label="Filtrer le journal">
        <button class="bouton bouton-fantome" type="button" data-filtre="tous" aria-pressed="true">Tous</button>
        <button class="bouton bouton-fantome" type="button" data-filtre="deploiement" aria-pressed="false">Déploiement</button>
        <button class="bouton bouton-fantome" type="button" data-filtre="rollback" aria-pressed="false">Rollback</button>
        <button class="bouton bouton-fantome" type="button" data-filtre="seuils" aria-pressed="false">Seuils</button>
      </div>
      <div class="table-defilante">
        <table>
          <thead><tr><th scope="col">Date</th><th scope="col">Événement</th><th scope="col">Origine</th><th scope="col">Résumé</th></tr></thead>
          <tbody id="journal"></tbody>
        </table>
      </div>
    </section>
  </main>
```

- [ ] **Step 3: Rewrite `pilotage.js`**

Remplacer tout `app/web/pilotage.js` par :

```js
import {
  esc, fmtDuree, fmtEur, fmtMs, fmtPct, fmtScore, icone, initTheme, peindre, rendrePalier,
  rendreTrafic, surveillerResume,
} from "/static/commun.js";

const $ = (s) => document.querySelector(s);
const BADGES = { promotion: "ok", canary: "info", rollback: "danger", alerte: "warn", seuils: "neutre" };
const FILTRES = {
  tous: null,
  deploiement: ["canary", "promotion", "publication", "installation"],
  rollback: ["rollback"],
  seuils: ["seuils"],
};
const VERDICTS = {
  promouvoir: { classe: "ok", icone: "ok", libelle: "Promouvoir" },
  progresser: { classe: "ok", icone: "ok", libelle: "Progresser" },
  attendre: { classe: "warn", icone: "pause", libelle: "Attendre" },
  rollback: { classe: "danger", icone: "danger", libelle: "Revenir en arrière" },
};
const pctErreur = (v) => fmtPct(v == null ? null : v * 100);
const SIGNAUX = {
  score_moyen: { libelle: "Score moyen", sens: "≥", fmt: fmtScore, delta: (d) => fmtScore(d) },
  score_p10: { libelle: "Score p10", sens: "≥", fmt: fmtScore, delta: (d) => fmtScore(d) },
  taux_erreur: { libelle: "Taux d'erreur", sens: "≤", fmt: pctErreur, delta: (d) => `${fmtScore(d * 100)} pt` },
  latence_p95_ms: { libelle: "Latence P95", sens: "≤", fmt: fmtMs, delta: (d) => fmtMs(d) },
};
const ORDRE = ["score_moyen", "score_p10", "taux_erreur", "latence_p95_ms"];

let filtre = "tous";
let dernier = null;

// --- Verdict ----------------------------------------------------------------
function rendreVerdict(r) {
  const d = r.decision;
  if (!d) {
    return `<p class="verdict-titre statut neutre">${icone("ok")}Aucun canary en cours</p>
      <p>La version active reçoit tout le trafic : rien à décider.</p>${rendreTrafic(r.par_version)}`;
  }
  const v = VERDICTS[d.action];
  const suite = d.action === "progresser" ? ` vers ${esc(d.pourcentage_suivant)} %` : "";
  return `<p class="verdict-titre statut ${v.classe}">${icone(v.icone)}${v.libelle}${suite}</p>
    <p>${esc(d.motif)}</p>
    ${rendreTrafic(r.par_version)}
    ${d.action === "attendre" && !d.constats.length ? rendrePalier(r.palier) : ""}`;
}

// --- Canary vs active -------------------------------------------------------
function bullet(signal, c, active) {
  const echelle = Math.max(c.seuil, c.valeur ?? 0, active ?? 0) * 1.15 || 1;
  const pos = (x) => (x == null ? 0 : Math.min(100, (x / echelle) * 100)).toFixed(2);
  const s = SIGNAUX[signal];
  return `<svg class="bullet-svg" viewBox="0 0 100 12" preserveAspectRatio="none" role="img"
      aria-label="${esc(s.libelle)} du canary : ${esc(s.fmt(c.valeur))}, seuil ${s.sens} ${esc(s.fmt(c.seuil))}">
    <rect class="bullet-fond" x="0" y="2" width="100" height="8" rx="2"/>
    <rect class="bullet-barre ${c.ok ? "v2" : "ko"}" x="0" y="3.5" width="${pos(c.valeur)}" height="5" rx="1.5"/>
    <line class="bullet-cible" x1="${pos(c.seuil)}" x2="${pos(c.seuil)}" y1="0" y2="12" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

function ecart(signal, canary, active) {
  if (canary == null || active == null) return "—";
  const d = canary - active;
  return `${d > 0 ? "+" : ""}${SIGNAUX[signal].delta(d)}`;
}

const coutParAnalyse = (s) => (s && s.requetes ? s.cout_total_eur / s.requetes : null);

function rendreComparaison(r) {
  const d = r.decision;
  if (!d) return '<p class="vide">Aucun canary en cours : rien à comparer.</p>';
  const statsCanary = r.par_version[d.version];
  const statsActive = d.active ? r.par_version[d.active] : null;
  const constats = Object.fromEntries(d.constats.map((c) => [c.signal, c]));
  const lignes = ORDRE.map((signal) => {
    const s = SIGNAUX[signal];
    const c = constats[signal];
    const valeurCanary = c ? c.valeur : statsCanary?.[signal] ?? null;
    const valeurActive = statsActive?.[signal] ?? null;
    const etat = !c
      ? '<span class="statut neutre">en attente</span>'
      : c.ok ? `<span class="statut ok">${icone("ok")}tenu</span>`
        : `<span class="statut danger">${icone("danger")}non tenu</span>`;
    return `<tr>
      <th scope="row">${s.libelle}</th>
      <td class="chiffre">${esc(s.fmt(valeurActive))}</td>
      <td class="chiffre">${esc(s.fmt(valeurCanary))}</td>
      <td>${c ? bullet(signal, c, valeurActive) : ""}</td>
      <td class="chiffre">${c ? `${s.sens} ${esc(s.fmt(c.seuil))}` : "—"}</td>
      <td class="chiffre">${esc(ecart(signal, valeurCanary, valeurActive))}</td>
      <td>${etat}</td>
    </tr>`;
  });
  const cCanary = coutParAnalyse(statsCanary);
  const cActive = coutParAnalyse(statsActive);
  const ratio = cCanary != null && cActive ? `×${fmtScore(cCanary / cActive)}` : "—";
  lignes.push(`<tr>
    <th scope="row">Coût / analyse</th>
    <td class="chiffre">${esc(fmtEur(cActive))}</td><td class="chiffre">${esc(fmtEur(cCanary))}</td>
    <td></td><td>—</td><td class="chiffre">${ratio}</td>
    <td><span class="statut neutre">info, hors verdict</span></td>
  </tr>`);
  return `<div class="table-defilante"><table>
    <thead><tr><th scope="col">Signal</th><th scope="col">Active ${esc(d.active ?? "—")}</th>
      <th scope="col">Canary ${esc(d.version)}</th><th scope="col">Canary vs seuil</th>
      <th scope="col">Seuil</th><th scope="col">Écart</th><th scope="col">État</th></tr></thead>
    <tbody>${lignes.join("")}</tbody></table></div>
    <p class="legende">Trait vertical : le seuil. Coût total canary ${esc(fmtEur(statsCanary?.cout_total_eur))},
      active ${esc(fmtEur(statsActive?.cout_total_eur))} sur la fenêtre.</p>`;
}

// --- Rétroactions et seuils -------------------------------------------------
function derniere(journal, evenements) {
  const e = journal.slice().reverse().find((x) => evenements.includes(x.evenement));
  if (!e?.date) return "aucune dans les 20 derniers événements";
  const d = new Date(e.date);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("fr-FR");
}

function rendreRetroactions(r) {
  const s = r.seuils;
  if (!s) return `<p class="indispo">${icone("warn")}Seuils invalides : voir les alertes.</p>`;
  const lignes = [
    [`${icone("danger")}Rollback automatique`,
      `score &lt; ${fmtScore(s.derive.score_min)} · erreurs &gt; ${pctErreur(s.derive.taux_erreur_max)} · P95 &gt; ${fmtMs(s.derive.latence_p95_max_ms)}`,
      `dernier : ${esc(derniere(r.journal, ["rollback"]))}`],
    [`${icone("ok")}Promotion par paliers`,
      `paliers ${s.promotion.paliers.map(esc).join(" → ")} % · ${esc(s.promotion.requetes_min)} requêtes et ${fmtDuree(s.promotion.duree_min_s)} minimum`,
      `dernière : ${esc(derniere(r.journal, ["canary", "promotion"]))}`],
    [`${icone("warn")}Capture vers le golden dataset`,
      `confiance &lt; ${fmtScore(s.capture.score_max)} → candidat, validé par un juriste`,
      `${esc(r.candidats)} candidat(s) en attente`],
  ];
  return `<div class="table-defilante"><table>
    <thead><tr><th scope="col">Rétroaction</th><th scope="col">Déclencheur</th><th scope="col">État</th></tr></thead>
    <tbody>${lignes.map(([nom, regle, etat]) => `<tr><th scope="row">${nom}</th><td>${regle}</td><td>${etat}</td></tr>`).join("")}</tbody>
  </table></div>`;
}

function rendreSeuils(s) {
  if (!s) return '<p class="vide">Seuils indisponibles.</p>';
  const lignes = [];
  for (const section of ["derive", "promotion", "capture"]) {
    for (const [cle, valeur] of Object.entries(s[section])) {
      lignes.push(`<tr><td>${section}</td><td><code>${esc(cle)}</code></td><td class="chiffre">${esc(Array.isArray(valeur) ? valeur.join(", ") : valeur)}</td></tr>`);
    }
  }
  return `<p>Motif : ${esc(s.motif)}</p><div class="table-defilante"><table>
    <thead><tr><th scope="col">Section</th><th scope="col">Clé</th><th scope="col">Valeur</th></tr></thead>
    <tbody>${lignes.join("")}</tbody></table></div>`;
}

// --- Traçabilité ------------------------------------------------------------
function ligneJournal(e) {
  const date = (() => {
    if (!e.date) return "—";
    const d = new Date(e.date);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("fr-FR");
  })();
  const badge = BADGES[e.evenement] ?? "neutre";
  const origine = e.origine === "auto" ? "automatique" : e.origine ? "humaine" : "—";
  return `<tr>
    <td class="chiffre">${esc(date)}</td>
    <td><span class="badge ${badge}">${esc(e.evenement ?? "—")}</span></td>
    <td><span class="badge neutre">${esc(origine)}</span></td>
    <td>${esc(e.resume ?? e.motif ?? "")}</td>
  </tr>`;
}

function rendreJournal(journal) {
  const types = FILTRES[filtre];
  const lignes = journal.filter((e) => !types || types.includes(e.evenement)).reverse();
  peindre($("#journal"), lignes.length
    ? lignes.map(ligneJournal).join("")
    : '<tr><td colspan="4" class="vide">Aucun événement pour ce filtre.</td></tr>');
}

document.querySelectorAll("[data-filtre]").forEach((bouton) => {
  bouton.addEventListener("click", () => {
    filtre = bouton.dataset.filtre;
    document.querySelectorAll("[data-filtre]").forEach((b) => b.setAttribute("aria-pressed", String(b === bouton)));
    if (dernier) rendreJournal(dernier.journal);
  });
});

// --- Boucle -----------------------------------------------------------------
function rendre(r) {
  dernier = r;
  $("#fenetre").textContent = `fenêtre ${fmtDuree(r.fenetre_s)} · ${r.total} requête(s)`;
  peindre($("#verdict"), rendreVerdict(r));
  peindre($("#alertes"), r.alertes.length
    ? `<ul class="alertes">${r.alertes.map((a) => `<li>${icone("danger")}${esc(a)}</li>`).join("")}</ul>`
    : `<p class="statut ok">${icone("ok")}Aucune alerte</p>`);
  peindre($("#comparaison"), rendreComparaison(r));
  peindre($("#retroactions"), rendreRetroactions(r));
  peindre($("#seuils-contenu"), rendreSeuils(r.seuils));
  rendreJournal(r.journal);
}

function indisponible() {
  peindre($("#verdict"), `<p class="indispo">${icone("warn")}Résumé de pilotage indisponible — nouvel essai dans 5 s.</p>`);
}

$("#logo").innerHTML = icone("scale");
initTheme($("#theme"));
surveillerResume({ onData: rendre, onErreur: indisponible, boutonPause: $("#pause"), horodatage: $("#maj") });
```

- [ ] **Step 4: Update `styles.css`**

Supprimer les règles devenues mortes du bloc `/* Page Pilotage */` : `.grille-2`, `.compteur`, `.versions`, `.version`, `.version.v1`, `.stats`, `.stats dt`, `.stats dd`, `.stats small`, `.mini-graphes`, `.mini-graphes figure`, `.mini-graphes figcaption`, `.mini`, `.mini rect`, `.mini polyline` ; et dans le sélecteur `.chiffre, .bullet-valeur, .stats dd`, retirer `, .stats dd`. Supprimer le bloc `/* Bullet chart P95 vs SLO */` (`.bullet`, `.bullet > svg`, `.bullet-valeur`) **sauf** `.bullet-fond`, `.bullet-barre.v1`, `.bullet-barre.v2`, `.bullet-cible` ; et dans `@media (max-width: 767px)` les deux lignes `.bullet { … }` et `.bullet .statut { … }`.

Ajouter à la fin du bloc `/* Page Pilotage */` :

```css
.verdict-titre { font: 700 1.5rem var(--police-titre); margin: 0 0 8px; }
.statut.neutre { color: var(--muted-fg); }
.bullet-svg { width: 100%; min-width: 120px; height: 18px; display: block; }
.bullet-barre.ko { fill: var(--danger); }
#filtres { margin-bottom: 12px; }
#filtres .bouton[aria-pressed="true"] { background: var(--muted); color: var(--primary); font-weight: 700; }
```

- [ ] **Step 5: Run the full suite**

Run: `MOCK=on uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Visual check**

Isoler l'état pour ne pas modifier le registre versionné `ops/registry` : `export REGISTRY_PATH=$(mktemp -d)/registry METRICS_PATH=$(mktemp -d)/metrics.jsonl MOCK=on`, puis `uv run python -m ops.deploy publier` (étiquette la v2 si le gate passe en MOCK), `uv run python -m ops.deploy canary <version publiée> --pourcentage 10`, `uv run uvicorn app.main:app --port 8000` et, dans un autre terminal avec les mêmes variables, `make traffic`. Ouvrir `http://localhost:8000/pilotage`.
Vérifier : sans canary → « Aucun canary en cours » ; avec canary → verdict « Attendre » + jauges ; tableau canary vs active avec bullets et écarts ; ligne coût « info, hors verdict » ; rétroactions avec dernière occurrence ; « Seuils en vigueur » dépliable avec motif ; filtres du journal (aria-pressed) ; aucun bouton d'action ; 375 px sans scroll de page ; thèmes clair/sombre.

- [ ] **Step 7: Commit**

```bash
git add app/web/pilotage.html app/web/pilotage.js app/web/styles.css tests/integration/test_interface.py
git commit -m "feat(web): page Pilotage — verdict, canary vs active, rétroactions, seuils, traçabilité"
```

---

### Task 5: Sentry optionnel branché sur les spans OpenTelemetry existants

**Files:**
- Create: `app/config.py`, `app/sentry.py`, `tests/unit/test_sentry.py`
- Modify: `pyproject.toml`, `.env.example`, `app/telemetry.py` (dataclass `Telemetry`, `build_telemetry`), `app/main.py` (`create_app`), `tests/conftest.py` (fixture d'environnement autouse)

**Interfaces:**
- Produces:
  - `app.config.SentrySettings` (pydantic-settings, préfixe `SENTRY_`) : `dsn: str | None = None`, `environment: str = "local"`, `traces_sample_rate: float = 1.0`, `ui_url: str | None = None`.
  - `app.sentry.init_sentry(settings: SentrySettings, provider: TracerProvider | None) -> bool` — `True` si Sentry est actif.
  - `app.sentry.filtrer_evenement(event: dict, hint: dict) -> dict` — retire `request.data`, recopie `mardik.version` / `mardik.model_version` des attributs OTel en tags.
  - `Telemetry.provider: TracerProvider | None` (nouveau champ, défaut `None`).

- [ ] **Step 1: Add dependencies**

Dans `pyproject.toml`, ajouter à `dependencies` (après `"python-dotenv>=1,<2",`) :

```toml
    "pydantic-settings>=2.4,<3",
    "sentry-sdk[fastapi,opentelemetry]>=2.40,<3",
```

Run: `uv sync`
Expected: installation de `sentry-sdk` (≥ 2.40, vérifié avec 2.70.0) et `pydantic-settings`.

- [ ] **Step 2: Neutralise any local DSN in tests**

Dans `tests/conftest.py`, dans la fixture autouse qui fait les `monkeypatch.setenv(...)` (celle qui pose `OTEL_TRACES=off`), ajouter :

```python
    monkeypatch.delenv("SENTRY_DSN", raising=False)
```

- [ ] **Step 3: Write the failing tests**

Créer `tests/unit/test_sentry.py` :

```python
"""Sentry : optionnel, branché sur les spans OTel existants, sans texte de contrat."""
from __future__ import annotations

import pytest
import sentry_sdk
from opentelemetry.sdk.trace import TracerProvider
from sentry_sdk.transport import Transport

from app.config import SentrySettings
from app.sentry import filtrer_evenement, init_sentry

DSN_FACTICE = "https://cle@o0.ingest.sentry.io/1"


class TransportCapture(Transport):
    """Garde les envois en mémoire : aucun appel réseau."""

    def __init__(self, options=None):
        super().__init__(options)
        self.recus: list[dict] = []

    def capture_envelope(self, envelope):
        self.recus.extend(item.payload.json for item in envelope.items if item.payload.json)


@pytest.fixture(autouse=True)
def sentry_inactif_apres():
    yield
    sentry_sdk.init()          # client sans DSN : inactif pour les tests suivants


def test_sans_dsn_rien_n_est_initialise():
    assert init_sentry(SentrySettings(), TracerProvider()) is False
    assert not sentry_sdk.get_client().is_active()


def test_filtre_retire_le_texte_du_contrat_et_etiquette_la_version():
    event = {
        "request": {"url": "http://mardik/v2/analyse", "data": {"texte": "CONTRAT CONFIDENTIEL"}},
        "contexts": {"otel": {"attributes": {"mardik.version": "v2", "mardik.model_version": "v2.0.3"}}},
    }

    sortie = filtrer_evenement(event, {})

    assert "data" not in sortie["request"]
    assert "CONTRAT CONFIDENTIEL" not in repr(sortie)
    assert sortie["tags"] == {"mardik.version": "v2", "mardik.model_version": "v2.0.3"}


def test_spans_otel_envoyes_comme_transaction(monkeypatch):
    transports: list[TransportCapture] = []
    init_reel = sentry_sdk.init

    def init_capture(**options):
        transports.append(TransportCapture(options))
        return init_reel(transport=transports[-1], **options)

    monkeypatch.setattr(sentry_sdk, "init", init_capture)
    provider = TracerProvider()

    assert init_sentry(SentrySettings(dsn=DSN_FACTICE, environment="test"), provider) is True
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("analyse.requete") as span:
        span.set_attribute("mardik.version", "v2")
        with tracer.start_as_current_span("llm.appel"):
            pass
    sentry_sdk.flush()

    (transaction,) = [e for e in transports[0].recus if e.get("type") == "transaction"]
    assert transaction["transaction"] == "analyse.requete"
    assert transaction["environment"] == "test"
    assert transaction["tags"]["mardik.version"] == "v2"
    assert any("llm.appel" in (s.get("description"), s.get("op")) for s in transaction["spans"])
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `MOCK=on uv run pytest -q tests/unit/test_sentry.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 5: Implement the settings**

Créer `app/config.py` :

```python
"""Réglages lus dans l'environnement (pydantic-settings).

``app/__init__.py`` charge déjà ``.env`` dans l'environnement : les réglages
le voient sans ``env_file`` (et les tests les neutralisent avec ``monkeypatch``).
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class SentrySettings(BaseSettings):
    """Sentry est optionnel : sans ``SENTRY_DSN``, rien n'est initialisé."""

    model_config = SettingsConfigDict(env_prefix="SENTRY_", extra="ignore")

    dsn: str | None = None
    environment: str = "local"
    traces_sample_rate: float = 1.0
    ui_url: str | None = None   # ex. https://<organisation>.sentry.io — liens de la page Observabilité
```

- [ ] **Step 6: Implement the Sentry module**

Créer `app/sentry.py` :

```python
"""Sentry — backend des traces OpenTelemetry et des erreurs, optionnel.

Sans DSN : rien n'est initialisé, les spans restent sur la console.
Avec DSN : les spans existants (``analyse.requete`` → ``llm.appel`` …) deviennent
des transactions Sentry via ``SentrySpanProcessor`` (``instrumenter="otel"``) ;
aucune instrumentation n'est réécrite.

Confidentialité : le texte d'un contrat ne quitte jamais le serveur
(``send_default_pii=False`` et ``filtrer_evenement``).
"""
from __future__ import annotations

from typing import Any

import sentry_sdk
from opentelemetry.sdk.trace import TracerProvider
from sentry_sdk.integrations.opentelemetry import SentrySpanProcessor

from app.config import SentrySettings

ATTRIBUTS_EN_TAGS = ("mardik.version", "mardik.model_version")
_providers_branches: set[int] = set()


def filtrer_evenement(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Retire le corps des requêtes (le texte du contrat) et recopie la version
    portée par le span en tag, pour filtrer les traces par version dans Sentry."""
    requete = event.get("request")
    if isinstance(requete, dict):
        requete.pop("data", None)
    attributs = ((event.get("contexts") or {}).get("otel") or {}).get("attributes") or {}
    tags = event.setdefault("tags", {})
    if isinstance(tags, dict):
        for cle in ATTRIBUTS_EN_TAGS:
            if cle in attributs:
                tags[cle] = attributs[cle]
    return event


def init_sentry(settings: SentrySettings, provider: TracerProvider | None) -> bool:
    """Initialise Sentry si un DSN est configuré ; renvoie ``True`` s'il est actif."""
    if not settings.dsn:
        return False
    sentry_sdk.init(
        dsn=settings.dsn,
        environment=settings.environment,
        traces_sample_rate=settings.traces_sample_rate,
        instrumenter="otel",
        send_default_pii=False,
        before_send=filtrer_evenement,
        before_send_transaction=filtrer_evenement,
    )
    if provider is not None and id(provider) not in _providers_branches:
        provider.add_span_processor(SentrySpanProcessor())
        _providers_branches.add(id(provider))
    return True
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `MOCK=on uv run pytest -q tests/unit/test_sentry.py`
Expected: PASS (3 tests).

- [ ] **Step 8: Expose the provider and wire Sentry at startup**

Dans `app/telemetry.py` :
- ajouter `provider: TracerProvider | None = None` comme **dernier** champ de la dataclass `Telemetry` ;
- dans `build_telemetry`, passer `provider=provider` au constructeur `Telemetry(...)`.

Dans `app/main.py` :
- ajouter les imports `from app.config import SentrySettings` et `from app.sentry import init_sentry` ;
- remplacer la ligne `    build_default_telemetry()` de `create_app` par :

```python
    telemetry = build_default_telemetry()
    init_sentry(SentrySettings(), telemetry.provider)
```

Dans `.env.example`, ajouter à la fin :

```bash
# --- Sentry (optionnel) : sans SENTRY_DSN, traces et logs restent sur la console ---
# SENTRY_DSN=https://<cle>@<id>.ingest.sentry.io/<projet>
# SENTRY_ENVIRONMENT=local
# SENTRY_TRACES_SAMPLE_RATE=1.0
# SENTRY_UI_URL=https://<organisation>.sentry.io
```

- [ ] **Step 9: Run the full suite and lint**

Run: `MOCK=on uv run pytest -q && uv run ruff check .`
Expected: PASS, aucune erreur ruff.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml .env.example app/config.py app/sentry.py app/telemetry.py app/main.py tests/conftest.py tests/unit/test_sentry.py
git commit -m "feat(observabilite): Sentry optionnel sur les spans OTel, sans texte de contrat"
```

---

### Task 6: Résumé d'observabilité — `GET /observabilite/resume`

**Files:**
- Create: `ops/observabilite.py`, `app/observabilite.py`, `tests/integration/test_observabilite.py`
- Modify: `app/main.py` (import + `include_router`)

**Interfaces:**
- Consumes: `MetricsStore.lire(depuis_s)` → `list[Mesure]` (`ts, version, route, latence_ms, erreur, score, cout_eur, appels_llm, tokens, tronque`) ; `ops.signaux.agreger(mesures) -> Agregat` ; `ops.seuils.charger_seuils_pilotage()` ; `SentrySettings` (Task 5).
- Produces:
  - `ops.observabilite.resume_observabilite(metriques: MetricsStore | None = None, *, settings: SentrySettings | None = None, fenetre_s: float | None = None) -> dict` avec les clés `fenetre_s`, `par_route`, `erreurs_recentes`, `sentry`.
  - `ops.observabilite.liens_sentry(settings) -> dict[str, str] | None` (`issues`, `traces`, `performance`).
  - `app.observabilite.router` (préfixe `/observabilite`), dépendance `get_resume_observabilite`, schéma `ResumeObservabilite`.

- [ ] **Step 1: Write the failing tests**

Créer `tests/integration/test_observabilite.py` :

```python
"""Observabilité : agrégats par route, erreurs récentes, état Sentry."""
from __future__ import annotations

import time

from app.config import SentrySettings
from app.observabilite import ResumeObservabilite
from app.telemetry import Mesure
from ops.observabilite import liens_sentry, resume_observabilite


def test_resume_par_route_et_erreurs_recentes(metriques):
    maintenant = time.time()
    for i in range(3):
        metriques.enregistrer(Mesure(ts=maintenant, version="v1.0.0", route="/v1/analyse",
                                     latence_ms=1000.0 + i, appels_llm=1, tokens=4800,
                                     tronque=True, cout_eur=0.01))
    for i in range(12):
        metriques.enregistrer(Mesure(ts=maintenant + i, version="v2.0.0", route="/v2/analyse",
                                     latence_ms=5000.0, erreur=True))

    r = resume_observabilite(metriques, settings=SentrySettings(), fenetre_s=300)

    v1 = next(s for s in r["par_route"] if s["route"] == "/v1/analyse")
    assert (v1["version"], v1["requetes"], v1["taux_erreur"]) == ("v1.0.0", 3, 0.0)
    assert (v1["appels_llm_moyen"], v1["tokens_moyen"], v1["taux_tronque"]) == (1.0, 4800.0, 1.0)
    assert v1["latence_p50_ms"] == 1001.0 and v1["cout_total_eur"] == 0.03
    v2 = next(s for s in r["par_route"] if s["route"] == "/v2/analyse")
    assert v2["taux_erreur"] == 1.0 and v2["latence_p95_ms"] is None
    assert len(r["erreurs_recentes"]) == 10
    assert r["erreurs_recentes"][0]["route"] == "/v2/analyse"
    assert r["sentry"] == {"actif": False, "environnement": "local", "liens": None}


def test_liens_sentry_seulement_si_actif_et_url_connue():
    actif = SentrySettings(dsn="https://cle@o0.ingest.sentry.io/1",
                           ui_url="https://mardik.sentry.io/", environment="production")

    assert liens_sentry(actif) == {
        "issues": "https://mardik.sentry.io/issues/?environment=production",
        "traces": "https://mardik.sentry.io/traces/?environment=production",
        "performance": "https://mardik.sentry.io/insights/backend/?environment=production",
    }
    assert liens_sentry(SentrySettings(ui_url="https://mardik.sentry.io")) is None
    assert liens_sentry(SentrySettings(dsn="https://cle@o0.ingest.sentry.io/1")) is None


def test_endpoint_resume_observabilite(client):
    r = client.get("/observabilite/resume")

    assert r.status_code == 200
    corps = ResumeObservabilite.model_validate(r.json())
    assert corps.par_route == [] and corps.erreurs_recentes == []
    assert corps.sentry.actif is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MOCK=on uv run pytest -q tests/integration/test_observabilite.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observabilite'`.

- [ ] **Step 3: Implement the service**

Créer `ops/observabilite.py` :

```python
"""Observabilité locale : agrégats par route depuis ``ops/metrics.jsonl``.

Les traces et les erreurs détaillées sont dans Sentry ; ici, les chiffres qui
disent où regarder. Même fenêtre que le pilote (``ops/seuils_pilotage.yaml``).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from app.config import SentrySettings
from app.telemetry import Mesure, MetricsStore
from ops.seuils import ErreurSeuilsPilotage, charger_seuils_pilotage
from ops.signaux import agreger

NB_ERREURS_RECENTES = 10
FENETRE_DEFAUT_S = 300


def _stats_route(route: str, version: str, mesures: Sequence[Mesure]) -> dict[str, Any]:
    a = agreger(mesures)
    n = len(mesures)
    return {
        "route": route,
        "version": version,
        "requetes": a.requetes,
        "taux_erreur": a.taux_erreur,
        "latence_p50_ms": a.latence_p50_ms,
        "latence_p95_ms": a.latence_p95_ms,
        "appels_llm_moyen": round(sum(m.appels_llm for m in mesures) / n, 2),
        "tokens_moyen": round(sum(m.tokens for m in mesures) / n, 1),
        "taux_tronque": round(sum(1 for m in mesures if m.tronque) / n, 4),
        "cout_total_eur": a.cout_total_eur,
    }


def liens_sentry(settings: SentrySettings) -> dict[str, str] | None:
    """Liens profonds vers l'organisation Sentry, filtrés par environnement."""
    if not settings.dsn or not settings.ui_url:
        return None
    base = settings.ui_url.rstrip("/")
    env = quote(settings.environment)
    return {
        "issues": f"{base}/issues/?environment={env}",
        "traces": f"{base}/traces/?environment={env}",
        "performance": f"{base}/insights/backend/?environment={env}",
    }


def resume_observabilite(
    metriques: MetricsStore | None = None,
    *,
    settings: SentrySettings | None = None,
    fenetre_s: float | None = None,
) -> dict[str, Any]:
    metriques = metriques or MetricsStore()
    settings = settings or SentrySettings()
    if fenetre_s is None:
        try:
            fenetre_s = charger_seuils_pilotage().fenetre_s
        except ErreurSeuilsPilotage:
            fenetre_s = FENETRE_DEFAUT_S
    mesures = metriques.lire(depuis_s=fenetre_s)
    groupes: dict[tuple[str, str], list[Mesure]] = {}
    for m in mesures:
        groupes.setdefault((m.route, m.version), []).append(m)
    erreurs = sorted((m for m in mesures if m.erreur), key=lambda m: m.ts, reverse=True)
    return {
        "fenetre_s": fenetre_s,
        "par_route": [_stats_route(route, version, du) for (route, version), du in sorted(groupes.items())],
        "erreurs_recentes": [
            {
                "date": datetime.fromtimestamp(m.ts, timezone.utc).isoformat(timespec="seconds"),
                "route": m.route,
                "version": m.version,
                "latence_ms": round(m.latence_ms, 1),
            }
            for m in erreurs[:NB_ERREURS_RECENTES]
        ],
        "sentry": {
            "actif": bool(settings.dsn),
            "environnement": settings.environment,
            "liens": liens_sentry(settings),
        },
    }
```

- [ ] **Step 4: Implement the router and schemas**

Créer `app/observabilite.py` :

```python
"""``GET /observabilite/resume`` : métriques par route, erreurs récentes, état Sentry."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ops.observabilite import resume_observabilite

router = APIRouter(prefix="/observabilite", tags=["observabilite"])


class StatsRoute(BaseModel):
    route: str
    version: str
    requetes: int
    taux_erreur: float
    latence_p50_ms: float | None
    latence_p95_ms: float | None
    appels_llm_moyen: float
    tokens_moyen: float
    taux_tronque: float
    cout_total_eur: float


class ErreurRecente(BaseModel):
    date: str
    route: str
    version: str
    latence_ms: float


class LiensSentry(BaseModel):
    issues: str
    traces: str
    performance: str


class EtatSentry(BaseModel):
    actif: bool
    environnement: str
    liens: LiensSentry | None


class ResumeObservabilite(BaseModel):
    fenetre_s: float
    par_route: list[StatsRoute]
    erreurs_recentes: list[ErreurRecente]
    sentry: EtatSentry


def get_resume_observabilite() -> dict[str, Any]:
    return resume_observabilite()


@router.get("/resume", response_model=ResumeObservabilite)
def resume(r: dict[str, Any] = Depends(get_resume_observabilite)) -> ResumeObservabilite:
    return ResumeObservabilite.model_validate(r)
```

Dans `app/main.py` : remplacer `from app import api_v1, api_v2, gateway, pilotage` par `from app import api_v1, api_v2, gateway, observabilite, pilotage` et ajouter `    app.include_router(observabilite.router)` après `app.include_router(pilotage.router)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `MOCK=on uv run pytest -q tests/integration/test_observabilite.py`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full suite and lint**

Run: `MOCK=on uv run pytest -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ops/observabilite.py app/observabilite.py app/main.py tests/integration/test_observabilite.py
git commit -m "feat(observabilite): résumé par route, erreurs récentes et état Sentry"
```

---

### Task 7: Page Observabilité

**Files:**
- Modify: `app/web/observabilite.html` (tout le `<main>`), `app/web/commun.js` (`surveillerResume`), `app/web/styles.css`
- Rewrite: `app/web/observabilite.js`
- Test: `tests/integration/test_interface.py`

**Interfaces:**
- Consumes: `GET /observabilite/resume` (Task 6).
- Produces: `surveillerResume({ url = "/pilotage/resume", onData, onErreur, boutonPause, horodatage })` — paramètre `url` ajouté, défaut inchangé pour Pilotage.

- [ ] **Step 1: Write the failing test**

Ajouter dans `tests/integration/test_interface.py` :

```python
def test_page_observabilite_zones(client):
    page = client.get("/observabilite").text

    for zone in ('id="etat-sentry"', 'id="routes"', 'id="erreurs"', 'id="liens"'):
        assert zone in page
```

Run: `MOCK=on uv run pytest -q tests/integration/test_interface.py::test_page_observabilite_zones`
Expected: FAIL — `id="etat-sentry"` absent.

- [ ] **Step 2: Make the polling URL configurable**

Dans `app/web/commun.js`, remplacer la signature et le `fetch` de `surveillerResume` :

```js
export function surveillerResume({ url = "/pilotage/resume", onData, onErreur, boutonPause, horodatage }) {
```

```js
      const r = await fetch(url, { headers: { Accept: "application/json" } });
```

- [ ] **Step 3: Write the page markup**

Dans `app/web/observabilite.html`, remplacer **tout** le contenu de `<main id="observabilite">…</main>` par :

```html
  <main id="observabilite">
    <div class="barre-maj">
      <h1 class="titre-page">Observabilité</h1>
      <span id="fenetre" class="chiffre"></span>
      <span id="maj" aria-live="off">en attente…</span>
      <button id="pause" class="bouton" type="button" aria-pressed="false">Pause</button>
    </div>

    <div id="etat-sentry" class="carte" aria-live="polite"><p class="vide">Chargement…</p></div>

    <section class="carte" aria-labelledby="t-routes">
      <h2 id="t-routes">Par route</h2>
      <div class="table-defilante">
        <table>
          <thead><tr>
            <th scope="col">Route</th><th scope="col">Version</th><th scope="col">Requêtes</th>
            <th scope="col">Erreurs</th><th scope="col">P50</th><th scope="col">P95</th>
            <th scope="col">Appels LLM</th><th scope="col">Tokens</th><th scope="col">Tronqués</th>
            <th scope="col">Coût</th>
          </tr></thead>
          <tbody id="routes"></tbody>
        </table>
      </div>
    </section>

    <section class="carte" aria-labelledby="t-erreurs">
      <h2 id="t-erreurs">Dernières erreurs</h2>
      <div class="table-defilante">
        <table>
          <thead><tr><th scope="col">Date</th><th scope="col">Route</th><th scope="col">Version</th><th scope="col">Durée</th></tr></thead>
          <tbody id="erreurs"></tbody>
        </table>
      </div>
    </section>

    <section class="carte" aria-labelledby="t-sentry">
      <h2 id="t-sentry">Traces et logs dans Sentry</h2>
      <div id="liens" class="liens-sentry"></div>
    </section>
  </main>
```

- [ ] **Step 4: Rewrite `observabilite.js`**

Remplacer tout `app/web/observabilite.js` par :

```js
import { esc, fmtDuree, fmtEur, fmtMs, fmtPct, icone, initTheme, peindre, surveillerResume } from "/static/commun.js";

const $ = (s) => document.querySelector(s);
const nombre = (n, d = 0) => new Intl.NumberFormat("fr-FR", { maximumFractionDigits: d }).format(n);
const LIENS = [["traces", "Traces"], ["issues", "Erreurs (issues)"], ["performance", "Performance"]];

function etatSentry(s) {
  if (s.actif) return `<p class="statut ok">${icone("ok")}Sentry actif (environnement ${esc(s.environnement)}) : traces et erreurs y sont envoyées, sans le texte des contrats.</p>`;
  return `<p class="statut neutre">${icone("warn")}Sentry non configuré : traces et logs restent sur la console du serveur.
    Définir <code>SENTRY_DSN</code> pour activer.</p>`;
}

function ligneRoute(s) {
  return `<tr>
    <th scope="row"><code>${esc(s.route)}</code></th><td>${esc(s.version)}</td>
    <td class="chiffre">${esc(s.requetes)}</td><td class="chiffre">${fmtPct(s.taux_erreur * 100)}</td>
    <td class="chiffre">${fmtMs(s.latence_p50_ms)}</td><td class="chiffre">${fmtMs(s.latence_p95_ms)}</td>
    <td class="chiffre">${nombre(s.appels_llm_moyen, 1)}</td><td class="chiffre">${nombre(s.tokens_moyen)}</td>
    <td class="chiffre">${fmtPct(s.taux_tronque * 100)}</td><td class="chiffre">${fmtEur(s.cout_total_eur)}</td>
  </tr>`;
}

function ligneErreur(e) {
  const d = new Date(e.date);
  const date = Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("fr-FR");
  return `<tr><td class="chiffre">${esc(date)}</td><td><code>${esc(e.route)}</code></td>
    <td>${esc(e.version)}</td><td class="chiffre">${fmtMs(e.latence_ms)}</td></tr>`;
}

function liens(s) {
  if (!s.actif) return '<p class="vide">Liens disponibles une fois Sentry configuré.</p>';
  if (!s.liens) return '<p class="vide">Définir <code>SENTRY_UI_URL</code> pour afficher les liens vers Sentry.</p>';
  return LIENS.map(([cle, libelle]) =>
    `<a class="bouton" href="${esc(s.liens[cle])}" target="_blank" rel="noopener noreferrer">${libelle}<span class="visuellement-cache"> (nouvel onglet)</span></a>`).join("");
}

function rendre(r) {
  $("#fenetre").textContent = `fenêtre ${fmtDuree(r.fenetre_s)}`;
  peindre($("#etat-sentry"), etatSentry(r.sentry));
  peindre($("#routes"), r.par_route.length
    ? r.par_route.map(ligneRoute).join("")
    : '<tr><td colspan="10" class="vide">Aucun trafic dans la fenêtre.</td></tr>');
  peindre($("#erreurs"), r.erreurs_recentes.length
    ? r.erreurs_recentes.map(ligneErreur).join("")
    : '<tr><td colspan="4" class="vide">Aucune erreur dans la fenêtre.</td></tr>');
  peindre($("#liens"), liens(r.sentry));
}

function indisponible() {
  peindre($("#etat-sentry"), `<p class="indispo">${icone("warn")}Résumé d'observabilité indisponible — nouvel essai dans 5 s.</p>`);
}

$("#logo").innerHTML = icone("scale");
initTheme($("#theme"));
surveillerResume({
  url: "/observabilite/resume", onData: rendre, onErreur: indisponible,
  boutonPause: $("#pause"), horodatage: $("#maj"),
});
```

- [ ] **Step 5: Add styles**

Dans `app/web/styles.css`, ajouter avant `@media (max-width: 767px)` :

```css
/* Page Observabilité */
#etat-sentry p { margin: 0; white-space: normal; }
.liens-sentry { display: flex; flex-wrap: wrap; gap: 12px; }
.liens-sentry .bouton { text-decoration: none; }
```

- [ ] **Step 6: Run the full suite**

Run: `MOCK=on uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Visual check**

Run: `MOCK=on uv run uvicorn app.main:app --port 8000`, lancer quelques analyses depuis `/`, puis ouvrir `http://localhost:8000/observabilite`.
Vérifier : bandeau « Sentry non configuré » ; tableau par route avec `/v1/analyse` et `/v2/analyse` ; « Aucune erreur » ou erreurs récentes ; message des liens ; 375 px sans scroll de page ; thèmes clair et sombre.
Puis relancer avec `SENTRY_DSN=https://cle@o0.ingest.sentry.io/1 SENTRY_UI_URL=https://exemple.sentry.io` : bandeau « Sentry actif » et trois boutons de liens (l'envoi réel échoue silencieusement avec ce DSN factice, sans impact sur l'app).

- [ ] **Step 8: Commit**

```bash
git add app/web/observabilite.html app/web/observabilite.js app/web/commun.js app/web/styles.css tests/integration/test_interface.py
git commit -m "feat(web): page Observabilité — métriques par route, erreurs, liens Sentry"
```

---

### Task 8: Vérification finale

**Files:** aucun (sauf corrections issues de la vérification).

- [ ] **Step 1: Full suite and lint**

Run: `MOCK=on uv run pytest -q && uv run ruff check .`
Expected: tout vert (344 de départ + nouveaux), ruff sans erreur.

- [ ] **Step 2: No dead references**

Run: `grep -rn "rendreP95\|SLO_P95_MS\|kpi-p95\|kpi-canary\|carteVersion\|sparkline" app/web`
Expected: aucune ligne.

- [ ] **Step 3: Pre-delivery UI checklist (UI UX PRO MAX), les trois pages**

Avec l'app lancée (`MOCK=on uv run uvicorn app.main:app --port 8000`), à 375 px, 768 px, 1440 px, thèmes clair et sombre :
- aucun emoji comme icône ; tous les états avec icône + texte ;
- focus visible au clavier sur nav, modes, boutons, `<details>`, filtres ;
- aucun scroll horizontal de page (seuls les tableaux défilent) ;
- contraste lisible des badges et statuts dans les deux thèmes ;
- `prefers-reduced-motion` : aucun mouvement hors timer textuel ;
- console du navigateur sans erreur.

- [ ] **Step 4: Commit fixes if any, then push**

```bash
git push
```
