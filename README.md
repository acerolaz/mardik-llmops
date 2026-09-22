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
  départ ; il l'est resté à chaque sous-projet, et les 10 tests d'acceptance
  sont verts depuis le sous-projet 4.
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
make up                      # app + interface :8000, proxy de dérive :8080, dashboard brut :8501, pilote
```

Sans docker : `make proxy` dans un terminal, `make serve` dans un autre.

**L'interface** : <http://localhost:8000/>. La page **Analyse** envoie le même contrat à
`/v1` et `/v2` côte à côte (ou via la gateway canary, avec la version servie), avec deux
exemples prêts : *Exemple court* (`c02`) et *Exemple long* (`c07`, 62 ko : « Contrat
tronqué » côté v1, indice de fiabilité et clauses par section côté v2). Son bandeau suit
la latence P95 face au SLO de 8 s et le canary en cours. La page **Pilotage**
(<http://localhost:8000/pilotage>) affiche tout le tableau de bord : alertes, trafic,
palier, candidats, métriques par version, journal. Rafraîchies toutes les 5 s (bouton pause),
clair/sombre.

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
| `make up` / `make down` | app + interface + proxy + dashboard brut + pilote (docker compose) |
| `make serve` | app + interface en local, sans docker (<http://localhost:8000/>) |
| `make test` | tout, en `MOCK=on` |
| `make test-unit` | les tests unitaires du pipeline v2, du gate, des versions, du routage, des transitions de déploiement, des workflows, des signaux, du pilotage, des seuils et de l'anonymisation (verts) |
| `make test-integration` | les tests hérités de la remédiation, de la v2, du gate, de la publication, de la gateway, de la CLI de déploiement, de la surveillance, du pilote, de la capture, de l'enrichissement, du dashboard et de l'interface (verts) |
| `make test-web` | le module JS partagé de l'interface (`node --test`, sans dépendance) |
| `make test-acceptance` | les 10 tests du brief (verts) |
| `make etat` | répartition courante du trafic vue par la gateway (`APP_URL`, défaut `http://localhost:8000`) |
| `make eval VERSION=v2` | le gate d'évaluation sur le **vrai** modèle (`ARGS="--essais 3"`) |
| `make traffic MODE=derive-score` | trafic sur la gateway + dérive commandée (`normal`, `derive-latence`, `erreurs`) |
| `make dashboard` | tableau de bord brut (texte) ; `DASH=serve` pour sa page HTML sur :8501 |
| `make pilote` | boucle du pilote : surveillance, rollback automatique, promotion canary |
| `make calibrer VERSION=v2.0.0` | propose des seuils (moyenne − k·σ) sur la production ; n'écrit rien |
| `make candidats` | cas v2 à faible confiance capturés, en attente de versement |
| `make verser ID=… CLAUSES=a,b` | verse un candidat relu dans le jeu d'évaluation (`eval/contrats/`, `attendus.jsonl`) |
| `make ci` | l'équivalent local du workflow GitHub (le gate MOCK passe avant l'acceptance ; tests JS si Node est présent) |
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
              llm_client.py [FOURNI], telemetry.py [FOURNI], capture.py [SP4],
              pilotage.py [GET /pilotage/resume — interface]
app/web/     index.html + analyse.js (Analyse), pilotage.html + pilotage.js (Pilotage),
              commun.js, styles.css [interface client]
app/pipeline/ decoupage.py, extraction.py, consolidation.py, confiance.py, erreurs.py [FAIT — v2.0.0]
models/       v1/config.yaml [FOURNI], v2/config.yaml [FAIT — bundle map_reduce_clauses]
eval/         contrats/ (12 contrats, 3 longs), attendus.jsonl, fixtures/ (MOCK), seuils.yaml [FAIT],
              run_eval.py [FAIT — gate], history.jsonl, rapport.json [GÉNÉRÉS], enrichir.py [SP4]
ops/          drift_proxy.py [FOURNI], registry/ [FOURNI], deploy.py [publier, installer, canary,
              promotion, rollback FAIT — SP3 ; surveiller, piloter FAIT — SP4], dashboard.py, pilotage.py,
              signaux.py, seuils.py [FAIT — SP4]
