# Interface client — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Servir depuis l'app `:8000` une page « Analyse » (v1 | v2 côte à côte + mode gateway + 2 KPI) et une page « Pilotage » (tout `ops.dashboard.resume()`), sans toucher aux contrats existants.

**Architecture:** Une route mince `GET /pilotage/resume` (Pydantic `ResumePilotage`) enveloppe `ops.dashboard.resume()`. `app/main.py` monte `app/web/` sous `/static`, `eval/contrats/` sous `/exemples`, et sert les deux pages HTML. Le front est en HTML/CSS/JS vanilla (modules ES), avec des graphiques en SVG faits main.

**Tech Stack:** FastAPI (`StaticFiles`, `FileResponse`), Pydantic v2, pytest + `TestClient`, JS ES modules sans dépendance, Google Fonts (EB Garamond + Lato).

**Spec:** `docs/superpowers/specs/2026-09-22-interface-client-design.md`

## Global Constraints

- Aucun fichier modifié parmi `app/api_v1.py`, `app/api_v2.py`, `app/gateway.py`, `ops/dashboard.py`, `docker-compose.yml`, `Dockerfile`.
- Aucune nouvelle dépendance Python ni JS ; pas de chaîne Node, pas de CDN JS.
- Tout texte venant de l'API ou du contrat est inséré dans le DOM **via `esc()`** (extraits LLM, warnings, versions, journal).
- SLO P95 = **8 000 ms** (`docs/besoin_client.md`) ; limite v1 = **16 000** caractères (`models/v1/config.yaml`) ; seuil de relecture = **0,6** (`models/v2/config.yaml`) ; `min_length` = **20** ; paliers = **10 / 50 / 100** (`ops/seuils_pilotage.yaml`) — constantes JS commentées avec leur source.
- Vocabulaire métier : « Contrat tronqué », « Analyse non fiable », « Indice de fiabilité ».
- Rafraîchissement des KPI : **5 s**, bouton pause/reprise, suspendu si onglet masqué.
- Couleurs uniquement via tokens CSS (`var(--…)`) ; états toujours **icône + texte**.
- `prefers-reduced-motion: reduce` coupe toute transition/animation.
- Tests : `MOCK=on uv run pytest -q` vert ; ruff (`line-length = 100`) propre.

## Review Focus

