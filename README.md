# Mardik — livrer et piloter la nouvelle version

*Brief de création, Phase 4 — chaîne LLMOps & observabilité.*

Mardik analyse des contrats commerciaux avec un LLM et en liste les clauses.
La **v1** tourne en production et un client interne (`scripts/client_v1.py`)
en dépend. Le client juridique veut une **v2** : contrats longs sans
troncature, score de confiance — livrée par une chaîne automatique,
déployée progressivement, et pilotable (rollback en une opération).

Lisez d'abord `docs/besoin_client.md`. Puis `docs/schema_remediation.md`.

## Règles du jeu

- **`app/api_v1.py` est intouchable.** Le client historique doit continuer
  de fonctionner tel quel, à chaque commit. C'était le seul test vert au
  départ ; depuis la v2.0.0, `test_contrat_v2_long_analyse_sans_troncature`
  et `test_erreurs_explicites_jamais_de_500` le sont aussi.
- **Une version du modèle est un bundle de configuration.** Voyez
  `models/v1/config.yaml` : modèle de base + prompt + paramètres + schéma de
  sortie + stratégie. La v2 (`models/v2/config.yaml`) est le même client LLM
  avec un autre bundle. C'est ce bundle qu'on étiquette, qu'on livre et qu'on
  rollback.
- **Les modèles sont de vrais LLM.** Vraie latence, vrai non-déterminisme,
  vrai coût. La troncature de la v1 sur les contrats longs est une conséquence
  réelle de sa config, pas une simulation.
- **La dérive est injectée par un proxy** (`ops/drift_proxy.py`), pas par le
  modèle : `DRIFT=latence|erreurs|score`, pilotable à chaud. C'est ce qui rend
  la démo de fin de phase commandable.
- **`MOCK=on` est réservé à la CI.** Les gates doivent tourner vite et
  gratuitement à chaque fusion : en CI, le client rejoue des réponses
  enregistrées (`eval/fixtures/`). En salle, `MOCK=off`.
- **Les modules à construire sont des stubs** qui lèvent `NotImplementedError`
  avec, en docstring, le contrat attendu. Les tests d'acceptance sont fournis
  et rouges : le brief se joue du rouge au vert.

## Mise en route

```bash
make install                 # uv sync
cp .env.example .env         # choisir le fournisseur : ollama (gratuit) ou azure (clé API)
ollama pull llama3.2:3b      # si LLM_PROVIDER=ollama
make up                      # app :8000, proxy de dérive :8080, dashboard :8501
```

Sans docker : `make proxy` dans un terminal, `make serve` dans un autre.

```bash
# la v1 répond et analyse un contrat court
curl -s localhost:8000/v1/analyse -H 'content-type: application/json' \
     -d @<(jq -Rs '{texte: .}' eval/contrats/c01.txt) | jq

# la douleur de départ : un contrat long est tronqué
curl -s localhost:8000/v1/analyse -H 'content-type: application/json' \
     -d @<(jq -Rs '{texte: .}' eval/contrats/c12.txt) | jq '.tronque, .clauses'

python scripts/client_v1.py  # le client historique : vert
make test-acceptance         # 3 verts, 7 rouges depuis la v2.0.0 (1 vert, 9 rouges au départ)
```

## Commandes

| Commande | Quoi |
|---|---|
| `make up` / `make down` | app + proxy + dashboard (docker compose) |
| `make test` | tout, en `MOCK=on` |
| `make test-unit` | les tests unitaires du pipeline v2 et de `analyser_v2` (verts) |
| `make test-integration` | les tests hérités de la remédiation et ceux de la v2 (verts) |
| `make test-acceptance` | les 10 tests du brief (3 verts, 7 en attente des sous-projets 2 à 4) |
| `make eval VERSION=v2` | le gate d'évaluation sur le **vrai** modèle (`ARGS="--essais 3"`) |
| `make traffic MODE=derive-score` | trafic sur la gateway + dérive commandée (`normal`, `derive-latence`, `erreurs`) |
| `make dashboard` | tableau de bord (texte) ; `DASH=serve` pour la page HTML |
| `make ci` | l'équivalent local du workflow GitHub |
| `make fixtures` | (ré)enregistre les fixtures `MOCK` avec le vrai modèle |

