# Refonte frontend — Analyse, Pilotage, Observabilité

*Spec de conception — 2026-09-23. Part de `origin/main` @ `62140b8`
(interface client SP « interface » fusionnée : `2026-09-22-interface-client-design.md`).
Système visuel : UI UX PRO MAX « Trust & Authority » déjà en place dans
`app/web/styles.css` (EB Garamond + Lato, navy + or, clair/sombre) — confirmé,
non remplacé.*

## 1. Contexte et décisions de cadrage

Le brief UX demande trois pages dédiées : l'**analyse** d'un contrat pour le
juriste, le **pilotage** du cycle de vie (canary, rollback) et
l'**observabilité** (traces, logs, métriques) adossée à **Sentry**.
L'existant sert deux pages (`/` et `/pilotage`, HTML/JS vanilla, ~780 lignes) ;
il n'y a ni page d'observabilité ni Sentry (spans OTel exportés sur la console,
`app/telemetry.py`).

**Décisions validées en brainstorming :**

| # | Question | Décision |
|---|---|---|
| D1 | Découpage | **Une spec, trois lots** livrables séparément : Analyse → Pilotage → Observabilité, précédés d'un socle commun. |
| D2 | « Auteur de l'analyse » | **Le moteur** : `modele` + version étiquetée (déjà renvoyés par l'API). Pas d'auteur humain (aucune authentification). |
| D3 | Actions de pilotage | **Lecture seule.** Promotion et rollback restent dans la chaîne (`spec.md` : « jamais à la main »). Aucun bouton d'action. |
| D4 | Niveau Sentry | **Backend + page de liens** : `sentry-sdk[fastapi]`, spans OTel existants routés vers Sentry, page `/observabilite` = résumé local + liens profonds. Pas de SDK navigateur. |
| D5 | DSN | Inconnu à ce jour → **DSN optionnel** : sans DSN, Sentry n'est pas initialisé et le comportement actuel (console) est conservé. |
| D6 | Approche technique | **Vanilla + SVG maison**, zéro dépendance frontend. Pas de Chart.js, pas de framework. |
| D7 | Configuration | **pydantic-settings** (règle du CLAUDE.md), limité aux réglages introduits ici (`SentrySettings`). Le reste de la config n'est pas migré. |

**Hors périmètre** : authentification / auteur humain, actions de pilotage
depuis l'UI, SDK Sentry navigateur, graphes temporels (fournis par Sentry),
migration du reste de la configuration vers pydantic-settings.

## 2. Socle commun (lot 0, livré avec le lot 1)

- **Navigation** à trois onglets — Analyse · Pilotage · Observabilité — dans les
  trois pages, `aria-current="page"` sur l'onglet actif ; bouton thème inchangé.
- **`styles.css`** : tokens existants conservés ; trois composants ajoutés :
  - `.tableau` — tableau dans un conteneur `overflow-x: auto` (pas de scroll de
    page sur mobile) ;
  - `.badge` — variantes `ok` / `warn` / `danger` / `neutre`, **toujours icône
    SVG + texte** ;
  - `.json-brut` — `<details>` repliable contenant un `<pre>` de la réponse.
- **`commun.js`** : formats existants réutilisés (`fmtMs`, `fmtScore`, `fmtEur`,
  `esc`) ; ajout d'un **timer** (`demarrerTimer(el) → arreter()`, basé sur
  `performance.now()`).
- **`main.py`** : route `GET /observabilite` (page provisoire « bientôt » au
  lot 0, complète au lot 3).

## 3. Lot 1 — Page Analyse (`/`)

Aucune modification backend. Contrats `/v1/analyse` et `/v2/analyse` inchangés
(protégés par `test_chaine.py`, dont `test_client_v1_fonctionne`).