1. **Contrat ou extrait LLM contenant du HTML** (`<img src=x onerror=alert(1)>`) → affiché comme texte, jamais exécuté. Pin : vérification navigateur Task 5 étape 3 + grep Task 3 étape 5 (aucun `innerHTML` sans `esc`/constante).
2. **v2 renvoie 413 (document > 200 000 caractères) pendant que v1 répond 200** → la colonne v1 s'affiche, la colonne v2 montre « Erreur 413 » et le `detail`. Pin : Task 5 étape 3.
3. **Registre corrompu (`index.json` illisible)** → `GET /pilotage/resume` répond 200 avec une alerte « registre illisible », pas un 500. Pin : `test_resume_registre_illisible` (Task 1).
4. **Version dont toutes les requêtes sont en erreur** → `latence_p95_ms` à `null`, la page affiche « — » sans planter. Pin : `test_resume_version_toute_en_erreur` (Task 1) + Task 5 étape 3.
5. **Double clic sur « Analyser » pendant un appel long** → une seule analyse part (bouton désactivé jusqu'à la fin). Pin : Task 5 étape 3.

---

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `app/pilotage.py` (créé) | Schéma `ResumePilotage` + route `GET /pilotage/resume` |
| `app/main.py` (modifié) | Routeur pilotage, montages `/static` et `/exemples`, pages `/` et `/pilotage` |
| `app/web/styles.css` (créé) | Tokens clair/sombre + composants partagés |
| `app/web/commun.js` (créé) | `esc`, formatage, icônes, thème, rafraîchissement du résumé, rendus P95 / trafic / palier |
| `app/web/index.html` + `app/web/analyse.js` (créés) | Page Analyse |
| `app/web/pilotage.html` + `app/web/pilotage.js` (créés) | Page Pilotage |
| `tests/integration/test_interface.py` (créé) | Tests de la route et des pages servies |
| `Makefile` (modifié) | Lien de l'interface dans le message de `make up` |

---

### Task 1 : Route `GET /pilotage/resume`

**Files:**
- Create: `app/pilotage.py`
- Modify: `app/main.py` (import + `include_router`)
- Test: `tests/integration/test_interface.py`

**Interfaces:**
- Consumes: `ops.dashboard.resume() -> dict[str, Any]` (sans argument : chemins via `METRICS_PATH`, `REGISTRY_PATH`, `CANDIDATS_PATH`).
- Produces: `app.pilotage.router`, `app.pilotage.ResumePilotage`, `app.pilotage.get_resume`. JSON : `{fenetre_s, total, par_version: {<version>: {requetes, trafic_pct, latence_p50_ms, latence_p95_ms, taux_erreur, score_moyen, score_p10, cout_total_eur, histogramme_score, serie_minute: [{minute, requetes, latence_p95_ms, taux_erreur}]}}, palier: {version, pourcentage, depuis_s, requetes, duree_min_s, requetes_min} | null, alertes: [str], candidats: int, journal: [{date, evenement, origine, resume, motif, …}]}` — consommé par `commun.js`, `analyse.js` et `pilotage.js`.

- [ ] **Step 1 : Écrire les tests qui échouent**

`tests/integration/test_interface.py` :

```python
"""Interface web : résumé de pilotage et pages servies."""
from __future__ import annotations

import time

from app.llm_client import Bundle
from app.pilotage import ResumePilotage
from app.telemetry import Mesure


def _canary(registry, pourcentage=10):
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    registry.definir_canary("v2.0.0", pourcentage)
    registry.journaliser("canary", version="v2.0.0", pourcentage=pourcentage, origine="manuel")


def _mesures(metriques, version, n, latence_ms=900.0, **champs):
    for _ in range(n):
        metriques.enregistrer(Mesure(ts=time.time(), version=version, route="/analyse",
                                     latence_ms=latence_ms, **champs))


def test_resume_avec_canary(client, registry, metriques):
    _canary(registry)
    _mesures(metriques, "v1.0.0", 5)
    _mesures(metriques, "v2.0.0", 3, latence_ms=2000.0, score=0.9, cout_eur=0.02)

    r = client.get("/pilotage/resume")

    assert r.status_code == 200
    corps = ResumePilotage.model_validate(r.json())
    assert set(corps.par_version) == {"v1.0.0", "v2.0.0"}
    assert corps.par_version["v1.0.0"].score_moyen is None
    assert sum(p.requetes for p in corps.par_version["v2.0.0"].serie_minute) == 3
    assert corps.palier is not None
    assert (corps.palier.version, corps.palier.pourcentage) == ("v2.0.0", 10)
    assert corps.journal[-1].evenement == "canary"
    assert r.json()["journal"][-1]["pourcentage"] == 10     # champs en plus conservés


def test_resume_sans_trafic(client, registry):
    r = client.get("/pilotage/resume")

    assert r.status_code == 200
    corps = r.json()
    assert (corps["total"], corps["par_version"], corps["palier"]) == (0, {}, None)


def test_resume_version_toute_en_erreur(client, registry, metriques):
    _mesures(metriques, "v1.0.0", 4, erreur=True)

    v1 = client.get("/pilotage/resume").json()["par_version"]["v1.0.0"]

    assert v1["latence_p95_ms"] is None
    assert v1["taux_erreur"] == 1.0


def test_resume_registre_illisible(client, registry):
    (registry.root / "index.json").write_text("{", encoding="utf-8")

    r = client.get("/pilotage/resume")

    assert r.status_code == 200
    assert any("registre illisible" in a for a in r.json()["alertes"])
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `MOCK=on uv run pytest tests/integration/test_interface.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pilotage'`

- [ ] **Step 3 : Implémenter la route**

`app/pilotage.py` :

```python
"""Résumé de pilotage pour l'interface web — route mince sur ``ops.dashboard.resume``.

    GET /pilotage/resume → ResumePilotage

Même calcul et même fenêtre que le pilote (``resume()`` sans argument) : aucune
agrégation n'est dupliquée ici. Les sources illisibles sont déjà dégradées en
``alertes`` par ``resume()`` — jamais de 500.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ops.dashboard import resume

router = APIRouter(prefix="/pilotage", tags=["pilotage"])


class PointMinute(BaseModel):
    model_config = ConfigDict(extra="allow")
    minute: int
    requetes: int
    latence_p95_ms: float | None
    taux_erreur: float


class StatsVersion(BaseModel):
    requetes: int
    trafic_pct: float
    latence_p50_ms: float | None
    latence_p95_ms: float | None
    taux_erreur: float
    score_moyen: float | None
    score_p10: float | None
    cout_total_eur: float
    histogramme_score: list[int]
    serie_minute: list[PointMinute]


class Palier(BaseModel):
    version: str
    pourcentage: int | None
    depuis_s: float | None
    requetes: int
    duree_min_s: float | None
    requetes_min: int | None


class EntreeJournal(BaseModel):
    """Les événements du registre sont hétérogènes : on garde tous leurs champs."""

    model_config = ConfigDict(extra="allow")
    date: str | None = None
    evenement: str | None = None
    origine: str | None = None
    resume: str | None = None
    motif: str | None = None


class ResumePilotage(BaseModel):
    fenetre_s: float
    total: int
    par_version: dict[str, StatsVersion]
    palier: Palier | None
    alertes: list[str]
    candidats: int
    journal: list[EntreeJournal]


def get_resume() -> dict[str, Any]:
    return resume()


@router.get("/resume", response_model=ResumePilotage)
def resume_pilotage(r: dict[str, Any] = Depends(get_resume)) -> ResumePilotage:
    return ResumePilotage.model_validate(r)
```

Dans `app/main.py`, remplacer `from app import api_v1, api_v2, gateway` par :

```python
from app import api_v1, api_v2, gateway, pilotage
```

et ajouter après `app.include_router(gateway.router)` :

```python
    app.include_router(pilotage.router)
```

Ajouter à la docstring du module, après la puce gateway :

```
* ``/pilotage/resume`` expose le résumé du tableau de bord (``ops.dashboard``)
  à l'interface web ; ``/`` et ``/pilotage`` servent ses deux pages.
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `MOCK=on uv run pytest tests/integration/test_interface.py -v`
Expected: 4 PASS

- [ ] **Step 5 : Suite complète + lint**

Run: `MOCK=on uv run pytest -q && uv run ruff check .`
Expected: tout vert, `All checks passed!`

- [ ] **Step 6 : Commit**

```bash
git add app/pilotage.py app/main.py tests/integration/test_interface.py
git commit -m "feat(ui): route GET /pilotage/resume pour l'interface web"
```

---

### Task 2 : Service des pages et des fichiers statiques

**Files:**
- Modify: `app/main.py`
- Create: `app/web/index.html`, `app/web/pilotage.html` (squelettes — complétés Tasks 4 et 5), `app/web/styles.css` (vide — complété Task 3)
- Test: `tests/integration/test_interface.py`

**Interfaces:**
- Consumes: `app.pilotage.router` (Task 1).
- Produces: `GET /` → `app/web/index.html` ; `GET /pilotage` → `app/web/pilotage.html` ; `GET /static/<fichier>` → `app/web/<fichier>` ; `GET /exemples/<id>.txt` → `eval/contrats/<id>.txt`. Les pages référencent `/static/styles.css` et leur module JS par chemin absolu.

- [ ] **Step 1 : Écrire les tests qui échouent** (ajouter à `tests/integration/test_interface.py`)

```python
import pytest


@pytest.mark.parametrize("chemin, marqueur", [
    ("/", 'id="form-analyse"'),
    ("/pilotage", 'id="versions"'),
])
def test_pages_servies(client, chemin, marqueur):
    r = client.get(chemin)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert marqueur in r.text
    assert "/static/styles.css" in r.text


@pytest.mark.parametrize("chemin", ["/static/styles.css", "/exemples/c02.txt", "/exemples/c07.txt"])
def test_fichiers_statiques(client, chemin):
    assert client.get(chemin).status_code == 200


def test_exemples_hors_dossier_refuses(client):
    assert client.get("/exemples/../../app/main.py").status_code == 404
```

(Placer `import pytest` avec les autres imports en tête de fichier.)

- [ ] **Step 2 : Vérifier l'échec**

Run: `MOCK=on uv run pytest tests/integration/test_interface.py -v`
Expected: les nouveaux tests FAIL (404), les 4 de Task 1 PASS.

- [ ] **Step 3 : Créer les squelettes**

`app/web/styles.css` : fichier vide pour l'instant (une ligne `/* Mardik — système visuel (Task 3) */`).

`app/web/index.html` :

```html
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mardik — Analyse</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <form id="form-analyse"></form>
</body>
</html>
```

`app/web/pilotage.html` :

```html
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mardik — Pilotage</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <div id="versions"></div>
</body>
</html>
```

- [ ] **Step 4 : Monter les fichiers dans `app/main.py`**

Imports à ajouter / compléter :

```python
from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
```

Constantes après les imports :

```python
RACINE = Path(__file__).resolve().parent.parent
WEB = RACINE / "app" / "web"
EXEMPLES = RACINE / "eval" / "contrats"
```

Dans `create_app()`, après `app.include_router(pilotage.router)` :

```python
    app.mount("/static", StaticFiles(directory=WEB), name="static")
    app.mount("/exemples", StaticFiles(directory=EXEMPLES), name="exemples")

    @app.get("/", include_in_schema=False)
    def page_analyse() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/pilotage", include_in_schema=False)
    def page_pilotage() -> FileResponse:
        return FileResponse(WEB / "pilotage.html")
```

- [ ] **Step 5 : Vérifier le succès**

Run: `MOCK=on uv run pytest tests/integration/test_interface.py -v`
Expected: tous PASS

- [ ] **Step 6 : Suite complète (garde-fou `/v1`) + lint**

Run: `MOCK=on uv run pytest -q && uv run ruff check .`
Expected: tout vert — en particulier `tests/acceptance/test_chaine.py::test_client_v1_fonctionne`.

- [ ] **Step 7 : Commit**

```bash
git add app/main.py app/web tests/integration/test_interface.py
git commit -m "feat(ui): servir les pages /, /pilotage, /static et /exemples"
```

---

### Task 3 : Système visuel et module partagé

**Files:**
- Modify: `app/web/styles.css` (contenu complet)
- Create: `app/web/commun.js`

**Interfaces:**
- Consumes: le JSON `ResumePilotage` (Task 1).
- Produces (exports de `/static/commun.js`) :
  - `SLO_P95_MS: number`, `PALIERS: number[]`
  - `esc(v: any) -> string`
  - `fmtMs(v)`, `fmtScore(v)`, `fmtPct(v)`, `fmtEur(v)`, `fmtDuree(s)` → `string` (`"—"` si `null`)
  - `icone(nom: "ok"|"warn"|"danger"|"pause"|"play"|"sun"|"moon"|"scale") -> string` (SVG)
  - `couleurVersion(v: string) -> "v1"|"v2"` (classe CSS)
  - `initTheme(bouton: HTMLButtonElement) -> void`
  - `surveillerResume({onData(r), onErreur(e), boutonPause, horodatage}) -> void`
  - `rendreP95(parVersion) -> string`, `rendreTrafic(parVersion) -> string`, `rendrePalier(palier|null) -> string`
- Classes CSS utilisées par les Tasks 4-5 : `.entete`, `.nav`, `.bouton`, `.bouton-primaire`, `.bouton-fantome`, `.icone`, `.carte`, `.grille-kpi`, `.barre-maj`, `.statut.ok|warn|danger`, `.badge.ok|warn|danger|info|neutre`, `.vide`, `.indispo`, `.chiffre`, `.chiffre-grand`, `.squelette`, `.apparait`, `.resultats`, `.une-colonne`, `.colonne`, `.erreur`, `.clause`, `.warnings`, `.meta`, `.fiabilite`, `.mini`, `.mini-graphes`, `.stats`, `.table-defilante`, `.version.v1|v2`, `.visuellement-cache`, `.titre-page`, `.barre-maj-kpi`, `.alertes`, `.grille-2`, `.compteur`, `.versions`.

Pas de test automatisé JS (spec §6) : ce module est vérifié en navigateur aux Tasks 4 et 5.

- [ ] **Step 1 : Écrire `app/web/styles.css`**

```css
/* Mardik — système visuel « Trust & Authority » (UI UX PRO MAX).
   Palette « Legal Services » : navy d'autorité + or de confiance. */
@import url("https://fonts.googleapis.com/css2?family=EB+Garamond:wght@500;600;700&family=Lato:wght@400;700&display=swap");

:root {
  color-scheme: light;
  --primary: #1E3A8A;
  --on-primary: #FFFFFF;
  --accent: #B45309;
  --on-accent: #FFFFFF;
  --bg: #F8FAFC;
  --card: #FFFFFF;
  --fg: #0F172A;
  --muted-fg: #64748B;
  --muted: #E9EEF5;
  --border: #CBD5E1;
  --v1: #64748B;
  --v2: #1E3A8A;
  --ok: #15803D;
  --warn: #B45309;
  --danger: #DC2626;
  --info: #1E40AF;
  --ombre: 0 1px 2px rgb(15 23 42 / 0.06);
  --rayon: 10px;
  --police-titre: "EB Garamond", Georgia, "Times New Roman", serif;
  --police-texte: "Lato", system-ui, -apple-system, "Segoe UI", sans-serif;
  --duree: 200ms;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --primary: #93B4F5; --on-primary: #0B1220; --accent: #F59E0B; --on-accent: #0B1220;
    --bg: #0B1220; --card: #111A2E; --fg: #E2E8F0; --muted-fg: #94A3B8; --muted: #1A2540;
    --border: #23304A; --v1: #94A3B8; --v2: #93B4F5;
    --ok: #4ADE80; --warn: #FBBF24; --danger: #F87171; --info: #93B4F5;
    --ombre: none;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --primary: #93B4F5; --on-primary: #0B1220; --accent: #F59E0B; --on-accent: #0B1220;
  --bg: #0B1220; --card: #111A2E; --fg: #E2E8F0; --muted-fg: #94A3B8; --muted: #1A2540;
  --border: #23304A; --v1: #94A3B8; --v2: #93B4F5;
  --ok: #4ADE80; --warn: #FBBF24; --danger: #F87171; --info: #93B4F5;
  --ombre: none;
}

*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.5 var(--police-texte);
}
h1, h2, h3 { font-family: var(--police-titre); font-weight: 600; line-height: 1.2; margin: 0 0 .5rem; }
h1 { font-size: 1.6rem; } h2 { font-size: 1.3rem; } h3 { font-size: 1.1rem; }
a { color: var(--primary); }
code { font-size: .9em; background: var(--muted); padding: .1em .35em; border-radius: 4px; }
main { max-width: 1200px; margin: 0 auto; padding: 24px 16px 48px; display: grid; gap: 24px; }
:focus-visible { outline: 3px solid var(--primary); outline-offset: 2px; }