Déploiement : `python -m ops.deploy publier v2.0.0 | canary v2.0.0 --pourcentage 10 | promouvoir v2.0.0 | rollback | surveiller --boucle`.

## Arborescence

```
app/          main.py (FastAPI, handler 413), api_v1.py [INTOUCHABLE], api_v2.py [FAIT — v2.0.0],
              gateway.py [STUB], llm_client.py [FOURNI], telemetry.py [FOURNI]
app/pipeline/ decoupage.py, extraction.py, consolidation.py, confiance.py, erreurs.py [FAIT — v2.0.0]
models/       v1/config.yaml [FOURNI], v2/config.yaml [FAIT — bundle map_reduce_clauses]
eval/         contrats/ (12 contrats, 3 longs), attendus.jsonl, fixtures/ (MOCK), run_eval.py [STUB], history.jsonl [GÉNÉRÉ]
ops/          drift_proxy.py [FOURNI], registry/ [FOURNI], deploy.py [STUB], dashboard.py [STUB]
scripts/      client_v1.py [FOURNI], traffic_sim.py [FOURNI]
tests/        unit/ (pipeline et analyser_v2, 61 tests), integration/ (v1 + v2, 17 tests),
              acceptance/ (10 tests du brief : 3 verts, 7 en attente des sous-projets 2 à 4)
docs/         besoin_client.md, schema_remediation.md, dossier-conception.pdf, exploitation.md [À RÉDIGER],
              superpowers/specs/ et superpowers/plans/ (spec et plan du moteur v2)
.github/      workflows/llmops.yml [TEMPLATE] — étapes posées, gates en TODO
```

## Les chantiers

1. **Le bundle v2** — `models/v2/config.yaml` : stratégie, schéma de sortie, paramètres.
   *Qu'est-ce qui, dans ce fichier, fait qu'une version est une version ?*
2. **Le pipeline v2** — `app/pipeline/` (découpage, extraction, consolidation, confiance)
   et `app/api_v2.py`. Sans casser `/v1`.
3. **Le gate d'évaluation** — `eval/run_eval.py` : une note par version, sur les 12
   contrats annotés, avec latence et coût. *Deux exécutions ne donnent pas la même
   note : que faites-vous ?*
4. **La chaîne** — `ops/deploy.py` (étiquetage, canary, promotion, rollback,
   surveillance) et `.github/workflows/llmops.yml`. *Que peut vérifier la CI sans
   le vrai modèle ?*
5. **Le pilotage** — `app/gateway.py` (routeur canary), `ops/dashboard.py`,
   `docs/exploitation.md`.

Démo de fin : `make traffic MODE=derive-score` en live → dérive vue au tableau
de bord → rollback → entrée au journal.

## Coût et non-déterminisme

Le gate rejoue 12 contrats à chaque exécution. Avec plusieurs équipes qui
poussent souvent : Ollama en local, ou un plafond — le gate en CI tourne en
`MOCK=on`, seul le gate « de release » appelle le vrai modèle. Et deux
exécutions du gate ne donnent pas la même note : c'est le sujet (seed,
température, moyenne sur *n* essais, seuil avec marge). Ne le « corrigez »
pas : concevez avec.

## Changelog

### v1.0.0 — dépôt de départ (cloné)

*2026-09-15 — commit `8c59599`, état « lundi matin » du brief.*

- **`POST /v1/analyse`** en production : bundle `models/v1/config.yaml`,
  stratégie `monolithique` (un seul appel LLM avec tout le contrat),
  `temperature: 0.2`, sortie en texte libre (une clause par ligne).
- **Troncature des contrats longs** : au-delà de `contexte_max_caracteres: 16000`,
  le contrat est coupé et la réponse porte `"tronque": true` — les clauses de
  la fin manquent. C'est la douleur de départ.
- **Fourni** : client LLM unique (`app/llm_client.py`, Ollama ou Azure,
  `MOCK=on` qui rejoue `eval/fixtures/` ou répond par mots-clés), télémétrie
  (`app/telemetry.py` : traces OpenTelemetry, logs structlog, métriques JSONL),
  proxy de dérive (`ops/drift_proxy.py`), registre de versions avec la
  `v1.0.0` étiquetée et active (`ops/registry/`), client historique
  (`scripts/client_v1.py`), simulateur de trafic, 12 contrats annotés dont
  3 longs (`eval/contrats/`, `eval/attendus.jsonl`).
