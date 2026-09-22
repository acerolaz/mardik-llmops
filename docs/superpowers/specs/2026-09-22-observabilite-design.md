# Sous-projet 4 — Observabilité & boucles de rétroaction

*Spec de conception — 2026-09-22. Quatrième des quatre sous-projets de la v2
Mardik (cf. `2026-09-21-moteur-v2-design.md`, §1). Part de `main` @ `41297d9`
(sous-projets 1 et 2 fusionnés). Suppose le sous-projet 3 (routage &
déploiement, `2026-09-22-routage-deploiement-design.md`, branche
`feature/routage-deploiement`) livré avant la phase B.*

## 1. Contexte et décisions de cadrage

Le brief (Chantier 2) veut une observabilité **qui pilote la chaîne** : des
signaux, des seuils, et trois rétroactions tracées — rollback, promotion canary,
enrichissement du jeu d'évaluation. La télémétrie est fournie et fonctionnelle
(`app/telemetry.py` : spans `analyse.requete` → `llm.appel`, logs structurés,
`MetricsStore` → `ops/metrics.jsonl`) ; la v2 (SP1) y écrit déjà son `score`.
Restent à écrire : le tableau de bord (`ops/dashboard.py`), la surveillance
(`ops/deploy.py::surveiller`), la promotion pilotée par les métriques,
l'enrichissement et le journal de pilotage enrichi.

**Décisions validées en brainstorming :**

| # | Question | Décision |
|---|---|---|
| D1 | Périmètre | **Socle + les trois boucles** : dashboard, surveillance et rollback automatique, promotion 10 → 50 → 100 % pilotée par les métriques, capture des cas à faible confiance, journal de pilotage enrichi (signal, valeur, seuil, origine, résumé métier). Signaux métier B2.1 (signalements clients, taux de relecture) hors périmètre. |
| D2 | Décision de promotion | **Automatique**, journalisée avec `origine: "auto"`. Un humain peut toujours interrompre (`rollback.yml` / CLI). |
| D3 | Enrichissement | **Capture automatique, versement humain** : capture anonymisée vers `eval/candidats.jsonl` ; `python -m eval.enrichir verser <id> --clauses …` crée le contrat annoté (validation juriste, B2.4) ; le gate le rejoue à la fusion suivante. |
| D4 | Seuils | **Config as code** : `ops/seuils_pilotage.yaml` versionné, même convention que `eval/seuils.yaml` (SP2, `motif` obligatoire). Tout changement de l'un ou l'autre fichier est journalisé (`seuils`). `calibrer` propose des valeurs, ne les écrit pas. |
| D5 | Exécution des boucles | **Processus `pilote` dédié** (service docker-compose) : `python -m ops.deploy piloter`, une tour toutes les 5 s, **sans état** (tout est relu : métriques, registre, journal). |
| D6 | Phasage | **Décider ≠ exécuter** : la logique de décision est pure (`ops/pilotage.py`) ; `ops/deploy.py` ne fait que l'exécuter. Phase A démarre sans SP3 ; phase B câble après sa fusion (§2). |
| D7 | Calibration (C2.2, C2.19) | Sur la **distribution de production** (`metrics.jsonl`) et non sur le golden dataset : les rapports du gate ne portent pas de score de confiance par contrat, seulement la note de rappel. Écart assumé à C2.19 ; la référence « golden » reste la note du gate. |

**Alignement avec SP3** : vocabulaire `origine` ∈ `manuel` | `ci:<acteur>` |
`auto` ; progression de palier = rappel de `deployer_canary` avec la même
version ; rollback à un niveau ; transitions sérialisées par `_verrou`.

## 2. Périmètre

**Phase A — indépendante de SP3 (démarre depuis `main`)**