/* En-tête */
.entete {
  display: flex; align-items: center; gap: 24px; padding: 12px 16px;
  background: var(--card); border-bottom: 1px solid var(--border);
}
.marque { display: flex; align-items: center; gap: 8px; font: 700 1.4rem var(--police-titre); color: var(--primary); text-decoration: none; }
.nav { display: flex; gap: 4px; margin-right: auto; }
.nav a {
  padding: 10px 14px; min-height: 44px; display: inline-flex; align-items: center;
  color: var(--muted-fg); text-decoration: none; border-bottom: 2px solid transparent;
}
.nav a[aria-current="page"] { color: var(--primary); border-bottom-color: var(--primary); font-weight: 700; }
.nav a:hover { color: var(--fg); }

/* Icônes et boutons */
.icone { width: 1.1em; height: 1.1em; flex: none; vertical-align: -0.15em; }
.bouton {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  min-height: 44px; min-width: 44px; padding: 8px 16px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--card); color: var(--fg);
  font: inherit; cursor: pointer; transition: background-color var(--duree), border-color var(--duree);
}
.bouton:hover { border-color: var(--primary); }
.bouton:disabled { opacity: .6; cursor: progress; }
.bouton-primaire { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: 700; }
.bouton-primaire:hover { filter: brightness(1.08); }
.bouton-fantome { border-color: transparent; background: transparent; }

/* Cartes et KPI */
.carte {
  background: var(--card); border: 1px solid var(--border); border-radius: var(--rayon);
  padding: 20px; box-shadow: var(--ombre); min-width: 0;
}
.grille-kpi { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr)); }
.barre-maj { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 12px; color: var(--muted-fg); font-size: .9rem; }
.chiffre, .bullet-valeur, .stats dd { font-variant-numeric: tabular-nums; }
.chiffre-grand { font: 700 2.2rem var(--police-titre); font-variant-numeric: tabular-nums; color: var(--primary); }
.legende, .meta { color: var(--muted-fg); font-size: .875rem; margin: 8px 0 0; }
.vide, .indispo { color: var(--muted-fg); margin: 0; }
.indispo { display: flex; gap: 6px; align-items: center; }

/* Statuts et badges : toujours icône + texte */
.statut { display: inline-flex; align-items: center; gap: 4px; font-weight: 700; font-size: .9rem; }
.statut.ok, .badge.ok { color: var(--ok); }
.statut.warn, .badge.warn { color: var(--warn); }
.statut.danger, .badge.danger { color: var(--danger); }
.badge.info { color: var(--info); }
.badge.neutre { color: var(--muted-fg); }
.badge {
  display: inline-flex; align-items: center; gap: 6px; margin: 0 0 12px; padding: 4px 10px;
  border: 1px solid currentColor; border-radius: 999px; font-size: .875rem; font-weight: 700;
}

/* Bullet chart P95 vs SLO */
.bullet { display: grid; grid-template-columns: 5.5rem 1fr 6rem 7rem; align-items: center; gap: 10px; margin-bottom: 10px; }
.bullet svg { width: 100%; height: 18px; display: block; }
.bullet-fond { fill: var(--muted); }
.bullet-barre.v1 { fill: var(--v1); } .bullet-barre.v2 { fill: var(--v2); }
.bullet-cible { stroke: var(--fg); stroke-width: 2; }
.bullet-valeur { text-align: right; }

