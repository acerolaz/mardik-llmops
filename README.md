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
  départ ; il l'est resté à chaque sous-projet, et 8 tests d'acceptance sur
  10 sont verts depuis le sous-projet 3.
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
make test-acceptance         # 10 verts ; 1 vert, 9 rouges au départ
```

```bash
# la gateway : route vers la version active ou le canary, selon l'index du registre
make etat                    # GET /gateway/etat → {"active": …, "canary": …, "canary_percent": …}
curl -si localhost:8000/analyse -H 'content-type: application/json' \
     -d @<(jq -Rs '{texte: .}' eval/contrats/c01.txt) | grep -i x-mardik-version
```

## Commandes

| Commande | Quoi |
|---|---|
| `make up` / `make down` | app + proxy + dashboard (docker compose) |
| `make test` | tout, en `MOCK=on` |
| `make test-unit` | les tests unitaires du pipeline v2, du gate, des versions, du routage, des transitions de déploiement et des workflows (verts) |
| `make test-integration` | les tests hérités de la remédiation, de la v2, du gate, de la publication, de la gateway et de la CLI de déploiement (verts) |
| `make test-acceptance` | les 10 tests du brief (verts) |
| `make etat` | répartition courante du trafic vue par la gateway (`APP_URL`, défaut `http://localhost:8000`) |
| `make eval VERSION=v2` | le gate d'évaluation sur le **vrai** modèle (`ARGS="--essais 3"`) |
| `make traffic MODE=derive-score` | trafic sur la gateway + dérive commandée (`normal`, `derive-latence`, `erreurs`) |
| `make dashboard` | tableau de bord (texte) ; `DASH=serve` pour la page HTML |
| `make pilote` | boucle du pilote : surveillance, rollback automatique, promotion canary |
| `make ci` | l'équivalent local du workflow GitHub (le gate MOCK passe avant l'acceptance) |
| `make fixtures` | (ré)enregistre les fixtures `MOCK` avec le vrai modèle |

Gate : `python -m eval.run_eval --version v2 [--essais 3] [--sortie rapport.json]` (seuils :
`eval/seuils.yaml`).

Déploiement : `python -m ops.deploy publier [v2.0.0] [--commit SHA] [--rapport rapport.json] | installer v2.0.0 --depuis DOSSIER [--origine ci:acteur] | canary v2.0.0 --pourcentage 10 [--origine ci:acteur] | promouvoir v2.0.0 [--origine ci:acteur] | rollback [--motif M] [--origine ci:acteur] | surveiller [--boucle] | piloter [--tours N]`. Seuils : `ops/seuils_pilotage.yaml` ; calibration : `python -m ops.seuils calibrer --version vX.Y.Z` ; enrichissement : `python -m eval.enrichir lister | verser <id> --clauses …`.

En production, canary, promotion et rollback passent par la chaîne, jamais à
la main : `ci.yml` (installation + 10 %), `promotion.yml` (50 %, 100 %) et
`rollback.yml` (`gh workflow run rollback.yml -f motif="…"`), exécutés sur le
runner auto-hébergé. Procédure : `docs/exploitation.md` §4 et §5.

## Arborescence