```
 Traitement :  (•) v1   ( ) v2   ( ) Comparer v1 + v2          [ Analyser ]
 ⏱ v1  1,8 s  ✔ terminé        ⏱ v2  4,2 s  ⟳ en cours…

 Document              │ v1 · historique      │ v2 · nouvelle version
 Indice de fiabilité   │ non mesuré           │ 0,82  ✔ fiable
 Lecture complète      │ ✖ tronqué à 16 000   │ ✔ oui (6 sections)
 Auteur                │ llama3.2 · v1.0.0    │ llama3.2 · v2.0.3

 Clauses clés          │ v1                   │ v2 (extrait · fiabilité)
 Résiliation           │ ✔ détectée           │ « …préavis de 3 mois » 0,91
 Pénalités             │ —                    │ « …1 % par jour… » 0,62 ⚠

 Alertes et préconisations
  ⚠ v2 · clause « pénalités » : confiance 0,62 < 0,70 — relecture conseillée
  ⚠ v1 · contrat tronqué — préférez la v2
 ▸ JSON brut v1   ▸ JSON brut v2
```

- **Modes** : `v1`, `v2`, `comparer`. Le mode « Via la gateway » est retiré de
  cette page. En `comparer`, les deux requêtes partent **en parallèle** ; chaque
  colonne se remplit dès sa réponse.
- **Timer par endpoint** : chronomètre live, figé à la durée finale (succès ou
  erreur). **Remplace** les cartes « Latence P95 vs SLO » et « Canary en cours »
  et supprime leur polling de `/pilotage/resume`.
- **Informations affichées** (brief §3) :

| Information | v1 (`ReponseAnalyseV1`) | v2 (`ReponseAnalyseV2`) |
|---|---|---|
| Clauses clés | `clauses: list[str]` (type seul) | `clauses[].type`, `.extrait`, `.sections` |
| Fiabilité par clause | « non mesuré » | `clauses[].confiance` |
| Fiabilité document | « non mesuré » | `confiance_globale` + verdict vs seuil de relecture |
| Alertes et préconisations | déduite côté front : `tronque` → « contrat tronqué — préférez la v2 » | `warnings[]` |
| Lecture complète | `tronque` (non / oui) | toujours oui + `sections` |
| Auteur | `modele` · `version` | `modele` · `model_version` |

- « Non mesuré » n'est **jamais** affiché comme 0.
- Tableau de synthèse document + tableau clauses (lignes = union des types v1 ∪
  v2, triés), JSON brut repliable par colonne.
- **Erreurs** (422, `DocumentTropLong`, LLM indisponible) : message dans la
  colonne concernée, timer figé ; l'autre colonne n'est pas affectée.

## 4. Lot 2 — Page Pilotage (`/pilotage`, lecture seule)

Répond aux deux questions du brief : *le canary est-il plus efficace que la
version active ?* et *faut-il promouvoir, attendre ou revenir en arrière ?*

```
┌ VERDICT ───────────────────────────────────────────────────────────┐
│  ⏸ ATTENDRE   canary v2.0.3 à 10 % depuis 42 s                    │
│  palier 10 % : 14/20 requêtes, 42/60 s                             │
└────────────────────────────────────────────────────────────────────┘
 Canary vs active
 Signal          active    canary   bullet vs seuil   seuil    écart   état
 Score moyen     —         0,81     ▕████▌|           ≥ 0,75   —       ✔
 Score p10       —         0,68     ▕███▌ |           ≥ 0,65   —       ✔
 Taux d'erreur   3 %       4 %      ▕█|               ≤ 5 %    +1 pt   ✔
 Latence P95     2,1 s     5,8 s    ▕███▌ |           ≤ 8 s    +3,7 s  ✔
 Coût / analyse  0,0004 €  0,0011 €                   —        ×2,7    info
 Les 3 rétroactions : ↩ Rollback auto · ↗ Promotion · ⊕ Capture (7 candidats)
 ▸ Seuils en vigueur (motif : « … »)
 Traçabilité [Tous] [Déploiement] [Rollback] [Seuils]
```

### 4.1 Backend — ajouts à `ResumePilotage`