/* Barre de trafic */
.trafic { display: flex; height: 32px; border-radius: 8px; overflow: hidden; margin-bottom: 16px; }
.trafic-part {
  flex-basis: 0; min-width: 4.5rem; display: flex; align-items: center; justify-content: center;
  font-size: .8rem; font-weight: 700; color: var(--on-primary); white-space: nowrap; overflow: hidden;
}
.trafic-part.v1 { background: var(--v1); } .trafic-part.v2 { background: var(--v2); }

/* Stepper de palier et jauges */
.palier-titre { margin: 0 0 12px; }
.stepper { display: flex; list-style: none; padding: 0; margin: 0 0 16px; }
.etape { flex: 1; display: flex; align-items: center; gap: 8px; color: var(--muted-fg); font-variant-numeric: tabular-nums; }
.etape:not(:last-child)::after { content: ""; flex: 1; height: 2px; background: var(--border); margin-right: 8px; }
.pastille { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--border); background: var(--card); flex: none; }
.etape.fait .pastille { background: var(--primary); border-color: var(--primary); }
.etape.actif { color: var(--fg); font-weight: 700; }
.etape.actif .pastille { background: var(--accent); border-color: var(--accent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 25%, transparent); }
.jauge { margin-bottom: 10px; }
.jauge-tete { display: flex; justify-content: space-between; font-size: .875rem; margin-bottom: 4px; }
.jauge-piste { height: 8px; border-radius: 4px; background: var(--muted); overflow: hidden; }
.jauge-remplie { height: 100%; background: var(--primary); transition: width var(--duree) ease-out; }

/* Formulaire */
.champ label { display: block; font-weight: 700; margin-bottom: 6px; }
.champ-tete { display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 8px; }
textarea {
  width: 100%; min-height: 180px; resize: vertical; padding: 12px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--bg); color: var(--fg); font: 15px/1.5 var(--police-texte);
}
.aide { color: var(--muted-fg); font-size: .875rem; margin: 6px 0 0; }
.aide .warn { color: var(--warn); font-weight: 700; }
.erreur-champ { color: var(--danger); font-size: .875rem; margin: 6px 0 0; min-height: 1.3em; }
.actions { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; margin-top: 12px; }
.modes { display: flex; gap: 4px; border: 1px solid var(--border); border-radius: 8px; padding: 4px; margin: 0; }
.visuellement-cache, .modes legend {
  position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap;
}
.titre-page { margin: 0 auto 0 0; }
.barre-maj-kpi { margin-top: 12px; }
.modes label { display: inline-flex; align-items: center; gap: 6px; min-height: 40px; padding: 0 12px; border-radius: 6px; cursor: pointer; }
.modes label:has(input:checked) { background: var(--muted); color: var(--primary); font-weight: 700; }

/* Résultats */
.resultats { display: grid; gap: 16px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.resultats.une-colonne { grid-template-columns: minmax(0, 1fr); }
.resultats:empty { display: none; }
.colonne h3 { color: var(--primary); }
.colonne.v1 h3 { color: var(--v1); }
.clauses-v1 { padding-left: 1.2rem; margin: 0; }
.fiabilite { display: flex; flex-wrap: wrap; align-items: baseline; gap: 12px; margin-bottom: 12px; }
.clause { border-top: 1px solid var(--border); padding: 10px 0; }
.clause summary { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; cursor: pointer; min-height: 32px; }
.clause-type { font-weight: 700; margin-right: auto; }
.clause-sections { color: var(--muted-fg); font-size: .875rem; }
.clause blockquote { margin: 8px 0 0; padding: 8px 12px; border-left: 3px solid var(--primary); background: var(--muted); font-size: .9rem; white-space: pre-wrap; }
.warnings { list-style: none; padding: 0; margin: 12px 0 0; display: grid; gap: 6px; }
.warnings li { display: flex; gap: 6px; color: var(--warn); font-size: .9rem; }
.erreur { display: flex; gap: 10px; color: var(--danger); }
.erreur p { margin: 4px 0 0; color: var(--fg); }

/* Squelette de chargement et apparition */
.squelette { display: grid; gap: 10px; }
.squelette span { height: 14px; border-radius: 4px; background: var(--muted); animation: pulse 1.2s ease-in-out infinite; }
.squelette span:nth-child(2) { width: 80%; } .squelette span:nth-child(3) { width: 60%; }
@keyframes pulse { 50% { opacity: .45; } }
.apparait { animation: apparition var(--duree) ease-out; }
@keyframes apparition { from { opacity: 0; transform: translateY(6px); } }

/* Page Pilotage */
.alertes { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.alertes li { display: flex; gap: 8px; color: var(--danger); font-weight: 700; }
.grille-2 { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)); }
.compteur { font: 700 3rem var(--police-titre); color: var(--primary); margin: 0; font-variant-numeric: tabular-nums; }
.versions { display: grid; gap: 16px; }
.version { border-left: 4px solid var(--v2); }
.version.v1 { border-left-color: var(--v1); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 0 0 16px; }
.stats dt { color: var(--muted-fg); font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; }
.stats dd { margin: 2px 0 0; font-weight: 700; }
.stats small { font-weight: 400; color: var(--muted-fg); }
.mini-graphes { display: flex; flex-wrap: wrap; gap: 24px; }
.mini-graphes figure { margin: 0; }
.mini-graphes figcaption { font-size: .8rem; color: var(--muted-fg); margin-bottom: 4px; }
.mini { width: 160px; height: 44px; display: block; }
.mini rect { fill: var(--primary); }
.mini polyline { fill: none; stroke: var(--accent); stroke-width: 2; vector-effect: non-scaling-stroke; }
.table-defilante { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; min-width: 560px; }
th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted-fg); font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; }