- `ops/seuils_pilotage.yaml` + `ops/seuils.py` : chargement, validation, empreinte, `calibrer`, CLI
- `ops/signaux.py` : agrégats purs sur des `Mesure`
- `ops/pilotage.py` : décisions pures (`detecter_derive`, `evaluer_palier`, `debut_palier`, `changements_seuils`) et résumés métier
- `ops/dashboard.py` : `resume`, `rendre_texte`, `rendre_html`
- `app/capture.py` : anonymisation + `capturer` ; hook sur `POST /v2/analyse` (`app/api_v2.py`)
- `eval/enrichir.py` : CLI `lister`, `verser`
- `.gitignore` : `eval/candidats.jsonl` ; `tests/conftest.py` : `CANDIDATS_PATH` dans `tmp_path` (une ligne)
- `docs/exploitation.md` : §6 « Surveillance et seuils », §7 « Preuve d'exécution » (les §4–5 appartiennent à SP3)
- Tests unitaires et d'intégration de ces unités

**Phase B — après fusion de SP3**

- `ops/deploy.py` : `surveiller`, `piloter`, exécution des décisions ; `**details` keyword-only ajoutés à `deployer_canary`, `promouvoir`, `rollback` (passés à `_transition`) ; CLI `piloter`
- Hook de capture sur la gateway `POST /analyse` (`app/gateway.py`)
- `docker-compose.yml` : service `pilote` ; `Makefile` : cible `pilote`
- Tests d'intégration de `surveiller` / `piloter`, démo live

**Hors périmètre**

- Signaux métier B2.1 (signalements clients, taux de relecture manuelle) — nécessiteraient une nouvelle entrée API
- Propagation d'un `request_id`, collecteur OpenTelemetry, rétention et échantillonnage des traces (C2.13, C2.14) : la télémétrie fournie reste telle quelle
- Enregistrement automatique des fixtures MOCK pour les contrats versés (voir §8.3)
- Confirmation humaine avant rollback dans la marge (C2.16) : la marge produit une **alerte** ; l'humain agit via `rollback.yml`

## 3. Architecture et flux

```
 trafic ─► /analyse (gateway) ─┐                    ┌─► ops/metrics.jsonl ◄──────────────┐
          /v2/analyse ─────────┼─► moteur v1|v2 ────┤                                    │
                               │                    └─► BackgroundTasks: capturer ─► eval/candidats.jsonl
                               │                                                         │
                               │         eval.enrichir verser (humain) ◄─────────────────┘
                               │              └─► eval/contrats/cNN.txt + attendus.jsonl ─► gate (fusion suivante)
                               │
 pilote (toutes les 5 s) ──────┴─ lit metrics.jsonl, registre, journal, seuils
     ops/deploy.piloter ─► ops/pilotage (décisions pures) ─► rollback | deployer_canary | promouvoir
                                                                   └─► ops/registry/journal.jsonl
 dashboard :8501 ─► ops/dashboard.resume ─► ops/signaux + ops/pilotage (mêmes calculs)
```

| Unité | Rôle | Dépend de |
|---|---|---|
| `ops/seuils.py` | `SeuilsPilotage` (dataclass), `charger_seuils_pilotage`, `empreinte`, `calibrer` | yaml |
| `ops/signaux.py` | P50/P95/P10, taux d'erreur, score moyen, histogramme, série par minute, coût | `Mesure` |
| `ops/pilotage.py` | Décisions pures + gabarits de résumé métier | signaux, seuils |
| `ops/dashboard.py` | Agrège et rend | signaux, pilotage, `Registry` (lecture) |
| `ops/deploy.py` | Exécute les décisions (phase B) | pilotage, transitions SP3 |
| `app/capture.py` | Anonymise et capture | seuils |
| `eval/enrichir.py` | Liste et verse les candidats | capture (format), `Registry` (journal) |

`ops/signaux.py` est partagé par le dashboard et les décisions : **ce qu'on voit
à l'écran est exactement ce qui a déclenché l'action.**

### 3.1 Tableau de pilotage

Valeurs initiales de `ops/seuils_pilotage.yaml` (fenêtres de palier courtes pour
la démo ; valeurs de production entre parenthèses).