Ajouts **uniquement** (aucun champ existant modifié) ; schémas Pydantic dédiés
dans `app/pilotage.py`.

```python
class ConstatResponse(BaseModel):
    signal: str            # score_moyen | score_p10 | taux_erreur | latence_p95_ms
    valeur: float | None
    seuil: float
    ok: bool

class DecisionResponse(BaseModel):
    action: Literal["rollback", "promouvoir", "progresser", "attendre"]
    version: str
    motif: str
    pourcentage_suivant: int | None
    constats: list[ConstatResponse]

class SeuilsResponse(BaseModel):   # miroir de ops/seuils_pilotage.yaml
    fenetre_s: float
    minimum: int
    derive: dict[str, float]
    promotion: dict[str, Any]
    capture: dict[str, float]
    motif: str

class ResumePilotage(BaseModel):
    ...                                # champs existants inchangés
    decision: DecisionResponse | None  # None : pas de canary ou seuils invalides
    seuils: SeuilsResponse | None      # None : seuils invalides (alerte déjà émise)
```

**Calcul** dans `ops/dashboard.resume()`, sans nouvelle logique de décision :
1. pas de canary dans l'index → `decision = None` ;
2. `detecter_derive()` niveau `critique` → `action = "rollback"`, motif = motif
   de dérive, constats de dérive ;
3. sinon `evaluer_palier()` → son `action` (`attendre` / `progresser` /
   `promouvoir`), `motif`, `pourcentage_suivant`, `constats`.

### 4.2 Frontend

- **Carte Verdict** en tête : 4 états + « Aucun canary — `<active>` sert 100 % ».
  Icône + libellé + couleur (jamais la couleur seule), `aria-live="polite"`.
  Pour `attendre` faute de volume : deux jauges requêtes / durée (depuis
  `palier`).
- **Tableau canary vs active** : un **bullet chart SVG inline** par constat
  (valeur, repère au seuil, zone ok / ko), valeur et écart écrits en texte. Les
  valeurs active viennent de `par_version[active]`. Ligne **coût / analyse**
  (`cout_total_eur / requetes`), marquée « info » : n'entre pas dans le verdict.
- **Les 3 rétroactions** : règle (depuis `seuils`), dernière occurrence (dernier
  `rollback` / `promotion` / `capture` du `journal`), compteur `candidats`.
- **Seuils** : `<details>` listant `seuils` et son `motif`.
- **Traçabilité** : `journal` existant, filtres par type d'événement, origine
  (humaine / auto) en badge.
- Encart : « Promotion et rollback s'exécutent via la chaîne
  (`python -m ops.deploy piloter`) ». Rafraîchissement et bouton Pause actuels
  conservés.

## 5. Lot 3 — Observabilité + Sentry

### 5.1 Répartition des signaux

| Signal | Lieu |
|---|---|
| Métriques agrégées (requêtes, erreurs, P50/P95, appels LLM, tokens, troncatures, coût) | page `/observabilite` (depuis `ops/metrics.jsonl`) |
| Traces (`analyse.requete` → `chunking` → `llm.appel` ×N → `score.agregation`) | Sentry Performance |
| Erreurs et logs (`ErreurLLM`, `DocumentTropLong`, exceptions ; breadcrumbs structlog) | Sentry Issues |

### 5.2 Backend

- **Dépendances** : `sentry-sdk[fastapi]`, `pydantic-settings`.
- **`app/config.py`** :

```python
class SentrySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTRY_", env_file=".env", extra="ignore")
    dsn: str | None = None
    environment: str = "local"
    traces_sample_rate: float = 1.0
    ui_url: str | None = None      # ex. https://<org>.sentry.io/ — pour les liens profonds
```