@media (max-width: 767px) {
  .resultats { grid-template-columns: minmax(0, 1fr); }
  .entete { flex-wrap: wrap; gap: 8px; }
  .bullet { grid-template-columns: 4.5rem 1fr 5rem; }
  .bullet .statut { grid-column: 2 / -1; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
```

- [ ] **Step 2 : Écrire `app/web/commun.js`**

```js
// Partagé par les pages Analyse et Pilotage : échappement, formatage, icônes,
// thème, rafraîchissement du résumé de pilotage et petits graphiques SVG.

export const SLO_P95_MS = 8000;        // docs/besoin_client.md : P95 < 8 s
export const PALIERS = [10, 50, 100];  // ops/seuils_pilotage.yaml : paliers du canary
const PERIODE_MS = 5000;

const ENTITES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ENTITES[c]);

const nombre = (d) => new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
export const fmtMs = (v) => (v == null ? "—" : `${nombre(0).format(v)} ms`);
export const fmtScore = (v) => (v == null ? "—" : nombre(2).format(v));
export const fmtPct = (v) => (v == null ? "—" : `${nombre(1).format(v)} %`);
export const fmtEur = (v) => (v == null ? "—" : `${nombre(3).format(v)} €`);
export const fmtDuree = (s) => {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)} s`;
  return `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, "0")} s`;
};

// Tracés Lucide (ISC), inline : ni CDN ni emoji.
const TRACES = {
  ok: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  warn: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  danger: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
  pause: '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  scale: '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
};
export const icone = (nom) =>
  `<svg class="icone" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${TRACES[nom]}</svg>`;

export const couleurVersion = (v) => (String(v).startsWith("v1") ? "v1" : "v2");

export function initTheme(bouton) {
  const racine = document.documentElement;
  const systemeSombre = matchMedia("(prefers-color-scheme: dark)");
  try {
    const memo = localStorage.getItem("mardik-theme");
    if (memo === "dark" || memo === "light") racine.dataset.theme = memo;
  } catch { /* stockage indisponible : on suit le système */ }
  const courant = () => racine.dataset.theme || (systemeSombre.matches ? "dark" : "light");
  const peindre = () => {
    const sombre = courant() === "dark";
    bouton.innerHTML = icone(sombre ? "sun" : "moon");
    bouton.setAttribute("aria-label", sombre ? "Passer en thème clair" : "Passer en thème sombre");
  };
  bouton.addEventListener("click", () => {
    racine.dataset.theme = courant() === "dark" ? "light" : "dark";
    try { localStorage.setItem("mardik-theme", racine.dataset.theme); } catch { /* idem */ }
    peindre();
  });
  systemeSombre.addEventListener("change", peindre);
  peindre();
}

export function surveillerResume({ onData, onErreur, boutonPause, horodatage }) {
  let minuterie = null;
  let enPause = false;
  let derniere = null;
  async function tour() {
    try {
      const r = await fetch("/pilotage/resume", { headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      onData(await r.json());
      derniere = Date.now();
    } catch (e) {
      onErreur(e);
    }
  }
  function planifier() {
    clearInterval(minuterie);
    minuterie = null;
    if (enPause || document.hidden) return;
    tour();
    minuterie = setInterval(tour, PERIODE_MS);
  }
  setInterval(() => {
    const age = derniere == null ? null : Math.round((Date.now() - derniere) / 1000);
    horodatage.textContent = enPause ? "en pause" : age == null ? "en attente…" : `màj il y a ${age} s`;
  }, 1000);
  boutonPause.addEventListener("click", () => {
    enPause = !enPause;
    boutonPause.setAttribute("aria-pressed", String(enPause));
    boutonPause.innerHTML = `${icone(enPause ? "play" : "pause")}<span>${enPause ? "Reprendre" : "Pause"}</span>`;
    planifier();
  });
  document.addEventListener("visibilitychange", planifier);
  planifier();
}

const AUCUN_TRAFIC = '<p class="vide">Aucun trafic dans la fenêtre.</p>';

export function rendreP95(parVersion) {
  const versions = Object.entries(parVersion);
  if (!versions.length) return AUCUN_TRAFIC;
  const echelle = Math.max(SLO_P95_MS * 1.25, ...versions.map(([, s]) => s.latence_p95_ms ?? 0));
  const cible = ((SLO_P95_MS / echelle) * 100).toFixed(2);
  const lignes = versions.map(([v, s]) => {
    const p95 = s.latence_p95_ms;
    const largeur = p95 == null ? 0 : ((p95 / echelle) * 100).toFixed(2);
    const statut = p95 == null
      ? '<span class="statut">—</span>'
      : p95 <= SLO_P95_MS
        ? `<span class="statut ok">${icone("ok")}dans le SLO</span>`
        : `<span class="statut danger">${icone("danger")}hors SLO</span>`;
    return `<div class="bullet">
      <span class="chiffre">${esc(v)}</span>
      <svg viewBox="0 0 100 12" preserveAspectRatio="none" role="img"
           aria-label="P95 ${esc(v)} : ${fmtMs(p95)}, cible ${fmtMs(SLO_P95_MS)}">
        <rect class="bullet-fond" x="0" y="2" width="100" height="8" rx="2"/>
        <rect class="bullet-barre ${couleurVersion(v)}" x="0" y="3.5" width="${largeur}" height="5" rx="1.5"/>
        <line class="bullet-cible" x1="${cible}" x2="${cible}" y1="0" y2="12" vector-effect="non-scaling-stroke"/>
      </svg>
      <span class="bullet-valeur">${fmtMs(p95)}</span>${statut}
    </div>`;
  });
  return `${lignes.join("")}<p class="legende">Trait vertical : SLO ${fmtMs(SLO_P95_MS)} pour 95 % des analyses.</p>`;
}

export function rendreTrafic(parVersion) {
  const versions = Object.entries(parVersion);
  if (!versions.length) return AUCUN_TRAFIC;
  const libelle = versions.map(([v, s]) => `${v} ${fmtPct(s.trafic_pct)}`).join(", ");
  const parts = versions.map(([v, s]) =>
    `<span class="trafic-part ${couleurVersion(v)}" style="flex-grow:${Number(s.trafic_pct) || 0}">${esc(v)} · ${fmtPct(s.trafic_pct)}</span>`);
  return `<div class="trafic" role="img" aria-label="Part de trafic : ${esc(libelle)}">${parts.join("")}</div>`;
}

function jauge(libelle, valeur, cible, fmt) {
  const pct = valeur == null || !cible ? 0 : Math.min(100, (valeur / cible) * 100);
  return `<div class="jauge">
    <div class="jauge-tete"><span>${libelle}</span>
      <span class="chiffre">${valeur == null ? "—" : fmt(valeur)} / ${cible == null ? "—" : fmt(cible)}</span></div>
    <div class="jauge-piste" role="progressbar" aria-label="${libelle}" aria-valuemin="0"
         aria-valuemax="100" aria-valuenow="${Math.round(pct)}"><div class="jauge-remplie" style="width:${pct}%"></div></div>
  </div>`;
}

export function rendrePalier(palier) {
  if (!palier) return '<p class="vide">Aucun canary en cours : la version active reçoit tout le trafic.</p>';
  const etapes = PALIERS.map((p) => {
    const etat = p < palier.pourcentage ? "fait" : p === palier.pourcentage ? "actif" : "a-venir";
    return `<li class="etape ${etat}"${etat === "actif" ? ' aria-current="step"' : ""}><span class="pastille"></span>${p} %</li>`;
  });
  return `<p class="palier-titre">${esc(palier.version)} reçoit <strong>${esc(palier.pourcentage)} %</strong> du trafic</p>
    <ol class="stepper" aria-label="Paliers du canary">${etapes.join("")}</ol>
    ${jauge("Requêtes du palier", palier.requetes, palier.requetes_min, (n) => String(n))}
    ${jauge("Durée du palier", palier.depuis_s, palier.duree_min_s, fmtDuree)}`;
}
```

- [ ] **Step 3 : Vérifier que les fichiers sont servis**

Run: `MOCK=on uv run pytest tests/integration/test_interface.py -q`
Expected: tout PASS (`/static/styles.css` toujours 200).

- [ ] **Step 4 : Vérifier la syntaxe du module**

Run: `node --check app/web/commun.js` (si Node est installé ; sinon la console du navigateur le vérifie à la Task 4)
Expected: aucune sortie, code retour 0.

- [ ] **Step 5 : Garde-fou échappement**

Run: `grep -n 'innerHTML\s*=' app/web/*.js`
Expected: chaque affectation reçoit soit une constante, soit le retour d'une fonction de rendu dont toutes les valeurs dynamiques passent par `esc()` ou un `fmt*()` (nombres). Relire chaque ligne listée.

- [ ] **Step 6 : Commit**

```bash
git add app/web/styles.css app/web/commun.js
git commit -m "feat(ui): système visuel Legal Services et module partagé"
```

---

### Task 4 : Page « Pilotage »

**Files:**
- Modify: `app/web/pilotage.html` (contenu complet)
- Create: `app/web/pilotage.js`