```
app/          main.py (FastAPI, handler 413), api_v1.py [INTOUCHABLE], api_v2.py [FAIT — v2.0.0],
              gateway.py [FAIT — routage canary, SP3], routage.py [FAIT — SP3],
              llm_client.py [FOURNI], telemetry.py [FOURNI], capture.py [SP4]
app/pipeline/ decoupage.py, extraction.py, consolidation.py, confiance.py, erreurs.py [FAIT — v2.0.0]
models/       v1/config.yaml [FOURNI], v2/config.yaml [FAIT — bundle map_reduce_clauses]
eval/         contrats/ (12 contrats, 3 longs), attendus.jsonl, fixtures/ (MOCK), seuils.yaml [FAIT],
              run_eval.py [FAIT — gate], history.jsonl, rapport.json [GÉNÉRÉS], enrichir.py [SP4]
ops/          drift_proxy.py [FOURNI], registry/ [FOURNI], deploy.py [publier, installer, canary,
              promotion, rollback FAIT — SP3 ; surveiller, piloter FAIT — SP4], dashboard.py, pilotage.py,
              signaux.py, seuils.py [FAIT — SP4]
scripts/      client_v1.py [FOURNI], traffic_sim.py [FOURNI]
tests/        unit/ (pipeline, analyser_v2, gate, versions, routage, transitions, workflows),
              integration/ (v1 + v2 + gate + publication + gateway + cli_deploy),
              acceptance/ (10 tests du brief : verts)
docs/         besoin_client.md, schema_remediation.md, dossier-conception.pdf, exploitation.md [FAIT — SP3],
              superpowers/specs/ et superpowers/plans/ (moteur v2, gate, publication, routage/déploiement,
              observabilité)
.github/      workflows/ci.yml — gates (MOCK + release), build, publication, canary (installation + 10 %) ;
              promotion.yml (50 %, 100 %) et rollback.yml — pilotage manuel
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
   surveillance) et `.github/workflows/ci.yml`. *Que peut vérifier la CI sans
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

### Non publié — gate d'évaluation et publication (sous-projet 2)

*2026-09-22 — branche `feature/gate-publication`, commit `ac4f20a`. Spec :
`docs/superpowers/specs/2026-09-21-gate-publication-design.md`.*

**Ajouté**

- **Seuils versionnés** (`eval/seuils.yaml`, surchargeable par `SEUILS_PATH`) :
  note globale ≥ 0,75, latence P95 < 8 000 ms, coût moyen < 0,15 € par
  analyse, avec un `motif` obligatoire. Le seuil de chaque contrat reste
  `seuil_note` dans `eval/attendus.jsonl` (0,80 pour les contrats longs).
- **Gate d'évaluation** (`eval/run_eval.py::evaluer`) :
  - le moteur est choisi par la stratégie du bundle (`monolithique` → v1,
    `map_reduce_clauses` → v2 ; une v3 = une entrée dans `MOTEURS`) ;
  - chaque contrat reçoit une note (rappel des clauses attendues) ; avec
    `n` essais, les notes, latences et coûts sont moyennés ;
  - le gate calcule la latence P95 et le coût moyen, et liste les motifs
    d'échec. `passe` est vrai seulement s'il n'y a aucun motif ;
  - une erreur LLM ou un document trop long donne une note 0 et un motif
    d'échec, sans faire planter le gate ;
  - les mesures vont dans `eval/.metrics_eval.jsonl`, jamais dans le
    `ops/metrics.jsonl` de production.
- **Rapport** : chaque exécution ajoute une ligne à `eval/history.jsonl`, avec
  `mode_eval` (`mock` ou `reel`), les seuils appliqués et l'empreinte du
  bundle. `--sortie rapport.json` écrit le rapport en JSON. Codes de sortie :
  0 gate passé, 1 gate en échec, 2 gate mal configuré.
- **Publication** (`ops/deploy.py::publier`) :
  - si aucune version n'est fournie, le patch suivant de la base `vX.Y` du
    bundle est calculé à partir du registre et des tags git (`v2.0.0`,
    puis `v2.0.1`, …) ;
  - une version fournie est refusée si sa base ne correspond pas au bundle
    ou si elle est déjà publiée ;
  - si le rapport vient d'un autre bundle (empreinte différente), la
    publication est refusée sans rien écrire ;
  - si le gate échoue, la publication est refusée, rien n'est étiqueté et
    aucun numéro n'est consommé ; le refus est journalisé
    (`publication_refusee`) ;
  - sinon la version est étiquetée dans le registre avec la note, le mode
    et les seuils du gate, puis journalisée (`publication`).
- **CLI** : `python -m ops.deploy publier [vX.Y.Z] [--commit SHA] [--rapport
  rapport.json]` affiche le manifeste en JSON. Relancée sur un commit déjà
  étiqueté, elle republie le même numéro.
- **Chaîne CI** (`.github/workflows/ci.yml`, renommé depuis `llmops.yml`) :
  - `gate-evaluation` joue le gate en `MOCK=on` à chaque PR et à chaque
    fusion ; il est bloquant ;
  - `gate-release` joue le gate sur le vrai modèle (3 essais), sur un tag
    `v*` ou un lancement manuel ;
  - `build` ;
  - `publication`, sur `main` ou sur un tag seulement : elle relit le
    rapport du gate (un seul rapport fait foi), envoie l'artefact
    `mardik-vX.Y.Z`, puis pose le tag git. Si ce tag existe déjà sur un
    autre commit, le job échoue et rien n'est écrasé.
- **`make ci`** joue le gate avant les tests d'acceptance.
- **Tests** : 44 tests unitaires (`test_seuils`, `test_notation`,
  `test_moteurs`, `test_versions`) et 26 tests d'intégration (`test_gate`,
  `test_publication`). 6 tests d'acceptance sur 10 sont verts, dont
  `test_gate_evaluation_note_par_version`,
  `test_evaluation_enrichie_latence_et_cout` et
  `test_etiquetage_version_apres_gate`.

**Modifié** : dans `ops/deploy.py`, `_commit_courant` n'attrape plus que
`FileNotFoundError` et `CalledProcessError`.

**Inchangé** : le registre (`ops/registry/`), `app/llm_client.py`,
`app/telemetry.py`, le moteur v2 et `/v1`.

**Reste à faire**

- Les 4 derniers tests d'acceptance attendent les sous-projets 3 et 4 :
  canary, promotion et rollback, tableau de bord et boucle de dérive.
- En `MOCK=on`, le gate CI donne 1,0 à la v2 et sa latence n'est pas
  représentative. Il garantit la non-régression mécanique ; seul le gate de
  release mesure la qualité réelle.
- À vérifier au premier run GitHub après la fusion : le tag est posé,
  l'artefact `mardik-vX.Y.Z` est complet et le job canary n'est pas sauté.

### Non publié — routage canary et déploiement piloté (sous-projet 3)

*2026-09-22 — branche `feature/routage-deploiement`. Spec :
`docs/superpowers/specs/2026-09-22-routage-deploiement-design.md` · Plan :
`docs/superpowers/plans/2026-09-22-routage-deploiement.md`.*

**Ajouté**

- **Gateway canary** (`app/gateway.py`, `app/routage.py`) : `POST /analyse`
  relit `ops/registry/index.json` à **chaque requête** — une promotion ou un
  rollback prend effet sans redémarrage — choisit la version (`active` ou
  `canary`, selon `canary_percent` et un tirage aléatoire) et le moteur
  associé à sa stratégie (`monolithique` → v1, `map_reduce_clauses` → v2).
  `GET /gateway/etat` expose `active`/`canary`/`canary_percent`. Erreurs
  explicites, jamais de 500 brut : `503` si aucune version active
  (`AucuneVersionActive`), si la stratégie livrée n'est pas routable
  (`StrategieInconnue`) ou si le bundle livré est illisible
  (`BundleIllisible` — `config.yaml` ou son `prompt_fichier` absent/corrompu) ;
  `503` aussi si le fournisseur LLM échoue.
- **Déploiement piloté** (`ops/deploy.py`) : `installer` (copie l'artefact CI
  dans le registre de prod, idempotent à empreinte égale, exige un
  `config.yaml` dans le dossier source), `deployer_canary` (pourcentage de 1
  à 99, par défaut `CANARY_PERCENT` sinon 10 ; rappelé avec la même version,
  il fait progresser le canary, 10 → 50 ; refuse sans version active ou avec
  un autre canary en cours),
  `promouvoir` (l'ancienne active devient `precedente`), `rollback` (un seul
  niveau, retire le canary en priorité). Toute transition passe par
  `_transition` : verrou exclusif inter-processus (`index.lock`), calcul pur
  qui lève `ErreurDeploiement` en cas de refus, écriture atomique de l'index,
  puis entrée de journal `{evenement, avant, apres, origine}`. `origine` :
  `manuel`, `ci:<acteur GitHub>` ou `auto` (sous-projet 4).
- **CLI** : `python -m ops.deploy installer vX.Y.Z --depuis DOSSIER | canary
  vX.Y.Z [--pourcentage N] | promouvoir vX.Y.Z | rollback [--motif M]`, chacune
  avec `--origine`. Refus métier → `REFUSÉ : …` sur stderr, code 1 ; une
  erreur système non enveloppée (permission, disque, registre corrompu) →
  `ÉCHEC : …` sur stderr, code 1 — jamais de traceback brut.
- **Chaîne GitHub** : `ci.yml` (job `deploiement-canary`, runner auto-hébergé
  `[self-hosted, mardik]`) installe puis déploie 10 % de canary juste après
  la publication sur `main` ou un tag ; `promotion.yml` (déclenchement manuel)
  passe le canary à 50 % puis promeut à 100 % ; `rollback.yml` (déclenchement
  manuel) exécute le rollback en une opération. Les trois jobs de prod
  partagent `environment: production`, `concurrency: mardik-production` et
  ne s'exécutent jamais sur une pull request. Une étape « Préconditions »
  vérifie que `MARDIK_REGISTRY_PATH/index.json` existe avant toute écriture,
  pour ne jamais opérer sur un registre fantôme silencieusement recréé vide.
- **`docs/exploitation.md`**, §4 et §5 rédigés : déploiement progressif
  (étapes, refus, effet d'une relance), procédure de rollback, installation
  et durcissement du runner de prod (dépôt privé ou approbation des workflows
  de fork, environnement `production` restreint à `main`/`v*`,
  `MARDIK_REGISTRY_PATH` hors du dossier `_work` du runner).
- **`make etat`** : `GET /gateway/etat` sur l'app (`APP_URL`).
- **Tests** : 56 tests unitaires (`test_choisir_version`, `test_routage`,
  `test_transitions`, `test_workflows` — invariants de sécurité des workflows
  de prod) et 13 tests d'intégration (`test_gateway`, `test_cli_deploy`).
  8 tests d'acceptance sur 10 sont verts, dont
  `test_rollback_en_une_operation`, `test_promotion_canary_puis_totale` et
  `test_client_v1_fonctionne`.

**Modifié**

- `ops/registry/__init__.py` : `ecrire_index` écrit dans `index.json.tmp`
  puis `os.replace` (écriture atomique : la gateway relit l'index à chaque
  requête). C'est la seule modification du registre fourni.
- `ci.yml`, job `publication` : il expose `outputs.version` ; l'étape de
  récupération du rapport de gate, qui avait des clés dupliquées (workflow
  refusé par GitHub), est scindée en deux étapes, et le rapport `gate-reel`
  est téléchargé dans `eval/` comme `gate-mock`.
- `Makefile` : commentaires des cibles de test, `make ci` lance `actionlint`
  s'il est installé.

**Inchangé** : `app/api_v1.py`, `models/v1/`, `app/llm_client.py`,
`app/telemetry.py`, le moteur v2, le gate et `publier`.

**Reste à faire**

- Les 2 derniers tests d'acceptance attendent le sous-projet 4 : tableau de
  bord par version et détection de dérive avec rollback automatique
  (`ops/deploy.surveiller`, `ops/dashboard.py`), puis `docs/exploitation.md`
  §6 et §7. Les §1 à §3 restent à rédiger par l'équipe.
- Réglages GitHub à poser à la main (voir `docs/exploitation.md` §5) : runner
  `[self-hosted, mardik]`, variable `MARDIK_REGISTRY_PATH`, environnement
  `production` restreint à `main`/`v*`, approbation des workflows de fork.
- Workflows vérifiés par `tests/unit/test_workflows.py` seulement : à valider
  par `actionlint` et par un premier run réel (canary 10 %, promotion,
  rollback) sur le runner.