- **Initialisation** (`telemetry.py`, au démarrage de l'app) : si `dsn` est
  défini, `sentry_sdk.init(...)` avec l'intégration FastAPI et l'intégration
  OpenTelemetry branchée sur le `TracerProvider` existant (spans actuels
  réutilisés, aucun réécrit ; API exacte du SDK vérifiée au plan). Sans `dsn` :
  rien n'est initialisé, export console inchangé.
- **Étiquetage** : `release` = version étiquetée servie ; tags `mardik.version`
  et `route` (filtrage « traces v2.0.3 »).
- **Confidentialité (bloquant)** : `send_default_pii=False` ; `before_send` et
  `before_send_transaction` retirent le corps de requête (`texte` du contrat) ;
  aucun attribut de span ne contient de texte de contrat.
- **Endpoint** `GET /observabilite/resume` → `ResumeObservabilite` :

```python
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
    liens: LiensSentry | None      # None si inactif ou ui_url absent

class ResumeObservabilite(BaseModel):
    fenetre_s: float
    par_route: list[StatsRoute]
    erreurs_recentes: list[ErreurRecente]   # 10 dernières
    sentry: EtatSentry
```

  Routeur mince (`app/observabilite.py`) ; agrégation dans le service en
  réutilisant `ops/signaux.py` (`agreger`, `filtrer`). Fenêtre = celle de
  `seuils_pilotage.yaml`.

### 5.3 Frontend

- `observabilite.html` + `observabilite.js` : tableau par route, dernières
  erreurs (lien Sentry par ligne si actif), bloc « Explorer dans Sentry »
  (Traces de la version active et canary, Issues, Performance).
- Sans Sentry : bandeau neutre « Sentry non configuré — traces et logs sur la
  console du serveur. Définir `SENTRY_DSN` pour activer. », liens masqués.

## 6. Tests

| Lot | Tests |
|---|---|
| 0 | `test_pages_servies` paramétré avec `/observabilite` ; fichiers statiques nouveaux servis. |
| 1 | Aucun test backend nouveau ; suite existante verte (contrats v1/v2 inchangés). |
| 2 | Unitaires (`test_dashboard` / `test_pilotage`) : verdict `rollback` prioritaire sur le palier ; `attendre` (volume) ; `attendre` (critère non tenu) ; `progresser` ; `promouvoir` ; pas de canary → `decision is None`. Intégration (`test_interface`) : `test_resume_avec_canary` étendu à `decision` et `seuils` ; seuils invalides → `seuils is None` + alerte. |
| 3 | Sans DSN : Sentry non initialisé, `sentry.actif is False`, `liens is None`. `before_send` : un événement contenant `texte` en ressort sans. `GET /observabilite/resume` sur métriques de test : stats par route, 10 erreurs max. Aucun appel réseau. |

Frontend : pas de framework de test JS (logique de décision testée côté Python ;
le JS rend). Vérification visuelle de chaque lot en lançant l'app : captures
375 px et 1440 px, thèmes clair et sombre.

## 7. Qualité UI (checklist UI UX PRO MAX, chaque lot)

- Contraste ≥ 4,5:1 dans les deux thèmes (tokens existants).
- Aucun état porté par la seule couleur : icône SVG + texte ; pas d'emoji
  comme icône.
- Clavier : focus visible, onglets, `<details>` et filtres accessibles ;
  `aria-live="polite"` sur timers et verdict (mise à jour annoncée à la fin,
  pas à chaque tick).
- Squelette de chargement au-delà de 300 ms ; `prefers-reduced-motion`
  respecté ; cibles ≥ 44 px.
- Tableaux en conteneur scrollable : pas de scroll horizontal de page.

## 8. Risques

| Risque | Parade |
|---|---|
| Texte de contrat envoyé à Sentry | `before_send` + test obligatoire ; lot 3 non livrable sans lui. |
| v2 lente (13 s observées) en mode comparer | Colonnes indépendantes, v1 affichée dès prête, timers visibles. |
| Rafraîchissement pilotage trop agressif | Intervalle et Pause actuels conservés. |
| Dérive entre verdict UI et décision du pilote | Même fonctions (`detecter_derive`, `evaluer_palier`), mêmes seuils ; aucune logique dupliquée côté JS. |