**Interfaces:**
- Consumes: exports de `/static/commun.js` (Task 3) ; JSON `ResumePilotage` (Task 1).
- Produces: la page `/pilotage`, dont l'élément `#versions` est testé par `test_pages_servies`.

- [ ] **Step 1 : Écrire `app/web/pilotage.html`**

```html
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mardik — Pilotage</title>
  <meta name="description" content="Tableau de bord complet du déploiement Mardik : trafic, canary, latence, score, journal.">
  <link rel="stylesheet" href="/static/styles.css">
  <script type="module" src="/static/pilotage.js"></script>
</head>
<body>
  <header class="entete">
    <a class="marque" href="/"><span id="logo"></span>Mardik</a>
    <nav class="nav" aria-label="Navigation principale">
      <a href="/">Analyse</a>
      <a href="/pilotage" aria-current="page">Pilotage</a>
    </nav>
    <button id="theme" class="bouton bouton-fantome" type="button" aria-label="Changer de thème"></button>
  </header>
  <main>
    <div class="barre-maj">
      <h1 class="titre-page">Pilotage</h1>
      <span id="fenetre" class="chiffre"></span>
      <span id="maj" aria-live="off">en attente…</span>
      <button id="pause" class="bouton" type="button" aria-pressed="false">Pause</button>
    </div>

    <section class="carte" aria-labelledby="t-alertes">
      <h2 id="t-alertes">Alertes</h2>
      <div id="alertes" aria-live="polite"><p class="vide">Chargement…</p></div>
    </section>

    <section class="carte" aria-labelledby="t-trafic">
      <h2 id="t-trafic">Part de trafic</h2>
      <div id="trafic"></div>
    </section>

    <div class="grille-2">
      <section class="carte" aria-labelledby="t-palier">
        <h2 id="t-palier">Palier canary</h2>
        <div id="palier" aria-live="polite"></div>
      </section>
      <section class="carte" aria-labelledby="t-candidats">
        <h2 id="t-candidats">Candidats à verser</h2>
        <p id="candidats" class="compteur">—</p>
        <p class="legende">Analyses v2 à faible confiance capturées, en attente de relecture (<code>make candidats</code>).</p>
      </section>
    </div>

    <section aria-labelledby="t-versions">
      <h2 id="t-versions">Par version</h2>
      <div id="versions" class="versions"></div>
    </section>

    <section class="carte" aria-labelledby="t-journal">
      <h2 id="t-journal">Journal de pilotage</h2>
      <div class="table-defilante">
        <table>
          <thead><tr><th scope="col">Date</th><th scope="col">Événement</th><th scope="col">Origine</th><th scope="col">Résumé</th></tr></thead>
          <tbody id="journal"></tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>
```

- [ ] **Step 2 : Écrire `app/web/pilotage.js`**

```js
import {
  couleurVersion, esc, fmtDuree, fmtEur, fmtMs, fmtPct, fmtScore, icone, initTheme,
  rendrePalier, rendreTrafic, surveillerResume,
} from "/static/commun.js";

const $ = (s) => document.querySelector(s);
const BADGES = { promotion: "ok", canary: "info", rollback: "danger", alerte: "warn", seuils: "neutre" };

function histogramme(comptes) {
  const haut = Math.max(0, ...comptes);
  if (!haut) return '<span class="vide">—</span>';
  const pas = 120 / comptes.length;
  const barres = comptes.map((c, i) => {
    const h = (c / haut) * 36;
    const de = (i / comptes.length).toFixed(1);
    const a = ((i + 1) / comptes.length).toFixed(1);
    return `<rect x="${(i * pas).toFixed(1)}" y="${(36 - h).toFixed(1)}" width="${(pas - 1).toFixed(1)}" height="${h.toFixed(1)}"><title>${de}–${a} : ${c}</title></rect>`;
  });
  return `<svg class="mini" viewBox="0 0 120 36" preserveAspectRatio="none" role="img" aria-label="Distribution du score, de 0 à 1">${barres.join("")}</svg>`;
}

function sparkline(valeurs, libelle, fmt) {
  const points = valeurs.filter((v) => v != null);
  if (points.length < 2) return '<span class="vide">—</span>';
  const bas = Math.min(...points);
  const etendue = Math.max(...points) - bas || 1;
  const pas = 120 / (points.length - 1);
  const coords = points.map((v, i) => `${(i * pas).toFixed(1)},${(34 - ((v - bas) / etendue) * 32).toFixed(1)}`);
  return `<svg class="mini" viewBox="0 0 120 36" preserveAspectRatio="none" role="img"
    aria-label="${libelle}, dernière valeur ${fmt(points.at(-1))}"><polyline points="${coords.join(" ")}"/></svg>`;
}

function carteVersion([version, s]) {
  const score = s.score_moyen == null
    ? "— <small>(v1 ne produit pas de score)</small>"
    : `${fmtScore(s.score_moyen)} <small>(P10 ${fmtScore(s.score_p10)})</small>`;
  return `<article class="carte version ${couleurVersion(version)}">
    <h3>${esc(version)}</h3>
    <dl class="stats">
      <div><dt>Trafic</dt><dd>${fmtPct(s.trafic_pct)} <small>(${esc(s.requetes)} req.)</small></dd></div>
      <div><dt>Latence P50 / P95</dt><dd>${fmtMs(s.latence_p50_ms)} / ${fmtMs(s.latence_p95_ms)}</dd></div>
      <div><dt>Taux d'erreur</dt><dd>${fmtPct(s.taux_erreur * 100)}</dd></div>
      <div><dt>Score moyen</dt><dd>${score}</dd></div>
      <div><dt>Coût total</dt><dd>${fmtEur(s.cout_total_eur)}</dd></div>
    </dl>
    <div class="mini-graphes">
      <figure><figcaption>Distribution du score</figcaption>${histogramme(s.histogramme_score)}</figure>
      <figure><figcaption>P95 par minute</figcaption>${sparkline(s.serie_minute.map((p) => p.latence_p95_ms), "P95 par minute", fmtMs)}</figure>
      <figure><figcaption>Erreurs par minute</figcaption>${sparkline(s.serie_minute.map((p) => p.taux_erreur * 100), "Erreurs par minute", fmtPct)}</figure>
    </div>
  </article>`;
}

function ligneJournal(e) {
  const date = e.date ? new Date(e.date).toLocaleString("fr-FR") : "—";
  const badge = BADGES[e.evenement] ?? "neutre";
  return `<tr>
    <td class="chiffre">${esc(date)}</td>
    <td><span class="badge ${badge}">${esc(e.evenement ?? "—")}</span></td>
    <td>${esc(e.origine ?? "—")}</td>
    <td>${esc(e.resume ?? e.motif ?? "")}</td>
  </tr>`;
}

function rendre(r) {
  $("#fenetre").textContent = `fenêtre ${fmtDuree(r.fenetre_s)} · ${r.total} requête(s)`;
  $("#alertes").innerHTML = r.alertes.length
    ? `<ul class="alertes">${r.alertes.map((a) => `<li>${icone("danger")}${esc(a)}</li>`).join("")}</ul>`
    : `<p class="statut ok">${icone("ok")}Aucune alerte</p>`;
  $("#trafic").innerHTML = rendreTrafic(r.par_version);
  $("#palier").innerHTML = rendrePalier(r.palier);
  $("#candidats").textContent = String(r.candidats);
  const versions = Object.entries(r.par_version);
  $("#versions").innerHTML = versions.length
    ? versions.map(carteVersion).join("")
    : '<p class="carte vide">Aucun trafic dans la fenêtre — lancez <code>make traffic</code> pour alimenter le tableau de bord.</p>';
  $("#journal").innerHTML = r.journal.length
    ? r.journal.slice().reverse().map(ligneJournal).join("")
    : '<tr><td colspan="4" class="vide">Journal vide.</td></tr>';
}

function indisponible() {
  $("#alertes").innerHTML = `<p class="indispo">${icone("warn")}Résumé de pilotage indisponible — nouvel essai dans 5 s.</p>`;
}

$("#logo").innerHTML = icone("scale");
initTheme($("#theme"));
surveillerResume({ onData: rendre, onErreur: indisponible, boutonPause: $("#pause"), horodatage: $("#maj") });
```