scripts/      client_v1.py [FOURNI], traffic_sim.py [FOURNI]
tests/        unit/ (pipeline, analyser_v2, gate, versions, routage, transitions, workflows,
              signaux, pilotage, seuils_pilotage, calibrer, resume_metier, anonymisation, verrou_capture),
              integration/ (v1 + v2 + gate + publication + gateway + cli_deploy + surveiller + piloter
              + capture + enrichir + dashboard + details_journal + interface),
              web/ (module JS de l'interface, node --test),
              acceptance/ (10 tests du brief : verts)
docs/         besoin_client.md, schema_remediation.md, dossier-conception.pdf, exploitation.md [§4-§7 FAIT — SP3, SP4],
              superpowers/specs/ et superpowers/plans/ (moteur v2, gate, publication, routage/déploiement,
              observabilité, interface client)
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
6. **L'interface client** — `app/web/`, `app/pilotage.py` : le livrable « un client via
   un frontend accessible via un lien ».

Démo de fin : *Exemple long* sur <http://localhost:8000/> (v1 tronque, v2 non), puis
`make traffic MODE=derive-score` en live → dérive vue sur <http://localhost:8000/pilotage> →
rollback automatique par le pilote → entrée au journal. Procédure des
trois boucles (rollback, promotion, enrichissement) : `docs/exploitation.md` §7.

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

### Non publié — observabilité et boucles de rétroaction (sous-projet 4)

*2026-09-22 — branche `feature/observabilite`. Spec :
`docs/superpowers/specs/2026-09-22-observabilite-design.md` · Plans :
`docs/superpowers/plans/2026-09-22-observabilite.md`,
`docs/superpowers/plans/2026-09-22-corrections-observabilite.md`.*

**Ajouté**

- **Seuils de pilotage** (`ops/seuils_pilotage.yaml`, `ops/seuils.py`) : config
  as code, même convention que `eval/seuils.yaml` (`motif` obligatoire),
  chargement strict qui nomme la clé fautive (`fenetre_s`, `minimum`,
  `intervalle_s` strictement positifs). `calibrer` propose `moyenne − k·σ` à
  partir de la production, sans rien écrire.
- **Décisions pures** (`ops/signaux.py`, `ops/pilotage.py`) : agrégats par
  version (P50/P95/P10, erreurs, score, coût), détection de dérive (seuil dur,
  marge, échantillon minimal), décision de palier canary (durée, volume, écart
  d'erreurs, P95, score moyen, P10), résumés en langage métier du journal.
- **Pilote** (`ops/deploy.py surveiller | piloter`, service docker `pilote`,
  `make pilote`) : dérive critique → rollback automatique tracé
  (`origine: auto`), marge → alerte, promotion canary 10 → 50 → 100 % quand
  les métriques tiennent ; refus, changements de seuils et canary sans début
  de palier journalisés une fois par fenêtre. Un seuil invalide au démarrage
  l'arrête (code 1) ; en cours de route, il garde les derniers seuils valides
  et survit aux incidents d'I/O transitoires.
- **Capture** (`app/capture.py`) : les analyses v2 à faible confiance
  (`/v2/analyse` et la gateway) sont anonymisées (e-mail, IBAN, téléphone,
  SIRET, personnes) et ajoutées à `eval/candidats.jsonl` en tâche de fond,
  sous verrou, sans jamais affecter la réponse client.
- **Enrichissement** (`eval/enrichir.py`, `make candidats`, `make verser`) :
  versement humain d'un candidat relu dans le jeu d'évaluation ; le contrat est
  publié en dernier, par renommage atomique.
- **Tableau de bord** (`ops/dashboard.py`) : par version, palier en cours
  (compté comme le pilote), alertes, candidats à verser, 5 dernières entrées du
  journal ; rendu texte et page HTML auto-rafraîchie. Une source illisible
  donne une alerte, jamais un 500.
- **`docs/exploitation.md`** §6 (surveillance, seuils, calibration,
  enrichissement, garde-fous du pilote) et §7 (preuve d'exécution des trois
  boucles).
- **Makefile** : cibles `pilote`, `calibrer`, `candidats`, `verser`.
- **Tests** : 56 tests unitaires et 45 tests d'intégration. Les 10 tests
  d'acceptance sont verts, dont `test_dashboard_par_version` et
  `test_journal_derive_et_rollback_automatique`. `MOCK=on uv run pytest -q` :
  328 passés.

**Modifié**

- `app/api_v2.py`, `app/gateway.py` : capture branchée en `BackgroundTasks`.
- `docker-compose.yml` : service `pilote` ; le dashboard monte `./eval`.
- `promotion.yml` : devient la voie manuelle, le pilote promeut seul.

**Inchangé** : `app/api_v1.py`, `ops/registry/`, `app/llm_client.py`,
`app/telemetry.py`, le moteur v2, le gate.

**Reste à faire**

- Remettre `promotion.duree_min_s: 1800` et `requetes_min: 500` (valeurs de
  production) après la démo, avec un nouveau `motif`.
- `ops/registry/__init__.py::journal()` ne tolère pas une ligne corrompue
  (fichier fourni, hors périmètre) : le pilote survit et le dashboard alerte.
- Premier run réel du service `pilote` sur l'environnement de prod.

### Non publié — interface client

*2026-09-22 — branche `worktree-interface-client`, PR #6. Spec :
`docs/superpowers/specs/2026-09-22-interface-client-design.md` · Plan :
`docs/superpowers/plans/2026-09-22-interface-client.md`.*

**Ajouté**

- **Page Analyse** (`GET /`, `app/web/index.html` + `analyse.js`) : le même contrat
  envoyé à `/v1` et `/v2` côte à côte (chaque colonne se remplit seule : un 413 v2
  n'empêche pas v1), ou via la gateway avec la version servie (`X-Mardik-Version`) ;
  exemples court et long ; avertissement avant troncature v1 ; vocabulaire métier
  (« Contrat tronqué », « Indice de fiabilité », « Analyse non fiable », « à relire ») ;
  bandeau P95 vs SLO 8 s et canary en cours.
- **Page Pilotage** (`GET /pilotage`, `pilotage.html` + `pilotage.js`) : tous les champs du
  résumé — alertes, part de trafic, palier, candidats, cartes par version (P50/P95,
  erreurs, score, coût, histogramme, sparklines), journal.
- **`GET /pilotage/resume`** (`app/pilotage.py`) : route mince sur `ops.dashboard.resume()`,
  schéma Pydantic `ResumePilotage` ; même fenêtre que le pilote, jamais de 500 sur une
  source illisible.
- **Design system** (UI UX PRO MAX) : « Trust & Authority », palette « Legal Services »,
  EB Garamond + Lato, tokens `light-dark()`, contrastes AA, `prefers-reduced-motion`,
  responsive 375 → 1440 px. HTML/CSS/JS vanilla, sans dépendance ni build.
- **Tests** : `tests/integration/test_interface.py` (10) et `tests/web/commun.test.mjs`
  (arrondi aligné sur Python, `make test-web`). `MOCK=on uv run pytest -q` : 338 passés.
- **Makefile** : cible `test-web`, tests JS dans `make ci` si Node est présent, liens de
  l'interface dans `make up`.

**Modifié**

- `app/main.py` : routeur de pilotage, montages `/static` (`app/web`) et `/exemples`
  (`eval/contrats`, lecture seule), pages `/` et `/pilotage`.

**Inchangé** : `app/api_v1.py`, `app/api_v2.py`, `app/gateway.py`, `ops/dashboard.py`
(la page brute reste sur :8501), `docker-compose.yml`, `Dockerfile`.

**Reste à faire**

- Rafraîchissement sans garde « requête en cours » : des réponses lentes peuvent
  arriver dans le désordre.
- Un pourcentage de canary hors 10/50/100 n'active aucune étape du palier (paliers
  dupliqués en constante JS).
- Une réponse 200 non JSON laisse le squelette de chargement affiché.