| Signal | Seuil | Rétroaction | Trace (journal) |
|---|---|---|---|
| Score moyen de la version surveillée (fenêtre 300 s, ≥ 10 mesures) | < 0,70 (seuil dur) | **Rollback** automatique | `rollback` : `origine: auto`, signal, valeur, seuil, résumé métier |
| Score moyen dans la marge | [0,70 ; 0,75[ | Alerte, aucune action | `alerte` |
| Taux d'erreur | > 10 % | Rollback | `rollback` |
| Latence P95 | > 8 000 ms | Rollback | `rollback` |
| Palier tenu : ≥ 60 s (1 800 s) **et** ≥ 20 requêtes (500) ; erreurs canary ≤ active + 2 pts ; P95 < 8 s ; score moyen ≥ 0,75 ; P10 du score ≥ 0,65 | tous vrais | **Promotion** 10 → 50 → 100 % | `canary` / `promotion` avec les critères et leurs valeurs |
| Score d'une analyse v2 servie | < 0,70 | **Capture** → versement humain → gate | `enrichissement` (au versement) |
| `seuils_pilotage.yaml` ou `eval/seuils.yaml` modifié | empreinte différente | Rechargement | `seuils` : fichier, avant, après, motif |

Le P10 du score traduit C2.12 : une queue basse que la moyenne masque.

## 4. Seuils — `ops/seuils_pilotage.yaml`, `ops/seuils.py`

```yaml
# Seuils de pilotage — config as code (même convention que eval/seuils.yaml).
fenetre_s: 300              # fenêtre glissante de surveillance (C2.22)
minimum: 10                 # mesures minimales avant toute décision (C2.15)
intervalle_s: 5             # période d'une tour de `piloter`
derive:
  score_min: 0.70           # seuil dur → rollback
  marge: 0.05               # [score_min ; score_min + marge[ → alerte
  taux_erreur_max: 0.10
  latence_p95_max_ms: 8000
promotion:
  paliers: [10, 50, 100]    # 100 = promouvoir
  duree_min_s: 60           # production : 1800
  requetes_min: 20          # production : 500
  ecart_erreur_max: 0.02    # taux d'erreur canary ≤ active + 0,02
  latence_p95_max_ms: 8000
  score_moyen_min: 0.75
  score_p10_min: 0.65
capture:
  score_max: 0.70           # confiance_globale < score_max → candidat
motif: "seuils initiaux — score_min = seuil de relecture juriste ; fenêtres de palier courtes pour la démo live"
```

- `charger_seuils_pilotage(chemin=None) -> SeuilsPilotage` : chemin =
  `PILOTAGE_SEUILS_PATH` sinon `ops/seuils_pilotage.yaml`, relu à chaque appel.
  Toute clé manquante, non numérique, `paliers` non croissant ou ne finissant
  pas par 100, ou `motif` vide → `ErreurSeuilsPilotage(ValueError)` avec un
  message qui nomme la clé. Classe propre à `ops/seuils.py` : réutiliser
  `eval.run_eval.ErreurSeuils` créerait un import circulaire
  (`app.capture` → `ops.seuils` → `eval.run_eval` → `app.api_v2` → `app.capture`).
  `ops/seuils.py` n'importe ni `app.api_*` ni `eval`.
- `empreinte(chemin) -> str` : sha256 (12 car.) du contenu du fichier.
- `calibrer(metriques, version, *, fenetre_s=3600, k=1.5, minimum=30) -> dict` :
  sur les scores non nuls, sans erreur, de `version` : `{"version", "mesures",
  "moyenne", "ecart_type", "p10", "score_min_propose": moyenne − k·σ,
  "score_p10_min_propose": p10 − marge}`. Moins de `minimum` mesures →
  `ErreurSeuilsPilotage("échantillon insuffisant …")`.
- CLI : `python -m ops.seuils calibrer --version v2.0.0 [--fenetre 3600] [--k 1.5]`
  affiche la proposition et la ligne YAML à reporter ; **n'écrit rien**. Le
  report se fait par commit, avec un `motif` qui cite la calibration.

## 5. Signaux et tableau de bord

### 5.1 `ops/signaux.py`

Fonctions pures sur `list[Mesure]` ; les **erreurs sont exclues** des latences
et des scores, pas des comptes :

- `percentile(valeurs, p)` : interpolation linéaire ; liste vide → `None`
- `agreger(mesures) -> Agregat` (dataclass) : `requetes`, `taux_erreur`,
  `latence_p50_ms`, `latence_p95_ms`, `score_moyen`, `score_p10`,
  `cout_total_eur`, `cout_moyen_eur`. `score_moyen`/`score_p10` = `None` si
  aucune mesure ne porte de score (v1).
- `histogramme(scores, pas=0.1) -> list[int]` : 10 intervalles sur [0 ; 1].
- `serie_par_minute(mesures) -> list[dict]` : `{minute, requetes, latence_p95_ms, taux_erreur, score_moyen}`.

### 5.2 `resume(metriques=None, *, fenetre_s=300, registry=None) -> dict`

Contrat du stub (vérifié par `test_dashboard_par_version`), enrichi sans le casser :

```json
{
  "fenetre_s": 300, "total": 128,
  "par_version": {
    "v2.0.0": {"requetes": 13, "trafic_pct": 10.2, "latence_p50_ms": 3100.0,
               "latence_p95_ms": 5200.0, "taux_erreur": 0.0, "score_moyen": 0.84,
               "score_p10": 0.71, "cout_total_eur": 0.41,
               "histogramme_score": [0,0,0,0,0,0,0,1,7,5], "serie_minute": [...]}
  },
  "palier": {"version": "v2.0.0", "pourcentage": 10, "depuis_s": 42.0,
             "requetes": 13, "duree_min_s": 60, "requetes_min": 20},
  "alertes": ["v2.0.0 : score moyen 0,72 dans la marge [0,70 ; 0,75["],
  "candidats": 3,
  "journal": [ …5 derniers événements… ]
}
```

- `trafic_pct` arrondi à 0,1 ; les versions sans trafic dans la fenêtre sont absentes.
- `palier` = `None` sans canary. `alertes` = sortie de `pilotage.detecter_derive`
  pour la version surveillée (sans action) ; seuils invalides → une alerte
  « seuils invalides : … », le reste du résumé est produit.
- `candidats` = nombre de candidats `en_attente` (§8).

### 5.3 Rendus

- `rendre_texte(r)` : un bloc par version (trafic, P50/P95, erreurs, score
  moyen/P10, coût), puis palier, alertes, journal (colonne `resume`).
- `rendre_html(r)` : page autonome, `<meta http-equiv="refresh" content="5">`,
  SVG inline sans dépendance : jauge de part de trafic, histogrammes de score
  côte à côte par version, sparklines P95 et erreurs par minute, alertes, journal
  en langage métier. Fenêtre sans trafic → « aucun trafic dans la fenêtre ».

## 6. Décisions — `ops/pilotage.py` (pur, phase A)

Aucune écriture : chaque fonction reçoit des mesures, des seuils et des entrées
de journal, et renvoie une décision.

```python
@dataclass(frozen=True)
class Constat:          # un critère évalué
    signal: str         # "score_moyen" | "taux_erreur" | "latence_p95_ms" | "score_p10" | …
    valeur: float | None
    seuil: float
    ok: bool

@dataclass(frozen=True)
class Derive:
    version: str
    mesures: int
    niveau: str                  # "aucune" | "marge" | "critique" | "echantillon_insuffisant"
    constats: list[Constat]
    motif: str                   # ex. "score moyen 0.52 < 0.70"
    sous_seuil: int              # scores individuels < score_min (résumé métier)

@dataclass(frozen=True)
class DecisionPalier:
    version: str
    action: str                  # "attendre" | "progresser" | "promouvoir"
    pourcentage_suivant: int | None
    constats: list[Constat]
    motif: str
```

- `version_surveillee(index) -> str | None` : le canary s'il existe, sinon l'active.
- `debut_palier(journal, version) -> float | None` : `ts` de la dernière entrée
  `canary` de `version` (quelle que soit son `origine`), à condition qu'aucun
  `rollback` ne la suive ; sinon `None`.
- `detecter_derive(version, mesures, seuils_derive, *, minimum) -> Derive` : `critique`
  si l'un des seuils durs est franchi (score, erreurs, P95 — le score n'est pas
  évalué s'il est `None`) ; sinon `marge` si le score moyen est dans la marge ;
  `echantillon_insuffisant` sous `minimum` mesures. Le `motif` nomme le premier
  signal critique (ordre : score, erreurs, P95) — le test d'acceptance exige
  que « score » y figure.
- `evaluer_palier(version, mesures_canary, mesures_active, seuils, *, depuis_s, pourcentage) -> DecisionPalier` :
  `attendre` tant que `depuis_s < duree_min_s` ou `requetes < requetes_min`, ou
  qu'un critère échoue (le motif dit lequel) ; sinon `progresser` vers le palier
  suivant de `paliers`, ou `promouvoir` si le suivant est 100. Le critère
  d'erreur compare à l'active si elle a ≥ `minimum` mesures, sinon au seuil
  absolu `derive.taux_erreur_max`.
- `changements_seuils(journal, fichiers) -> list[dict]` : pour chaque fichier
  dont l'empreinte diffère de la dernière entrée `seuils` le concernant, un
  événement à journaliser (`fichier`, `empreinte`, `avant`, `apres`, `motif`).
  Au premier lancement, l'état initial est journalisé (`avant: null`).
- `resume_metier(evenement, **details) -> str` : un gabarit par événement, en
  français lisible par un non-technicien (B2.5). Exemples :
  - rollback/score : « Version v2.0.0 retirée : 15 analyses sur 27 jugées peu fiables (score < 0,70). »
  - rollback/erreurs : « Version v2.0.0 retirée : 12 % des analyses en échec (maximum toléré 10 %). »
  - rollback/latence : « Version v2.0.0 retirée : analyses trop lentes (P95 9,1 s, maximum 8,0 s). »
  - canary : « Version v2.0.0 étendue à 50 % des clients : 23 analyses conformes en 64 s. »
  - promotion : « Version v2.0.0 servie à tous les clients : tous les critères tenus. »
  - alerte : « Version v2.0.0 à surveiller : score moyen 0,72, proche du seuil 0,70. »
  - enrichissement : « Contrat c13 ajouté au jeu d'évaluation (score en production 0,48). »
  - seuils : « Seuil de pilotage modifié : score_min 0,70 → 0,68 (motif : … ). »

## 7. Exécution — `ops/deploy.py` (phase B)

### 7.1 Détails passés au journal

`deployer_canary`, `promouvoir`, `rollback` reçoivent `**details` keyword-only,
transmis tels quels à `_transition` (SP3) : ajout rétrocompatible, les appels
existants (tests, CLI, workflows) ne changent pas.

Entrée de journal produite par le pilotage (C2.8 + B2.5) :

```json
{"ts": 1790000531.2, "date": "2026-09-22T14:02:11+00:00", "evenement": "rollback",
 "origine": "auto", "motif": "score moyen 0.52 < 0.70",
 "signal": "score_moyen", "valeur": 0.52, "seuil": 0.70, "mesures": 27,
 "constats": [{"signal": "score_moyen", "valeur": 0.52, "seuil": 0.70, "ok": false}, …],
 "resume": "Version v2.0.0 retirée : 15 analyses sur 27 jugées peu fiables (score < 0,70).",
 "avant": {"active": "v1.0.0", "canary": "v2.0.0", …}, "apres": {"active": "v1.0.0", "canary": null, …}}
```

Événements ajoutés par ce sous-projet (via `registry.journaliser`) : `alerte`,
`pilotage_refus`, `seuils`, `seuils_invalides`, `enrichissement`. Anti-spam :
`alerte` et `pilotage_refus` ne sont pas rejournalisés pour la même version et
le même signal tant que la précédente date de moins de `fenetre_s`.

### 7.2 `surveiller`

```python
def surveiller(registry=None, metriques=None, *, fenetre_s=None, score_min=None,
               taux_erreur_max=None, latence_p95_max_ms=None, minimum=None,
               seuils=None) -> dict
```

Les paramètres à `None` prennent la valeur de `seuils` (sinon du fichier) ; les
valeurs explicites l'emportent (le test d'acceptance les passe). Déroulé :
version surveillée → mesures de cette version sur `fenetre_s`, limitées à
`ts ≥ debut_palier` s'il s'agit d'un canary → `detecter_derive` →
`critique` : `rollback(registry, motif=…, origine="auto", signal=…, valeur=…,
seuil=…, mesures=…, constats=…, resume=…)` ; `marge` : `alerte`. Renvoie
`{"version", "mesures", "derive", "niveau", "motif", "rollback"}` (`derive` vrai
seulement pour `critique`).

### 7.3 `piloter`

```python
def piloter(registry=None, metriques=None, *, tours=None, attendre=time.sleep) -> None
```

Chaque tour : (1) `charger_seuils_pilotage` — échec au premier tour → sortie
code 1 ; échec ensuite → derniers seuils valides + `seuils_invalides` (une fois) ;
(2) `changements_seuils` → événements `seuils` ; (3) `surveiller` — rollback
→ fin de tour ; (4) canary en cours → `evaluer_palier` → `progresser` :
`deployer_canary(v, pourcentage_suivant, origine="auto", …)`, `promouvoir` :
`promouvoir(v, origine="auto", …)`. `ErreurDeploiement` → `pilotage_refus`,
la boucle continue. `tours` (tests) borne le nombre de tours ; `attendre` est
injectable. Redémarrer `pilote` ne perd rien : le palier et son début sont relus
dans le journal.

CLI : `python -m ops.deploy piloter [--tours N]` (la période vient de `intervalle_s`). Le service
docker-compose `pilote` la lance (mêmes volumes `ops/`, `eval/` que `app`) ;
`make pilote` en local.

## 8. Enrichissement du jeu d'évaluation

### 8.1 Capture — `app/capture.py`

- `anonymiser(texte) -> str` : remplace e-mails `[EMAIL]`, téléphones FR
  `[TELEPHONE]`, IBAN `[IBAN]`, SIRET/SIREN `[SIRET]`, civilité + nom
  (`M.`, `Mme`, `Monsieur`, `Madame`, `Me` suivis de mots capitalisés)
  `[PERSONNE]`. Raisons sociales et adresses ne sont **pas** masquées : c'est
  au juriste de le vérifier avant versement (§8.2).
- `Capture` (dataclass : `chemin`, `score_max`) fournie par la dépendance
  `get_capture()` (chemin `CANDIDATS_PATH`, sinon `eval/candidats.jsonl` ;
  `score_max=None` → lu dans les seuils de pilotage **au moment de la capture**,
  jamais à la résolution de la dépendance) — surchargeable dans les tests.
- `capturer(capture, texte, reponse) -> str | None` : si
  `reponse.confiance_globale < capture.score_max`, ajoute une ligne
  `{"type": "candidat", "id": "cand-<empreinte[:10]>", "date", "version",
  "score", "texte" (anonymisé), "clauses_trouvees", "empreinte"}` —
  `empreinte` = sha256 du texte **brut** (dédoublonnage : un texte déjà
  capturé n'est pas réécrit). Écriture en ajout, sous verrou. `OSError` et
  `ErreurSeuilsPilotage` sont journalisées (`capture.echec`) et avalées : **la réponse
  client n'est jamais affectée**.
- Branchement : `POST /v2/analyse` (phase A) et `POST /analyse` (phase B)
  reçoivent `BackgroundTasks` et ajoutent `capturer` après une réponse v2. Pas
  dans `analyser_v2` : le gate l'appelle aussi et ne doit rien capturer.

### 8.2 Versement — `eval/enrichir.py`

`eval/candidats.jsonl` est **append-only** : un versement ajoute une ligne
`{"type": "verse", "id", "contrat_id", "date"}` ; l'état d'un candidat se déduit
en rejouant le fichier. Le fichier est ignoré par git (données client, même
anonymisées) ; seuls les contrats versés, relus, entrent au dépôt par PR.

- `python -m eval.enrichir lister` : candidats `en_attente` (id, date, version,
  score, clauses trouvées, 200 premiers caractères).
- `python -m eval.enrichir verser <id> --clauses "résiliation,durée,…" [--seuil-note 0.75]` :
  1. refus (`ErreurEnrichissement`, code 1, rien n'est écrit) si l'id est
     inconnu ou déjà versé, si `--clauses` est vide ou contient un type hors
     `TYPES_CLAUSES` ;
  2. `contrat_id` = `c` + (max des numéros existants + 1) sur 2 chiffres ;
  3. écrit `eval/contrats/<id>.txt` (texte anonymisé) puis ajoute à
     `eval/attendus.jsonl` `{"contrat_id", "pages": max(1, round(len/3000)),
     "clauses_attendues", "seuil_note", "origine": "production"}` ;
  4. ajoute la ligne `verse` et journalise `enrichissement` (`origine: manuel`,
     `candidat`, `contrat_id`, `score`, `resume`).

Le gate (`_charger_contrats`) lit tous les contrats d'`attendus.jsonl` : le
nouveau contrat est rejoué à la fusion suivante sans autre modification.

### 8.3 Limite : fixtures MOCK

Le gate de CI tourne en `MOCK=on` : pour un contrat versé, aucune fixture
n'existe et le client utilise la réponse de repli par mots-clés. Le contrat est
donc rejoué, mais **réellement évalué seulement par le gate de release** (vrai
modèle, SP2) ou après `make fixtures`. Documenté dans `exploitation.md` §6.

## 9. Gestion des erreurs

Jamais d'`except Exception` : chaque cas nomme son exception.

| Cas | Comportement |
|---|---|
| Seuils de pilotage absents / invalides / sans `motif` | `ErreurSeuilsPilotage` nommant la clé. `piloter` au démarrage : code 1. En cours : derniers seuils valides + `seuils_invalides` une fois. Dashboard : alerte, résumé produit. |
| Moins de `minimum` mesures | `echantillon_insuffisant` : ni rollback ni promotion ; `motif: "échantillon insuffisant (n/10)"`. |
| Transition refusée (`ErreurDeploiement`) | `pilotage_refus` (raison), la boucle continue et retente à la tour suivante. |
| Dérive critique sans retour arrière possible (ni canary ni `precedente`) | `pilotage_refus` avec « aucun retour arrière possible » ; l'alerte reste visible au dashboard. |
| Ligne corrompue dans `metrics.jsonl` | Ignorée (`MetricsStore._lignes`, fourni). |
| Échec de capture (I/O, seuils) | Log `capture.echec`, réponse client inchangée. |
| `verser` invalide | `ErreurEnrichissement`, code 1, aucune écriture. |
| Dashboard sans données | `par_version: {}`, « aucun trafic dans la fenêtre ». |

**Limite connue** : `DocumentTropLong` (413, erreur du client) enregistre une
`Mesure(erreur=True)` ; `Mesure` ne porte pas de code HTTP, donc des documents
trop longs gonflent le taux d'erreur surveillé. Non corrigé (le format `Mesure`
est partagé) ; documenté dans `exploitation.md` §6.

## 10. Tests

### 10.1 Unitaires — `tests/unit/`

| Fichier | Couvre |
|---|---|
| `test_signaux.py` | percentiles (vide, un élément, interpolation), erreurs exclues des latences/scores, `score_moyen` `None` en v1, histogramme, série par minute |
| `test_seuils_pilotage.py` | chargement du fichier du dépôt ; chaque cas d'`ErreurSeuilsPilotage` ; `PILOTAGE_SEUILS_PATH` ; empreinte stable |
| `test_calibrer.py` | moyenne − k·σ sur une distribution connue ; échantillon insuffisant ; erreurs et scores nuls exclus |
| `test_pilotage.py` | `detecter_derive` (critique par signal, marge, insuffisant, score `None`, ordre du motif) ; `evaluer_palier` (attente durée/requêtes, chaque critère en échec, progression 10→50, 50→promouvoir, repli sur seuil absolu) ; `debut_palier` après rollback ; `changements_seuils` |
| `test_resume_metier.py` | un gabarit par événement, nombres au format français |
| `test_anonymisation.py` | chaque motif masqué ; clauses juridiques intactes |

### 10.2 Intégration — `tests/integration/`

| Fichier | Phase | Couvre |
|---|---|---|
| `test_dashboard.py` | A | `resume` complet (palier, alertes, candidats, journal) ; `rendre_texte` ; `rendre_html` (refresh, SVG, versions) ; fenêtre vide |
| `test_capture.py` | A | `TestClient` sur `/v2/analyse` : score bas → candidat ; score haut → rien ; texte déjà capturé → pas de doublon ; `evaluer("v2")` → aucun candidat ; I/O en échec → 200 inchangé |
| `test_enrichir.py` | A | `verser` : `cNN.txt`, ligne d'`attendus`, ligne `verse`, entrée `enrichissement` ; chaque refus ; `evaluer` inclut le nouveau contrat |
| `test_surveiller.py` | B | rollback par score / erreurs / P95 ; alerte de marge unique ; échantillon insuffisant ; mesures d'un palier antérieur ignorées ; `pilotage_refus` sans retour arrière |
| `test_piloter.py` | B | `tours=` : conforme → 10 → 50 → promotion (trois entrées `origine: auto`) ; dérive → rollback sans promotion ; redémarrage → palier relu ; seuils modifiés → `seuils` ; seuils cassés en cours → `seuils_invalides` et boucle vivante |
| `test_capture.py` (ajout) | B | même chose via la gateway `/analyse` quand le canary v2 est tiré |

### 10.3 Critères de fin

- **Phase A** : `test_dashboard_par_version` vert ; tests unitaires et
  d'intégration de la phase A verts ; aucune régression sur `main` ; `ruff` propre.
- **Phase B** : `test_journal_derive_et_rollback_automatique` vert ; les 10 tests
  d'acceptance verts ; `docker compose up` lance `pilote` ; les trois scénarios
  de démo (§11) exécutés et consignés dans `exploitation.md` §7.

## 11. Démo live (C2.10) — `docs/exploitation.md` §7

1. **Rollback** : canary v2 à 10 %, `make traffic MODE=derive-score` (proxy
   `DRIFT=score`) → dérive détectée, rollback `auto`, visible au dashboard et au
   journal avec son résumé métier.
2. **Promotion** : `DRIFT=off`, `make traffic MODE=normal` → 10 → 50 → 100 %
   en quelques minutes (fenêtres de démo), trois entrées au journal.
3. **Enrichissement** : un contrat ambigu produit un score bas → `eval.enrichir
   lister` → `verser` → `make eval` le rejoue.

## 12. Risques

| Risque | Parade |
|---|---|
| Conflits avec SP3 en cours sur `ops/deploy.py`, `docker-compose.yml`, `exploitation.md` | Phase A ne touche pas `ops/deploy.py` ; les autres fichiers reçoivent des ajouts dans des zones distinctes (§6–7 d'`exploitation.md`, nouveau service). Phase B part de `main` après fusion de SP3. |
| Oscillation promotion/rollback sur un petit échantillon | `minimum`, `requetes_min`, `duree_min_s` ; mesures limitées au palier courant ; rollback à un niveau (SP3). |
| Fenêtres de démo trop courtes en production | Valeurs de production dans le YAML (commentaires) et le `motif` ; ajustement = commit + entrée `seuils`. |
| Données client dans les candidats | Anonymisation, fichier hors git, versement conditionné à la relecture juriste. |
| `metrics.jsonl` qui grossit, relu à chaque tour | Lecture linéaire acceptable au volume du brief ; rotation hors périmètre, signalée dans `exploitation.md`. |
| Calibration sur la production plutôt que le golden (D7) | Écart assumé et documenté ; la note du gate reste la référence golden. |
