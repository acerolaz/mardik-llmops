# Corrections de la revue de code — sous-projet 4 (observabilité)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corriger les sept points de robustesse relevés par la revue de code sur `feature/observabilite` : écritures concurrentes qui peuvent se corrompre, compteur de palier faux à l'écran, versement qui laisse un fichier orphelin bloquant, pilote qui peut sortir sur une erreur d'I/O, seuils non validés, dashboard qui tombe en 500, canary bloqué sans trace.

**Architecture:** Aucune nouvelle unité. Six fichiers existants sont durcis là où ils touchent le système de fichiers ou l'horloge : `app/capture.py` (verrou), `eval/enrichir.py` (ordre d'écriture), `ops/dashboard.py` (fenêtre du palier, tolérance aux I/O), `ops/deploy.py` (boucle du pilote), `ops/seuils.py` (validation). Les décisions pures (`ops/pilotage.py`, `ops/signaux.py`) ne changent pas, sauf un appel de journalisation ajouté dans `ops/deploy.py::tour`.

**Tech Stack:** Python 3.11, FastAPI, PyYAML, structlog, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-09-22-observabilite-design.md` (sous-projet 4). Ce plan corrige l'implémentation livrée par `docs/superpowers/plans/2026-09-22-observabilite.md`, il ne change pas la spec.

## Global Constraints

- Répertoire de travail : la racine du dépôt (le dossier qui contient `pyproject.toml`). Toutes les commandes s'y lancent.
- Branche `feature/observabilite` (déjà en place, 22 commits). Ne pas créer ni changer de branche, ne pas pousser, ne pas ouvrir de PR : le contrôleur s'en charge.
- **Ne jamais modifier** : `ops/registry/__init__.py`, `app/llm_client.py`, `app/telemetry.py`, `app/api_v1.py`, `app/pipeline/`, `app/routage.py`, `eval/run_eval.py`, `eval/seuils.yaml`, `tests/acceptance/`, `.github/workflows/*.yml`.
- Les 10 tests d'acceptance restent verts, ainsi que les tests du sous-projet 3 (`tests/unit/test_transitions.py`, `tests/integration/test_cli_deploy.py`, `tests/integration/test_gateway.py`). État de départ : `MOCK=on uv run pytest -q` → 318 verts.
- Tests : `MOCK=on uv run pytest …`. `tests/conftest.py` n'est pas modifié par ce plan.
- Exceptions : jamais `except Exception` ; types précis (`OSError`, `FileNotFoundError`, `json.JSONDecodeError`, `ErreurRegistre`, `ErreurSeuilsPilotage`, `ErreurEnrichissement`, `ErreurDeploiement`).
- La réponse client n'est jamais affectée par la capture (erreurs avalées, journalisées `capture.echec`).
- `uv run ruff check .` propre après chaque tâche (longueur de ligne 100).
- Messages, identifiants et commits en français. La ligne `Co-Authored-By` est celle que le harness donne au modèle qui écrit le commit.

---

## File Structure

| Fichier | Rôle dans ce plan | Tâches |
|---|---|---|
| `app/capture.py` | `_verrouille` vide le tampon avant de relâcher le verrou | 1 |
| `tests/unit/test_verrou_capture.py` | Prouve que le fichier est complet au moment du déverrouillage | 1 |
| `ops/dashboard.py` | `_palier` compte sur tout le palier ; `resume` tolère les I/O en échec | 2, 6 |
| `tests/integration/test_dashboard.py` | Palier hors fenêtre ; sources illisibles | 2, 6 |
| `eval/enrichir.py` | `verser` écrit le contrat en dernier, par fichier temporaire | 3 |
| `tests/integration/test_enrichir.py` | Échec d'`attendus.jsonl` → aucun orphelin, versement suivant possible | 3 |
| `ops/deploy.py` | Journalisation des seuils invalides protégée ; palier sans début journalisé | 4, 7 |
| `tests/integration/test_piloter.py` | Le pilote survit à un journal illisible ; refus tracé | 4, 7 |
| `ops/seuils.py` | `fenetre_s`, `minimum`, `intervalle_s` strictement positifs | 5 |
| `tests/unit/test_seuils_pilotage.py` | Chaque valeur nulle ou négative refusée | 5 |

**Hors périmètre, avec la raison :**

- Tolérance de `ops/registry/__init__.py::journal()` aux lignes corrompues : fichier de la liste « ne jamais modifier ». La tâche 4 en limite l'effet (le pilote survit), mais le correctif de fond appartient à un sous-projet qui a le droit de toucher le registre.
- Mise en cache des seuils de pilotage lus à chaque analyse v2 : optimisation, sans effet de correction ; à mesurer avant d'ajouter un cache et son invalidation.
- Sur-masquage de la regex SIRET (montants écrits avec des espaces) : le sur-masquage est le sens sûr pour des données personnelles, et la relecture juriste précède le versement. Le resserrer risque de laisser passer de vrais SIRET.

---

### Task 1: Le verrou de capture ne protège que ce qui est déjà écrit

**Files:**
- Modify: `app/capture.py` (fonction `_verrouille`, lignes 88-96)
- Test: `tests/unit/test_verrou_capture.py` (créer)

**Interfaces:**
- Consumes: `app.capture._verrouille(chemin) -> Iterator[IO[str]]` (existant).
- Produces: même signature ; garantie nouvelle : à la sortie du bloc `with`, tout ce que l'appelant a écrit est sur le disque **avant** que le verrou ne soit relâché. `eval/enrichir.py::_ajouter_ligne_verrouillee` en hérite sans changement.

Le tampon texte de Python fait 8 Kio. `capturer` écrit le contrat anonymisé en une ligne, et le jeu d'évaluation contient des contrats plus longs que cela. Aujourd'hui `flock(LOCK_UN)` est exécuté dans le `finally` interne, alors que le vidage du tampon n'a lieu qu'à la fermeture du fichier, donc après. Deux écritures concurrentes peuvent donc entrelacer leurs fins de ligne : deux lignes JSON corrompues, silencieusement ignorées à la lecture, deux candidats perdus sans trace.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_verrou_capture.py` :

```python
"""Le verrou d'écriture ne doit être relâché qu'une fois la ligne sur le disque."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path

from app import capture


def test_le_fichier_est_complet_avant_le_deverrouillage(tmp_path: Path, monkeypatch):
    chemin = tmp_path / "candidats.jsonl"
    tailles: list[int] = []
    vrai_flock = fcntl.flock

    def espion(fichier, operation):
        if operation == fcntl.LOCK_UN:
            tailles.append(chemin.stat().st_size)
        return vrai_flock(fichier, operation)

    monkeypatch.setattr(capture.fcntl, "flock", espion)
    # Plus long que le tampon texte de 8 Kio : sans vidage explicite, le disque
    # ne porte qu'un multiple de 8 Kio au moment du déverrouillage.
    ligne = json.dumps({"texte": "x" * 20000}, ensure_ascii=False) + "\n"
    with capture._verrouille(chemin) as fichier:
        fichier.write(ligne)
    assert tailles == [len(ligne.encode("utf-8"))]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_verrou_capture.py -v`
Expected: FAIL — `assert [16384] == [20014]` (ou une autre taille tronquée à un multiple de 8192).

- [ ] **Step 3: Write minimal implementation**

Dans `app/capture.py`, remplacer le corps de `_verrouille` par :

```python
@contextmanager
def _verrouille(chemin: Path) -> Iterator[IO[str]]:
    """Ouvre ``chemin`` en ajout, sous verrou exclusif.

    Le tampon est vidé **avant** le déverrouillage : sinon la fin d'une ligne
    longue partirait hors verrou et pourrait s'entrelacer avec l'écriture d'un
    autre processus (l'app et le versement humain écrivent le même fichier).
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("a+", encoding="utf-8") as fichier:
        fcntl.flock(fichier, fcntl.LOCK_EX)
        try:
            yield fichier
        finally:
            fichier.flush()
            os.fsync(fichier.fileno())
            fcntl.flock(fichier, fcntl.LOCK_UN)
```

(`os` et `fcntl` sont déjà importés dans ce module ; `contextmanager` aussi.)

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_verrou_capture.py tests/integration/test_capture.py tests/integration/test_enrichir.py -v && uv run ruff check app/capture.py tests/unit/test_verrou_capture.py`
Expected: PASS (1 + 8 + 8 tests) ; ruff : `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add app/capture.py tests/unit/test_verrou_capture.py
git commit -m "fix(capture): vider le tampon avant de relâcher le verrou d'écriture"
```

---

### Task 2: Le compteur de palier du tableau de bord est borné à la fenêtre

**Files:**
- Modify: `ops/dashboard.py` (fonction `_palier` et son appel dans `resume`)
- Test: `tests/integration/test_dashboard.py` (ajout)

**Interfaces:**
- Consumes: `app.telemetry.MetricsStore.lire(depuis_s=…)` ; `ops.signaux.filtrer` ; `ops.pilotage.debut_palier`.
- Produces: `_palier(index, journal, metriques, seuils, maintenant) -> dict | None` — la signature change : `mesures: list[Mesure]` devient `metriques: MetricsStore`. Les clés du dict rendu (`version`, `pourcentage`, `depuis_s`, `requetes`, `duree_min_s`, `requetes_min`) ne changent pas.

`resume` lit les mesures de la fenêtre de surveillance (300 s par défaut) et `_palier` compte dedans, alors que `ops/deploy.py::tour` compte sur tout le palier (`metriques.lire(depuis_s=depuis_s + 1)`). Avec les valeurs de production (`duree_min_s: 1800`, `fenetre_s: 300`), l'écran affiche « 80/500 requêtes » pour un palier que le pilote s'apprête à promouvoir sur 500 : l'invariant « ce qu'on voit à l'écran est exactement ce qui a déclenché l'action » est faux là où il compte le plus.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/integration/test_dashboard.py` (le fichier importe déjà `time` et `resume`) :

```python
def test_palier_compte_toutes_les_requetes_du_palier(metriques, registry, tmp_path, monkeypatch):
    """Le palier peut être plus long que la fenêtre : le compteur suit le palier."""
    vrai_time = time.time
    depart = vrai_time() - 600
    monkeypatch.setattr(time, "time", lambda: depart)   # le canary est daté d'il y a 600 s
    _canary(registry)
    monkeypatch.setattr(time, "time", vrai_time)

    _mesures(metriques, "v2.0.0", 5, score=0.9, ts=depart + 10)   # dans le palier, hors fenêtre
    _mesures(metriques, "v2.0.0", 3, score=0.9)                   # dans la fenêtre

    r = resume(metriques, registry=registry, fenetre_s=300, candidats=tmp_path / "absent.jsonl")
    assert r["total"] == 3                      # la fenêtre ne montre que les 3 récentes
    assert r["palier"]["requetes"] == 8         # le palier en compte 8, comme le pilote
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_dashboard.py::test_palier_compte_toutes_les_requetes_du_palier -v`
Expected: FAIL — `assert 3 == 8`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/dashboard.py`, remplacer `_palier` par :

```python
def _palier(
    index: dict[str, Any],
    journal: list[dict[str, Any]],
    metriques: MetricsStore,
    seuils: SeuilsPilotage | None,
    maintenant: float,
) -> dict[str, Any] | None:
    """Le palier canary en cours, compté sur **tout le palier** et non sur la
    fenêtre de surveillance : ``ops.deploy.tour`` décide sur ce même compte."""
    canary = index.get("canary")
    if canary is None:
        return None
    debut = debut_palier(journal, canary)
    if debut is None:
        requetes = 0
    else:
        du_palier = metriques.lire(depuis_s=max(maintenant - debut, 0) + 1)
        requetes = len(filtrer(du_palier, canary, depuis_ts=debut))
    return {
        "version": canary,
        "pourcentage": index.get("canary_percent"),
        "depuis_s": round(maintenant - debut, 1) if debut is not None else None,
        "requetes": requetes,
        "duree_min_s": seuils.promotion.duree_min_s if seuils else None,
        "requetes_min": seuils.promotion.requetes_min if seuils else None,
    }
```

et, dans `resume`, remplacer l'appel `_palier(index, journal, mesures, seuils, maintenant)` par :

```python
        "palier": _palier(index, journal, metriques, seuils, maintenant),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_dashboard.py tests/acceptance/test_observabilite.py -v && uv run ruff check ops/dashboard.py tests/integration/test_dashboard.py`
Expected: PASS ; les 5 tests d'acceptance de `test_observabilite.py` restent verts ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/dashboard.py tests/integration/test_dashboard.py
git commit -m "fix(dashboard): compter les requêtes du palier sur le palier, pas sur la fenêtre"
```

---

### Task 3: Un versement interrompu bloque tous les suivants

**Files:**
- Modify: `eval/enrichir.py` (fonction `verser`, écriture du contrat)
- Test: `tests/integration/test_enrichir.py` (ajout)

**Interfaces:**
- Consumes: `_ajouter_ligne`, `_ajouter_ligne_verrouillee` (existants).
- Produces: `verser(...)` — même signature, même dict rendu. Nouvelle garantie : si l'ajout à `eval/attendus.jsonl` échoue, aucun `eval/contrats/cNN.txt` ne subsiste.

Aujourd'hui le fichier du contrat est écrit en premier. Si l'ajout à `attendus.jsonl` échoue (disque plein, `eval/` en lecture seule, interruption), `cNN.txt` reste sur place alors qu'`attendus.jsonl` est inchangé : l'appel suivant recalcule le même `cNN` et se heurte pour toujours à la garde « le contrat … existe déjà », pour **tous** les candidats, jusqu'à suppression manuelle.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/integration/test_enrichir.py` :

```python
def test_echec_d_attendus_ne_laisse_pas_de_contrat_orphelin(
    candidats, contrats_courts, attendus, registry, monkeypatch
):
    """Un versement interrompu ne doit pas bloquer les versements suivants."""
    from eval import enrichir

    def _echec(chemin, data):
        raise OSError("disque plein")

    monkeypatch.setattr(enrichir, "_ajouter_ligne", _echec)
    with pytest.raises(OSError):
        _verser(candidats, contrats_courts, attendus, registry)
    assert not (contrats_courts / "c05.txt").exists()
    assert not list(contrats_courts.glob("*.tmp"))

    monkeypatch.undo()
    ligne = _verser(candidats, contrats_courts, attendus, registry)
    assert ligne["contrat_id"] == "c05"
    assert (contrats_courts / "c05.txt").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_enrichir.py::test_echec_d_attendus_ne_laisse_pas_de_contrat_orphelin -v`
Expected: FAIL — `assert not True` sur `(contrats_courts / "c05.txt").exists()` (l'orphelin est là).

- [ ] **Step 3: Write minimal implementation**

Dans `eval/enrichir.py`, remplacer les deux lignes :

```python
    fichier.write_text(texte, encoding="utf-8")
    _ajouter_ligne(Path(attendus), ligne)
```

par :

```python
    # Le contrat est publié en dernier, par renommage atomique : si l'ajout à
    # ``attendus.jsonl`` échoue, aucun ``cNN.txt`` orphelin ne reste, sinon la
    # garde « le contrat existe déjà » bloquerait tous les versements suivants.
    provisoire = fichier.with_name(f"{contrat_id}.txt.tmp")
    provisoire.write_text(texte, encoding="utf-8")
    try:
        _ajouter_ligne(Path(attendus), ligne)
    except OSError:
        provisoire.unlink(missing_ok=True)
        raise
    os.replace(provisoire, fichier)
```

(`os` est déjà importé dans ce module.)

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_enrichir.py -v && uv run ruff check eval/enrichir.py tests/integration/test_enrichir.py`
Expected: PASS (9 tests) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add eval/enrichir.py tests/integration/test_enrichir.py
git commit -m "fix(enrichir): publier le contrat par renommage atomique, après attendus.jsonl"
```

---

### Task 4: Le pilote sort encore sur une erreur d'I/O au rechargement des seuils

**Files:**
- Modify: `ops/deploy.py` (fonction `piloter`, branche `except ErreurSeuilsPilotage`)
- Test: `tests/integration/test_piloter.py` (ajout)

**Interfaces:**
- Consumes: `_journaliser_une_fois`, `resume_metier` (existants).
- Produces: `_journaliser_incident_seuils(registry, seuils, exc) -> None` ; `piloter(...)` inchangée pour l'appelant. Garantie : seul un seuil invalide **au démarrage** fait sortir le pilote.

Le commentaire de `piloter` dit déjà l'intention : « seul un seuil invalide AU DÉMARRAGE doit l'arrêter ». Mais l'appel à `_journaliser_une_fois` du bloc `except ErreurSeuilsPilotage` est hors du `try` qui protège `tour` : il lit et écrit le journal, et un `OSError` à cet instant — précisément l'aléa transitoire que ce `try` existe pour absorber — remonte hors de `piloter`, `main` renvoie 1, le conteneur sort. Avec `restart: unless-stopped`, cela devient une boucle de redémarrage au lieu de la tolérance documentée.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/integration/test_piloter.py` :

```python
def test_journal_illisible_pendant_un_rechargement_de_seuils(registry, metriques):
    """Seuils invalides + journal momentanément illisible : la boucle continue."""
    appels = {"n": 0}

    def charger():
        appels["n"] += 1
        if appels["n"] > 1:
            raise ErreurSeuilsPilotage("YAML invalide")
        return BASE

    vrai_journal = registry.journal
    pannes = {"n": 0}

    def journal_capricieux():
        pannes["n"] += 1
        if pannes["n"] == 1:
            raise OSError("journal momentanément illisible")
        return vrai_journal()

    registry.journal = journal_capricieux            # type: ignore[method-assign]
    assert piloter(registry, metriques, tours=2, charger=charger, attendre=lambda _: None) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_piloter.py::test_journal_illisible_pendant_un_rechargement_de_seuils -v`
Expected: FAIL — `OSError: journal momentanément illisible` remonte hors de `piloter`.

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py`, ajouter avant `piloter` :

```python
def _journaliser_incident_seuils(
    registry: Registry, seuils: SeuilsPilotage, exc: ErreurSeuilsPilotage
) -> None:
    """Trace des seuils illisibles. Un incident d'écriture du journal ne doit
    pas faire sortir le pilote : seul un seuil invalide au démarrage l'arrête."""
    try:
        _journaliser_une_fois(
            registry, "seuils_invalides", seuils.fenetre_s, {"raison": str(exc)},
            fichier="ops/seuils_pilotage.yaml",
            resume=resume_metier("seuils_invalides",
                                 fichier="ops/seuils_pilotage.yaml", raison=str(exc)),
        )
    except (ErreurRegistre, OSError, json.JSONDecodeError) as incident:
        structlog.get_logger("mardik").warning("pilotage.incident", cause=str(incident))
```

et, dans `piloter`, remplacer le corps de la branche `except ErreurSeuilsPilotage as exc:` (l'appel à `_journaliser_une_fois` et ses arguments) par :

```python
            except ErreurSeuilsPilotage as exc:
                _journaliser_incident_seuils(registry, seuils, exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_piloter.py tests/integration/test_surveiller.py -v && uv run ruff check ops/deploy.py tests/integration/test_piloter.py`
Expected: PASS (7 + 6 tests) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/integration/test_piloter.py
git commit -m "fix(deploy): protéger la journalisation des seuils invalides dans la boucle du pilote"
```

---

### Task 5: Des seuils nuls ou négatifs passent la validation

**Files:**
- Modify: `ops/seuils.py` (fonction `_nombre`, appels de `charger_seuils_pilotage`)
- Test: `tests/unit/test_seuils_pilotage.py` (ajout de cas au paramétrage existant)

**Interfaces:**
- Consumes: rien de nouveau.
- Produces: `_nombre(section, cle, chemin, prefixe="", *, strictement_positif=False) -> float` ; `charger_seuils_pilotage` refuse désormais `fenetre_s`, `minimum` ou `intervalle_s` nuls ou négatifs, avec un message qui nomme la clé.

`_nombre` accepte n'importe quel nombre. `intervalle_s: 0` — une coquille plausible en réglant la démo, ce que la §6 d'`exploitation.md` invite justement à faire — fait tourner le pilote sans aucune pause, à relire les métriques, le journal et les deux YAML en continu. `fenetre_s: 0` fait tomber `MetricsStore.lire(depuis_s=0)` dans la branche « aucune limite » et relit toutes les métriques jamais enregistrées au lieu d'aucune.

- [ ] **Step 1: Write the failing test**

Dans `tests/unit/test_seuils_pilotage.py`, ajouter trois cas à la liste de `@pytest.mark.parametrize` de `test_fichier_incoherent_refuse` :

```python
        (lambda d: {**d, "intervalle_s": 0}, "intervalle_s"),
        (lambda d: {**d, "fenetre_s": 0}, "fenetre_s"),
        (lambda d: {**d, "minimum": -1}, "minimum"),
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_seuils_pilotage.py -v`
Expected: FAIL — `DID NOT RAISE <class 'ops.seuils.ErreurSeuilsPilotage'>` sur les trois nouveaux cas.

- [ ] **Step 3: Write minimal implementation**

Dans `ops/seuils.py`, remplacer `_nombre` par :

```python
def _nombre(
    section: dict[str, Any],
    cle: str,
    chemin: Path,
    prefixe: str = "",
    *,
    strictement_positif: bool = False,
) -> float:
    nom = f"{prefixe}{cle}"
    if cle not in section:
        raise ErreurSeuilsPilotage(f"{chemin} : clé « {nom} » manquante")
    valeur = section[cle]
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        raise ErreurSeuilsPilotage(
            f"{chemin} : « {nom} » doit être un nombre, reçu {valeur!r}"
        )
    if strictement_positif and valeur <= 0:
        raise ErreurSeuilsPilotage(
            f"{chemin} : « {nom} » doit être strictement positif, reçu {valeur!r}"
        )
    return float(valeur)
```

et, dans `charger_seuils_pilotage`, les trois premiers champs :

```python
        fenetre_s=_nombre(data, "fenetre_s", chemin, strictement_positif=True),
        minimum=int(_nombre(data, "minimum", chemin, strictement_positif=True)),
        intervalle_s=_nombre(data, "intervalle_s", chemin, strictement_positif=True),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_seuils_pilotage.py tests/unit/test_calibrer.py -v && uv run ruff check ops/seuils.py tests/unit/test_seuils_pilotage.py`
Expected: PASS (14 + 3 tests) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/seuils.py tests/unit/test_seuils_pilotage.py
git commit -m "fix(seuils): refuser une fenêtre, un minimum ou un intervalle nul ou négatif"
```

---

### Task 6: Le tableau de bord tombe en 500 quand une source est illisible

**Files:**
- Modify: `ops/dashboard.py` (fonction `resume`)
- Test: `tests/integration/test_dashboard.py` (ajout)

**Interfaces:**
- Consumes: `MetricsStore.lire`, `Registry.index`, `Registry.journal`, `candidats_en_attente` (existants).
- Produces: `resume(...)` — mêmes clés. Nouvelle garantie : une source illisible produit une alerte et un résumé dégradé, jamais une exception.

`resume` traite déjà les seuils invalides en alerte, mais `metriques.lire`, `registry.index()`, `registry.journal()` et `candidats_en_attente` ne sont pas protégés. Un `candidats.jsonl` tronqué ou un souci de droits sur le montage `./eval` emporte toute la page — y compris les alertes de dérive et le journal de déploiement, c'est-à-dire précisément ce que l'exploitant regarde à ce moment-là. La page se rafraîchit toutes les 5 s : il ne voit qu'un 500 permanent.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/integration/test_dashboard.py` :

```python
def test_sources_illisibles_signalees_sans_500(metriques, registry, tmp_path, monkeypatch):
    """Un fichier de candidats illisible ne doit pas emporter toute la page."""
    from ops import dashboard

    _mesures(metriques, "v1.0.0", 3, score=None)

    def _illisible(chemin=None):
        raise OSError("eval/candidats.jsonl : permission refusée")

    monkeypatch.setattr(dashboard, "candidats_en_attente", _illisible)
    r = resume(metriques, registry=registry)
    assert r["par_version"]["v1.0.0"]["requetes"] == 3        # le reste est produit
    assert r["candidats"] == 0
    assert any("candidats illisibles" in a for a in r["alertes"])
    assert "candidats illisibles" in rendre_texte(r)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_dashboard.py::test_sources_illisibles_signalees_sans_500 -v`
Expected: FAIL — `OSError: eval/candidats.jsonl : permission refusée`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/dashboard.py`, à l'intérieur de `resume`, remplacer les deux lignes de lecture :

```python
    mesures = metriques.lire(depuis_s=fenetre)
    index, journal = registry.index(), registry.journal()
```

par :

```python
    try:
        mesures = metriques.lire(depuis_s=fenetre)
    except (OSError, json.JSONDecodeError) as exc:
        alertes.append(f"métriques illisibles : {exc}")
        mesures = []
    try:
        index, journal = registry.index(), registry.journal()
    except (ErreurRegistre, OSError, json.JSONDecodeError) as exc:
        alertes.append(f"registre illisible : {exc}")
        index, journal = {}, []
```

et remplacer la valeur de la clé `candidats` du dict rendu par une variable calculée juste avant le `return` :

```python
    try:
        candidats_en_attente_de_versement = len(candidats_en_attente(candidats))
    except (OSError, json.JSONDecodeError) as exc:
        alertes.append(f"candidats illisibles : {exc}")
        candidats_en_attente_de_versement = 0
```

```python
        "candidats": candidats_en_attente_de_versement,
```

Compléter les imports du module : `from ops.registry import ErreurRegistre, Registry` (`json` est déjà importé).

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_dashboard.py tests/acceptance/test_observabilite.py -v && uv run ruff check ops/dashboard.py tests/integration/test_dashboard.py`
Expected: PASS ; acceptance toujours vert ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/dashboard.py tests/integration/test_dashboard.py
git commit -m "fix(dashboard): dégrader en alerte quand métriques, registre ou candidats sont illisibles"
```

---

### Task 7: Un canary sans entrée au journal reste bloqué sans trace

**Files:**
- Modify: `ops/deploy.py` (fonction `tour`, branche `canary is None or debut is None`)
- Test: `tests/integration/test_piloter.py` (ajout)

**Interfaces:**
- Consumes: `_journaliser_une_fois`, `resume_metier` (existants ; gabarit `pilotage_refus`).
- Produits: `tour(...)` — même dict rendu. Nouveau : un canary présent à l'index mais sans début de palier au journal produit une entrée `pilotage_refus` (une seule par fenêtre, action `palier`).

`debut_palier` rend `None` quand aucune entrée `canary` non périmée ne nomme la version, et `tour` s'arrête alors sans rien journaliser. Comme `ops/registry/journal.jsonl` est ignoré par git, une production restaurée avec un `index.json` portant un canary mais un journal neuf ou perdu laisse ce canary en place pour toujours : pas de promotion, pas de refus, et rien à l'écran pour expliquer l'immobilité.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/integration/test_piloter.py` :

```python
def test_canary_sans_debut_de_palier_journalise_un_refus(registry, metriques):
    """Index avec canary mais journal sans entrée « canary » : le blocage est tracé."""
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    registry.definir_canary("v2.0.0", 10)          # aucune entrée « canary » au journal

    piloter(registry, metriques, tours=2, attendre=lambda _: None)
    refus = _evenements(registry, "pilotage_refus")
    assert len(refus) == 1                          # une seule fois par fenêtre
    assert refus[0]["action"] == "palier" and refus[0]["origine"] == "auto"
    assert "palier" in refus[0]["resume"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_piloter.py::test_canary_sans_debut_de_palier_journalise_un_refus -v`
Expected: FAIL — `assert 0 == 1` (aucun `pilotage_refus` journalisé).

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py`, fonction `tour`, remplacer :

```python
    if canary is None or debut is None:
        return {"surveillance": surveillance, "palier": None}
```

par :

```python
    if canary is not None and debut is None:
        # Canary à l'index, mais aucun début de palier au journal (journal perdu
        # ou tronqué) : sans trace, la version resterait figée sans explication.
        raison = "aucun début de palier au journal (journal perdu ou tronqué)"
        _journaliser_une_fois(
            registry, "pilotage_refus", seuils.fenetre_s,
            {"version": canary, "action": "palier"},
            raison=raison,
            resume=resume_metier("pilotage_refus", action="palier", raison=raison),
        )
    if canary is None or debut is None:
        return {"surveillance": surveillance, "palier": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_piloter.py tests/integration/test_surveiller.py -v && uv run ruff check ops/deploy.py tests/integration/test_piloter.py`
Expected: PASS (8 + 6 tests) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/integration/test_piloter.py
git commit -m "feat(deploy): journaliser un refus quand le palier canary n'a pas de début au journal"
```

---

### Task 8: Vérification d'ensemble et note d'exploitation

**Files:**
- Modify: `docs/exploitation.md` (§6, sous « Faux positifs connus »)

**Interfaces:**
- Consumes: les tâches 1 à 7.
- Produces: la §6 mentionne les garde-fous ajoutés ; la branche est prête pour la revue de PR.

- [ ] **Step 1: Documenter les garde-fous**

Dans `docs/exploitation.md` §6, ajouter après le paragraphe « **Faux positifs connus.** » :

```markdown
**Garde-fous du pilote.** Un seuil invalide **au démarrage** arrête le pilote
(code 1, `SEUILS INVALIDES`) ; en cours de route, il garde les derniers seuils
valides, journalise `seuils_invalides` et continue. Une erreur d'entrée-sortie
transitoire (registre ou métriques momentanément illisibles) est journalisée
(`pilotage.incident`) et la boucle se poursuit. `fenetre_s`, `minimum` et
`intervalle_s` doivent être strictement positifs : un `intervalle_s: 0` ferait
tourner le pilote sans pause, il est refusé au chargement. Si le journal a été
perdu alors qu'un canary est encore à l'index, le pilote journalise un
`pilotage_refus` (action `palier`) plutôt que de rester figé sans explication :
relancer `python -m ops.deploy canary vX.Y.Z --pourcentage N` recrée le palier.
```

- [ ] **Step 2: Vérification complète**

Run: `MOCK=on uv run pytest -q && MOCK=on uv run pytest -v tests/acceptance && uv run ruff check . && git status --short`
Expected: toute la suite verte (au moins 323 tests : 318 au départ + 5 ajoutés) ; les 10 tests d'acceptance verts ; ruff propre ; aucun fichier de données (`ops/metrics.jsonl`, `eval/candidats.jsonl`, `eval/contrats/*.tmp`) dans le dépôt.

- [ ] **Step 3: Commit**

```bash
git add docs/exploitation.md
git commit -m "docs(exploitation): garde-fous du pilote (seuils, incidents d'I/O, palier sans journal)"
```
