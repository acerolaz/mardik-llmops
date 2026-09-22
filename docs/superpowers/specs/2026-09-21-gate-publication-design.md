# Sous-projet 2 — Gate d'évaluation & publication

*Spec de conception — 2026-09-21. Deuxième des quatre sous-projets de la v2
Mardik (cf. `2026-09-21-moteur-v2-design.md`, §1). Part de `main` @ `0cacb87`
(sous-projet 1 fusionné).*

## 1. Contexte et décisions de cadrage

Le brief exige que toute fusion produise une version étiquetée (code + modèle +
config), testée par des gates bloquants, et qu'un gate en échec bloque la
livraison. Le repo fournit le registre (`ops/registry`, [FOURNI]) ; restent à
écrire le gate (`eval/run_eval.py::evaluer`), la publication
(`ops/deploy.py::publier`) et les jobs CI correspondants.

**Décisions validées en brainstorming :**

| # | Question | Décision |
|---|---|---|
| D1 | Gate en CI vs vrai modèle | **Deux niveaux** : gate CI en `MOCK=on` (12 contrats, chaque PR/fusion, bloquant) ; gate de release sur le vrai modèle (tag `v*` ou `workflow_dispatch`, `essais_eval` passes). Le rapport et le manifeste portent `mode_eval: "mock" \| "reel"`. |
| D2 | Numérotation des versions | **Patch auto-incrémenté** : la base `vX.Y` vient du bundle ; `publier` sans version prend `vX.Y.(max+1)` des versions connues. Un tag git fixe la version explicitement (refus si déjà connue). |
| D3 | Registre en CI | **Tags git = mémoire des versions ; registre = artefact de build.** La CI calcule la version sur l'union tags git ∪ registre, publie dans le registre du runner, l'envoie en artefact `mardik-vX.Y.Z` et pousse le tag. En local, `ops/registry/` reste inchangé. |
| D4 | Seuils du gate | **Fichier versionné `eval/seuils.yaml`** (note, latence P95, coût, `motif`). Les seuils par contrat restent `seuil_note` dans `attendus.jsonl`. Chaque rapport recopie les seuils appliqués. |

**Vérifié sur `main` (MOCK=on)** : rappel v2 = 1,0 sur les 12 contrats ; v1 =
0,50 / 0,40 / 0,33 sur c07 / c10 / c12 (troncature à 16 000 caractères). Les
critères « v2 passe » et « v2 > v1 sur les longs » sont donc atteignables.

## 2. Périmètre

**Dans le périmètre**

- `eval/run_eval.py` : `evaluer`, `Rapport` enrichi, `charger_seuils`, option CLI `--sortie`
- `eval/seuils.yaml` : nouveau
- `ops/deploy.py` : `publier`, `versions_connues`, `prochaine_version`, `charger_rapport`, CLI `publier [version] --commit --rapport`
- `.github/workflows/ci.yml` : renommage de `llmops.yml` (`git mv`, nom attendu par le brief) ; jobs `gate-evaluation`, `gate-release` (nouveau), `publication`
- `README.md` : les deux références à `llmops.yml` → `ci.yml`
- `Makefile` : cible `ci` avec le gate
- Tests unitaires et d'intégration (§7)

**Hors périmètre**

- `ops/registry/__init__.py`, `app/llm_client.py`, `app/telemetry.py`, `tests/conftest.py`, `models/v1/`, `app/api_v1.py` (fournis, non modifiés)
- Le moteur v2 (`app/pipeline/`, `app/api_v2.py`) : utilisé, non modifié
- `deployer_canary`, `promouvoir`, `rollback`, `surveiller`, gateway → sous-projet 3
- Journal de pilotage des ajustements de seuils, dashboard → sous-projet 4
- Push d'image Docker vers un registre de conteneurs
- Enregistrement des fixtures réelles (`make fixtures`) : opération manuelle, hors code

## 3. Architecture et flux

