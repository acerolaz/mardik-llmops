# Sous-projet 3 — Routage & déploiement (gateway, canary, rollback)

*Spec de conception — 2026-09-22. Troisième des quatre sous-projets de la v2
Mardik (cf. `2026-09-21-moteur-v2-design.md`, §1). Part de `main` @ `41297d9`
(sous-projets 1 et 2 fusionnés).*

## 1. Contexte et décisions de cadrage

Le CTO exige une livraison automatisée et réversible : la v2 remplace la v1
progressivement (canary 10 → 50 → 100 %), le client v1 ne casse jamais, et le
rollback s'exécute via la chaîne, en une action, sans rebuild. Le repo fournit
le registre (`ops/registry`, qui enregistre) ; restent à écrire la gateway
(`app/gateway.py`), les transitions de déploiement (`ops/deploy.py`) et les
jobs CI qui agissent sur la prod.

**Décisions validées en brainstorming :**

| # | Question | Décision |
|---|---|---|
| D1 | Périmètre | **Cœur routage** : gateway, `deployer_canary`, `promouvoir`, `rollback`, `installer`, CLI, jobs CI de prod. `publier` est livré (SP2) ; `surveiller` et la promotion pilotée par les métriques → SP4. |
| D2 | Dépendance au moteur v2 | Implémentation **après** la fusion de SP1 et SP2 : les tests d'acceptance visés passent au vert dès ce sous-projet. |
| D3 | CI ↔ prod | **Runner auto-hébergé** sur la machine de démo (`runs-on: [self-hosted, mardik]`) ; il agit directement sur le registre de prod (volume `./ops` du docker compose). Lint, tests, gates et build restent sur `ubuntu-latest`. |
| D4 | Architecture de la gateway | **Router mince + service** `app/routage.py` : la route délègue ; le service lit l'index, choisit la version, charge le bundle livré et choisit le moteur selon la stratégie. |
| D5 | `CANARY_PERCENT` | **Écart au stub** : ne force **pas** le pourcentage dans la gateway (sinon une promotion à 50 % serait sans effet en prod). Il reste la valeur par défaut de `deployer_canary`. |
| D6 | Écriture de l'index | **Écart au [FOURNI]** : `Registry.ecrire_index` devient atomique (fichier temporaire + `os.replace`) — la gateway lit l'index à chaque requête et ne doit jamais lire un JSON à moitié écrit. |

**Vérifié sur `main` (MOCK=on)** : 154 tests verts, 4 rouges —
`test_rollback_en_une_operation` et `test_promotion_canary_puis_totale`
(ce sous-projet), `test_dashboard_par_version` et
`test_journal_derive_et_rollback_automatique` (SP4).

## 2. Périmètre

**Dans le périmètre**

- `app/gateway.py` : `choisir_version`, `GET /gateway/etat`, `POST /analyse`, dépendances `get_tirage` et `get_fabrique_client`
- `app/routage.py` : nouveau — `Cible`, `resoudre`, `analyser`, `AucuneVersionActive`, `StrategieInconnue`
- `app/main.py` : gestionnaires d'exception des deux erreurs de routage (503) ; docstring
- `ops/registry/__init__.py` : `ecrire_index` atomique (seule modification du fichier)
- `ops/deploy.py` : `deployer_canary`, `promouvoir`, `rollback`, `installer`, helpers `_verrou` et `_transition` ; CLI `installer`, option `--origine`
- `.github/workflows/ci.yml` : `outputs.version` du job `publication` ; job `deploiement-canary`
- `.github/workflows/promotion.yml`, `.github/workflows/rollback.yml` : nouveaux
- `.gitignore` : `ops/registry/index.lock`, `ops/registry/index.json.tmp`
- `docs/exploitation.md` : §4 (mécanique du canary) et §5 (procédure de rollback, installation du runner)
- Tests unitaires et d'intégration (§7)

**Hors périmètre**

