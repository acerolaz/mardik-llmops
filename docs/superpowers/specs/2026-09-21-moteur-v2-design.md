# Sous-projet 1 — Moteur v2 (pipeline + API `/v2/analyse`)

*Spec de conception — 2026-09-21. Premier des quatre sous-projets de la v2 Mardik.*

## 1. Contexte et décisions de cadrage

La v2 doit analyser les contrats longs sans troncature et fournir un score de
confiance par clause et global (`docs/besoin_client.md`), sans toucher à `/v1`.

**Source de vérité.** En cas de divergence entre le dossier de conception
(`docs/dossier-conception.pdf`) et le repo, **les tests d'acceptance fournis et
les contrats des stubs font foi**. Les apports du dossier compatibles avec les
tests sont ajoutés en sus : `warnings[]`, `model_version`, refus `413`. Le
dossier et `spec.md` seront réalignés sur le repo en fin de chantier.

**Découpage de la v2 en sous-projets** (chacun : spec → plan → implémentation) :

| # | Sous-projet | Tests d'acceptance visés |
|---|---|---|
| **1** | **Moteur v2 — pipeline + API** (ce document) | `test_contrat_v2_long_analyse_sans_troncature`, `test_erreurs_explicites_jamais_de_500` |
| 2 | Gate d'évaluation & publication | `test_gate_evaluation…`, `test_evaluation_enrichie…`, `test_etiquetage…` |
| 3 | Routage & déploiement (gateway, canary, rollback) | `test_promotion_canary…`, `test_rollback…` |
| 4 | Observabilité & boucles de rétroaction | `test_dashboard…`, `test_journal_derive…` + boucles live |

`test_client_v1_fonctionne` est un garde-fou permanent.

## 2. Périmètre

**Dans le périmètre**

- `app/pipeline/decoupage.py`, `extraction.py`, `consolidation.py`, `confiance.py`
- `app/api_v2.py` : `analyser_v2` et la route `POST /v2/analyse`
- `models/v2/config.yaml` : bundle v2 complet
- `app/main.py` : gestionnaire d'exception `DocumentTropLong` → 413
- Tests unitaires (`tests/unit/`) et d'intégration (`tests/integration/test_analyse_v2.py`)

**Hors périmètre**

- `app/api_v1.py` et `models/v1/` (intouchables)
- `app/llm_client.py`, `app/telemetry.py`, `tests/conftest.py` (fournis, non modifiés)
- Enregistrement des fixtures avec le vrai modèle (`make fixtures`) et gate d'évaluation → sous-projet 2
- Gateway `/analyse` → sous-projet 3
- Traduction, chatbot (hors périmètre de la v2 selon le dossier)

## 3. Architecture et flux

```
POST /v2/analyse  (route mince, def synchrone → pool de threads FastAPI)
  └─ analyser_v2(texte, client, telemetry)          ← aussi appelée par le gate (sous-projet 2)
       span analyse.requete [mardik.version, mardik.model_version, mardik.sections,
                             mardik.confiance_globale]
       1. len(texte) > taille_max_document  → DocumentTropLong
       2. decouper(texte, taille_max)        → list[Section]
          len(sections) > sections_max       → DocumentTropLong
       3. map parallèle : extraire(section, client) ; un span llm.appel par section
       4. consolider(par_section)            → une clause par type
       5. scorer(clauses, texte)             → confiance par clause + globale
       6. warnings
       7. Mesure(...) → telemetry.metriques
  ErreurLLM        → 503 « fournisseur LLM indisponible : … »   (dans la route, comme la v1)
  DocumentTropLong → 413 explicite                             (handler dans main.py)
```

**Parallélisme.** `analyser_v2` reste synchrone et utilise un
`concurrent.futures.ThreadPoolExecutor(max_workers=min(parallelisme, n_sections))`.
Le client LLM fourni est synchrone ; la route est un `def` synchrone exécuté par
FastAPI dans son pool de threads, donc la boucle d'événements n'est jamais
bloquée. Chaque tâche rattache (`opentelemetry.context.attach` / `detach`) le
contexte OTel capturé dans le thread appelant, pour que les spans `llm.appel`
soient enfants de `analyse.requete`. Les résultats sont réordonnés par indice
de section.