- [ ] **Step 3 : Tests serveur**

Run: `MOCK=on uv run pytest tests/integration/test_interface.py -q`
Expected: tout PASS (`id="versions"` présent).

- [ ] **Step 4 : Vérification navigateur**

Lancer : `MOCK=on uv run uvicorn app.main:app --port 8000` puis, dans un second terminal, `MOCK=on uv run python scripts/traffic_sim.py --mode normal --duree 60 --rps 2` (ou `make traffic`). Ouvrir `http://localhost:8000/pilotage` et vérifier :
- cartes par version remplies, histogramme et sparklines visibles après ~1 min ;
- sans trafic (arrêter le simulateur, attendre la fin de la fenêtre) : état vide « lancez make traffic » ;
- bouton Pause → « en pause », plus aucune requête `/pilotage/resume` dans l'onglet réseau ; Reprendre relance ;
- thème : bascule clair ↔ sombre, persistant après rechargement ;
- 375 px de large : pas de défilement horizontal de la page (le journal défile dans sa carte).

Prendre une capture clair et une sombre.

- [ ] **Step 5 : Commit**

```bash
git add app/web/pilotage.html app/web/pilotage.js
git commit -m "feat(ui): page Pilotage — tous les éléments du tableau de bord"
```

---

### Task 5 : Page « Analyse »

**Files:**
- Modify: `app/web/index.html` (contenu complet)
- Create: `app/web/analyse.js`
- Modify: `Makefile` (message de `make up`)

**Interfaces:**
- Consumes: exports de `/static/commun.js` (Task 3) ; `POST /v1/analyse` → `{clauses: [str], modele, version, tronque}` ; `POST /v2/analyse` → `{clauses: [{type, extrait, confiance, sections}], confiance_globale, modele, version, model_version, sections, appels_llm, latence_ms, cout_eur, warnings}` ; `POST /analyse` → l'une des deux formes + en-tête `X-Mardik-Version` ; erreurs `{detail: str | [{msg}]}`.
- Produces: la page `/`, dont l'élément `#form-analyse` est testé par `test_pages_servies`.

- [ ] **Step 1 : Écrire `app/web/index.html`**

```html
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mardik — Analyse</title>
  <meta name="description" content="Analyse de contrats Mardik : comparer v1 et v2, ou passer par la gateway canary.">
  <link rel="stylesheet" href="/static/styles.css">
  <script type="module" src="/static/analyse.js"></script>
</head>
<body>
  <header class="entete">
    <a class="marque" href="/"><span id="logo"></span>Mardik</a>
    <nav class="nav" aria-label="Navigation principale">
      <a href="/" aria-current="page">Analyse</a>
      <a href="/pilotage">Pilotage</a>
    </nav>
    <button id="theme" class="bouton bouton-fantome" type="button" aria-label="Changer de thème"></button>
  </header>
  <main>
    <section aria-labelledby="t-kpi">
      <h1 id="t-kpi" class="visuellement-cache">Analyse de contrats — indicateurs clés</h1>
      <div class="grille-kpi">
        <article class="carte" aria-labelledby="t-p95">
          <h2 id="t-p95">Latence P95 vs SLO 8 s</h2>
          <div id="kpi-p95" aria-live="polite"><div class="squelette"><span></span><span></span></div></div>
        </article>
        <article class="carte" aria-labelledby="t-canary">
          <h2 id="t-canary">Canary en cours</h2>
          <div id="kpi-canary" aria-live="polite"><div class="squelette"><span></span><span></span></div></div>
        </article>
      </div>
      <div class="barre-maj barre-maj-kpi">
        <span id="maj">en attente…</span>
        <button id="pause" class="bouton" type="button" aria-pressed="false">Pause</button>
        <a href="/pilotage">Tout le pilotage →</a>
      </div>
    </section>

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
          <legend>Mode d'analyse</legend>
          <label><input type="radio" name="mode" value="comparer" checked> Comparer v1 / v2</label>
          <label><input type="radio" name="mode" value="gateway"> Via la gateway</label>
        </fieldset>
        <button id="analyser" class="bouton bouton-primaire" type="submit">Analyser</button>
      </div>
    </form>

    <div id="resultats" class="resultats"></div>
  </main>
</body>
</html>
```

- [ ] **Step 2 : Écrire `app/web/analyse.js`**