- `surveiller`, détection de dérive, rollback automatique, promotion pilotée par les métriques, dashboard, journal des ajustements de seuils → sous-projet 4
- `app/api_v1.py`, `models/v1/`, `app/llm_client.py`, `app/telemetry.py`, `tests/conftest.py` (fournis, non modifiés)
- Le moteur v2 et le gate/publication : utilisés, non modifiés (hormis l'`outputs.version` du job `publication`)
- Push d'image Docker, orchestrateur de conteneurs : la « prod » est le docker compose de démo
- `docs/exploitation.md` §6 et §7 (surveillance, preuve d'exécution) → SP4

## 3. Architecture et flux

```
                        index.json (lu à CHAQUE requête)
                              │
POST /analyse ──► gateway.analyse (mince)
  Depends: registry, telemetry, tirage, fabrique_client
                              │
                  routage.analyser(texte, …)
                    ├─ resoudre(registry, tirage) ─► Cible(version, bundle, moteur)
                    │     choisir_version(active, canary, %, tirage)
                    │     registry.bundle(version)       ← l'artefact livré
                    │     MOTEURS_HTTP[bundle.strategie] ← analyser_v1 | analyser_v2
                    └─ moteur(texte, fabrique_client(bundle), telemetry)
                              │
       ◄── ReponseAnalyseV1 | ReponseAnalyseV2 + en-tête X-Mardik-Version

CI (runner self-hosted)            ops/deploy.py                 ops/registry/
  deploiement-canary ─► installer ─┐                             vX.Y.Z/ (copie)
                        canary ────┼─► _transition (verrou) ──►  index.json (atomique)
  promotion.yml ──────► promouvoir ┤                             journal.jsonl (+1)
  rollback.yml ───────► rollback ──┘
```

Le registre enregistre, `deploy` décide, la gateway lit. Aucun redémarrage
n'est nécessaire : une transition réécrit l'index, la requête suivante le relit.

## 4. Gateway et routage

### 4.1 `app/gateway.py` — couche HTTP

- `choisir_version(active, canary, canary_percent, tirage) -> str` : pure ;
  renvoie `canary` si `canary is not None and tirage < canary_percent`, sinon
  `active`. Reste dans ce module (le test d'acceptance l'y importe).
- Dépendances (les deux premières existent et sont surchargées par `conftest.py`) :
  - `get_registry() -> Registry`
  - `get_telemetry() -> Telemetry`
  - `get_tirage() -> float` : `random.uniform(0, 100)`
  - `get_fabrique_client() -> Callable[[Bundle], LLMClient]` : renvoie `LLMClient`
- `EtatGateway(BaseModel)` : `active: str | None`, `canary: str | None`, `canary_percent: int`.
- `GET /gateway/etat` → `EtatGateway`, lu depuis `registry.index()`.
- `POST /analyse` (`def` synchrone, exécuté dans le pool de threads, comme `/v1`
  et `/v2`) : appelle `routage.analyser`, pose `response.headers["X-Mardik-Version"]`,
  renvoie la réponse du moteur. `response_model=ReponseAnalyseV1 | ReponseAnalyseV2`.
  `ErreurLLM` → `HTTPException(503, "fournisseur LLM indisponible : …")`.
- Docstring mise à jour : la règle « `CANARY_PERCENT` force le pourcentage » est
  retirée (D5).

### 4.2 `app/routage.py` — service

```python
MOTEURS_HTTP: dict[str, Moteur] = {
    "monolithique": analyser_v1,
    "map_reduce_clauses": analyser_v2,
}

@dataclass(frozen=True)
class Cible:
    version: str
    bundle: Bundle
    moteur: Moteur

def resoudre(registry: Registry, tirage: float) -> Cible: ...
def analyser(texte, registry, telemetry, tirage, fabrique_client) -> tuple[str, BaseModel]: ...
```

`resoudre` :
1. lit `registry.index()` ; `active is None` → `AucuneVersionActive` ;
2. `version = choisir_version(active, canary, canary_percent, tirage)` ;
3. `bundle = registry.bundle(version)` — jamais `models/` : on sert ce qui a été
   livré ; le `bundle.version` vaut le tag (`v2.0.0`), donc les `Mesure`
   enregistrées par les moteurs sont attribuées à la bonne version ;
4. `MOTEURS_HTTP.get(bundle.strategie)` absent → `StrategieInconnue(version, strategie)`.

Pas de cache de bundle : un tag est immuable, mais relire un YAML par requête ne
coûte presque rien (YAGNI). Ajouter une stratégie = ajouter une entrée à
`MOTEURS_HTTP` (OCP). La table est distincte de `eval.run_eval.MOTEURS`, dont
les moteurs renvoient des listes de types pour l'évaluation, pas des réponses HTTP.

### 4.3 Erreurs — `app/main.py`

| Exception | Statut | Détail |
|---|---|---|
| `AucuneVersionActive` | 503 | « aucune version active dans le registre » |
| `StrategieInconnue` | 503 | « version vX.Y.Z : stratégie '<s>' non routable » |
| `ErreurLLM` (dans la route) | 503 | comme `/v1` et `/v2` |
| `DocumentTropLong` (moteur v2) | 413 | gestionnaire existant |
| corps invalide | 422 | Pydantic |

Deux gestionnaires `@app.exception_handler` sont ajoutés sur le modèle de
`DocumentTropLong`.

**Limite connue** : le libellé `route` des `Mesure` reste celui du moteur
(`/v1/analyse`, `/v2/analyse`) ; `api_v1` est intouchable. La surveillance
(SP4) filtre par `version`, ce qui suffit.

## 5. Déploiement — `ops/deploy.py`

### 5.1 Écriture atomique de l'index — `ops/registry/__init__.py`

`ecrire_index` écrit dans `index.json.tmp` puis `os.replace(tmp, index.json)`.
C'est la seule modification du fichier fourni.

### 5.2 Verrou et transition

```python
@contextmanager
def _verrou(registry: Registry) -> Iterator[None]:
    # fcntl.flock(LOCK_EX) sur <root>/index.lock

def _transition(registry, evenement, calcul, *, origine, **details) -> dict:
    with _verrou(registry):
        avant = _etat(registry.index())      # sans "mis_a_jour"
        apres = calcul(dict(avant))          # lève ErreurDeploiement si refus
        registry.ecrire_index(apres)
        registry.journaliser(evenement, avant=avant, apres=apres,
                             origine=origine, **details)
    return registry.index()
```

Deux écrivains concurrents existent : la CI et la surveillance automatique
(SP4). Le verrou évite qu'un read-modify-write simultané perde une écriture.
Toute `ErreurRegistre` levée pendant la transition (version inconnue…) est
convertie en `ErreurDeploiement`.

**Entrée de journal** : `{ts, date, evenement, version?, avant, apres, origine, motif?, pourcentage?}`.
`origine` ∈ `manuel` (défaut), `ci:<acteur GitHub>`, `auto` (SP4).

### 5.3 Transitions

Signatures (compatibles avec les tests d'acceptance ; `origine` est keyword-only) :

```python
deployer_canary(version, pourcentage=None, registry=None, *, origine="manuel") -> dict
promouvoir(version, registry=None, *, origine="manuel") -> dict
rollback(registry=None, motif="manuel", *, origine="manuel") -> dict
installer(version, depuis, registry=None, *, origine="manuel") -> dict
```

| Transition | Effet sur l'index | Refusée (`ErreurDeploiement`) si |
|---|---|---|
| `deployer_canary` | `canary = version`, `canary_percent = pourcentage` (défaut : `CANARY_PERCENT` de l'environnement, sinon 10 ; valeur non entière → `ErreurDeploiement`). Rappel avec la même version = étape de progression. Journal `canary`. | version inconnue ; version déjà active ; pourcentage hors [1, 99] ; canary d'une **autre** version en cours |
| `promouvoir` | `precedente = active`, `active = version`, `canary = None`, `canary_percent = 0`. Journal `promotion`. | version inconnue ; version déjà active ; canary d'une **autre** version en cours |
| `rollback` | canary en cours → retiré (`canary = None`, 0 %), `active` et `precedente` inchangées ; sinon `active = precedente`, `precedente = None`. Journal `rollback` avec `motif`. | ni canary ni `precedente` : rien à annuler |

`promouvoir` n'exige pas de canary préalable (`test_rollback_en_une_operation`
promeut directement) : c'est la chaîne CI qui impose l'ordre canary → promotion.
Le rollback est à **un niveau** (N-1) : un second rollback consécutif est refusé,
ce qui empêche un va-et-vient v1 ↔ v2 déclenché par la surveillance. Aucun
rebuild : seul l'index bascule vers une version déjà présente dans le registre.

### 5.4 `installer`

Installe dans le registre de prod une version publiée par la CI (artefact
`mardik-vX.Y.Z`, cf. SP2/D3). Sous `_verrou`, sans modifier l'index.

| Situation | Comportement |
|---|---|
| `depuis/manifest.json` absent, ou `manifest["version"] != version` | `ErreurDeploiement` |
| version absente du registre | copie de `depuis/` vers `<root>/<version>/` ; journal `installation` (`version`, `commit`, `note_eval`, `empreinte`, `origine`) |
| version présente, même `empreinte` | rien (relance idempotente), pas d'entrée de journal |
| version présente, empreinte différente | `ErreurDeploiement` (un tag est immuable) |

Le `journal.jsonl` contenu dans l'artefact n'est pas fusionné : le journal de
prod ne trace que ce qui s'est passé en prod.

### 5.5 Ligne de commande

```
python -m ops.deploy installer vX.Y.Z --depuis DOSSIER [--origine O]
python -m ops.deploy canary vX.Y.Z [--pourcentage N] [--origine O]
python -m ops.deploy promouvoir vX.Y.Z [--origine O]
python -m ops.deploy rollback [--motif M] [--origine O]
```

Succès : l'index (ou le manifeste pour `installer`) est affiché en JSON, code 0.
`ErreurDeploiement` : `REFUSÉ : …` sur stderr, code 1 (comportement existant de
`main`). Pas de cible `make` de déploiement : la procédure documentée passe par
les workflows (« jamais à la main »).

## 6. Chaîne CI

### 6.1 Règles communes aux jobs de prod

Tout job qui touche la prod :

```yaml
runs-on: [self-hosted, mardik]
environment: production
concurrency:
  group: mardik-production
  cancel-in-progress: false
env:
  REGISTRY_PATH: ${{ vars.MARDIK_REGISTRY_PATH }}
```

`MARDIK_REGISTRY_PATH` est le chemin absolu du `ops/registry` monté par le
docker compose de démo ; sans lui, le runner écrirait dans son propre checkout.
Étapes communes : `actions/checkout@v4`, `astral-sh/setup-uv@v3`, `uv sync`.
Aucun job self-hosted ne s'exécute sur `pull_request` : le code d'un fork ne
tourne jamais sur la machine de prod. `workflow_dispatch` est réservé aux
collaborateurs ayant le droit d'écriture.

### 6.2 `ci.yml`

- `publication` : l'étape « Étiquetage dans le registre » reçoit `id: etiquetage`
  et écrit aussi `version=$VERSION` dans `$GITHUB_OUTPUT` ; le job déclare
  `outputs: version: ${{ steps.etiquetage.outputs.version }}`. Rien d'autre ne change.
- `deploiement-canary` (conditions `if` inchangées, règles §6.1) :
  1. échec explicite si `needs.publication.outputs.version` est vide ;
  2. `actions/download-artifact@v4` de `mardik-<version>` dans `${{ runner.temp }}/mardik` ;
  3. `uv run python -m ops.deploy installer $VERSION --depuis "$RUNNER_TEMP/mardik/ops/registry/$VERSION" --origine "ci:$GITHUB_ACTOR"` ;
  4. `uv run python -m ops.deploy canary $VERSION --pourcentage 10 --origine "ci:$GITHUB_ACTOR"`.

Les valeurs issues du contexte GitHub passent par `env:` et non par
interpolation directe dans `run:` (injection de script).

### 6.3 `promotion.yml` (nouveau)

`workflow_dispatch` ; entrées `version` (string, requise) et `etape`
(choice : `50`, `100`). Un job (règles §6.1) :
`50` → `ops.deploy canary $VERSION --pourcentage 50 --origine ci:<acteur>` ;
`100` → `ops.deploy promouvoir $VERSION --origine ci:<acteur>`.
Dans ce sous-projet la décision de promouvoir est humaine ; SP4 branchera
`surveiller` et les critères de promotion sur ces mêmes commandes.

### 6.4 `rollback.yml` (nouveau)

`workflow_dispatch` ; entrée `motif` (string, requise). Un job (règles §6.1) :
`ops.deploy rollback --motif "$MOTIF" --origine ci:<acteur>`. Une action unique,
depuis l'onglet Actions ou `gh workflow run rollback.yml -f motif="…"`.
Pas d'artefact : la version N-1 est déjà dans le registre de prod.

### 6.5 `docs/exploitation.md`

- §4 Déploiement progressif : 10 % automatique après publication, 50 % et 100 %
  via `promotion.yml` ; ce qui est regardé avant de promouvoir (renvoi à SP4 pour
  les critères automatiques).
- §5 Procédure de rollback : la commande, qui a le droit, vérification
  (`GET /gateway/etat`, dernière entrée du journal), installation du runner
  (labels `self-hosted, mardik`, variable `MARDIK_REGISTRY_PATH`, `uv`).

## 7. Tests

TDD : chaque test est écrit et vu rouge avant le code.

### 7.1 Unitaires — `tests/unit/`

| Fichier | Couvre |
|---|---|
| `test_choisir_version.py` | sans canary → active ; 0 % ; 99 % ; borne `tirage == percent` → active ; 30 tirages sur `range(100)` vers le canary à 30 % |
| `test_routage.py` | table stratégie → moteur ; bundle issu du registre (`bundle.version == "v1.0.0"`) ; `AucuneVersionActive` ; `StrategieInconnue` (bundle modifié dans un registre `tmp_path`) |
| `test_transitions.py` | chaque transition et chacun de ses refus (§5.3) ; journal (`avant`, `apres`, `origine`, `motif`) ; rollback à un niveau ; les quatre cas d'`installer` ; `ErreurRegistre` convertie ; écriture atomique (`os.replace` qui lève → `index.json` intact) |
| `test_workflows.py` | dans `ci.yml`, `promotion.yml`, `rollback.yml` : tout job `self-hosted` a le groupe de concurrence `mardik-production` ; aucun n'est exécutable sur `pull_request` (déclencheurs du workflow + `if` du job). PyYAML lit la clé `on:` comme `True` : le test lit `data.get("on", data.get(True))`. |

### 7.2 Intégration — `tests/integration/`

| Fichier | Couvre |
|---|---|
| `test_gateway.py` | `TestClient` (fixture `client`) avec `get_tirage` surchargé : en-tête `X-Mardik-Version` ; `GET /gateway/etat` ; promotion puis rollback pris en compte sans nouvelle app ; 503 `AucuneVersionActive`, `StrategieInconnue`, `ErreurLLM` (via `get_fabrique_client` renvoyant un client qui lève) ; 422 |
| `test_cli_deploy.py` | `installer`, `canary`, `promouvoir`, `rollback` avec `REGISTRY_PATH` sur `tmp_path` : code 0 et JSON ; refus → code 1 et `REFUSÉ` sur stderr |

### 7.3 Workflows

Pas d'exécution locale possible : `test_workflows.py` + `actionlint` s'il est
installé. La démo de bout en bout sur le runner est une vérification manuelle,
décrite dans `docs/exploitation.md` §5.

### 7.4 Critères de fin

- `test_rollback_en_une_operation` et `test_promotion_canary_puis_totale` verts ;
- `test_client_v1_fonctionne` vert ;
- `MOCK=on uv run pytest -q` : seuls les deux tests SP4 restent rouges ;
- `uv run ruff check .` propre.

## 8. Risques

| Risque | Parade |
|---|---|
| Runner hors ligne : canary et rollback bloqués | Le job reste en file d'attente (visible dans Actions) ; procédure de redémarrage du runner dans `exploitation.md` §5. En dernier recours, la CLI sur la machine reste tracée (`origine` = `manuel`). |
| `MARDIK_REGISTRY_PATH` mal réglé : la CI modifie un registre que l'app ne lit pas | Le job affiche `GET /gateway/etat` après la transition ; le chemin est documenté §5. |
| Concurrence CI / surveillance SP4 | Verrou `fcntl` sur toute transition (§5.2), `concurrency` GitHub entre jobs de prod. |
| Lecture d'un index partiellement écrit | Écriture atomique (D6). |
| Exécution de code de fork sur la machine de prod | Aucun job self-hosted sur `pull_request` ; invariant testé (§7.1). |
| `fcntl` indisponible sous Windows | Runner et conteneurs sous macOS/Linux ; hors cible. |