```
eval/seuils.yaml ─┐
attendus.jsonl ───┼─► evaluer(version) ──► Rapport ──► history.jsonl (+1 ligne)
contrats/*.txt ───┘        │                  │
                           │                  └──► --sortie rapport.json
              moteur selon bundle.strategie
              (analyser_v1 | analyser_v2)
                           │
                     Mesure → MetricsStore dédié (latence, coût)

rapport.json ──► publier(version?) ──► gate passé ? ──non──► journal « publication_refusee »
                    │                      │ oui               + ErreurDeploiement
       versions_connues (registre ∪ tags)  ▼
       prochaine_version / validation   registry.etiqueter + journal « publication »
                                           │
                                  manifest.json (JSON sur stdout)
```

`evaluer` mesure et juge ; `publier` décide de l'étiquetage ; le registre
enregistre. Aucun des deux ne connaît GitHub : la CI n'est qu'un appelant de
leurs lignes de commande.

## 4. Le gate — `eval/run_eval.py`

### 4.1 Seuils — `eval/seuils.yaml`

```yaml
note_min: 0.75
latence_p95_max_ms: 8000
cout_moyen_max_eur: 0.15
motif: "seuils initiaux — contraintes client (P95 < 8 s, < 0,15 € par analyse), note = seuil_note de base"
```

`CHEMIN_SEUILS` = variable d'environnement `SEUILS_PATH`, défaut
`eval/seuils.yaml` ; `evaluer` accepte aussi `seuils: Path = CHEMIN_SEUILS`.

`charger_seuils(chemin=CHEMIN_SEUILS) -> Seuils` (dataclass figée :
`note_min`, `latence_p95_max_ms`, `cout_moyen_max_eur`, `motif`). Fichier
absent, clé manquante ou valeur non numérique → `ErreurSeuils(ValueError)`
nommant le fichier et la clé.

Les paramètres `seuil`, `latence_max_ms`, `cout_max_eur` de `evaluer` (et les
options CLI) passent à `None` par défaut : `None` = valeur du fichier ; une
valeur fournie **surcharge** celle du fichier (usage local et tests :
`seuil=0.75`, `latence_max_ms=0.0001`). La CI n'utilise aucune surcharge.

### 4.2 Déroulé de `evaluer`

1. **Charger** : bundle (`charger_bundle`, existant), attendus, seuils, contrats
   (restreints à `sous_ensemble` si fourni). Chaque contrat retenu doit exister
   dans `attendus` **et** dans `contrats/` : contrat annoté sans fichier dans
   `contrats/` → `FileNotFoundError` ; contrat demandé absent de
   `attendus.jsonl` → `ValueError` — toutes deux avant toute analyse (un golden
   dataset incohérent ne passe jamais). `n = n_essais if n_essais is not None
   else bundle.parametres.get("essais_eval", 1)` ; `n < 1` → `ValueError`
   également avant toute analyse (donc code 2 en CLI).
2. **Moteur** : table `MOTEURS = {"monolithique": _types_v1, "map_reduce_clauses": _types_v2}`.
   Chaque adaptateur prend `(texte, client, telemetry)` et renvoie
   `list[str]` des types trouvés (`analyser_v1(...).clauses` ;
   `[c.type for c in analyser_v2(...).clauses]`). Stratégie absente de la
   table → `ValueError("stratégie 'x' sans moteur d'évaluation")`. Ajouter une
   v3 = ajouter une entrée.
3. **Analyser** : `n = n_essais or bundle.parametres.get("essais_eval", 1)`.
   Pour chaque essai, chaque contrat : un `LLMClient(bundle)` et la télémétrie
   (`telemetry` injectée, sinon `build_telemetry(span_exporter=NoopSpanExporter(),
   metrics_path=…)` avec `METRICS_EVAL_PATH`, défaut `eval/.metrics_eval.jsonl`,
   ignoré par git — jamais `ops/metrics.jsonl`, que lit le dashboard de
   production) ; la dernière `Mesure` du store (`metriques.lire()[-1]`, le gate
   est séquentiel) fournit `latence_ms` et `cout_eur`.
   `ErreurLLM` ou `DocumentTropLong` → le contrat reçoit une note 0 pour cet
   essai et un motif `"c07 : erreur LLM — <message>"` / `"c07 : document trop
   long — <message>"`. Le gate échoue ; il ne plante pas.