**Erreur LLM pendant le map.** La première `ErreurLLM` remontée par une tâche
interrompt l'analyse : une `Mesure(erreur=True)` est enregistrée, l'erreur est
journalisée (`analyse.echec`) et relancée. Les tâches encore en attente sont
annulées (`cancel_futures=True` à la fermeture de l'exécuteur).

## 4. Contrat d'API `/v2/analyse`

**Requête** (inchangée par rapport au stub)

```json
{ "texte": "<contrat, ≥ 20 caractères>", "contrat_id": "c07" }
```

**Réponse 200** — tous les champs du stub, plus `warnings` et `model_version` :

```json
{
  "clauses": [
    { "type": "résiliation", "extrait": "…", "confiance": 0.91, "sections": [3, 7] }
  ],
  "confiance_globale": 0.87,
  "modele": "llama3.2:3b",
  "version": "v2.0.0",
  "model_version": "v2.0.0-3f9a1c2b7d10",
  "sections": 14,
  "appels_llm": 14,
  "latence_ms": 5230.4,
  "cout_eur": 0.031,
  "warnings": ["clause « garantie » : confiance 0,42 < 0,60 — relecture conseillée"]
}
```

- `model_version` = `f"{bundle.version}-{bundle.empreinte()}"` : relie chaque
  réponse au bundle exact publié au registre.
- `confiance` et `confiance_globale` sont arrondis à 3 décimales.

**Erreurs** — toujours un corps `{"detail": "..."}`, jamais de 500 brut :

| Code | Cause | Exemple de `detail` |
|---|---|---|
| 422 | Corps invalide (Pydantic : champ manquant, texte < 20 caractères) | format Pydantic par défaut |
| 413 | Texte > `taille_max_document` ou sections > `sections_max` | « document de 312 000 caractères, limite 200 000 : découpez le contrat ou contactez le support » / « document découpé en 52 sections, limite 40 : … » |
| 503 | `ErreurLLM` (fournisseur injoignable, HTTP en erreur) | « fournisseur LLM indisponible : … » |

`DocumentTropLong` est vérifié **avant tout appel LLM** : aucun coût engagé.

## 5. Bundle `models/v2/config.yaml`

| Clé | Valeur | Rôle |
|---|---|---|
| `version` | `v2.0.0` | |
| `modele` | `${LLM_MODEL}` | |
| `strategie` | `map_reduce_clauses` | attendu par la gateway et le registre |
| `prompt` | prompt par section | impose les libellés de `TYPES_CLAUSES`, une citation littérale de l'extrait, une confiance 0–1, et une sortie JSON `{"clauses": [...]}` |
| `schema_sortie` | schéma JSON `{clauses: [{type, extrait, confiance}]}` | injecté au prompt en `json_mode` ; indispensable pour que le repli `MOCK` renvoie du JSON |
| `parametres.temperature` | `0.0` | stabilité du gate |
| `parametres.seed` | `42` | reproductibilité |
| `parametres.max_tokens` | `800` | une section peut contenir plusieurs clauses |
| `parametres.contexte_max_caracteres` | `6000` | = `taille_max` du découpage |
| `parametres.taille_max_document` | `200000` | limite absolue → 413 |
| `parametres.sections_max` | `40` | limite absolue → 413 |
| `parametres.parallelisme` | `8` | workers du map |
| `parametres.seuil_relecture` | `0.6` | seuil des warnings de clause |
| `parametres.essais_eval` | `1` | conservé pour le gate (sous-projet 2) |
| `cout_par_1k_tokens` | `0.002` | |

## 6. Modules du pipeline

Signatures inchangées par rapport aux stubs.

### 6.1 `decoupage.decouper(texte, taille_max=6000) -> list[Section]`

1. **Intitulés** : regex multiligne reconnaissant en début de ligne
   `Article 3 — …`, `ARTICLE 3 : …`, `Article 3 - …`, `3. Résiliation`. Le texte
   situé avant le premier intitulé forme une unité `préambule` (omise si vide).
   Un texte sans intitulé forme une seule unité `préambule`.
2. **Unité > `taille_max`** : scindée en fin de phrase (`.`, `!`, `?`, `;`
   suivis d'un blanc), en remplissant chaque morceau au plus près de
   `taille_max`. Une phrase seule > `taille_max` est coupée au dernier blanc
   avant la limite (ou à la limite exacte s'il n'y en a pas) — seule entorse
   documentée à « ne pas couper une phrase ».
3. **Regroupement** : les unités consécutives sont concaténées tant que la
   longueur cumulée reste ≤ `taille_max`. `titre` = titre de la première unité,
   suffixé de ` (+k)` si le groupe contient k unités supplémentaires ;
   `indice` = 0…n−1.
4. **Invariant** : `"".join(s.texte for s in sections) == texte`. Le découpage
   se fait par positions (tranches), sans `strip`.
5. Chaque section a `len(texte) ≤ taille_max`.

### 6.2 `extraction.extraire(section, client) -> tuple[list[Clause], ReponseLLM]`

- Un appel `client.completer(f"Section : {section.titre}\n\n{section.texte}", json_mode=True)`.
- `ErreurLLM` levée par `completer` (transport / fournisseur) : **propagée** (→ 503).
- `reponse.json()` en échec (JSON illisible) : **pas d'exception**, retour `[]`,
  `metadonnees["clauses_ignorees"] = 1` et `metadonnees["reponse_invalide"] = True`.
- Accepte `{"clauses": [...]}` ou directement `[...]` ; toute autre forme est
  traitée comme une réponse invalide.
- Chaque élément est validé par un modèle Pydantic interne `ClauseExtraite`
  (`type` ∈ `TYPES_CLAUSES` après normalisation `strip().lower()`, `extrait`
  non vide, `confiance` ∈ [0, 1]). Un élément invalide est ignoré et compté.
- `reponse.metadonnees["clauses_ignorees"]` = nombre d'éléments ignorés ;
  consigné aussi en attribut de span (`extraction.clauses_ignorees`) et en log
  `extraction.clauses_ignorees` quand > 0.
- Chaque `Clause` : `sections=[section.indice]`, `confiance_llm` = valeur déclarée.

### 6.3 `consolidation.consolider(par_section) -> list[Clause]`

Regroupement par `type` : extrait **le plus long**, `sections` unies et triées,
`confiance_llm` **maximale**. Sortie ordonnée par première section
d'apparition (puis ordre d'apparition dans la section). Jamais deux clauses de
même type. Entrée vide → `[]`. Fonction pure (ne mute pas les entrées).

### 6.4 `confiance.scorer(clauses, texte) -> tuple[list[Clause], float]`

- **Normalisation** (extrait et contrat) : minuscules, apostrophes et
  guillemets typographiques ramenés à leur forme ASCII, blancs multiples
  réduits à un espace.
- **Ancrage** ∈ [0, 1] : proportion des trigrammes de mots de l'extrait
  présents dans l'ensemble des trigrammes du contrat. Extrait de moins de 3
  mots : 1 si sous-chaîne du contrat normalisé, sinon 0. Coût linéaire en la
  taille du contrat.
- **Récurrence** : 1,0 si la clause apparaît dans ≥ 2 sections, sinon 0,5.
- **Score clause** : `ancrage × (0,7 × confiance_llm + 0,3 × récurrence)`,
  borné à [0, 1]. Poids = constantes du module (`POIDS_LLM`, `POIDS_RECURRENCE`).
- **Score global** : moyenne des scores de clause ; 0,0 si aucune clause.

### 6.5 `api_v2.analyser_v2` — warnings

Fonction pure `construire_warnings(clauses, ignorees_par_section, seuil) -> list[str]` :

- clause sous `seuil_relecture` : « clause « {type} » : confiance {x} < {seuil} — relecture conseillée » ;
- clauses ignorées : « {n} clause(s) ignorée(s) : réponse LLM hors schéma (sections : {titres}) » ;
- aucune clause : « aucune clause détectée — relecture conseillée ».

### 6.6 Télémétrie

- Span `analyse.requete` : `mardik.version`, `mardik.model_version`,
  `mardik.sections`, `mardik.confiance_globale`.
- Span `llm.appel` (un par section) : `llm.section`, `llm.latence_ms`,
  `llm.tokens`, `extraction.clauses_ignorees`.
- `Mesure(version, route="/v2/analyse", latence_ms, score=confiance_globale,
  cout_eur=Σ, appels_llm=n_sections, tokens=Σ, erreur, tronque=False)`.
- Logs structlog `analyse.terminee` / `analyse.echec` / `analyse.refusee` (413).

## 7. Tests

Pas de base de données ni de ChromaDB dans ce projet : les règles de
`CLAUDE.md` sur les transactions et `EphemeralClient` ne s'appliquent pas.
Tous les tests tournent en `MOCK=on` (fixture `environnement` du `conftest.py`
fourni). Développement en TDD : chaque test est écrit et vu rouge avant le code.

### 7.1 Tests unitaires — `tests/unit/`

`tests/unit/conftest.py` fournit un `FauxClient` : réponses `ReponseLLM`
scriptées (ou `ErreurLLM`), délai optionnel, compteur d'appels et prompts reçus,
`bundle` réel v2, `cout_eur` déterministe.

| Fichier | Cas |
|---|---|
| `test_decoupage.py` | 3 formats d'intitulé (paramétré) ; préambule ; texte sans intitulé ; **invariant de non-perte sur les 12 contrats** (paramétré) ; chaque section ≤ `taille_max` ; article long coupé en fin de phrase ; phrase géante coupée sans perte ; titre `(+k)` ; indices consécutifs ; c01 regroupé en peu de sections |
| `test_extraction.py` | JSON valide → clauses avec `sections=[indice]` et `confiance_llm` ; type normalisé ; type inconnu / extrait vide / confiance 1,4 ignorés et comptés ; forme liste acceptée ; JSON illisible → `[]` sans exception ; `ErreurLLM` propagée ; prompt contenant titre et texte, `json_mode=True` |
| `test_consolidation.py` | fusion par type (extrait le plus long, sections unies triées, confiance max) ; ordre de première apparition ; aucun doublon ; entrée vide ; entrées non mutées |
| `test_confiance.py` | extrait exact → ancrage 1 ; **citation inventée avec llm 0,95 → score < 0,1** ; apostrophes typographiques et blancs → ancrage 1 ; extrait < 3 mots ; récurrence augmente le score ; bornes [0, 1] ; global = moyenne ; aucune clause → 0,0 |
| `test_analyser_v2.py` | `FauxClient` + télémétrie en mémoire : **413 avant tout appel LLM** (> 200 000 caractères ; > `sections_max` abaissé) ; `construire_warnings` (score bas, ignorées, aucune clause) ; format `model_version` ; **parallélisme effectif** (8 sections à 0,2 s, `parallelisme=8` → < 1 s) ; totaux coût, tokens, appels ; `ErreurLLM` en cours de map → propagée + `Mesure(erreur=True)` |

### 7.2 Tests d'intégration — `tests/integration/test_analyse_v2.py`

Vrai `Bundle.charger("v2")`, vrai `LLMClient` (`MOCK=on`), fixtures `telemetry`,
`span_exporter`, `metriques`, `client`, `contrat` du `conftest.py` fourni.

1. `analyser_v2` sur c12 : `sections > 1`, `résiliation` et `droit applicable` présentes, scores ∈ [0, 1], `model_version == f"v2.0.0-{empreinte}"`.
2. Traces : un seul `analyse.requete` ; N `llm.appel` **enfants** de ce span, même `trace_id` (propagation du contexte dans les threads).
3. Mesure : `version="v2.0.0"`, `score == confiance_globale`, `appels_llm == sections`, `cout_eur > 0`, `erreur=False`.
4. Fournisseur injoignable (`MOCK=off`, `LLM_PROXY_URL` sur port fermé) → `ErreurLLM` + `Mesure(erreur=True)`.
5. Document trop long → `DocumentTropLong`, `Mesure(erreur=True)`, 0 appel LLM.
6. HTTP : 200 avec `warnings` et `model_version` ; 422 (champ manquant, texte trop court) ; **413 avec `detail` explicite** ; 503 avec `detail` contenant « LLM ».
7. Non-régression : réponse `/v1/analyse` au format v1 exact pendant que `/v2` est actif.

### 7.3 Critères de fin

- `make test` : unitaires, intégration (existants + nouveaux),
  `test_contrat_v2_long_analyse_sans_troncature`,
  `test_erreurs_explicites_jamais_de_500` et `test_client_v1_fonctionne` verts ;
  les 7 autres tests d'acceptance restent rouges (sous-projets 2 à 4).
- `uv run ruff check .` propre.
- `git diff` vide sur `app/api_v1.py`, `models/v1/`, `app/llm_client.py`,
  `app/telemetry.py`, `tests/conftest.py`.

## 8. Risques

- **P95 < 8 s sur contrats longs avec un vrai modèle** : ~15 sections pour c12,
  8 en parallèle → ~2 vagues d'appels. À mesurer au sous-projet 2 (gate avec
  le vrai modèle) ; leviers : `parallelisme`, `taille_max`.
- **Qualité du score en MOCK** : le repli déterministe renvoie `confiance 0,86`
  et des extraits littéraux ; le score composite y sera donc élevé et peu
  discriminant. La calibration réelle se fait au gate (sous-projet 2).