```js
import {
  esc, fmtEur, fmtMs, fmtScore, icone, initTheme, rendreP95, rendrePalier, rendreTrafic,
  surveillerResume,
} from "/static/commun.js";

const LIMITE_V1 = 16000;      // models/v1/config.yaml : contexte_max_caracteres (au-delà, v1 coupe)
const SEUIL_RELECTURE = 0.6;  // models/v2/config.yaml : seuil_relecture
const MIN_CARACTERES = 20;    // RequeteAnalyseV1/V2 : min_length=20

const $ = (s) => document.querySelector(s);
const texte = $("#texte");
const nombre = (n) => n.toLocaleString("fr-FR");

// --- Indicateurs clés -------------------------------------------------------
surveillerResume({
  boutonPause: $("#pause"),
  horodatage: $("#maj"),
  onData(r) {
    $("#kpi-p95").innerHTML = rendreP95(r.par_version);
    $("#kpi-canary").innerHTML = rendreTrafic(r.par_version) + rendrePalier(r.palier);
  },
  onErreur() {
    const m = `<p class="indispo">${icone("warn")}Indicateurs indisponibles — nouvel essai dans 5 s.</p>`;
    $("#kpi-p95").innerHTML = m;
    $("#kpi-canary").innerHTML = m;
  },
});

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
  const debut = performance.now();
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
  return { ok: true, corps, servi: r.headers.get("X-Mardik-Version"), duree: performance.now() - debut };
}

// --- Rendu ------------------------------------------------------------------
const SQUELETTE = '<div class="squelette"><span></span><span></span><span></span></div>';
const colonne = (id, classe, titre) =>
  `<section class="carte colonne ${classe}" aria-labelledby="t-${id}">
     <h3 id="t-${id}">${titre}</h3><div id="c-${id}" aria-live="polite" aria-busy="true">${SQUELETTE}</div>
   </section>`;
const badgeGateway = (servi) => (servi ? `<p class="badge info">servi par ${esc(servi)}</p>` : "");
const arrondi2 = (v) => Math.round(v * 100) / 100;   // même arrondi que construire_warnings

function rendreV1({ corps, duree, servi }) {
  const etat = corps.tronque
    ? `<p class="badge warn">${icone("warn")}Contrat tronqué</p>`
    : `<p class="badge ok">${icone("ok")}Contrat analysé en entier</p>`;
  const clauses = corps.clauses.length
    ? `<ul class="clauses-v1">${corps.clauses.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`
    : '<p class="vide">Aucune clause détectée.</p>';
  return `${badgeGateway(servi)}${etat}${clauses}
    <p class="meta">${corps.clauses.length} clause(s) · ${fmtMs(duree)} · ${esc(corps.version)} · ${esc(corps.modele)}</p>`;
}

function rendreClause(c) {
  const relire = arrondi2(c.confiance) < SEUIL_RELECTURE
    ? `<span class="statut warn">${icone("warn")}à relire</span>` : "";
  return `<details class="clause">
    <summary><span class="clause-type">${esc(c.type)}</span>
      <span class="chiffre">${fmtScore(c.confiance)}</span>${relire}
      <span class="clause-sections">§ ${c.sections.map(esc).join(", ")}</span></summary>
    <blockquote>${esc(c.extrait)}</blockquote>
  </details>`;
}

function rendreV2({ corps, servi }) {
  const fiable = arrondi2(corps.confiance_globale) >= SEUIL_RELECTURE;
  const verdict = fiable
    ? `<span class="statut ok">${icone("ok")}fiable</span>`
    : `<span class="statut danger">${icone("danger")}Analyse non fiable</span>`;
  const clauses = corps.clauses.map(rendreClause).join("") || '<p class="vide">Aucune clause détectée.</p>';
  const warnings = corps.warnings.length
    ? `<ul class="warnings">${corps.warnings.map((w) => `<li>${icone("warn")}<span>${esc(w)}</span></li>`).join("")}</ul>`
    : "";
  return `${badgeGateway(servi)}
    <div class="fiabilite"><span>Indice de fiabilité</span>
      <strong class="chiffre-grand">${fmtScore(corps.confiance_globale)}</strong>${verdict}</div>
    ${clauses}${warnings}
    <p class="meta">${esc(corps.sections)} section(s) · ${fmtMs(corps.latence_ms)} · ${fmtEur(corps.cout_eur)} · ${esc(corps.model_version)}</p>`;
}

function remplir(id, r, rendre) {
  const cible = document.getElementById(`c-${id}`);
  cible.innerHTML = r.ok
    ? rendre(r)
    : `<div class="erreur" role="alert">${icone("danger")}<div>
         <strong>${r.statut ? `Erreur ${r.statut}` : "Erreur réseau"}</strong><p>${esc(r.detail)}</p></div></div>`;
  cible.setAttribute("aria-busy", "false");
  cible.classList.add("apparait");
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
  const bouton = $("#analyser");
  bouton.disabled = true;
  bouton.textContent = "Analyse…";
  const resultats = $("#resultats");
  try {
    if (new FormData(evenement.target).get("mode") === "gateway") {
      resultats.className = "resultats une-colonne";
      resultats.innerHTML = colonne("gateway", "", "Via la gateway canary");
      const r = await appeler("/analyse", contenu);
      remplir("gateway", r, r.ok && "confiance_globale" in r.corps ? rendreV2 : rendreV1);
    } else {
      resultats.className = "resultats";
      resultats.innerHTML = colonne("v1", "v1", "v1 · historique") + colonne("v2", "", "v2 · nouvelle version");
      await Promise.allSettled([
        appeler("/v1/analyse", contenu).then((r) => remplir("v1", r, rendreV1)),
        appeler("/v2/analyse", contenu).then((r) => remplir("v2", r, rendreV2)),
      ]);
    }
  } finally {
    bouton.disabled = false;
    bouton.textContent = "Analyser";
  }
});

$("#logo").innerHTML = icone("scale");
initTheme($("#theme"));
majCompteur();
```

- [ ] **Step 3 : Vérification navigateur (Review Focus 1, 2, 4, 5)**

Lancer : `MOCK=on uv run uvicorn app.main:app --port 8000`. Ouvrir `http://localhost:8000/` et vérifier :
1. *Exemple court* puis *Analyser* (mode comparaison) : deux colonnes, v1 « Contrat analysé en entier », v2 « Indice de fiabilité » + clauses dépliables.
2. *Exemple long* : le compteur affiche l'avertissement 16 000 ; après analyse, v1 montre **« Contrat tronqué »**, v2 l'indice de fiabilité et « N section(s) ».
3. Mode *Via la gateway* : une colonne avec le badge « servi par v1.0.0 » (ou v2 si un canary est actif).
4. **XSS** : coller `Contrat de test <img src=x onerror=alert(1)> résiliation <script>alert(2)</script>` → aucun `alert`, le texte des extraits s'affiche littéralement.
5. **413** : dans la console, `document.querySelector("#texte").value = "résiliation ".repeat(20000)` puis *Analyser* → colonne v1 remplie, colonne v2 « Erreur 413 » + message « découpez le contrat… ».
6. **Validation** : texte de 5 caractères → message sous le champ, aucun appel réseau.
7. **Double clic** : pendant l'analyse, le bouton affiche « Analyse… » et est désactivé ; un seul couple de requêtes dans l'onglet réseau.
8. **KPI** : bandeau P95 et canary remplis (lancer `make traffic` en parallèle) ; version toute en erreur (`MODE=erreurs make traffic`) → « — » sans erreur console ; arrêter l'app → « Indicateurs indisponibles », le formulaire reste utilisable.
9. Thème sombre, 375 px, navigation au clavier (Tab : focus visible sur chaque bouton, radio et lien).

Prendre des captures (clair, sombre, 375 px) de l'exemple long en mode comparaison.

- [ ] **Step 4 : Mettre le lien dans `make up`**

Dans `Makefile`, remplacer la ligne `@echo "app : http://localhost:8000/docs — proxy …"` de la cible `up` par :

```make
	@echo "interface : http://localhost:8000/ — pilotage : http://localhost:8000/pilotage — API : http://localhost:8000/docs — proxy : http://localhost:8080/_drift — dashboard brut : http://localhost:8501 — pilote : docker compose logs -f pilote"
```

- [ ] **Step 5 : Suite complète + lint**

Run: `MOCK=on uv run pytest -q && uv run ruff check .`
Expected: tout vert.

- [ ] **Step 6 : Commit**

```bash
git add app/web/index.html app/web/analyse.js Makefile
git commit -m "feat(ui): page Analyse — v1 | v2 côte à côte, gateway, KPI P95 et canary"
```

---

### Task 6 : Recette finale

**Files:** aucun nouveau (corrections éventuelles seulement).

- [ ] **Step 1 : Checklist UI UX PRO MAX (pré-livraison)**

Vérifier sur les deux pages : pas d'emoji comme icône ; `cursor: pointer` sur tout élément cliquable ; transitions 150–300 ms ; contraste ≥ 4,5:1 en clair et en sombre (outil de l'inspecteur du navigateur sur `--muted-fg` / `--bg`, `--on-accent` / `--accent`) ; focus visible ; `prefers-reduced-motion` (émulation DevTools) coupe squelette et apparition ; 375 / 768 / 1024 / 1440 px sans défilement horizontal.

- [ ] **Step 2 : Suite complète**

Run: `MOCK=on uv run pytest -q && uv run ruff check .`
Expected: tout vert.

- [ ] **Step 3 : Contrôle du périmètre**

Run: `git diff --stat aa92e7b -- app/api_v1.py app/api_v2.py app/gateway.py ops/dashboard.py docker-compose.yml Dockerfile`
Expected: aucune sortie.

- [ ] **Step 4 : Commit des corrections éventuelles**

```bash
git add -A app/web
git commit -m "fix(ui): corrections de recette (contraste, responsive)"
```
(uniquement s'il y a eu des corrections)