4. **Noter** (fonction pure `noter_contrat(trouves, attendu) -> dict`) :
   `note = |attendues ∩ trouvées| / |attendues|`, `trouvees` / `manquantes`
   (listes dans l'ordre de `clauses_attendues`), `passe = note >= seuil_note`.
   Sur plusieurs essais : `note` = moyenne ; `trouvees` / `manquantes` du
   dernier essai ; `latence_ms` / `cout_eur` = moyennes.
5. **Agréger** (fonction pure `agreger(par_contrat, latences, couts, seuils) -> (note, p95, cout_moyen, motifs)`) :
   note globale = moyenne des notes par contrat ; P95 (`_p95` existant) sur
   toutes les latences de tous les essais ; coût moyen par analyse. Motifs :
   - `"note 0.620 < seuil 0.75"`
   - `"c10 : note 0.71 < seuil_note 0.80"`
   - `"latence P95 9120 ms ≥ 8000 ms"`
   - `"coût moyen 0.1800 € ≥ 0.15 €"`
   plus les motifs d'erreur de l'étape 3. `passe = motifs == []`.
6. **Écrire** : `Rapport` ; une ligne JSON ajoutée à `historique` s'il n'est pas `None`.

### 4.3 `Rapport`

Champs existants conservés (`version`, `date`, `essais`, `note`,
`par_contrat`, `latence_p95_ms`, `cout_moyen_eur`, `passe`, `motifs`,
`seuil`), plus :

| Champ | Valeur |
|---|---|
| `mode_eval` | `"reel"` si `mode_mock() == "off"`, sinon `"mock"` (`record` appelle le vrai modèle mais sert à enregistrer : `"mock"`) |
| `seuils` | dict des seuils appliqués (après surcharge), `motif` compris |
| `bundle_empreinte` | `bundle.empreinte()` |

`version` = `bundle.version` (ex. `v2.0.0`), comme l'attend
`test_gate_evaluation_note_par_version`. `seuil` = `seuils.note_min`
appliqué. `Rapport.depuis_dict(d)` reconstruit un rapport à partir de son JSON.

### 4.4 Ligne de commande

`python -m eval.run_eval --version v2 [--essais N] [--contrats c01,c07]
[--seuil X] [--latence-max-ms X] [--cout-max-eur X] [--sortie chemin.json]
[--historique chemin.jsonl]`

- `--historique` (défaut `eval/history.jsonl`) : permet aux tests de ne pas écrire dans le repo.
- `--sortie` écrit `rapport.to_dict()` en JSON (UTF-8, indenté).
- Codes de sortie : **0** gate passé, **1** gate en échec, **2** gate mal
  configuré (`ErreurSeuils`, golden dataset incohérent, stratégie inconnue) —
  message sur stderr.

## 5. Publication — `ops/deploy.py`

### 5.1 Fonctions pures

- `versions_connues(registry) -> set[str]` : `registry.versions()` ∪
  `_tags_git()`. `_tags_git()` = tous les tags `v*` (filtrés par
  `MOTIF_VERSION`) **moins** ceux qui pointent sur HEAD (`git tag -l "v*"
  --points-at HEAD`) : un tag posé **sur le commit publié** est son propre
  label, pas une version concurrente — sans cela, un run déclenché par le tag
  `v2.0.3` refuserait `v2.0.3` comme « déjà publiée ». On n'utilise pas
  `--no-contains HEAD` : ce filtre exclut aussi les tags posés sur des
  commits **descendants** de HEAD (tout tag futur deviendrait invisible dès
  qu'on republie un ancien commit), alors que `--points-at HEAD` n'exclut que
  le tag exact du commit courant. Git absent ou hors dépôt
  (`FileNotFoundError`, `subprocess.CalledProcessError`) → `set()` pour
  chacun des deux appels.
- `_tag_de_head(version_bundle) -> str | None` : tag `vX.Y.Z` posé sur HEAD
  (`git tag -l "v*" --points-at HEAD`), de même base `vX.Y` que
  `version_bundle` ; le plus grand patch si plusieurs ; `None` si aucun ou
  git absent. Sert à la relance idempotente (§5.3) : si le run précédent a
  posé le tag mais échoué avant de pousser l'artefact (ou en cas de relance
  manuelle), la CLI republie explicitement cette version au lieu de calculer
  le patch suivant — `publier` lui-même ne lit toujours pas git.
- `prochaine_version(version_bundle, connues) -> str` : base `vX.Y` tirée du
  bundle ; aucune `vX.Y.*` connue → `version_bundle` ; sinon
  `vX.Y.{max_patch + 1}`. Les autres bases (`v2.1.*`, `v1.*`) sont ignorées.
  Exemples : `∅ → v2.0.0` ; `{v2.0.0} → v2.0.1` ; `{v2.0.0, v2.0.5} → v2.0.6` ;
  `{v1.0.0, v2.1.0} → v2.0.0`.
- `valider_version(version, version_bundle, connues)` : `ErreurDeploiement` si
  hors motif `vX.Y.Z`, si `X.Y` diffère du bundle (« le tag v3.0.0 ne
  correspond pas au bundle v2.0.0 »), ou si déjà connue (« v2.0.3 déjà publiée »).

### 5.2 `publier`

```python
publier(version: str | None = None, *, bundle="v2", commit=None, registry=None,
        seuil=None, rapport=None, versions=None) -> dict
```

`publier` ne lit **pas** git lui-même : sinon le test d'acceptance
`publier("v2.0.0", …)` casserait dès que la CI aura poussé le tag `v2.0.0`.
Les versions connues sont `set(registry.versions()) | (versions or set())` ;
c'est la **ligne de commande** (§5.3), appelée par la CI, qui passe
`versions=versions_connues(registry)` (tags git inclus).

1. `b = Bundle.charger(bundle)`, `registry = registry or Registry()`,
   `commit = commit or _commit_courant()`, `connues = set(registry.versions()) | (versions or set())`.
2. Version : fournie → `valider_version` ; absente → `prochaine_version(b.version, connues)`.
3. Gate : `rapport` fourni, sinon `evaluer(bundle, seuil=seuil)` (un gate
   mal configuré — `ValueError` / `FileNotFoundError` — devient
   `ErreurDeploiement("gate mal configuré : …")`). Contrôle d'intégrité : si
   `rapport.bundle_empreinte` est non vide et diffère de
   `Bundle.charger(bundle).empreinte()`, `ErreurDeploiement("rapport d'un
   autre bundle : empreinte … ≠ …")` **avant tout étiquetage** — un rapport
   d'un autre bundle (ou périmé) ne doit jamais qualifier une publication. Si
   `not rapport.passe` : journal `publication_refusee` (`version`, `commit`,
   `motifs`) puis `ErreurDeploiement("gate en échec : " + " ; ".join(motifs))`.
   Rien n'est étiqueté ; aucun numéro n'est consommé.
4. `registry.etiqueter(version, b, commit=commit, note_eval=rapport.note,
   details={"bundle_source": bundle, "mode_eval", "seuils", "latence_p95_ms",
   "cout_moyen_eur", "essais"})` — les champs absents d'un rapport partiel
   valent `None`. `ErreurRegistre` → `ErreurDeploiement` (même message).
5. Journal `publication` (`version`, `commit`, `note_eval`, `mode_eval`) —
   dernière entrée. Renvoie le manifeste.

`_commit_courant` existant : son `except Exception` est resserré à
`(FileNotFoundError, subprocess.CalledProcessError)` (règle du projet).

### 5.3 Ligne de commande

`python -m ops.deploy publier [vX.Y.Z] [--bundle v2] [--commit SHA]
[--seuil X] [--rapport chemin.json]`

- `version` devient optionnelle. Si absente, la CLI appelle d'abord
  `_tag_de_head(Bundle.charger(bundle).version)` : si HEAD porte déjà un tag
  de la base publiée (relance idempotente), c'est **cette** version qui est
  passée explicitement à `publier` ; sinon `publier` calcule le patch suivant
  comme d'habitude.
- Passe `versions=versions_connues(registry)` à `publier` (tags git ∪ registre).
- `--rapport` charge le JSON via `Rapport.depuis_dict` (fichier absent ou JSON
  invalide → `ErreurDeploiement`).
- Affiche le manifeste en **JSON** (`json.dumps`, pas `repr`) sur stdout.
- Codes : 0 succès ; 1 refus (`REFUSÉ : <motif>` sur stderr, comportement existant).
- Les autres sous-commandes sont inchangées.

## 6. Chaîne CI — `.github/workflows/ci.yml`

Le fichier `llmops.yml` est renommé `ci.yml` (`git mv`, historique conservé) ;
le `name:` du workflow devient `ci`. Les références dans `Makefile` et
`README.md` suivent.

Principe : **un seul rapport fait foi**. Le gate écrit `eval/rapport.json` ;
la publication le relit au lieu de réévaluer — la note du manifeste est
exactement celle du gate qui a autorisé la livraison.

| Job | `needs` | Condition | Contenu |
|---|---|---|---|
| `lint` | — | toujours | inchangé |
| `tests` | lint | toujours | inchangé (unitaires, intégration, acceptance) |
| `gate-evaluation` | tests | toujours | `MOCK=on` ; `uv run python -m eval.run_eval --version v2 --sortie eval/rapport.json`. Code ≠ 0 → job rouge. Artefact `gate-mock` (`eval/history.jsonl`, `eval/rapport.json`) en `if: always()`. |
| `gate-release` | gate-evaluation | tag `v*` ou `workflow_dispatch` | environment `release` ; `MOCK=off`, `DRIFT=off`, secrets `LLM_PROVIDER`, `LLM_MODEL`, `AZURE_AI_ENDPOINT`, `AZURE_AI_API_KEY` ; démarre `uv run python -m ops.drift_proxy &` et attend le port 8080 (l'app parle toujours au proxy) ; `run_eval --version v2 --essais 3 --sortie eval/rapport.json`. Artefact `gate-reel` en `if: always()`. |
| `build` | gate-evaluation, gate-release | `always() && needs.gate-evaluation.result == 'success' && needs.gate-release.result != 'failure' && needs.gate-release.result != 'cancelled'` | `docker build` inchangé |
| `publication` | build, gate-release | `always() && needs.build.result == 'success' && github.event_name != 'pull_request' && (github.ref == 'refs/heads/main' \|\| github.ref_type == 'tag')` | voir ci-dessous |
| `deploiement-canary` | publication | `always() && needs.publication.result == 'success' && (github.ref == 'refs/heads/main' \|\| startsWith(github.ref, 'refs/tags/v'))` | reste TODO (sous-projet 3, consommera l'artefact `mardik-vX.Y.Z`) |

La condition de `publication` restreint la publication effective à `main`
ou à un tag : un `workflow_dispatch` sur une autre branche peut donc rejouer
le gate-release (toujours autorisé, cf. §4/`gate-release`) sans jamais poser
de tag ni toucher au registre. `deploiement-canary` utilise `always()` avec
un contrôle explicite de `needs.publication.result` : sans `always()`, un
`if` par défaut hériterait aussi de l'échec/annulation d'un job en amont
(`build`, `gate-release`) même quand `publication` ne s'exécute pas parce
qu'elle est hors périmètre (PR, autre branche) — `always()` garantit que
seul le résultat de `publication` compte.

**Job `publication`**

- `permissions: contents: write` ; `concurrency: { group: publication, cancel-in-progress: false }`
  (deux fusions rapprochées publient l'une après l'autre).
- `actions/checkout@v4` avec `fetch-depth: 0` (tags inclus).
- Télécharge `gate-reel` si `gate-release` a réussi, sinon `gate-mock`.
- `VERSION_ARG` = `${GITHUB_REF_NAME}` si `github.ref_type == 'tag'`, vide sinon.
- `uv run python -m ops.deploy publier $VERSION_ARG --commit ${GITHUB_SHA::7} --rapport eval/rapport.json > manifest.json`.
- `VERSION=$(jq -r .version manifest.json)`, exporté dans `$GITHUB_ENV`.
- Artefact `mardik-$VERSION` : `ops/registry/$VERSION/`, `ops/registry/journal.jsonl`, `manifest.json`.
- **Puis**, hors tag : l'étape compare `git rev-list -n1 refs/tags/$VERSION`
  au `$GITHUB_SHA` courant — no-op **seulement si le tag existe déjà et
  pointe sur ce commit précis** (relance du même run) ; sinon
  `git tag "$VERSION" && git push origin "$VERSION"` (le tag n'est posé
  qu'une fois l'artefact envoyé). Si le tag existe déjà mais sur un **autre**
  commit, `git tag "$VERSION"` échoue (le tag local ne peut pas être recréé)
  → job rouge, rien n'est écrasé — contrairement à un simple test
  d'existence (`git rev-parse --verify`), qui aurait pris ce cas pour une
  relance légitime et resterait silencieusement no-op sur le mauvais commit.
  Un tag poussé avec `GITHUB_TOKEN` ne relance pas le workflow (pas de
  boucle). Une course résiduelle fait échouer `git push` : le job est rouge,
  rien n'est écrasé.
- **Limite de concurrence** : `concurrency: { group: publication,
  cancel-in-progress: false }` ne fait la queue que d'**un** run en attente
  par groupe — GitHub Actions n'empile pas indéfiniment. Si plus de deux
  fusions se suivent de près, seul le run le plus récent en attente est
  conservé ; les publications intermédiaires entre les deux sont annulées
  (`cancelled`), pas exécutées en séquence. Ce n'est pas une perte : le
  commit le plus récent inclut déjà le contenu des commits qu'il a
  « sautés », et `prochaine_version` / `versions_connues` recalculent la
  version suivante correctement au run suivant.

**`Makefile`** : la cible `ci` joue `MOCK=on uv run python -m eval.run_eval
--version v2` **avant** les tests d'acceptance (qui restent rouges tant que
les sous-projets 3 et 4 ne sont pas livrés) et garde un `echo` pour le
canary, pointant vers `.github/workflows/ci.yml`. `eval/rapport.json` est
ajouté au `.gitignore`.

**Limite connue** : le job `tests` joue aussi l'acceptance ; tant que les 4
tests des sous-projets 3 et 4 sont rouges, la chaîne GitHub s'arrête avant
le gate et la publication. C'est l'état actuel de `main`, hors périmètre
ici ; la chaîne complète tournera au vert une fois le sous-projet 4 livré.

## 7. Tests

Tous en `MOCK=on` (fixture `environnement` du `conftest.py` fourni), registre
et historique en `tmp_path`. TDD : chaque test est écrit et vu rouge avant le
code. Pas de base de données ni de ChromaDB : les règles de `CLAUDE.md` sur
les transactions et `EphemeralClient` ne s'appliquent pas.

### 7.1 Unitaires — `tests/unit/`

| Fichier | Cas |
|---|---|
| `test_notation.py` | `noter_contrat` : tout trouvé → 1,0 ; 3/5 → 0,6 ; `manquantes` dans l'ordre attendu ; types en trop ignorés ; `passe` avec `seuil_note` 0,80 (0,75 → KO). `agreger` : moyenne ; P95 ; coût moyen ; motif note globale ; motif contrat sous son seuil ; motif contenant « latence » ; motif contenant « coût » ; aucun motif → liste vide. Moyenne sur 2 essais. |
| `test_seuils.py` | lecture du fichier du repo ; fichier temporaire valide ; clé manquante → `ErreurSeuils` nommant la clé ; valeur non numérique ; fichier absent. |
| `test_moteurs.py` | `monolithique` et `map_reduce_clauses` présents dans `MOTEURS` ; stratégie inconnue → `ValueError`. |
| `test_versions.py` | `prochaine_version` : les 4 exemples de §5.1. `valider_version` : motif invalide, base incohérente, déjà connue, cas valide. `versions_connues` : `subprocess.check_output` levant `FileNotFoundError` (monkeypatch) → registre seul ; tags simulés fusionnés au registre, tags hors motif ignorés. |

### 7.2 Intégration — `tests/integration/`

| Fichier | Cas |
|---|---|
| `test_gate.py` (fixture locale : `METRICS_EVAL_PATH` en `tmp_path`) | `evaluer("v1", contrats=contrats_courts, historique=h)` : 4 contrats, une ligne dans `h` avec `mode_eval == "mock"`, `seuils`, `bundle_empreinte`. Fournisseur injoignable (`MOCK=off`, `LLM_PROXY_URL` sur port fermé, `LLM_TIMEOUT_S=1`) → `passe` faux, motif « erreur LLM », pas d'exception. Contrat demandé absent → `ValueError`. CLI (`main([...])`, sous-ensemble `c01,c02`) : code 0 ; code 1 avec `--cout-max-eur 0` ; code 2 avec `SEUILS_PATH` pointé vers un fichier invalide ; `--sortie` produit un JSON relu par `Rapport.depuis_dict` à l'identique. |
| `test_publication.py` | deux `publier(bundle="v2", rapport=r, registry=reg)` successifs sans version → `v2.0.0` puis `v2.0.1` ; `versions={"v2.0.4"}` injecté → `v2.0.5` ; manifeste contenant `mode_eval`, `seuils`, `latence_p95_ms`, `cout_moyen_eur`, `bundle_source`. Gate en échec → `ErreurDeploiement`, journal `publication_refusee`, aucune version ajoutée. Tag `v3.0.0` → refus ; version en double → refus. CLI `publier --commit abc1234 --rapport r.json` (tags git simulés par monkeypatch de `ops.deploy._tags_git`) → stdout JSON parsable, code 0, version = tag simulé + 1 ; `--rapport` inexistant → code 1. |

### 7.3 Workflow

`actionlint` sur `ci.yml` s'il est disponible (`brew` / binaire) ; sinon
validation YAML (`uv run python -c "import yaml; yaml.safe_load(...)"`). Le
comportement réel (tag posé, artefact `mardik-vX.Y.Z`) se vérifie sur le
premier run GitHub après fusion.

### 7.4 Critères de fin

- `make test` : unitaires et intégration (existants + nouveaux) verts ;
  `test_gate_evaluation_note_par_version`,
  `test_evaluation_enrichie_latence_et_cout`,
  `test_etiquetage_version_apres_gate` **verts** ; les 3 tests déjà verts le
  restent ; seuls les 4 tests des sous-projets 3 et 4 restent rouges.
- `make ci` exécute le gate (`GATE : PASSE`) avant l'étape acceptance.
- `uv run ruff check .` propre.
- `git diff main` vide sur `ops/registry/__init__.py`, `app/llm_client.py`,
  `app/telemetry.py`, `tests/conftest.py`, `models/v1/`, `app/api_v1.py`,
  `app/pipeline/`, `app/api_v2.py`.

## 8. Risques

- **Gate MOCK peu discriminant** : sans fixtures réelles, le repli par
  mots-clés donne 1,0 à la v2 partout. Le gate CI garantit la non-régression
  mécanique (troncature, parsing, consolidation) ; seul le gate de release
  mesure la qualité réelle. `mode_eval` dans le manifeste l'expose.
- **P95 et coût en MOCK** : latence du repli fixe (5 ms) → la contrainte P95
  n'a de sens qu'au gate de release.
- **Secrets de release absents** : `gate-release` échoue → release bloquée
  (comportement voulu : pas de version « réelle » sans évaluation réelle).
- **Gate de release non déterministe** : `essais: 3` moyenne la variance ;
  un échec ponctuel se rejoue par `workflow_dispatch`.
- **Artefact envoyé mais tag refusé** (course entre deux runs) : l'artefact
  `mardik-vX.Y.Z` existe sans tag ; le run est rouge et le suivant recalcule
  la version depuis les tags. Ordre choisi : étiquetage → artefact → tag, pour
  qu'un tag ne désigne jamais une version sans artefact.