- **Stubs** (`NotImplementedError`) : `app/api_v2.py`, `app/gateway.py`,
  `app/pipeline/` (découpage, extraction, consolidation, confiance),
  `eval/run_eval.py`, `ops/deploy.py`, `ops/dashboard.py` ; bundle
  `models/v2/config.yaml` à compléter.
- **Tests** : 6 tests d'intégration v1 verts ; 10 tests d'acceptance dont un
  seul vert, `test_client_v1_fonctionne`.

### v2.0.0 — moteur v2 (sous-projet 1)

*2026-09-21 — branche `feature/moteur-v2`, commit `a4928ee`. Spec :
`docs/superpowers/specs/2026-09-21-moteur-v2-design.md`.*

**Ajouté**

- **`POST /v2/analyse`** : contrats longs analysés **sans troncature**, par un
  map-reduce sur les clauses :
  1. **Découpage** (`app/pipeline/decoupage.py`) aux intitulés d'articles
     (`Article 3 — …`, `ARTICLE 3 : …`, `3. Résiliation`), coupe en fin de
     phrase, articles consécutifs regroupés en sections ≤ 6 000 caractères ;
     aucun caractère perdu.
  2. **Extraction** (`extraction.py`) : un appel LLM par section, en parallèle
     (8 appels simultanés), sortie JSON contrainte par un schéma et validée
     par Pydantic ; une réponse hors schéma n'interrompt jamais l'analyse.
  3. **Consolidation** (`consolidation.py`) : une seule clause par type
     (extrait le plus long, sections réunies).
  4. **Score de confiance** (`confiance.py`) par clause et global :
     `ancrage × (0,7 × confiance du LLM + 0,3 × récurrence)`. L'ancrage vérifie
     que l'extrait cité figure vraiment dans le contrat : une citation
     inventée obtient un score bas, même si le modèle se dit sûr de lui.
- **Réponse** : `clauses` (type, extrait, confiance, sections),
  `confiance_globale`, `model_version` (`v2.0.0-<empreinte du bundle>`),
  `sections`, `appels_llm`, `latence_ms`, `cout_eur` et `warnings` (clause
  sous le seuil de relecture de 0,6, réponses LLM hors schéma, aucune clause
  détectée).
- **Erreurs explicites, jamais de 500 brut** : `413` pour un document de plus
  de 200 000 caractères ou de plus de 40 sections (vérifié avant tout appel
  LLM, donc sans coût), `422` pour un corps invalide, `503` si le fournisseur
  LLM est indisponible. La première erreur d'un appel annule les appels
  restants et est mesurée.
- **Bundle `models/v2/config.yaml`** : stratégie `map_reduce_clauses`, prompt
  par section, schéma de sortie, `temperature: 0.0`, `seed: 42`, limites
  absolues.
- **Télémétrie** : un span `analyse.requete` avec un span `llm.appel` enfant
  par section, une mesure par requête (latence, score, coût, tokens, erreur),
  logs `analyse.terminee` / `analyse.echec` / `analyse.refusee`.
- **Tests** : 61 tests unitaires (`tests/unit/`), 11 tests d'intégration v2
  (`tests/integration/test_analyse_v2.py`). 3 tests d'acceptance sur 10 sont
  verts : `test_contrat_v2_long_analyse_sans_troncature`,
  `test_erreurs_explicites_jamais_de_500` et `test_client_v1_fonctionne`.

**Inchangé** : `/v1` (`app/api_v1.py`, `models/v1/`), `app/llm_client.py`,
`app/telemetry.py` et les tests fournis.

**Reste à faire**

- Les 7 autres tests d'acceptance attendent les sous-projets 2 à 4 : gate
  d'évaluation et publication, routage canary et rollback, tableau de bord et
  boucles de rétroaction.
- `sections_max: 40` est trop bas face à `taille_max_document: 200000` : un
  contrat sous la limite de caractères peut être découpé en plus de 40
  sections et refusé en 413. À trancher avant la calibration du gate.
