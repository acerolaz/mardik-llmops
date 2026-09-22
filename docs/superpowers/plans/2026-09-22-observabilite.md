# Observabilité & boucles de rétroaction — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Les métriques de production pilotent la chaîne : un tableau de bord par version, une surveillance qui déclenche le rollback automatique, une promotion canary 10 → 50 → 100 % pilotée par des critères, la capture des cas à faible confiance vers le jeu d'évaluation, et un journal de pilotage lisible par le métier.

**Architecture:** Décider ≠ exécuter. `ops/signaux.py` agrège des `Mesure` (fonctions pures) ; `ops/seuils.py` charge `ops/seuils_pilotage.yaml` (config as code) ; `ops/pilotage.py` rend des décisions pures (dérive, palier, changements de seuils, résumés métier). `ops/dashboard.py` affiche avec les mêmes calculs. En partie 2, `ops/deploy.py::surveiller` et `piloter` exécutent ces décisions via les transitions du sous-projet 3 (fusionné sur `main`) (`rollback`, `deployer_canary`, `promouvoir`). La capture vit dans `app/capture.py` (tâche de fond des routes) ; le versement dans `eval/enrichir.py` (CLI humaine).

**Tech Stack:** Python 3.11, FastAPI (`BackgroundTasks`), PyYAML, structlog, pytest, ruff, uv, docker compose.

**Spec:** `docs/superpowers/specs/2026-09-22-observabilite-design.md`

## Global Constraints

- Répertoire de travail : racine du dépôt git (le dossier qui contient `pyproject.toml`). Toutes les commandes se lancent depuis cette racine.
- Une seule branche `feature/observabilite`, créée depuis `main` @ `8833345` ou plus récent (sous-projets 1 à 3 fusionnés). **Partie 1** (tâches 1–12) : décisions, tableau de bord, capture. **Partie 2** (tâches 13–17) : exécution des boucles. Une seule PR à la fin.
- État de départ vérifié : `MOCK=on uv run pytest tests/acceptance` → 8 verts, 2 rouges (`test_dashboard_par_version`, `test_journal_derive_et_rollback_automatique`).
- Tests : `MOCK=on uv run pytest …` (le `tests/conftest.py` force `MOCK=on`, `METRICS_PATH`, `REGISTRY_PATH` dans `tmp_path` ; la tâche 8 y ajoute `CANDIDATS_PATH`).
- **Ne jamais modifier** : `ops/registry/__init__.py`, `app/llm_client.py`, `app/telemetry.py`, `app/api_v1.py`, `app/pipeline/`, `app/routage.py`, `eval/run_eval.py`, `eval/seuils.yaml`, `tests/acceptance/`, `.github/workflows/*.yml` (sauf le commentaire d'en-tête de `promotion.yml`, tâche 17). Dans `ops/deploy.py` et `app/gateway.py` (code du sous-projet 3), seules les modifications décrites aux tâches 13–16. `tests/conftest.py` : **uniquement** la ligne `CANDIDATS_PATH` de la tâche 8.
- Signatures imposées par les tests d'acceptance : `resume(metriques=None, *, fenetre_s=300, registry=None, …) -> dict` ; `rendre_texte(r) -> str` ; `surveiller(registry=None, metriques=None, *, fenetre_s=…, score_min=…, taux_erreur_max=…, latence_p95_max_ms=…, minimum=…) -> dict` avec les clés `version`, `mesures`, `derive`, `motif`, `rollback`.
- Vocabulaire `origine` (fixé par SP3) : `manuel` | `ci:<acteur>` | `auto`. Le pilotage écrit `auto` ; le versement humain `manuel`.
- Valeurs initiales des seuils (spec §4) : `fenetre_s: 300`, `minimum: 10`, `intervalle_s: 5`, `derive.score_min: 0.70`, `derive.marge: 0.05`, `derive.taux_erreur_max: 0.10`, `derive.latence_p95_max_ms: 8000`, `promotion.paliers: [10, 50, 100]`, `promotion.duree_min_s: 60`, `promotion.requetes_min: 20`, `promotion.ecart_erreur_max: 0.02`, `promotion.latence_p95_max_ms: 8000`, `promotion.score_moyen_min: 0.75`, `promotion.score_p10_min: 0.65`, `capture.score_max: 0.70`.
- `ops/seuils.py` n'importe **ni** `app.api_*` **ni** `eval` (import circulaire `app.capture` → `ops.seuils` → `eval.run_eval` → `app.api_v2` → `app.capture`). `app/capture.py` n'importe pas `app.api_v2`.
- Exceptions : jamais `except Exception` ; types précis (`OSError`, `FileNotFoundError`, `yaml.YAMLError`, `json.JSONDecodeError`, `ErreurSeuilsPilotage`, `ErreurEnrichissement`, `ErreurDeploiement`).
- La réponse client n'est **jamais** affectée par la capture (erreurs avalées et journalisées `capture.echec`).
- `uv run ruff check .` propre après chaque tâche (longueur de ligne 100).
- Messages, identifiants et commits en français, comme le reste du dépôt. Chaque commit se termine par `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

---

## File Structure

| Fichier | Rôle | Tâches |
|---|---|---|
| `ops/seuils_pilotage.yaml` | Seuils de pilotage versionnés, avec `motif` | 1 |
| `ops/seuils.py` | `SeuilsPilotage`, chargement, `empreinte`, `lire_brut`, `calibrer`, CLI | 1, 3 |
| `ops/signaux.py` | `percentile`, `Agregat`, `agreger`, `scores`, `histogramme`, `serie_par_minute`, `filtrer` | 2 |
| `ops/pilotage.py` | `Constat`, `Derive`, `DecisionPalier`, `version_surveillee`, `detecter_derive`, `debut_palier`, `evaluer_palier`, `changements_seuils`, `resume_metier` | 4–6 |
| `app/capture.py` | `anonymiser`, `Capture`, `get_capture`, `capturer`, `lire_candidats`, `candidats_en_attente` | 7–8 |
| `app/api_v2.py` | Hook de capture sur `POST /v2/analyse` | 8 |
| `eval/enrichir.py` | `verser`, `prochain_id`, CLI `lister` / `verser` | 9 |
| `ops/dashboard.py` | `resume`, `rendre_texte`, `rendre_html` | 10–11 |
| `docs/exploitation.md` | §6 Surveillance et seuils ; §7 Preuve d'exécution | 12, 17 |
| `Makefile`, `.gitignore`, `tests/conftest.py` | Cibles, fichier de candidats ignoré, `CANDIDATS_PATH` | 8, 12, 17 |
| `ops/deploy.py` | `**details` sur les transitions ; `surveiller`, `tour`, `piloter`, CLI | 13–15 |
| `app/gateway.py` | Hook de capture sur `POST /analyse` | 16 |
| `docker-compose.yml` | Service `pilote` | 17 |
| `README.md`, `.github/workflows/promotion.yml` | Compteurs de tests, CLI `piloter` ; commentaire d'en-tête (la promotion est désormais pilotée) | 17 |
| `tests/unit/test_seuils_pilotage.py`, `test_signaux.py`, `test_calibrer.py`, `test_pilotage.py`, `test_resume_metier.py`, `test_anonymisation.py` | Unitaires | 1–7 |
| `tests/integration/test_capture.py`, `test_enrichir.py`, `test_dashboard.py` | Intégration partie 1 | 8–11 |
| `tests/integration/test_details_journal.py`, `test_surveiller.py`, `test_piloter.py` | Intégration partie 2 | 13–15 |

---

# Partie 1 — décisions, tableau de bord, capture

Préalable : `git switch main && git pull && git switch -c feature/observabilite`, puis `MOCK=on uv run pytest -q tests/acceptance` → 8 verts, 2 rouges.

### Task 1: Seuils de pilotage — `ops/seuils_pilotage.yaml` et chargement

**Files:**
- Create: `ops/seuils_pilotage.yaml`
- Create: `ops/seuils.py`
- Test: `tests/unit/test_seuils_pilotage.py`

**Interfaces:**
- Consumes: rien.
- Produces: `class ErreurSeuilsPilotage(ValueError)` ; dataclasses gelées `SeuilsDerive(score_min, marge, taux_erreur_max, latence_p95_max_ms)`, `SeuilsPromotion(paliers: tuple[int, ...], duree_min_s, requetes_min: int, ecart_erreur_max, latence_p95_max_ms, score_moyen_min, score_p10_min)`, `SeuilsCapture(score_max)`, `SeuilsPilotage(fenetre_s, minimum: int, intervalle_s, derive, promotion, capture, motif)` avec `to_dict()` ; `chemin_seuils_pilotage() -> Path` (lit `PILOTAGE_SEUILS_PATH` à l'appel) ; `chemin_seuils_gate() -> Path` (lit `SEUILS_PATH`, défaut `eval/seuils.yaml`) ; `charger_seuils_pilotage(chemin=None) -> SeuilsPilotage` ; `lire_brut(chemin) -> dict` ; `empreinte(chemin) -> str` (12 car.).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_seuils_pilotage.py` :

```python
"""Seuils de pilotage : chargement strict, variable d'environnement, empreinte."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ops.seuils import (
    CHEMIN_SEUILS_PILOTAGE_DEFAUT,
    ErreurSeuilsPilotage,
    charger_seuils_pilotage,
    empreinte,
    lire_brut,
)


def _base() -> dict:
    return yaml.safe_load(CHEMIN_SEUILS_PILOTAGE_DEFAUT.read_text(encoding="utf-8"))


def _ecrire(tmp_path: Path, data: dict) -> Path:
    chemin = tmp_path / "seuils_pilotage.yaml"
    chemin.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return chemin


def test_fichier_du_depot_charge():
    s = charger_seuils_pilotage()
    assert s.fenetre_s == 300 and s.minimum == 10 and s.intervalle_s == 5
    assert s.derive.score_min == 0.70 and s.derive.marge == 0.05
    assert s.derive.taux_erreur_max == 0.10 and s.derive.latence_p95_max_ms == 8000
    assert s.promotion.paliers == (10, 50, 100)
    assert s.promotion.duree_min_s == 60 and s.promotion.requetes_min == 20
    assert s.promotion.score_moyen_min == 0.75 and s.promotion.score_p10_min == 0.65
    assert s.capture.score_max == 0.70
    assert s.motif
    assert s.to_dict()["promotion"]["paliers"] == [10, 50, 100]


def test_variable_environnement(tmp_path, monkeypatch):
    data = _base()
    data["fenetre_s"] = 60
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(_ecrire(tmp_path, data)))
    assert charger_seuils_pilotage().fenetre_s == 60


def _sans(data: dict, section: str, cle: str) -> dict:
    del data[section][cle]
    return data


@pytest.mark.parametrize(
    "modifier, attendu",
    [
        (lambda d: _sans(d, "derive", "score_min"), "derive.score_min"),
        (lambda d: {**d, "minimum": "dix"}, "minimum"),
        (lambda d: {**d, "derive": {**d["derive"], "marge": True}}, "derive.marge"),
        (lambda d: {**d, "promotion": {**d["promotion"], "paliers": [50, 10, 100]}}, "paliers"),
        (lambda d: {**d, "promotion": {**d["promotion"], "paliers": [10, 50]}}, "paliers"),
        (lambda d: {k: v for k, v in d.items() if k != "capture"}, "capture"),
        (lambda d: {**d, "motif": "  "}, "motif"),
    ],
)
def test_fichier_incoherent_refuse(tmp_path, modifier, attendu):
    chemin = _ecrire(tmp_path, modifier(_base()))
    with pytest.raises(ErreurSeuilsPilotage, match=attendu):
        charger_seuils_pilotage(chemin)


def test_fichier_absent_ou_yaml_invalide(tmp_path):
    with pytest.raises(ErreurSeuilsPilotage, match="introuvable"):
        charger_seuils_pilotage(tmp_path / "absent.yaml")
    casse = tmp_path / "casse.yaml"
    casse.write_text("derive: [", encoding="utf-8")
    with pytest.raises(ErreurSeuilsPilotage, match="YAML invalide"):
        charger_seuils_pilotage(casse)
    with pytest.raises(ErreurSeuilsPilotage, match="introuvable"):
        lire_brut(tmp_path / "absent.yaml")


def test_empreinte_stable_et_sensible(tmp_path):
    chemin = _ecrire(tmp_path, _base())
    e1 = empreinte(chemin)
    assert len(e1) == 12 and e1 == empreinte(chemin)
    data = _base()
    data["derive"]["score_min"] = 0.68
    _ecrire(tmp_path, data)
    assert empreinte(chemin) != e1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_seuils_pilotage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops.seuils'`

- [ ] **Step 3: Write minimal implementation**

`ops/seuils_pilotage.yaml` :

```yaml
# Seuils de pilotage — config as code (même convention que eval/seuils.yaml).
#
# Toute modification passe par un commit et met à jour « motif ». Le pilote
# (python -m ops.deploy piloter) journalise chaque changement (événement
# « seuils »). Valeurs de production entre parenthèses dans les commentaires.

fenetre_s: 300              # fenêtre glissante de surveillance (C2.22)
minimum: 10                 # mesures minimales avant toute décision (C2.15)
intervalle_s: 5             # période d'une tour du pilote
derive:
  score_min: 0.70           # seuil dur → rollback automatique
  marge: 0.05               # [score_min ; score_min + marge[ → alerte
  taux_erreur_max: 0.10
  latence_p95_max_ms: 8000  # contrainte client : P95 < 8 s
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

`ops/seuils.py` :

```python
"""Seuils de pilotage — config as code (``ops/seuils_pilotage.yaml``).

Même convention que ``eval/seuils.yaml`` (gate) : fichier versionné, ``motif``
obligatoire, relu à chaque appel, erreurs explicites qui nomment la clé.

Ce module n'importe ni ``app.api_*`` ni ``eval`` : ``app.capture`` l'importe,
et ``eval.run_eval`` importe ``app.api_v2`` (import circulaire sinon).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_SEUILS_PILOTAGE_DEFAUT = RACINE / "ops" / "seuils_pilotage.yaml"
CHEMIN_SEUILS_GATE_DEFAUT = RACINE / "eval" / "seuils.yaml"


class ErreurSeuilsPilotage(ValueError):
    """Fichier de seuils de pilotage absent, illisible ou incohérent."""


@dataclass(frozen=True)
class SeuilsDerive:
    score_min: float
    marge: float
    taux_erreur_max: float
    latence_p95_max_ms: float


@dataclass(frozen=True)
class SeuilsPromotion:
    paliers: tuple[int, ...]
    duree_min_s: float
    requetes_min: int
    ecart_erreur_max: float
    latence_p95_max_ms: float
    score_moyen_min: float
    score_p10_min: float


@dataclass(frozen=True)
class SeuilsCapture:
    score_max: float


@dataclass(frozen=True)
class SeuilsPilotage:
    fenetre_s: float
    minimum: int
    intervalle_s: float
    derive: SeuilsDerive
    promotion: SeuilsPromotion
    capture: SeuilsCapture
    motif: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["promotion"]["paliers"] = list(self.promotion.paliers)
        return data


def chemin_seuils_pilotage() -> Path:
    """``PILOTAGE_SEUILS_PATH`` si défini, sinon ``ops/seuils_pilotage.yaml``."""
    return Path(os.environ.get("PILOTAGE_SEUILS_PATH") or CHEMIN_SEUILS_PILOTAGE_DEFAUT)


def chemin_seuils_gate() -> Path:
    """Le fichier du gate (SP2) : ``SEUILS_PATH`` si défini, sinon ``eval/seuils.yaml``."""
    return Path(os.environ.get("SEUILS_PATH") or CHEMIN_SEUILS_GATE_DEFAUT)


def lire_brut(chemin: Path | str) -> dict[str, Any]:
    """Le YAML tel quel (pour journaliser avant/après), sans validation des clés."""
    chemin = Path(chemin)
    try:
        contenu = chemin.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ErreurSeuilsPilotage(f"fichier de seuils introuvable : {chemin}") from exc
    except OSError as exc:
        raise ErreurSeuilsPilotage(f"fichier de seuils illisible : {chemin} ({exc})") from exc
    try:
        data = yaml.safe_load(contenu)
    except yaml.YAMLError as exc:
        raise ErreurSeuilsPilotage(f"{chemin} : YAML invalide ({exc})") from exc
    if not isinstance(data, dict):
        raise ErreurSeuilsPilotage(f"{chemin} : attendu un dictionnaire de seuils")
    return data


def empreinte(chemin: Path | str) -> str:
    chemin = Path(chemin)
    try:
        contenu = chemin.read_bytes()
    except FileNotFoundError as exc:
        raise ErreurSeuilsPilotage(f"fichier de seuils introuvable : {chemin}") from exc
    except OSError as exc:
        raise ErreurSeuilsPilotage(f"fichier de seuils illisible : {chemin} ({exc})") from exc
    return hashlib.sha256(contenu).hexdigest()[:12]


def _section(data: dict[str, Any], cle: str, chemin: Path) -> dict[str, Any]:
    section = data.get(cle)
    if not isinstance(section, dict):
        raise ErreurSeuilsPilotage(f"{chemin} : section « {cle} » manquante")
    return section


def _nombre(section: dict[str, Any], cle: str, chemin: Path, prefixe: str = "") -> float:
    nom = f"{prefixe}{cle}"
    if cle not in section:
        raise ErreurSeuilsPilotage(f"{chemin} : clé « {nom} » manquante")
    valeur = section[cle]
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        raise ErreurSeuilsPilotage(f"{chemin} : « {nom} » doit être un nombre, reçu {valeur!r}")
    return float(valeur)


def _paliers(section: dict[str, Any], chemin: Path) -> tuple[int, ...]:
    paliers = section.get("paliers")
    valide = (
        isinstance(paliers, list)
        and bool(paliers)
        and all(isinstance(p, int) and not isinstance(p, bool) for p in paliers)
        and paliers == sorted(set(paliers))
        and paliers[0] >= 1
        and paliers[-1] == 100
    )
    if not valide:
        raise ErreurSeuilsPilotage(
            f"{chemin} : « promotion.paliers » doit être une liste croissante d'entiers "
            f"finissant par 100, reçu {paliers!r}"
        )
    return tuple(paliers)


def charger_seuils_pilotage(chemin: Path | str | None = None) -> SeuilsPilotage:
    chemin = Path(chemin) if chemin else chemin_seuils_pilotage()
    data = lire_brut(chemin)
    derive = _section(data, "derive", chemin)
    promotion = _section(data, "promotion", chemin)
    capture = _section(data, "capture", chemin)
    motif = str(data.get("motif") or "").strip()
    if not motif:
        raise ErreurSeuilsPilotage(f"{chemin} : clé « motif » manquante ou vide")
    return SeuilsPilotage(
        fenetre_s=_nombre(data, "fenetre_s", chemin),
        minimum=int(_nombre(data, "minimum", chemin)),
        intervalle_s=_nombre(data, "intervalle_s", chemin),
        derive=SeuilsDerive(
            score_min=_nombre(derive, "score_min", chemin, "derive."),
            marge=_nombre(derive, "marge", chemin, "derive."),
            taux_erreur_max=_nombre(derive, "taux_erreur_max", chemin, "derive."),
            latence_p95_max_ms=_nombre(derive, "latence_p95_max_ms", chemin, "derive."),
        ),
        promotion=SeuilsPromotion(
            paliers=_paliers(promotion, chemin),
            duree_min_s=_nombre(promotion, "duree_min_s", chemin, "promotion."),
            requetes_min=int(_nombre(promotion, "requetes_min", chemin, "promotion.")),
            ecart_erreur_max=_nombre(promotion, "ecart_erreur_max", chemin, "promotion."),
            latence_p95_max_ms=_nombre(promotion, "latence_p95_max_ms", chemin, "promotion."),
            score_moyen_min=_nombre(promotion, "score_moyen_min", chemin, "promotion."),
            score_p10_min=_nombre(promotion, "score_p10_min", chemin, "promotion."),
        ),
        capture=SeuilsCapture(score_max=_nombre(capture, "score_max", chemin, "capture.")),
        motif=motif,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_seuils_pilotage.py -v && uv run ruff check ops/seuils.py tests/unit/test_seuils_pilotage.py`
Expected: PASS (11 tests) ; ruff : `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add ops/seuils_pilotage.yaml ops/seuils.py tests/unit/test_seuils_pilotage.py
git commit -m "feat(seuils): seuils de pilotage versionnés, chargement strict et empreinte

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Signaux — `ops/signaux.py`

**Files:**
- Create: `ops/signaux.py`
- Test: `tests/unit/test_signaux.py`

**Interfaces:**
- Consumes: `app.telemetry.Mesure`.
- Produces: `percentile(valeurs: Sequence[float], p: float) -> float | None` ; `@dataclass(frozen=True) Agregat(requetes: int, erreurs: int, taux_erreur: float, latence_p50_ms: float | None, latence_p95_ms: float | None, score_moyen: float | None, score_p10: float | None, cout_total_eur: float, cout_moyen_eur: float)` avec `to_dict()` ; `scores(mesures) -> list[float]` ; `agreger(mesures) -> Agregat` ; `histogramme(valeurs, pas=0.1) -> list[int]` ; `serie_par_minute(mesures) -> list[dict]` (clés `minute`, `requetes`, `latence_p95_ms`, `taux_erreur`, `score_moyen`) ; `filtrer(mesures, version, *, depuis_ts=None) -> list[Mesure]`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_signaux.py` :

```python
"""Agrégats purs sur des Mesure : ce que voient le dashboard et le pilote."""
from __future__ import annotations

import pytest

from app.telemetry import Mesure
from ops.signaux import agreger, filtrer, histogramme, percentile, scores, serie_par_minute


def m(version="v2.0.0", *, latence=1000.0, score=0.9, erreur=False, ts=0.0, cout=0.01):
    return Mesure(ts=ts, version=version, route="/analyse", latence_ms=latence,
                  erreur=erreur, score=score, cout_eur=cout)


def test_percentile():
    assert percentile([], 50) is None
    assert percentile([4.0], 95) == 4.0
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile(list(range(1, 101)), 95) == pytest.approx(95.05)


def test_agreger_exclut_les_erreurs_des_latences_et_scores():
    mesures = [
        m(latence=100, score=0.8),
        m(latence=200, score=0.9),
        m(latence=300, score=None),
        m(latence=9999, score=0.1, erreur=True),
    ]
    a = agreger(mesures)
    assert a.requetes == 4 and a.erreurs == 1 and a.taux_erreur == 0.25
    assert a.latence_p50_ms == 200.0
    assert a.latence_p95_ms == pytest.approx(290.0)
    assert a.score_moyen == pytest.approx(0.85)
    assert a.cout_total_eur == pytest.approx(0.04)
    assert a.cout_moyen_eur == pytest.approx(0.01)
    assert scores(mesures) == [0.8, 0.9]


def test_agreger_v1_sans_score():
    a = agreger([m("v1.0.0", score=None) for _ in range(3)])
    assert a.score_moyen is None and a.score_p10 is None


def test_agreger_vide():
    a = agreger([])
    assert a.requetes == 0 and a.taux_erreur == 0.0 and a.latence_p95_ms is None


def test_histogramme():
    assert histogramme([0.05, 0.3, 0.7, 0.95, 1.0]) == [1, 0, 0, 1, 0, 0, 0, 1, 0, 2]
    assert histogramme([]) == [0] * 10


def test_serie_par_minute():
    serie = serie_par_minute([m(ts=0), m(ts=30, erreur=True), m(ts=65)])
    assert [p["minute"] for p in serie] == [0, 60]
    assert serie[0]["requetes"] == 2 and serie[0]["taux_erreur"] == 0.5
    assert serie[1]["requetes"] == 1


def test_filtrer():
    mesures = [m("v1.0.0", ts=100), m(ts=10), m(ts=60)]
    assert [x.ts for x in filtrer(mesures, "v2.0.0")] == [10, 60]
    assert [x.ts for x in filtrer(mesures, "v2.0.0", depuis_ts=50)] == [60]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_signaux.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops.signaux'`

- [ ] **Step 3: Write minimal implementation**

`ops/signaux.py` :

```python
"""Signaux — agrégats purs sur des ``Mesure`` (``ops/metrics.jsonl``).

Partagés par le tableau de bord et le pilote : ce qu'on voit à l'écran est
exactement ce qui a déclenché l'action. Les erreurs comptent dans le trafic et
le taux d'erreur, jamais dans les latences ni les scores.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

from app.telemetry import Mesure


def percentile(valeurs: Sequence[float], p: float) -> float | None:
    """Percentile ``p`` (0–100) par interpolation linéaire ; ``None`` si vide."""
    if not valeurs:
        return None
    tri = sorted(valeurs)
    rang = (len(tri) - 1) * p / 100
    bas, haut = math.floor(rang), math.ceil(rang)
    return float(tri[bas] + (tri[haut] - tri[bas]) * (rang - bas))


def _arrondi(valeur: float | None, decimales: int) -> float | None:
    return None if valeur is None else round(valeur, decimales)


@dataclass(frozen=True)
class Agregat:
    requetes: int
    erreurs: int
    taux_erreur: float
    latence_p50_ms: float | None
    latence_p95_ms: float | None
    score_moyen: float | None
    score_p10: float | None
    cout_total_eur: float
    cout_moyen_eur: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scores(mesures: Sequence[Mesure]) -> list[float]:
    """Scores des mesures sans erreur qui en portent un (la v1 n'en porte pas)."""
    return [m.score for m in mesures if not m.erreur and m.score is not None]


def agreger(mesures: Sequence[Mesure]) -> Agregat:
    n = len(mesures)
    erreurs = sum(1 for m in mesures if m.erreur)
    latences = [m.latence_ms for m in mesures if not m.erreur]
    valeurs = scores(mesures)
    cout = sum(m.cout_eur for m in mesures)
    return Agregat(
        requetes=n,
        erreurs=erreurs,
        taux_erreur=round(erreurs / n, 4) if n else 0.0,
        latence_p50_ms=_arrondi(percentile(latences, 50), 1),
        latence_p95_ms=_arrondi(percentile(latences, 95), 1),
        score_moyen=round(fmean(valeurs), 4) if valeurs else None,
        score_p10=_arrondi(percentile(valeurs, 10), 4),
        cout_total_eur=round(cout, 6),
        cout_moyen_eur=round(cout / n, 6) if n else 0.0,
    )


def histogramme(valeurs: Sequence[float], pas: float = 0.1) -> list[int]:
    """Comptes par intervalle de largeur ``pas`` sur [0 ; 1] ; 1,0 tombe dans le dernier."""
    n = round(1 / pas)
    comptes = [0] * n
    for v in valeurs:
        indice = min(max(int(v * n + 1e-9), 0), n - 1)
        comptes[indice] += 1
    return comptes


def serie_par_minute(mesures: Sequence[Mesure]) -> list[dict[str, Any]]:
    groupes: dict[int, list[Mesure]] = {}
    for m in mesures:
        groupes.setdefault(int(m.ts // 60) * 60, []).append(m)
    serie = []
    for minute in sorted(groupes):
        a = agreger(groupes[minute])
        serie.append(
            {
                "minute": minute,
                "requetes": a.requetes,
                "latence_p95_ms": a.latence_p95_ms,
                "taux_erreur": a.taux_erreur,
                "score_moyen": a.score_moyen,
            }
        )
    return serie


def filtrer(
    mesures: Sequence[Mesure], version: str, *, depuis_ts: float | None = None
) -> list[Mesure]:
    return [
        m for m in mesures
        if m.version == version and (depuis_ts is None or m.ts >= depuis_ts)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_signaux.py -v && uv run ruff check ops/signaux.py tests/unit/test_signaux.py`
Expected: PASS (7 tests) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/signaux.py tests/unit/test_signaux.py
git commit -m "feat(signaux): agrégats purs par version (P50/P95/P10, erreurs, score, coût)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Calibration des seuils — `calibrer` et CLI `python -m ops.seuils`

**Files:**
- Modify: `ops/seuils.py` (imports ; fonctions `calibrer`, `main` en fin de fichier)
- Test: `tests/unit/test_calibrer.py`

**Interfaces:**
- Consumes: `ErreurSeuilsPilotage` (tâche 1) ; `percentile`, `scores` (tâche 2) ; `app.telemetry.MetricsStore`.
- Produces: `calibrer(metriques: MetricsStore, version: str, *, fenetre_s=3600, k=1.5, minimum=30, marge_p10=0.05) -> dict` (clés `version`, `mesures`, `moyenne`, `ecart_type`, `p10`, `k`, `score_min_propose`, `score_p10_min_propose`) ; `main(argv=None) -> int` (0 succès, 1 échantillon insuffisant).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_calibrer.py` :

```python
"""calibrer : moyenne − k·σ sur la distribution de production (D7)."""
from __future__ import annotations

import time

import pytest

from app.telemetry import Mesure
from ops.seuils import ErreurSeuilsPilotage, calibrer, main


def _remplir(metriques, scores, version="v2.0.0", erreur=False):
    for s in scores:
        metriques.enregistrer(Mesure(ts=time.time(), version=version, route="/analyse",
                                     latence_ms=1000, erreur=erreur, score=s))


def test_proposition_moyenne_moins_k_ecarts_types(metriques):
    _remplir(metriques, [0.7] * 15 + [0.9] * 15)
    _remplir(metriques, [0.1] * 5, erreur=True)          # erreurs exclues
    _remplir(metriques, [None] * 5)                      # sans score exclues
    _remplir(metriques, [0.2] * 30, version="v3.0.0")    # autre version exclue
    r = calibrer(metriques, "v2.0.0")
    assert r["mesures"] == 30
    assert r["moyenne"] == pytest.approx(0.8)
    assert r["ecart_type"] == pytest.approx(0.1)
    assert r["score_min_propose"] == pytest.approx(0.65)
    assert r["p10"] == pytest.approx(0.7)
    assert r["score_p10_min_propose"] == pytest.approx(0.65)


def test_echantillon_insuffisant(metriques):
    _remplir(metriques, [0.8] * 10)
    with pytest.raises(ErreurSeuilsPilotage, match="échantillon insuffisant"):
        calibrer(metriques, "v2.0.0")


def test_cli(metriques, capsys):
    _remplir(metriques, [0.7] * 15 + [0.9] * 15)
    assert main(["calibrer", "--version", "v2.0.0"]) == 0
    sortie = capsys.readouterr().out
    assert "score_min: 0.65" in sortie and "n'écrit rien" in sortie
    assert main(["calibrer", "--version", "v9.9.9"]) == 1
    assert "CALIBRATION IMPOSSIBLE" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_calibrer.py -v`
Expected: FAIL — `ImportError: cannot import name 'calibrer' from 'ops.seuils'`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/seuils.py`, compléter les imports en tête :

```python
import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

import yaml

from app.telemetry import MetricsStore
from ops.signaux import percentile, scores
```

Puis ajouter en fin de fichier :

```python
# ---------------------------------------------------------------- calibration
def calibrer(
    metriques: MetricsStore,
    version: str,
    *,
    fenetre_s: float = 3600,
    k: float = 1.5,
    minimum: int = 30,
    marge_p10: float = 0.05,
) -> dict[str, Any]:
    """Propose des seuils à partir de la distribution de production (C2.2, D7).

    N'écrit rien : la proposition est reportée à la main dans
    ``ops/seuils_pilotage.yaml`` par un commit dont le ``motif`` la cite.
    """
    valeurs = scores(metriques.lire(depuis_s=fenetre_s, version=version))
    if len(valeurs) < minimum:
        raise ErreurSeuilsPilotage(
            f"échantillon insuffisant pour calibrer {version} : "
            f"{len(valeurs)} scores < {minimum}"
        )
    moyenne = fmean(valeurs)
    ecart = pstdev(valeurs)
    p10 = percentile(valeurs, 10) or 0.0
    return {
        "version": version,
        "mesures": len(valeurs),
        "moyenne": round(moyenne, 4),
        "ecart_type": round(ecart, 4),
        "p10": round(p10, 4),
        "k": k,
        "score_min_propose": round(moyenne - k * ecart, 2),
        "score_p10_min_propose": round(p10 - marge_p10, 2),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seuils de pilotage Mardik")
    sub = parser.add_subparsers(dest="commande", required=True)
    c = sub.add_parser("calibrer", help="propose des seuils (n'écrit rien)")
    c.add_argument("--version", required=True)
    c.add_argument("--fenetre", type=float, default=3600)
    c.add_argument("--k", type=float, default=1.5)
    args = parser.parse_args(argv)
    try:
        r = calibrer(MetricsStore(), args.version, fenetre_s=args.fenetre, k=args.k)
    except ErreurSeuilsPilotage as exc:
        print(f"CALIBRATION IMPOSSIBLE : {exc}", file=sys.stderr)
        return 1
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print("\nProposition à reporter dans ops/seuils_pilotage.yaml (ce script n'écrit rien) :")
    print(f"derive:\n  score_min: {r['score_min_propose']}")
    print(f"promotion:\n  score_p10_min: {r['score_p10_min_propose']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_calibrer.py tests/unit/test_seuils_pilotage.py -v && uv run ruff check ops/ tests/unit/`
Expected: PASS ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/seuils.py tests/unit/test_calibrer.py
git commit -m "feat(seuils): calibrer — proposition moyenne − k·σ sur la production

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Décision de dérive — `ops/pilotage.py` (1/3)

**Files:**
- Create: `ops/pilotage.py`
- Test: `tests/unit/test_pilotage.py`

**Interfaces:**
- Consumes: `agreger` (tâche 2) ; `SeuilsDerive` (tâche 1).
- Produces: `@dataclass(frozen=True) Constat(signal: str, valeur: float | None, seuil: float, ok: bool)` avec `to_dict()` ; `@dataclass(frozen=True) Derive(version: str, mesures: int, niveau: str, constats: tuple[Constat, ...], motif: str, sous_seuil: int = 0)` avec propriétés `critique -> bool`, `principal -> Constat | None` ; `NIVEAUX = ("aucune", "marge", "critique", "echantillon_insuffisant")` ; `version_surveillee(index: dict) -> str | None` ; `detecter_derive(version: str, mesures, seuils: SeuilsDerive, *, minimum: int) -> Derive`. Motifs exacts : `"score moyen {v:.2f} < {s:.2f}"`, `"taux d'erreur {v:.0%} > {s:.0%}"`, `"latence P95 {v:.0f} ms > {s:.0f} ms"`, `"score moyen {v:.2f} dans la marge [{s:.2f} ; {s+marge:.2f}["`, `"échantillon insuffisant ({n}/{minimum})"`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_pilotage.py` :

```python
"""Décisions pures du pilote : dérive, palier, changements de seuils."""
from __future__ import annotations

from app.telemetry import Mesure
from ops.pilotage import detecter_derive, version_surveillee
from ops.seuils import SeuilsDerive

DERIVE = SeuilsDerive(score_min=0.70, marge=0.05, taux_erreur_max=0.10, latence_p95_max_ms=8000)


def m(version="v2.0.0", *, latence=1000.0, score=0.9, erreur=False, ts=0.0):
    return Mesure(ts=ts, version=version, route="/analyse", latence_ms=latence,
                  erreur=erreur, score=score)


def test_version_surveillee():
    assert version_surveillee({"active": "v1.0.0", "canary": "v2.0.0"}) == "v2.0.0"
    assert version_surveillee({"active": "v1.0.0", "canary": None}) == "v1.0.0"
    assert version_surveillee({}) is None


def test_echantillon_insuffisant():
    d = detecter_derive("v2.0.0", [m(score=0.1)] * 5, DERIVE, minimum=10)
    assert d.niveau == "echantillon_insuffisant" and not d.critique
    assert d.motif == "échantillon insuffisant (5/10)"


def test_derive_critique_sur_le_score():
    d = detecter_derive("v2.0.0", [m(score=0.5)] * 12, DERIVE, minimum=10)
    assert d.critique and d.niveau == "critique"
    assert d.motif == "score moyen 0.50 < 0.70"
    assert d.principal.signal == "score_moyen" and d.sous_seuil == 12


def test_derive_critique_sur_les_erreurs():
    d = detecter_derive("v2.0.0", [m()] * 10 + [m(erreur=True)] * 2, DERIVE, minimum=10)
    assert d.critique and d.motif.startswith("taux d'erreur 17%")


def test_derive_critique_sur_la_latence():
    d = detecter_derive("v2.0.0", [m(latence=9000)] * 12, DERIVE, minimum=10)
    assert d.critique and d.motif == "latence P95 9000 ms > 8000 ms"


def test_le_score_est_cite_en_premier():
    d = detecter_derive("v2.0.0", [m(score=0.5, latence=9000)] * 12, DERIVE, minimum=10)
    assert d.motif.startswith("score")


def test_marge_sans_action():
    d = detecter_derive("v2.0.0", [m(score=0.72)] * 12, DERIVE, minimum=10)
    assert d.niveau == "marge" and not d.critique
    assert d.motif == "score moyen 0.72 dans la marge [0.70 ; 0.75["


def test_aucune_derive():
    d = detecter_derive("v2.0.0", [m(score=0.9)] * 12, DERIVE, minimum=10)
    assert d.niveau == "aucune" and d.motif == "" and d.principal is None


def test_v1_sans_score_jugee_sur_erreurs_et_latence():
    d = detecter_derive("v1.0.0", [m("v1.0.0", score=None)] * 12, DERIVE, minimum=10)
    assert d.niveau == "aucune"
    assert "score_moyen" not in {c.signal for c in d.constats}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_pilotage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops.pilotage'`

- [ ] **Step 3: Write minimal implementation**

`ops/pilotage.py` :

```python
"""Décisions du pilote — fonctions pures, aucune écriture.

Chaque fonction reçoit des mesures, des seuils et des entrées de journal, et
renvoie une décision. ``ops/deploy.py`` exécute ces décisions (rollback,
progression, promotion) ; ``ops/dashboard.py`` les affiche.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from app.telemetry import Mesure
from ops.seuils import SeuilsDerive
from ops.signaux import agreger, scores

NIVEAUX = ("aucune", "marge", "critique", "echantillon_insuffisant")


@dataclass(frozen=True)
class Constat:
    """Un critère évalué : le signal, sa valeur, le seuil, tenu ou non."""

    signal: str
    valeur: float | None
    seuil: float
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Derive:
    version: str
    mesures: int
    niveau: str
    constats: tuple[Constat, ...]
    motif: str
    sous_seuil: int = 0

    @property
    def critique(self) -> bool:
        return self.niveau == "critique"

    @property
    def principal(self) -> Constat | None:
        return next((c for c in self.constats if not c.ok), None)


def version_surveillee(index: dict[str, Any]) -> str | None:
    """Le canary s'il y en a un, sinon l'active."""
    return index.get("canary") or index.get("active")


def _motif_derive(c: Constat) -> str:
    if c.signal == "score_moyen":
        return f"score moyen {c.valeur:.2f} < {c.seuil:.2f}"
    if c.signal == "taux_erreur":
        return f"taux d'erreur {c.valeur:.0%} > {c.seuil:.0%}"
    return f"latence P95 {c.valeur:.0f} ms > {c.seuil:.0f} ms"


def detecter_derive(
    version: str, mesures: Sequence[Mesure], seuils: SeuilsDerive, *, minimum: int
) -> Derive:
    """Seuils durs → ``critique`` ; score dans la marge → ``marge``.

    Ordre des constats (et donc du motif) : score, erreurs, P95. Le score n'est
    pas évalué pour une version qui n'en produit pas (v1).
    """
    a = agreger(mesures)
    if a.requetes < minimum:
        return Derive(version, a.requetes, "echantillon_insuffisant", (),
                      f"échantillon insuffisant ({a.requetes}/{minimum})")
    constats: list[Constat] = []
    if a.score_moyen is not None:
        constats.append(Constat("score_moyen", a.score_moyen, seuils.score_min,
                                a.score_moyen >= seuils.score_min))
    constats.append(Constat("taux_erreur", a.taux_erreur, seuils.taux_erreur_max,
                            a.taux_erreur <= seuils.taux_erreur_max))
    if a.latence_p95_ms is not None:
        constats.append(Constat("latence_p95_ms", a.latence_p95_ms, seuils.latence_p95_max_ms,
                                a.latence_p95_ms <= seuils.latence_p95_max_ms))
    sous_seuil = sum(1 for s in scores(mesures) if s < seuils.score_min)
    echecs = [c for c in constats if not c.ok]
    if echecs:
        return Derive(version, a.requetes, "critique", tuple(constats),
                      _motif_derive(echecs[0]), sous_seuil)
    haut = seuils.score_min + seuils.marge
    if a.score_moyen is not None and a.score_moyen < haut:
        return Derive(
            version, a.requetes, "marge", tuple(constats),
            f"score moyen {a.score_moyen:.2f} dans la marge "
            f"[{seuils.score_min:.2f} ; {haut:.2f}[",
            sous_seuil,
        )
    return Derive(version, a.requetes, "aucune", tuple(constats), "", sous_seuil)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_pilotage.py -v && uv run ruff check ops/pilotage.py tests/unit/test_pilotage.py`
Expected: PASS (9 tests) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/pilotage.py tests/unit/test_pilotage.py
git commit -m "feat(pilotage): détection de dérive pure (seuil dur, marge, échantillon minimal)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Décision de palier canary — `ops/pilotage.py` (2/3)

**Files:**
- Modify: `ops/pilotage.py` (import `SeuilsPilotage` ; nouvelles fonctions après `detecter_derive`)
- Test: `tests/unit/test_pilotage.py` (ajouts)

**Interfaces:**
- Consumes: `agreger` (tâche 2) ; `SeuilsPilotage` (tâche 1) ; `Constat` (tâche 4).
- Produces: `@dataclass(frozen=True) DecisionPalier(version: str, action: str, pourcentage_suivant: int | None, constats: tuple[Constat, ...], motif: str, requetes: int, depuis_s: float)` — `action` ∈ `"attendre" | "progresser" | "promouvoir"` ; `debut_palier(journal: list[dict], version: str) -> float | None` ; `evaluer_palier(version, mesures_canary, mesures_active, seuils: SeuilsPilotage, *, depuis_s: float, pourcentage: int) -> DecisionPalier`. Motif d'échec exact : `"critère non tenu : {signal} {valeur} (seuil {seuil})"` ; motif de succès : `"palier {p} % tenu : {n} analyses conformes en {depuis_s:.0f} s"` ; motif d'attente : `"palier {p} % : {n}/{requetes_min} requêtes, {depuis_s:.0f}/{duree_min_s:.0f} s"`.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/unit/test_pilotage.py` (imports en tête du fichier : compléter la ligne `from ops.pilotage import …` avec `debut_palier, evaluer_palier`, et ajouter `from ops.seuils import charger_seuils_pilotage`) :

```python
SEUILS = charger_seuils_pilotage()   # duree_min_s 60, requetes_min 20, minimum 10


def _canary(n=25, *, score=0.9, latence=2000.0, erreurs=0):
    return [m(score=score, latence=latence) for _ in range(n - erreurs)] + [
        m(erreur=True) for _ in range(erreurs)
    ]


def _active(n=25, erreurs=0):
    return [m("v1.0.0", score=None) for _ in range(n - erreurs)] + [
        m("v1.0.0", score=None, erreur=True) for _ in range(erreurs)
    ]


def test_debut_palier():
    j = [{"evenement": "canary", "version": "v2.0.0", "ts": 1.0},
         {"evenement": "canary", "version": "v2.0.0", "ts": 2.0}]
    assert debut_palier(j, "v2.0.0") == 2.0
    assert debut_palier(j + [{"evenement": "rollback", "ts": 3.0}], "v2.0.0") is None
    assert debut_palier(j + [{"evenement": "promotion", "ts": 3.0}], "v2.0.0") is None
    assert debut_palier(j, "v3.0.0") is None


def test_attendre_la_duree_minimale():
    d = evaluer_palier("v2.0.0", _canary(), _active(), SEUILS, depuis_s=30, pourcentage=10)
    assert d.action == "attendre" and "30/60 s" in d.motif


def test_attendre_le_nombre_de_requetes():
    d = evaluer_palier("v2.0.0", _canary(5), _active(), SEUILS, depuis_s=120, pourcentage=10)
    assert d.action == "attendre" and "5/20 requêtes" in d.motif


def test_progresser_puis_promouvoir():
    d = evaluer_palier("v2.0.0", _canary(), _active(), SEUILS, depuis_s=120, pourcentage=10)
    assert d.action == "progresser" and d.pourcentage_suivant == 50
    assert d.motif == "palier 10 % tenu : 25 analyses conformes en 120 s"
    assert all(c.ok for c in d.constats) and d.requetes == 25
    d = evaluer_palier("v2.0.0", _canary(), _active(), SEUILS, depuis_s=120, pourcentage=50)
    assert d.action == "promouvoir" and d.pourcentage_suivant == 100


def test_erreurs_superieures_a_la_v1():
    d = evaluer_palier("v2.0.0", _canary(erreurs=3), _active(), SEUILS,
                       depuis_s=120, pourcentage=10)
    assert d.action == "attendre" and "taux_erreur" in d.motif


def test_queue_basse_du_score():
    canary = [m(score=0.95) for _ in range(20)] + [m(score=0.5) for _ in range(5)]
    d = evaluer_palier("v2.0.0", canary, _active(), SEUILS, depuis_s=120, pourcentage=10)
    assert d.action == "attendre" and "score_p10" in d.motif


def test_repli_sur_le_seuil_absolu_si_active_peu_servie():
    canary = _canary(erreurs=2)   # 8 % d'erreurs
    assert evaluer_palier("v2.0.0", canary, _active(3), SEUILS,
                          depuis_s=120, pourcentage=10).action == "progresser"
    assert evaluer_palier("v2.0.0", canary, _active(25), SEUILS,
                          depuis_s=120, pourcentage=10).action == "attendre"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_pilotage.py -v`
Expected: FAIL — `ImportError: cannot import name 'debut_palier' from 'ops.pilotage'`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/pilotage.py`, remplacer `from ops.seuils import SeuilsDerive` par `from ops.seuils import SeuilsDerive, SeuilsPilotage`, puis ajouter après `detecter_derive` :

```python
# ------------------------------------------------------------------- palier
@dataclass(frozen=True)
class DecisionPalier:
    version: str
    action: str                        # "attendre" | "progresser" | "promouvoir"
    pourcentage_suivant: int | None
    constats: tuple[Constat, ...]
    motif: str
    requetes: int
    depuis_s: float


def debut_palier(journal: list[dict[str, Any]], version: str) -> float | None:
    """``ts`` du dernier ``canary`` de ``version`` qu'aucun rollback ni aucune
    promotion n'a suivi (quelle que soit son ``origine``) ; sinon ``None``."""
    debut: float | None = None
    for entree in journal:
        evenement = entree.get("evenement")
        if evenement == "canary":
            debut = entree.get("ts") if entree.get("version") == version else None
        elif evenement in ("rollback", "promotion"):
            debut = None
    return debut


def _valeur(v: float | None) -> str:
    return "absente" if v is None else f"{v:g}"


def evaluer_palier(
    version: str,
    mesures_canary: Sequence[Mesure],
    mesures_active: Sequence[Mesure],
    seuils: SeuilsPilotage,
    *,
    depuis_s: float,
    pourcentage: int,
) -> DecisionPalier:
    """Le palier courant est-il tenu ? (C2.5, C2.17)

    Les erreurs du canary sont comparées à celles de l'active si elle a au moins
    ``seuils.minimum`` mesures, sinon au seuil absolu ``derive.taux_erreur_max``.
    Un canary sans score (ou sans latence mesurable) ne progresse jamais.
    """
    p = seuils.promotion
    c = agreger(mesures_canary)
    a = agreger(mesures_active)
    if depuis_s < p.duree_min_s or c.requetes < p.requetes_min:
        return DecisionPalier(
            version, "attendre", None, (),
            f"palier {pourcentage} % : {c.requetes}/{p.requetes_min} requêtes, "
            f"{depuis_s:.0f}/{p.duree_min_s:.0f} s",
            c.requetes, depuis_s,
        )
    if a.requetes >= seuils.minimum:
        ref_erreur = round(a.taux_erreur + p.ecart_erreur_max, 4)
    else:
        ref_erreur = seuils.derive.taux_erreur_max
    constats = (
        Constat("taux_erreur", c.taux_erreur, ref_erreur, c.taux_erreur <= ref_erreur),
        Constat("latence_p95_ms", c.latence_p95_ms, p.latence_p95_max_ms,
                c.latence_p95_ms is not None and c.latence_p95_ms < p.latence_p95_max_ms),
        Constat("score_moyen", c.score_moyen, p.score_moyen_min,
                c.score_moyen is not None and c.score_moyen >= p.score_moyen_min),
        Constat("score_p10", c.score_p10, p.score_p10_min,
                c.score_p10 is not None and c.score_p10 >= p.score_p10_min),
    )
    echec = next((x for x in constats if not x.ok), None)
    if echec is not None:
        return DecisionPalier(
            version, "attendre", None, constats,
            f"critère non tenu : {echec.signal} {_valeur(echec.valeur)} (seuil {echec.seuil:g})",
            c.requetes, depuis_s,
        )
    suivant = next((x for x in p.paliers if x > pourcentage), 100)
    return DecisionPalier(
        version, "promouvoir" if suivant == 100 else "progresser", suivant, constats,
        f"palier {pourcentage} % tenu : {c.requetes} analyses conformes en {depuis_s:.0f} s",
        c.requetes, depuis_s,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_pilotage.py -v && uv run ruff check ops/pilotage.py tests/unit/test_pilotage.py`
Expected: PASS (16 tests) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/pilotage.py tests/unit/test_pilotage.py
git commit -m "feat(pilotage): décision de palier canary (durée, volume, erreurs, P95, score, P10)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Changements de seuils et résumés métier — `ops/pilotage.py` (3/3)

**Files:**
- Modify: `ops/pilotage.py` (import `Path`, `empreinte`, `lire_brut` ; fonctions en fin de fichier)
- Test: `tests/unit/test_pilotage.py` (ajouts), `tests/unit/test_resume_metier.py`

**Interfaces:**
- Consumes: `empreinte`, `lire_brut` (tâche 1).
- Produces: `changements_seuils(journal: list[dict], fichiers: dict[str, Path]) -> list[dict]` — chaque dict a les clés `fichier`, `empreinte`, `avant` (dict | None), `apres` (dict), `motif` ; `resume_metier(evenement: str, **details) -> str` pour `rollback`, `canary`, `promotion`, `alerte`, `pilotage_refus`, `enrichissement`, `seuils`, `seuils_invalides` (tout autre → `ValueError`).

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/unit/test_pilotage.py` (compléter l'import `from ops.pilotage import …` avec `changements_seuils`, et ajouter `import yaml`) :

```python
def test_changements_seuils(tmp_path):
    chemin = tmp_path / "s.yaml"
    chemin.write_text(yaml.safe_dump({"derive": {"score_min": 0.7}, "motif": "a"}),
                      encoding="utf-8")
    fichiers = {"ops/seuils_pilotage.yaml": chemin}
    [initial] = changements_seuils([], fichiers)
    assert initial["fichier"] == "ops/seuils_pilotage.yaml" and initial["avant"] is None
    assert initial["apres"]["derive"]["score_min"] == 0.7 and initial["motif"] == "a"
    journal = [{"evenement": "seuils", **initial}]
    assert changements_seuils(journal, fichiers) == []
    chemin.write_text(yaml.safe_dump({"derive": {"score_min": 0.68}, "motif": "b"}),
                      encoding="utf-8")
    [change] = changements_seuils(journal, fichiers)
    assert change["avant"] == initial["apres"] and change["apres"]["derive"]["score_min"] == 0.68
    assert change["empreinte"] != initial["empreinte"]
```

`tests/unit/test_resume_metier.py` :

```python
"""Résumés en langage métier du journal de pilotage (B2.5)."""
from __future__ import annotations

import pytest

from ops.pilotage import resume_metier


def test_rollback_sur_score():
    assert resume_metier("rollback", version="v2.0.0", signal="score_moyen", valeur=0.52,
                         seuil=0.70, mesures=27, sous_seuil=15) == (
        "Version v2.0.0 retirée : 15 analyses sur 27 jugées peu fiables (score < 0,70).")


def test_rollback_sur_erreurs_et_latence():
    assert resume_metier("rollback", version="v2.0.0", signal="taux_erreur", valeur=0.12,
                         seuil=0.10, mesures=25) == (
        "Version v2.0.0 retirée : 12 % des analyses en échec (maximum toléré 10 %).")
    assert resume_metier("rollback", version="v2.0.0", signal="latence_p95_ms", valeur=9100,
                         seuil=8000, mesures=25) == (
        "Version v2.0.0 retirée : analyses trop lentes (P95 9,1 s, maximum 8,0 s).")


def test_canary_promotion_alerte():
    assert resume_metier("canary", version="v2.0.0", pourcentage=50, requetes=23,
                         depuis_s=64.2) == (
        "Version v2.0.0 étendue à 50 % des clients : 23 analyses conformes en 64 s.")
    assert resume_metier("promotion", version="v2.0.0") == (
        "Version v2.0.0 servie à tous les clients : tous les critères tenus.")
    assert resume_metier("alerte", version="v2.0.0", valeur=0.72, seuil=0.70) == (
        "Version v2.0.0 à surveiller : score moyen 0,72, proche du seuil 0,70.")


def test_refus_enrichissement():
    assert resume_metier("pilotage_refus", action="rollback", raison="rien à annuler") == (
        "Action automatique « rollback » impossible : rien à annuler.")
    assert resume_metier("enrichissement", contrat_id="c13", score=0.48) == (
        "Contrat c13 ajouté au jeu d'évaluation (score en production 0,48).")


def test_seuils():
    assert resume_metier("seuils", fichier="ops/seuils_pilotage.yaml", avant=None,
                         apres={"motif": "m"}, motif="m") == (
        "Seuils en vigueur (ops/seuils_pilotage.yaml) : m.")
    assert resume_metier(
        "seuils", fichier="ops/seuils_pilotage.yaml",
        avant={"derive": {"score_min": 0.7}, "motif": "a"},
        apres={"derive": {"score_min": 0.68}, "motif": "b"}, motif="b",
    ) == "Seuils modifiés (ops/seuils_pilotage.yaml) : derive.score_min 0,70 → 0,68 (motif : b)."
    assert resume_metier("seuils_invalides", fichier="f.yaml", raison="YAML invalide") == (
        "Seuils illisibles (f.yaml) : derniers seuils valides conservés — YAML invalide.")


def test_evenement_inconnu():
    with pytest.raises(ValueError, match="sans gabarit"):
        resume_metier("inconnu")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_pilotage.py tests/unit/test_resume_metier.py -v`
Expected: FAIL — `ImportError: cannot import name 'changements_seuils'` / `'resume_metier'`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/pilotage.py`, ajouter aux imports `from pathlib import Path` et remplacer la ligne `from ops.seuils import …` par :

```python
from ops.seuils import SeuilsDerive, SeuilsPilotage, empreinte, lire_brut
```

Puis ajouter en fin de fichier :

```python
# ------------------------------------------------------------------- seuils
def changements_seuils(
    journal: list[dict[str, Any]], fichiers: dict[str, Path]
) -> list[dict[str, Any]]:
    """Un événement par fichier dont l'empreinte diffère de sa dernière entrée
    ``seuils`` (état initial journalisé au premier passage : ``avant: None``).
    Lève ``ErreurSeuilsPilotage`` si un fichier est absent ou illisible."""
    evenements = []
    for nom, chemin in fichiers.items():
        courante = empreinte(chemin)
        derniere = next(
            (e for e in reversed(journal)
             if e.get("evenement") == "seuils" and e.get("fichier") == nom),
            None,
        )
        if derniere is not None and derniere.get("empreinte") == courante:
            continue
        valeurs = lire_brut(chemin)
        evenements.append(
            {
                "fichier": nom,
                "empreinte": courante,
                "avant": derniere.get("apres") if derniere else None,
                "apres": valeurs,
                "motif": str(valeurs.get("motif", "")),
            }
        )
    return evenements


# -------------------------------------------------------- résumés métier
def _fr(valeur: float, decimales: int = 2) -> str:
    return f"{valeur:.{decimales}f}".replace(".", ",")


def _pct(valeur: float) -> str:
    return f"{valeur * 100:.0f} %"


def _valeur_fr(v: Any) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, int) or float(v).is_integer():
        return str(int(v))
    return _fr(v)


def _aplatir(d: dict[str, Any] | None, prefixe: str = "") -> dict[str, Any]:
    plat: dict[str, Any] = {}
    for cle, v in (d or {}).items():
        if isinstance(v, dict):
            plat.update(_aplatir(v, f"{prefixe}{cle}."))
        else:
            plat[f"{prefixe}{cle}"] = v
    return plat


def _differences(avant: dict[str, Any], apres: dict[str, Any]) -> list[str]:
    a, b = _aplatir(avant), _aplatir(apres)
    return [
        f"{cle} {_valeur_fr(a.get(cle))} → {_valeur_fr(b.get(cle))}"
        for cle in sorted(set(a) | set(b))
        if cle != "motif" and a.get(cle) != b.get(cle)
    ]


def _resume_rollback(d: dict[str, Any]) -> str:
    version, signal = d["version"], d["signal"]
    if signal == "score_moyen":
        return (f"Version {version} retirée : {d.get('sous_seuil', 0)} analyses sur "
                f"{d['mesures']} jugées peu fiables (score < {_fr(d['seuil'])}).")
    if signal == "taux_erreur":
        return (f"Version {version} retirée : {_pct(d['valeur'])} des analyses en échec "
                f"(maximum toléré {_pct(d['seuil'])}).")
    return (f"Version {version} retirée : analyses trop lentes "
            f"(P95 {_fr(d['valeur'] / 1000, 1)} s, maximum {_fr(d['seuil'] / 1000, 1)} s).")


def _resume_seuils(d: dict[str, Any]) -> str:
    if d.get("avant") is None:
        return f"Seuils en vigueur ({d['fichier']}) : {d['motif']}."
    diffs = ", ".join(_differences(d["avant"], d["apres"])) or "motif seul"
    return f"Seuils modifiés ({d['fichier']}) : {diffs} (motif : {d['motif']})."


_GABARITS = {
    "rollback": _resume_rollback,
    "canary": lambda d: (
        f"Version {d['version']} étendue à {d['pourcentage']} % des clients : "
        f"{d['requetes']} analyses conformes en {d['depuis_s']:.0f} s."
    ),
    "promotion": lambda d: (
        f"Version {d['version']} servie à tous les clients : tous les critères tenus."
    ),
    "alerte": lambda d: (
        f"Version {d['version']} à surveiller : score moyen {_fr(d['valeur'])}, "
        f"proche du seuil {_fr(d['seuil'])}."
    ),
    "pilotage_refus": lambda d: (
        f"Action automatique « {d['action']} » impossible : {d['raison']}."
    ),
    "enrichissement": lambda d: (
        f"Contrat {d['contrat_id']} ajouté au jeu d'évaluation "
        f"(score en production {_fr(d['score'])})."
    ),
    "seuils": _resume_seuils,
    "seuils_invalides": lambda d: (
        f"Seuils illisibles ({d['fichier']}) : derniers seuils valides conservés — "
        f"{d['raison']}."
    ),
}


def resume_metier(evenement: str, **details: Any) -> str:
    """La phrase lisible par un non-technicien (CTO, client) en cas d'audit."""
    gabarit = _GABARITS.get(evenement)
    if gabarit is None:
        raise ValueError(f"événement sans gabarit de résumé : {evenement}")
    return gabarit(details)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_pilotage.py tests/unit/test_resume_metier.py -v && uv run ruff check ops/ tests/unit/`
Expected: PASS (17 + 6 tests) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/pilotage.py tests/unit/test_pilotage.py tests/unit/test_resume_metier.py
git commit -m "feat(pilotage): changements de seuils tracés et résumés en langage métier

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Anonymisation — `app/capture.py` (1/2)

**Files:**
- Create: `app/capture.py`
- Test: `tests/unit/test_anonymisation.py`

**Interfaces:**
- Consumes: rien.
- Produces: `anonymiser(texte: str) -> str` — remplace par `[EMAIL]`, `[IBAN]`, `[TELEPHONE]`, `[SIRET]`, `[PERSONNE]`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_anonymisation.py` :

```python
"""Anonymisation des cas capturés avant tout stockage (B2.6)."""
from __future__ import annotations

import pytest

from app.capture import anonymiser


@pytest.mark.parametrize(
    "texte, attendu",
    [
        ("Contact : jean.dupont@acme-conseil.fr.", "Contact : [EMAIL]."),
        ("IBAN FR76 3000 6000 0112 3456 7890 189 ;", "IBAN [IBAN] ;"),
        ("Tél. 01 23 45 67 89 ou +33 6 12 34 56 78.", "Tél. [TELEPHONE] ou [TELEPHONE]."),
        ("SIRET 732 829 320 00074, SIREN 732829320.", "SIRET [SIRET], SIREN [SIRET]."),
        ("représentée par M. Jean Dupont, gérant", "représentée par [PERSONNE], gérant"),
        ("et Mme Claire Martin-Durand.", "et [PERSONNE]."),
        ("Maître : Me Lefèvre.", "Maître : [PERSONNE]."),
    ],
)
def test_motifs_masques(texte, attendu):
    assert anonymiser(texte) == attendu


def test_clauses_juridiques_intactes():
    texte = (
        "Article 3 — Durée. Le présent contrat est conclu pour une durée de 12 mois. "
        "Article 4 — Prix. Le prix est de 15 000 € HT, payable à 30 jours. "
        "Article 9 — Droit applicable. Le droit français est seul applicable."
    )
    assert anonymiser(texte) == texte
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_anonymisation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.capture'`

- [ ] **Step 3: Write minimal implementation**

`app/capture.py` :

```python
"""Capture des analyses à faible confiance — boucle d'enrichissement (C2.6, B2.6).

Une analyse v2 servie dont la ``confiance_globale`` est sous
``capture.score_max`` (``ops/seuils_pilotage.yaml``) est anonymisée puis
ajoutée à ``eval/candidats.jsonl`` (hors git). Un humain la verse ensuite dans
le jeu d'évaluation (``python -m eval.enrichir verser``).

La capture tourne en tâche de fond : elle n'affecte jamais la réponse client.
Ce module n'importe pas ``app.api_v2`` (qui l'importe).
"""
from __future__ import annotations

import re

_NOM = r"[A-ZÀ-Ý][\w'-]+"
_MOTIFS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"\bFR\d{2}(?:\s?[A-Z0-9]{4}){5}\s?[A-Z0-9]{3}\b"), "[IBAN]"),
    (re.compile(r"(?:\+33\s?|\b0)[1-9](?:[\s.-]?\d{2}){4}\b"), "[TELEPHONE]"),
    (re.compile(r"\b\d{3}\s?\d{3}\s?\d{3}(?:\s?\d{5})?\b"), "[SIRET]"),
    (re.compile(rf"\b(?:M\.|Mme|Monsieur|Madame|Me)\s+{_NOM}(?:\s+{_NOM})*"), "[PERSONNE]"),
)


def anonymiser(texte: str) -> str:
    """Masque e-mails, IBAN, téléphones, SIRET/SIREN et personnes (civilité + nom).

    Les raisons sociales et les adresses ne sont pas masquées : le juriste le
    vérifie avant tout versement dans le jeu d'évaluation.
    """
    for motif, remplacement in _MOTIFS:
        texte = motif.sub(remplacement, texte)
    return texte
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_anonymisation.py -v && uv run ruff check app/capture.py tests/unit/test_anonymisation.py`
Expected: PASS (8 tests) ; ruff propre. Si un cas échoue, ajuster l'expression régulière concernée — pas le test.

- [ ] **Step 5: Commit**

```bash
git add app/capture.py tests/unit/test_anonymisation.py
git commit -m "feat(capture): anonymisation des contrats capturés (e-mail, IBAN, tél., SIRET, personnes)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Capture sur `/v2/analyse` — `app/capture.py` (2/2)

**Files:**
- Modify: `app/capture.py` (fonctions après `anonymiser`)
- Modify: `app/api_v2.py` (import `BackgroundTasks`, `Capture`, `capturer`, `get_capture` ; route `analyse`)
- Modify: `tests/conftest.py` (une ligne dans `environnement`)
- Modify: `.gitignore`
- Test: `tests/integration/test_capture.py`

**Interfaces:**
- Consumes: `anonymiser` (tâche 7) ; `charger_seuils_pilotage`, `ErreurSeuilsPilotage` (tâche 1).
- Produces: `CHEMIN_CANDIDATS_DEFAUT` ; `chemin_candidats() -> Path` (lit `CANDIDATS_PATH`) ; `@dataclass(frozen=True) Capture(chemin: Path, score_max: float | None = None)` ; `get_capture() -> Capture` (dépendance FastAPI) ; `capturer(capture: Capture, texte: str, reponse: ReponseScoree) -> str | None` (id du candidat ou `None`) ; `lire_candidats(chemin: Path | None = None) -> tuple[dict[str, dict], dict[str, dict]]` (candidats par id, versements par id) ; `candidats_en_attente(chemin: Path | None = None) -> list[dict]`. Ligne candidat : `{"type": "candidat", "id": "cand-<10 hex>", "date", "version", "score", "texte", "clauses_trouvees", "empreinte"}`.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_capture.py` :

```python
"""Capture des analyses v2 à faible confiance, en tâche de fond."""
from __future__ import annotations

import json

from app.capture import Capture, candidats_en_attente, get_capture


def _brancher(client, capture: Capture) -> None:
    client.app.dependency_overrides[get_capture] = lambda: capture


def _lignes(chemin):
    return [json.loads(x) for x in chemin.read_text(encoding="utf-8").splitlines()]


def test_score_bas_capture_un_candidat_anonymise(client, contrat, tmp_path):
    chemin = tmp_path / "cand.jsonl"
    _brancher(client, Capture(chemin, score_max=1.01))   # tout est « bas »
    texte = contrat("c02") + "\nContact : juriste@client.fr"
    r = client.post("/v2/analyse", json={"texte": texte})
    assert r.status_code == 200
    [ligne] = _lignes(chemin)
    assert ligne["type"] == "candidat" and ligne["id"].startswith("cand-")
    assert ligne["version"] == r.json()["version"]
    assert ligne["score"] == r.json()["confiance_globale"]
    assert "[EMAIL]" in ligne["texte"] and "juriste@client.fr" not in ligne["texte"]
    assert ligne["clauses_trouvees"] == sorted({c["type"] for c in r.json()["clauses"]})
    assert [c["id"] for c in candidats_en_attente(chemin)] == [ligne["id"]]


def test_score_haut_ne_capture_rien(client, contrat, tmp_path):
    chemin = tmp_path / "cand.jsonl"
    _brancher(client, Capture(chemin, score_max=0.0))
    assert client.post("/v2/analyse", json={"texte": contrat("c02")}).status_code == 200
    assert not chemin.exists()


def test_meme_texte_capture_une_seule_fois(client, contrat, tmp_path):
    chemin = tmp_path / "cand.jsonl"
    _brancher(client, Capture(chemin, score_max=1.01))
    for _ in range(2):
        client.post("/v2/analyse", json={"texte": contrat("c02")})
    assert len(_lignes(chemin)) == 1


def test_echec_de_capture_sans_effet_sur_le_client(client, contrat, tmp_path):
    _brancher(client, Capture(tmp_path, score_max=1.01))   # un dossier : OSError
    r = client.post("/v2/analyse", json={"texte": contrat("c02")})
    assert r.status_code == 200 and "confiance_globale" in r.json()


def test_seuil_lu_dans_les_seuils_de_pilotage(client, contrat, tmp_path, monkeypatch):
    chemin = tmp_path / "cand.jsonl"
    seuils = tmp_path / "seuils.yaml"
    from ops.seuils import CHEMIN_SEUILS_PILOTAGE_DEFAUT
    seuils.write_text(
        CHEMIN_SEUILS_PILOTAGE_DEFAUT.read_text(encoding="utf-8").replace(
            "score_max: 0.70", "score_max: 1.01"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(seuils))
    _brancher(client, Capture(chemin))            # score_max=None → lu dans le fichier
    client.post("/v2/analyse", json={"texte": contrat("c02")})
    assert len(_lignes(chemin)) == 1


def test_le_gate_ne_capture_rien(tmp_path, monkeypatch, historique):
    from eval.run_eval import evaluer

    chemin = tmp_path / "cand.jsonl"
    monkeypatch.setenv("CANDIDATS_PATH", str(chemin))
    evaluer("v2", sous_ensemble=["c01"], historique=historique)
    assert not chemin.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_capture.py -v`
Expected: FAIL — `ImportError: cannot import name 'Capture' from 'app.capture'`

- [ ] **Step 3: Write minimal implementation**

Dans `app/capture.py`, remplacer le bloc d'imports par :

```python
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Protocol

import structlog

from ops.seuils import ErreurSeuilsPilotage, charger_seuils_pilotage

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_CANDIDATS_DEFAUT = RACINE / "eval" / "candidats.jsonl"
```

Puis ajouter après `anonymiser` :

```python
# ------------------------------------------------------------------ capture
class _Clause(Protocol):
    type: str


class ReponseScoree(Protocol):
    """Ce que la capture lit d'une réponse v2 (``ReponseAnalyseV2`` convient)."""

    confiance_globale: float
    version: str
    clauses: Sequence[_Clause]


def chemin_candidats() -> Path:
    """``CANDIDATS_PATH`` si défini, sinon ``eval/candidats.jsonl``."""
    return Path(os.environ.get("CANDIDATS_PATH") or CHEMIN_CANDIDATS_DEFAUT)


@dataclass(frozen=True)
class Capture:
    """Où capturer et sous quel score ; ``score_max=None`` → lu dans les seuils
    de pilotage au moment de la capture (jamais à la résolution de la dépendance :
    un fichier de seuils cassé ne doit pas faire échouer la requête)."""

    chemin: Path
    score_max: float | None = None


def get_capture() -> Capture:
    return Capture(chemin=chemin_candidats())


@contextmanager
def _verrouille(chemin: Path) -> Iterator[IO[str]]:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("a+", encoding="utf-8") as fichier:
        fcntl.flock(fichier, fcntl.LOCK_EX)
        try:
            yield fichier
        finally:
            fcntl.flock(fichier, fcntl.LOCK_UN)


def _lignes(fichier: IO[str]) -> Iterator[dict[str, Any]]:
    for ligne in fichier:
        ligne = ligne.strip()
        if ligne:
            try:
                yield json.loads(ligne)
            except json.JSONDecodeError:
                continue


def capturer(capture: Capture, texte: str, reponse: ReponseScoree) -> str | None:
    """Ajoute un candidat si le score est bas ; renvoie son id (``None`` sinon).

    Dédoublonnage sur l'empreinte du texte **brut**. Toute erreur d'écriture ou
    de seuils est journalisée (``capture.echec``) et avalée.
    """
    try:
        score_max = capture.score_max
        if score_max is None:
            score_max = charger_seuils_pilotage().capture.score_max
        if reponse.confiance_globale >= score_max:
            return None
        empreinte = hashlib.sha256(texte.encode("utf-8")).hexdigest()
        identifiant = f"cand-{empreinte[:10]}"
        with _verrouille(capture.chemin) as fichier:
            fichier.seek(0)
            if any(e.get("empreinte") == empreinte for e in _lignes(fichier)):
                return None
            fichier.seek(0, os.SEEK_END)
            ligne = {
                "type": "candidat",
                "id": identifiant,
                "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "version": reponse.version,
                "score": reponse.confiance_globale,
                "texte": anonymiser(texte),
                "clauses_trouvees": sorted({c.type for c in reponse.clauses}),
                "empreinte": empreinte,
            }
            fichier.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        return identifiant
    except (OSError, ErreurSeuilsPilotage) as exc:
        structlog.get_logger("mardik").warning("capture.echec", cause=str(exc))
        return None


def lire_candidats(
    chemin: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Rejoue ``candidats.jsonl`` (append-only) : (candidats par id, versements par id)."""
    chemin = chemin or chemin_candidats()
    candidats: dict[str, dict[str, Any]] = {}
    verses: dict[str, dict[str, Any]] = {}
    if not chemin.exists():
        return candidats, verses
    with chemin.open(encoding="utf-8") as fichier:
        for entree in _lignes(fichier):
            if entree.get("type") == "candidat":
                candidats[entree["id"]] = entree
            elif entree.get("type") == "verse":
                verses[entree["id"]] = entree
    return candidats, verses


def candidats_en_attente(chemin: Path | None = None) -> list[dict[str, Any]]:
    candidats, verses = lire_candidats(chemin)
    return [c for identifiant, c in candidats.items() if identifiant not in verses]
```

Dans `app/api_v2.py`, remplacer la ligne `from fastapi import APIRouter, Depends, HTTPException` par :

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
```

ajouter après `from app.llm_client import …` :

```python
from app.capture import Capture, capturer, get_capture
```

et remplacer la route `analyse` (fin du fichier) par :

```python
@router.post("/analyse", response_model=ReponseAnalyseV2)
def analyse(
    requete: RequeteAnalyseV2,
    taches: BackgroundTasks,
    client: LLMClient = Depends(get_client_v2),
    telemetry: Telemetry = Depends(get_telemetry),
    capture: Capture = Depends(get_capture),
) -> ReponseAnalyseV2:
    try:
        reponse = analyser_v2(requete.texte, client, telemetry)
    except ErreurLLM as exc:
        raise HTTPException(
            status_code=503, detail=f"fournisseur LLM indisponible : {exc}"
        ) from exc
    # Capture des cas à faible confiance (boucle d'enrichissement), après la
    # réponse ; jamais dans ``analyser_v2``, que le gate appelle aussi.
    taches.add_task(capturer, capture, requete.texte, reponse)
    return reponse
```

Dans `tests/conftest.py`, fixture `environnement`, ajouter sous la ligne `REGISTRY_PATH` :

```python
    monkeypatch.setenv("CANDIDATS_PATH", str(tmp_path / "candidats.jsonl"))
```

Dans `.gitignore`, ajouter sous `ops/metrics.jsonl` :

```
eval/candidats.jsonl
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_capture.py -v && MOCK=on uv run pytest -q && uv run ruff check .`
Expected: `test_capture.py` PASS (6 tests) ; suite complète : aucune régression (seuls les deux tests d'acceptance du sous-projet 4 restent rouges) ; ruff propre. Vérifier aussi `git status` : aucun `eval/candidats.jsonl` créé dans le dépôt.

- [ ] **Step 5: Commit**

```bash
git add app/capture.py app/api_v2.py tests/conftest.py .gitignore tests/integration/test_capture.py
git commit -m "feat(capture): capture des analyses v2 à faible confiance en tâche de fond

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Versement dans le jeu d'évaluation — `eval/enrichir.py`

**Files:**
- Create: `eval/enrichir.py`
- Test: `tests/integration/test_enrichir.py`

**Interfaces:**
- Consumes: `lire_candidats`, `candidats_en_attente`, `chemin_candidats` (tâche 8) ; `resume_metier` (tâche 6) ; `eval.run_eval.charger_attendus`, `DOSSIER_CONTRATS`, `CHEMIN_ATTENDUS` ; `app.llm_client.TYPES_CLAUSES` ; `ops.registry.Registry`.
- Produces: `class ErreurEnrichissement(ValueError)` ; `prochain_id(identifiants) -> str` ; `verser(id_candidat: str, clauses: list[str], *, seuil_note=0.75, candidats=None, contrats=DOSSIER_CONTRATS, attendus=CHEMIN_ATTENDUS, registry=None) -> dict` (la ligne ajoutée à `attendus.jsonl`) ; `main(argv=None) -> int` (sous-commandes `lister`, `verser`).

- [ ] **Step 1: Write the failing test**

`tests/integration/test_enrichir.py` :

```python
"""Versement humain d'un cas capturé dans le jeu d'évaluation (C2.7, B2.4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.enrichir import ErreurEnrichissement, main, prochain_id, verser

RACINE = Path(__file__).resolve().parents[2]


@pytest.fixture
def attendus(tmp_path) -> Path:
    lignes = (RACINE / "eval" / "attendus.jsonl").read_text(encoding="utf-8").splitlines()[:4]
    chemin = tmp_path / "attendus.jsonl"
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return chemin


@pytest.fixture
def candidats(tmp_path) -> Path:
    chemin = tmp_path / "candidats.jsonl"
    ligne = {"type": "candidat", "id": "cand-0123456789", "date": "2026-09-22T10:00:00+00:00",
             "version": "v2.0.0", "score": 0.48, "empreinte": "e",
             "texte": "Article 1 — Durée. Le contrat est conclu pour une durée de 12 mois. "
                      "Article 2 — Résiliation. Chaque partie peut résilier le contrat.",
             "clauses_trouvees": ["durée"]}
    chemin.write_text(json.dumps(ligne, ensure_ascii=False) + "\n", encoding="utf-8")
    return chemin


def _verser(candidats, contrats_courts, attendus, registry, **kw):
    return verser("cand-0123456789", kw.pop("clauses", ["durée", "résiliation"]),
                  candidats=candidats, contrats=contrats_courts, attendus=attendus,
                  registry=registry, **kw)


def test_prochain_id():
    assert prochain_id(["c01", "c09", "c12"]) == "c13"
    assert prochain_id([]) == "c01"


def test_verser_cree_le_contrat_annote(candidats, contrats_courts, attendus, registry):
    ligne = _verser(candidats, contrats_courts, attendus, registry)
    assert ligne["contrat_id"] == "c05" and ligne["origine"] == "production"
    assert ligne["clauses_attendues"] == ["durée", "résiliation"]
    assert (contrats_courts / "c05.txt").read_text(encoding="utf-8").startswith("Article 1")
    derniere = json.loads(attendus.read_text(encoding="utf-8").splitlines()[-1])
    assert derniere == ligne
    verse = json.loads(candidats.read_text(encoding="utf-8").splitlines()[-1])
    assert verse["type"] == "verse" and verse["contrat_id"] == "c05"
    entree = registry.journal()[-1]
    assert entree["evenement"] == "enrichissement" and entree["origine"] == "manuel"
    assert entree["contrat_id"] == "c05" and "c05" in entree["resume"]


@pytest.mark.parametrize(
    "identifiant, clauses, message",
    [
        ("cand-inconnu", ["durée"], "inconnu"),
        ("cand-0123456789", [], "au moins une clause"),
        ("cand-0123456789", ["durée", "clause magique"], "clause magique"),
    ],
)
def test_refus_sans_ecriture(candidats, contrats_courts, attendus, registry,
                             identifiant, clauses, message):
    avant = attendus.read_text(encoding="utf-8")
    with pytest.raises(ErreurEnrichissement, match=message):
        verser(identifiant, clauses, candidats=candidats, contrats=contrats_courts,
               attendus=attendus, registry=registry)
    assert attendus.read_text(encoding="utf-8") == avant
    assert not (contrats_courts / "c05.txt").exists()


def test_deja_verse(candidats, contrats_courts, attendus, registry):
    _verser(candidats, contrats_courts, attendus, registry)
    with pytest.raises(ErreurEnrichissement, match="déjà versé"):
        _verser(candidats, contrats_courts, attendus, registry)


def test_le_gate_rejoue_le_contrat_verse(candidats, contrats_courts, attendus, registry,
                                         historique):
    from eval.run_eval import evaluer

    _verser(candidats, contrats_courts, attendus, registry)
    rapport = evaluer("v2", contrats=contrats_courts, attendus=attendus, historique=historique)
    assert "c05" in rapport.par_contrat


def test_cli(candidats, capsys, monkeypatch):
    monkeypatch.setenv("CANDIDATS_PATH", str(candidats))
    assert main(["lister"]) == 0
    assert "cand-0123456789" in capsys.readouterr().out
    assert main(["verser", "cand-0123456789", "--clauses", ""]) == 1
    assert "REFUSÉ" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_enrichir.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.enrichir'`

- [ ] **Step 3: Write minimal implementation**

`eval/enrichir.py` :

```python
"""Enrichissement du jeu d'évaluation — versement humain des cas capturés.

    python -m eval.enrichir lister
    python -m eval.enrichir verser cand-0123456789 --clauses "durée,résiliation"

``verser`` crée ``eval/contrats/cNN.txt`` (texte déjà anonymisé) et ajoute sa
ligne à ``eval/attendus.jsonl`` ; le gate le rejoue à la fusion suivante. Les
clauses attendues sont celles que le juriste valide (B2.4), jamais celles que
la v2 a trouvées (ce serait évaluer le modèle sur ses propres sorties).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.capture import candidats_en_attente, chemin_candidats, lire_candidats
from app.llm_client import TYPES_CLAUSES
from eval.run_eval import CHEMIN_ATTENDUS, DOSSIER_CONTRATS, charger_attendus
from ops.pilotage import resume_metier
from ops.registry import Registry


class ErreurEnrichissement(ValueError):
    """Versement refusé : rien n'a été écrit."""


def prochain_id(identifiants: Iterable[str]) -> str:
    numeros = [int(m.group(1)) for i in identifiants if (m := re.fullmatch(r"c(\d+)", i))]
    return f"c{max(numeros, default=0) + 1:02d}"


def _ajouter_ligne(chemin: Path, data: dict[str, Any]) -> None:
    contenu = chemin.read_text(encoding="utf-8") if chemin.exists() else ""
    separateur = "" if not contenu or contenu.endswith("\n") else "\n"
    with chemin.open("a", encoding="utf-8") as f:
        f.write(separateur + json.dumps(data, ensure_ascii=False) + "\n")


def verser(
    id_candidat: str,
    clauses: list[str],
    *,
    seuil_note: float = 0.75,
    candidats: Path | None = None,
    contrats: Path = DOSSIER_CONTRATS,
    attendus: Path = CHEMIN_ATTENDUS,
    registry: Registry | None = None,
) -> dict[str, Any]:
    chemin = candidats or chemin_candidats()
    tous, verses = lire_candidats(chemin)
    if id_candidat not in tous:
        raise ErreurEnrichissement(f"candidat inconnu : {id_candidat}")
    if id_candidat in verses:
        raise ErreurEnrichissement(
            f"candidat déjà versé : {id_candidat} ({verses[id_candidat]['contrat_id']})"
        )
    clauses = [c.strip() for c in clauses if c.strip()]
    if not clauses:
        raise ErreurEnrichissement("au moins une clause attendue est requise (--clauses)")
    inconnues = [c for c in clauses if c not in TYPES_CLAUSES]
    if inconnues:
        raise ErreurEnrichissement(
            f"type(s) de clause inconnu(s) : {', '.join(inconnues)} "
            "— voir app/llm_client.py::TYPES_CLAUSES"
        )
    candidat = tous[id_candidat]
    contrat_id = prochain_id(charger_attendus(Path(attendus)))
    fichier = Path(contrats) / f"{contrat_id}.txt"
    if fichier.exists():
        raise ErreurEnrichissement(f"le contrat {fichier} existe déjà")

    texte = candidat["texte"]
    ligne = {
        "contrat_id": contrat_id,
        "pages": max(1, round(len(texte) / 3000)),
        "clauses_attendues": clauses,
        "seuil_note": seuil_note,
        "origine": "production",
    }
    fichier.write_text(texte, encoding="utf-8")
    _ajouter_ligne(Path(attendus), ligne)
    _ajouter_ligne(chemin, {
        "type": "verse",
        "id": id_candidat,
        "contrat_id": contrat_id,
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    (registry or Registry()).journaliser(
        "enrichissement",
        origine="manuel",
        candidat=id_candidat,
        contrat_id=contrat_id,
        score=candidat["score"],
        resume=resume_metier("enrichissement", contrat_id=contrat_id, score=candidat["score"]),
    )
    return ligne


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrichissement du jeu d'évaluation Mardik")
    sub = parser.add_subparsers(dest="commande", required=True)
    sub.add_parser("lister", help="candidats en attente de versement")
    v = sub.add_parser("verser", help="verse un candidat dans le jeu d'évaluation")
    v.add_argument("id")
    v.add_argument("--clauses", required=True, help="types attendus, séparés par des virgules")
    v.add_argument("--seuil-note", type=float, default=0.75)
    args = parser.parse_args(argv)

    if args.commande == "lister":
        attente = candidats_en_attente()
        if not attente:
            print("aucun candidat en attente")
        for c in attente:
            apercu = c["texte"][:200].replace("\n", " ")
            print(f"{c['id']}  {c['date']}  {c['version']}  score {c['score']:.2f}  "
                  f"trouvées : {', '.join(c['clauses_trouvees']) or '—'}\n    {apercu}…")
        return 0
    try:
        ligne = verser(args.id, args.clauses.split(","), seuil_note=args.seuil_note)
    except ErreurEnrichissement as exc:
        print(f"REFUSÉ : {exc}", file=sys.stderr)
        return 1
    print(json.dumps(ligne, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_enrichir.py -v && uv run ruff check eval/enrichir.py tests/integration/test_enrichir.py`
Expected: PASS (8 tests) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add eval/enrichir.py tests/integration/test_enrichir.py
git commit -m "feat(enrichir): versement humain d'un cas capturé dans le jeu d'évaluation

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Tableau de bord — `resume` et `rendre_texte`

**Files:**
- Modify: `ops/dashboard.py` (imports ; `resume`, `rendre_texte`)
- Test: `tests/integration/test_dashboard.py` ; acceptance `tests/acceptance/test_observabilite.py::test_dashboard_par_version` (non modifié)

**Interfaces:**
- Consumes: `agreger`, `filtrer`, `histogramme`, `scores`, `serie_par_minute` (tâche 2) ; `charger_seuils_pilotage`, `ErreurSeuilsPilotage`, `SeuilsPilotage` (tâche 1) ; `debut_palier`, `detecter_derive`, `version_surveillee` (tâches 4–5) ; `candidats_en_attente` (tâche 8).
- Produces: `resume(metriques=None, *, fenetre_s=300, registry=None, seuils=None, candidats=None, maintenant=None) -> dict` (clés `fenetre_s`, `total`, `par_version`, `palier`, `alertes`, `candidats`, `journal`) ; `rendre_texte(r: dict) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_dashboard.py` :

```python
"""Tableau de bord : résumé enrichi, rendus texte et HTML."""
from __future__ import annotations

import json
import time

from app.llm_client import Bundle
from app.telemetry import Mesure
from ops.dashboard import rendre_texte, resume


def _mesures(metriques, version, n, *, score=0.9, ts=None, latence=2000.0):
    for _ in range(n):
        metriques.enregistrer(Mesure(ts=ts or time.time(), version=version, route="/analyse",
                                     latence_ms=latence, score=score, cout_eur=0.02))


def _canary(registry, pourcentage=10):
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    registry.definir_canary("v2.0.0", pourcentage)
    return registry.journaliser("canary", version="v2.0.0", pourcentage=pourcentage,
                                origine="manuel")


def test_resume_complet(metriques, registry, tmp_path):
    entree = _canary(registry)
    _mesures(metriques, "v1.0.0", 30, score=None, latence=900)
    _mesures(metriques, "v2.0.0", 12, score=0.72)
    candidats = tmp_path / "cand.jsonl"
    candidats.write_text("\n".join(json.dumps(x) for x in [
        {"type": "candidat", "id": "a"}, {"type": "candidat", "id": "b"},
        {"type": "verse", "id": "a", "contrat_id": "c13"},
    ]) + "\n", encoding="utf-8")

    r = resume(metriques, registry=registry, candidats=candidats,
               maintenant=entree["ts"] + 42)
    v2 = r["par_version"]["v2.0.0"]
    assert v2["score_p10"] == 0.72 and sum(v2["histogramme_score"]) == 12
    assert v2["serie_minute"] and v2["cout_total_eur"] > 0
    assert r["palier"] == {"version": "v2.0.0", "pourcentage": 10, "depuis_s": 42.0,
                           "requetes": 12, "duree_min_s": 60, "requetes_min": 20}
    assert r["alertes"] == ["v2.0.0 : score moyen 0.72 dans la marge [0.70 ; 0.75["]
    assert r["candidats"] == 1
    assert r["journal"][-1]["evenement"] == "canary"


def test_sans_canary_ni_trafic(metriques, registry, tmp_path):
    r = resume(metriques, registry=registry, candidats=tmp_path / "absent.jsonl")
    assert r["total"] == 0 and r["par_version"] == {} and r["palier"] is None
    assert r["alertes"] == [] and r["candidats"] == 0
    assert "aucun trafic dans la fenêtre" in rendre_texte(r)


def test_seuils_invalides_signales_sans_bloquer(metriques, registry, monkeypatch, tmp_path):
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(tmp_path / "absent.yaml"))
    _mesures(metriques, "v1.0.0", 3, score=None)
    r = resume(metriques, registry=registry)
    assert r["par_version"]["v1.0.0"]["requetes"] == 3
    assert r["alertes"][0].startswith("seuils invalides")


def test_rendre_texte(metriques, registry):
    _canary(registry)
    _mesures(metriques, "v2.0.0", 12, score=0.72)
    texte = rendre_texte(resume(metriques, registry=registry))
    assert "v2.0.0" in texte and "palier : v2.0.0 à 10 %" in texte
    assert "alertes :" in texte and "journal :" in texte
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_dashboard.py tests/acceptance/test_observabilite.py::test_dashboard_par_version -v`
Expected: FAIL — `NotImplementedError: dashboard.resume — agrégats par version sur la fenêtre`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/dashboard.py`, mettre à jour la docstring du module (retirer `[STUB]`, ajouter les clés `palier`, `alertes`, `candidats`, `score_p10`, `histogramme_score`, `serie_minute` au contrat décrit), puis remplacer le bloc d'imports et les fonctions `resume` et `rendre_texte` par :

```python
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from app.capture import candidats_en_attente
from app.telemetry import Mesure, MetricsStore
from ops.pilotage import debut_palier, detecter_derive, version_surveillee
from ops.registry import Registry
from ops.seuils import ErreurSeuilsPilotage, SeuilsPilotage, charger_seuils_pilotage
from ops.signaux import agreger, filtrer, histogramme, scores, serie_par_minute


def _par_version(mesures: list[Mesure]) -> dict[str, dict[str, Any]]:
    total = len(mesures)
    par_version: dict[str, dict[str, Any]] = {}
    for version in sorted({m.version for m in mesures}):
        du = filtrer(mesures, version)
        a = agreger(du)
        par_version[version] = {
            "requetes": a.requetes,
            "trafic_pct": round(100 * a.requetes / total, 1),
            "latence_p50_ms": a.latence_p50_ms,
            "latence_p95_ms": a.latence_p95_ms,
            "taux_erreur": a.taux_erreur,
            "score_moyen": a.score_moyen,
            "score_p10": a.score_p10,
            "cout_total_eur": a.cout_total_eur,
            "histogramme_score": histogramme(scores(du)),
            "serie_minute": serie_par_minute(du),
        }
    return par_version


def _palier(
    index: dict[str, Any],
    journal: list[dict[str, Any]],
    mesures: list[Mesure],
    seuils: SeuilsPilotage | None,
    maintenant: float,
) -> dict[str, Any] | None:
    canary = index.get("canary")
    if canary is None:
        return None
    debut = debut_palier(journal, canary)
    return {
        "version": canary,
        "pourcentage": index.get("canary_percent"),
        "depuis_s": round(maintenant - debut, 1) if debut is not None else None,
        "requetes": len(filtrer(mesures, canary, depuis_ts=debut)),
        "duree_min_s": seuils.promotion.duree_min_s if seuils else None,
        "requetes_min": seuils.promotion.requetes_min if seuils else None,
    }


def resume(
    metriques: MetricsStore | None = None,
    *,
    fenetre_s: float = 300,
    registry: Registry | None = None,
    seuils: SeuilsPilotage | None = None,
    candidats: Path | None = None,
    maintenant: float | None = None,
) -> dict[str, Any]:
    metriques = metriques or MetricsStore()
    registry = registry or Registry()
    maintenant = maintenant if maintenant is not None else time.time()
    mesures = metriques.lire(depuis_s=fenetre_s)
    index, journal = registry.index(), registry.journal()

    alertes: list[str] = []
    if seuils is None:
        try:
            seuils = charger_seuils_pilotage()
        except ErreurSeuilsPilotage as exc:
            alertes.append(f"seuils invalides : {exc}")
    surveillee = version_surveillee(index)
    if seuils is not None and surveillee is not None:
        debut = debut_palier(journal, surveillee) if index.get("canary") == surveillee else None
        derive = detecter_derive(
            surveillee, filtrer(mesures, surveillee, depuis_ts=debut), seuils.derive,
            minimum=seuils.minimum,
        )
        if derive.niveau in ("critique", "marge"):
            alertes.append(f"{surveillee} : {derive.motif}")

    return {
        "fenetre_s": fenetre_s,
        "total": len(mesures),
        "par_version": _par_version(mesures),
        "palier": _palier(index, journal, mesures, seuils, maintenant),
        "alertes": alertes,
        "candidats": len(candidats_en_attente(candidats)),
        "journal": journal[-5:],
    }


def _ms(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f} ms"


def _score(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def rendre_texte(r: dict[str, Any]) -> str:
    lignes = [f"== Mardik — fenêtre {r['fenetre_s']:.0f} s — {r['total']} requêtes =="]
    if not r["par_version"]:
        lignes.append("aucun trafic dans la fenêtre")
    for version, s in r["par_version"].items():
        lignes.append(
            f"{version:<10} trafic {s['trafic_pct']:5.1f} %  P50 {_ms(s['latence_p50_ms'])}  "
            f"P95 {_ms(s['latence_p95_ms'])}  erreurs {s['taux_erreur'] * 100:.1f} %  "
            f"score {_score(s['score_moyen'])} (P10 {_score(s['score_p10'])})  "
            f"coût {s['cout_total_eur']:.4f} €"
        )
    p = r.get("palier")
    if p:
        depuis = "?" if p["depuis_s"] is None else f"{p['depuis_s']:.0f}"
        lignes.append(
            f"palier : {p['version']} à {p['pourcentage']} % depuis {depuis} s "
            f"({p['requetes']}/{p['requetes_min']} requêtes)"
        )
    if r.get("alertes"):
        lignes.append("alertes :")
        lignes.extend(f"  ! {a}" for a in r["alertes"])
    lignes.append(f"candidats à verser : {r.get('candidats', 0)}")
    lignes.append("journal :")
    for e in r.get("journal", []):
        texte = e.get("resume") or e.get("motif") or ""
        lignes.append(
            f"  {e.get('date', '')}  {e.get('evenement')}  [{e.get('origine', '-')}]  {texte}"
        )
    return "\n".join(lignes)
```

(`rendre_html` reste le stub jusqu'à la tâche 11, qui ajoute `from html import escape`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_dashboard.py tests/acceptance/test_observabilite.py::test_dashboard_par_version -v && uv run ruff check ops/dashboard.py tests/integration/test_dashboard.py`
Expected: PASS (4 tests + **`test_dashboard_par_version` vert**) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/dashboard.py tests/integration/test_dashboard.py
git commit -m "feat(dashboard): résumé par version, palier, alertes, candidats et rendu texte

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Tableau de bord — page HTML auto-rafraîchie

**Files:**
- Modify: `ops/dashboard.py` (`rendre_html` et helpers SVG)
- Test: `tests/integration/test_dashboard.py` (ajouts)

**Interfaces:**
- Consumes: le dict de `resume` (tâche 10).
- Produces: `rendre_html(r: dict) -> str` — page autonome, `<meta http-equiv="refresh" content="5">`, SVG inline, texte échappé.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/integration/test_dashboard.py` (compléter l'import : `from ops.dashboard import rendre_html, rendre_texte, resume`) :

```python
def test_rendre_html(metriques, registry):
    _canary(registry)
    _mesures(metriques, "v1.0.0", 20, score=None)
    _mesures(metriques, "v2.0.0", 12, score=0.72)
    registry.journaliser("alerte", origine="auto", version="v2.0.0",
                         resume="score <b>bas</b>")
    page = rendre_html(resume(metriques, registry=registry))
    assert page.startswith("<!doctype html>")
    assert '<meta http-equiv="refresh" content="5">' in page
    assert page.count("<svg") >= 3            # jauge + histogrammes
    assert "v1.0.0" in page and "v2.0.0" in page
    assert "score &lt;b&gt;bas&lt;/b&gt;" in page and "<b>bas</b>" not in page
    assert "palier" in page


def test_rendre_html_sans_trafic(metriques, registry, tmp_path):
    page = rendre_html(resume(metriques, registry=registry, candidats=tmp_path / "x"))
    assert "aucun trafic dans la fenêtre" in page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_dashboard.py -v`
Expected: FAIL — `NotImplementedError: dashboard.rendre_html — page HTML auto-rafraîchie`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/dashboard.py`, ajouter `from html import escape` aux imports de la bibliothèque standard, puis remplacer le stub `rendre_html` par :

```python
_COULEURS = ("#4c78a8", "#f58518", "#54a24b", "#b279a2")

_STYLE = """
:root { --fond: #fafafa; --texte: #1d1d1f; --carte: #fff; --trait: #d0d0d5; --alerte: #b42318; }
@media (prefers-color-scheme: dark) {
  :root { --fond: #16161a; --texte: #ececf1; --carte: #202027; --trait: #3a3a44; }
}
body { font: 14px/1.4 system-ui, sans-serif; margin: 0; padding: 16px;
       background: var(--fond); color: var(--texte); }
h1 { font-size: 18px; } h2 { font-size: 15px; margin-top: 24px; }
table { border-collapse: collapse; width: 100%; background: var(--carte); }
th, td { border-bottom: 1px solid var(--trait); padding: 6px 8px; text-align: left; }
svg { display: block; } svg.jauge { width: 100%; max-width: 600px; height: 20px; }
svg.histo, svg.spark { width: 120px; height: 36px; }
svg.histo rect { fill: #4c78a8; } svg.spark polyline { fill: none; stroke: #f58518; }
.alerte { color: var(--alerte); font-weight: 600; } .vide { opacity: .6; }
"""


def _svg_jauge(par_version: dict[str, dict[str, Any]]) -> str:
    x, parts = 0.0, []
    for i, (version, s) in enumerate(par_version.items()):
        largeur = s["trafic_pct"] * 3
        parts.append(
            f'<rect x="{x:.1f}" y="0" width="{largeur:.1f}" height="20" '
            f'fill="{_COULEURS[i % len(_COULEURS)]}"><title>{escape(version)} '
            f'{s["trafic_pct"]} %</title></rect>'
        )
        x += largeur
    return (f'<svg class="jauge" viewBox="0 0 300 20" role="img" '
            f'aria-label="part de trafic par version">{"".join(parts)}</svg>')


def _svg_histogramme(comptes: list[int], largeur: int = 120, hauteur: int = 36) -> str:
    haut = max(comptes) or 1
    pas = largeur / len(comptes)
    rects = "".join(
        f'<rect x="{i * pas:.1f}" y="{hauteur - c / haut * hauteur:.1f}" '
        f'width="{pas - 1:.1f}" height="{c / haut * hauteur:.1f}"/>'
        for i, c in enumerate(comptes)
    )
    return (f'<svg class="histo" viewBox="0 0 {largeur} {hauteur}" role="img" '
            f'aria-label="distribution du score, de 0 à 1">{rects}</svg>')


def _svg_sparkline(valeurs: list[float | None], largeur: int = 120, hauteur: int = 36) -> str:
    points = [v for v in valeurs if v is not None]
    if len(points) < 2:
        return '<span class="vide">—</span>'
    bas, haut = min(points), max(points)
    etendue = (haut - bas) or 1
    pas = largeur / (len(points) - 1)
    coords = " ".join(
        f"{i * pas:.1f},{hauteur - (v - bas) / etendue * hauteur:.1f}"
        for i, v in enumerate(points)
    )
    return (f'<svg class="spark" viewBox="0 0 {largeur} {hauteur}">'
            f'<polyline points="{coords}"/></svg>')


def rendre_html(r: dict[str, Any]) -> str:
    lignes_versions = []
    for version, s in r["par_version"].items():
        serie = s["serie_minute"]
        lignes_versions.append(
            "<tr>"
            f"<td>{escape(version)}</td><td>{s['trafic_pct']} %</td>"
            f"<td>{_ms(s['latence_p50_ms'])} / {_ms(s['latence_p95_ms'])}</td>"
            f"<td>{s['taux_erreur'] * 100:.1f} %</td>"
            f"<td>{_score(s['score_moyen'])} (P10 {_score(s['score_p10'])})</td>"
            f"<td>{_svg_histogramme(s['histogramme_score'])}</td>"
            f"<td>{_svg_sparkline([p['latence_p95_ms'] for p in serie])}</td>"
            f"<td>{_svg_sparkline([p['taux_erreur'] for p in serie])}</td>"
            "</tr>"
        )
    if lignes_versions:
        tableau = (
            "<table><tr><th>Version</th><th>Trafic</th><th>P50 / P95</th><th>Erreurs</th>"
            "<th>Score moyen</th><th>Distribution du score</th><th>P95 / min</th>"
            "<th>Erreurs / min</th></tr>" + "".join(lignes_versions) + "</table>"
        )
    else:
        tableau = '<p class="vide">aucun trafic dans la fenêtre</p>'
    p = r.get("palier")
    palier = (
        f"<p>palier : {escape(p['version'])} à {p['pourcentage']} % depuis "
        f"{p['depuis_s'] if p['depuis_s'] is not None else '?'} s "
        f"({p['requetes']}/{p['requetes_min']} requêtes)</p>"
        if p else "<p>palier : aucun canary en cours</p>"
    )
    alertes = "".join(f'<li class="alerte">{escape(a)}</li>' for a in r.get("alertes", []))
    alertes = alertes or '<li class="vide">aucune</li>'
    journal = "".join(
        f"<tr><td>{escape(str(e.get('date', '')))}</td><td>{escape(str(e.get('evenement')))}"
        f"</td><td>{escape(str(e.get('origine', '-')))}</td>"
        f"<td>{escape(str(e.get('resume') or e.get('motif') or ''))}</td></tr>"
        for e in reversed(r.get("journal", []))
    )
    return (
        "<!doctype html>\n<html lang=\"fr\"><head><meta charset=\"utf-8\">"
        '<meta http-equiv="refresh" content="5">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Pilotage Mardik</title><style>{_STYLE}</style></head><body>"
        f"<h1>Pilotage Mardik — fenêtre {r['fenetre_s']:.0f} s, {r['total']} requêtes</h1>"
        f"<h2>Part de trafic</h2>{_svg_jauge(r['par_version'])}"
        f"<h2>Par version</h2>{tableau}"
        f"<h2>Palier canary</h2>{palier}"
        f"<h2>Alertes</h2><ul>{alertes}</ul>"
        f"<h2>Candidats à verser</h2><p>{r.get('candidats', 0)}</p>"
        "<h2>Journal de pilotage</h2><table><tr><th>Date</th><th>Événement</th>"
        f"<th>Origine</th><th>Résumé</th></tr>{journal}</table>"
        "</body></html>"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_dashboard.py -v && uv run ruff check ops/dashboard.py`
Expected: PASS (6 tests) ; ruff propre. Contrôle visuel facultatif : `MOCK=on uv run python -m ops.dashboard --serve` puis ouvrir `http://localhost:8501`.

- [ ] **Step 5: Commit**

```bash
git add ops/dashboard.py tests/integration/test_dashboard.py
git commit -m "feat(dashboard): page HTML auto-rafraîchie (jauge, histogrammes, journal métier)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Documentation de surveillance et clôture de la partie 1

**Files:**
- Modify: `docs/exploitation.md` (§6 « Surveillance et seuils »)
- Modify: `Makefile` (commentaire de `test-acceptance`)

**Interfaces:**
- Consumes: tâches 1–11.
- Produces: la §6 d'`exploitation.md`, remplie ; la partie 1 terminée et poussée.

- [ ] **Step 1: Remplir la §6 d'`exploitation.md`**

Remplacer le commentaire HTML sous `## 6. Surveillance et seuils` par :

```markdown
Les seuils vivent dans `ops/seuils_pilotage.yaml` (config as code, `motif`
obligatoire) ; ceux du gate dans `eval/seuils.yaml`. Tout changement de l'un ou
l'autre passe par un commit ; le pilote le journalise (événement `seuils`,
avant → après, motif).

| Signal | Seuil initial | Rétroaction |
|---|---|---|
| Score moyen de la version surveillée | < 0,70 | rollback automatique |
| Score moyen dans la marge | [0,70 ; 0,75[ | alerte (un humain décide via `rollback.yml`) |
| Taux d'erreur | > 10 % | rollback automatique |
| Latence P95 | > 8 000 ms | rollback automatique |
| Palier canary tenu (≥ 60 s et ≥ 20 requêtes ; erreurs ≤ active + 2 pts ; P95 < 8 s ; score ≥ 0,75 ; P10 ≥ 0,65) | tous vrais | promotion 10 → 50 → 100 % |
| Score d'une analyse v2 | < 0,70 | capture vers `eval/candidats.jsonl` |

**Fenêtre et échantillon.** La surveillance lit les 300 dernières secondes de
`ops/metrics.jsonl`, limitées au palier courant pour un canary ; aucune
décision sous 10 mesures (`échantillon insuffisant`). Les fenêtres de palier
(60 s, 20 requêtes) sont réglées pour la démo ; en production : 1 800 s et 500
requêtes.

**Calibrer.** `uv run python -m ops.seuils calibrer --version v2.0.0` propose
`score_min` = moyenne − 1,5 σ et `score_p10_min` = P10 − 0,05 sur la
production de la dernière heure (au moins 30 scores). Il n'écrit rien :
reporter la valeur dans le YAML, avec un `motif` qui cite la calibration.

**Faux positifs connus.** Un document trop long (413) est enregistré comme une
erreur : une rafale de documents trop longs peut déclencher un rollback sur le
taux d'erreur. `Mesure` ne porte pas de code HTTP ; à surveiller dans le journal.

**Enrichissement.** `uv run python -m eval.enrichir lister` montre les cas
capturés (anonymisés : e-mails, IBAN, téléphones, SIRET, personnes — pas les
raisons sociales ni les adresses, à vérifier à la relecture). Après relecture
par un juriste : `uv run python -m eval.enrichir verser <id> --clauses
"durée,résiliation"`. Le gate CI (`MOCK=on`) rejoue le nouveau contrat avec la
réponse de repli ; seul le gate de release (vrai modèle) ou `make fixtures`
l'évalue réellement.
```

- [ ] **Step 2: Mettre à jour le `Makefile`**

Remplacer le commentaire de la cible `test-acceptance` (aujourd'hui « 8 verts, 2 rouges (sous-projet 4) ») :

```makefile
test-acceptance:    ## les 10 tests du brief : 9 verts, 1 rouge (rollback automatique, sous-projet 4)
```

- [ ] **Step 3: Vérifier la partie 1**

Run: `MOCK=on uv run pytest -q && uv run ruff check . && MOCK=on uv run pytest -v tests/acceptance`
Expected: unitaires et intégration verts ; acceptance : **9 verts**, `test_dashboard_par_version` compris ; reste rouge `test_journal_derive_et_rollback_automatique` (partie 2). `git status` ne montre aucun `eval/candidats.jsonl` ni `ops/metrics.jsonl`.

- [ ] **Step 4: Commit et push**

```bash
git add docs/exploitation.md Makefile
git commit -m "docs(exploitation): surveillance, seuils, calibration et enrichissement

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
git push -u origin feature/observabilite
```

---

# Partie 2 — exécution des boucles

Même branche. Le code du sous-projet 3 (fusionné en `8833345`) fournit dans `ops/deploy.py` : `_transition(registry, evenement, calcul, *, origine, **details)` sous verrou, `deployer_canary`, `promouvoir`, `rollback` (keyword-only `origine`, **sans** `**details`), un `main()` dont le `try` se termine par `except ErreurDeploiement` puis `except (ErreurRegistre, OSError, json.JSONDecodeError)`, et les imports `time`, `Callable`, `Path`, `MetricsStore`. Contrôle rapide : `grep -n "def _transition\|def rollback\|def deployer_canary\|def promouvoir\|^    except" ops/deploy.py`. Si ces signatures ont changé depuis, adapter les extraits des tâches 13–15 sans en changer le comportement.

### Task 13: Détails de pilotage dans le journal des transitions

**Files:**
- Modify: `ops/deploy.py` (`deployer_canary`, `promouvoir`, `rollback`)
- Test: `tests/integration/test_details_journal.py`

**Interfaces:**
- Consumes: `_transition(registry, evenement, calcul, *, origine, **details)` (SP3).
- Produces: `deployer_canary(version, pourcentage=None, registry=None, *, origine="manuel", **details)`, `promouvoir(version, registry=None, *, origine="manuel", **details)`, `rollback(registry=None, motif="manuel", *, origine="manuel", **details)` — `details` est recopié tel quel dans l'entrée de journal. Les appelants ne passent jamais `version`, `pourcentage` ni `motif` dans `details` (déjà des paramètres).

- [ ] **Step 1: Write the failing test**

`tests/integration/test_details_journal.py` :

```python
"""Les transitions acceptent des détails de pilotage, recopiés au journal (C2.8)."""
from __future__ import annotations

from app.llm_client import Bundle
from ops.deploy import deployer_canary, promouvoir, rollback


def _v2(registry):
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)


def test_details_recopies(registry):
    _v2(registry)
    deployer_canary("v2.0.0", 10, registry, origine="auto", resume="r1", requetes=23)
    rollback(registry, motif="score moyen 0.52 < 0.70", origine="auto",
             signal="score_moyen", valeur=0.52, seuil=0.70, resume="r2")
    canary, retour = registry.journal()[-2:]
    assert canary["resume"] == "r1" and canary["requetes"] == 23
    assert retour["signal"] == "score_moyen" and retour["valeur"] == 0.52
    assert retour["motif"] == "score moyen 0.52 < 0.70" and retour["origine"] == "auto"
    assert retour["avant"]["canary"] == "v2.0.0" and retour["apres"]["canary"] is None


def test_promotion_avec_details_et_appels_historiques_inchanges(registry):
    _v2(registry)
    promouvoir("v2.0.0", registry, origine="auto", resume="r3")
    assert registry.journal()[-1]["resume"] == "r3"
    rollback(registry)                                  # appel historique, sans détails
    assert registry.journal()[-1]["origine"] == "manuel"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_details_journal.py -v`
Expected: FAIL — `TypeError: deployer_canary() got an unexpected keyword argument 'resume'`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py` (code du sous-projet 3), trois modifications :

`deployer_canary` — signature et appel final :

```python
def deployer_canary(
    version: str,
    pourcentage: int | None = None,
    registry: Registry | None = None,
    *,
    origine: str = "manuel",
    **details: Any,
) -> dict[str, Any]:
```

```python
    return _transition(
        registry, "canary", calcul, origine=origine, version=version, pourcentage=pourcentage,
        **details,
    )
```

`promouvoir` :

```python
def promouvoir(
    version: str, registry: Registry | None = None, *, origine: str = "manuel", **details: Any
) -> dict[str, Any]:
```

```python
    return _transition(registry, "promotion", calcul, origine=origine, version=version, **details)
```

`rollback` :

```python
def rollback(
    registry: Registry | None = None,
    motif: str = "manuel",
    *,
    origine: str = "manuel",
    **details: Any,
) -> dict[str, Any]:
```

```python
    return _transition(registry, "rollback", calcul, origine=origine, motif=motif, **details)
```

Ajouter une phrase à chaque docstring : « ``details`` (pilotage) est recopié tel quel dans l'entrée de journal. »

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_details_journal.py -v && MOCK=on uv run pytest -q && uv run ruff check ops/deploy.py`
Expected: PASS ; les tests du sous-projet 3 (`tests/unit/test_transitions.py`, `tests/integration/test_cli_deploy.py`, `tests/integration/test_gateway.py`) restent verts.

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/integration/test_details_journal.py
git commit -m "feat(deploy): détails de pilotage recopiés au journal des transitions

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Surveillance et rollback automatique — `surveiller`

**Files:**
- Modify: `ops/deploy.py` (imports ; `_journaliser_une_fois`, `surveiller` ; CLI `surveiller --fenetre` par défaut `None`)
- Test: `tests/integration/test_surveiller.py` ; acceptance `test_journal_derive_et_rollback_automatique` (non modifié)

**Interfaces:**
- Consumes: `charger_seuils_pilotage`, `SeuilsPilotage` (tâche 1) ; `filtrer` (tâche 2) ; `detecter_derive`, `debut_palier`, `version_surveillee`, `resume_metier` (tâches 4–6) ; `rollback(**details)` (tâche 13).
- Produces: `surveiller(registry=None, metriques=None, *, fenetre_s=None, score_min=None, taux_erreur_max=None, latence_p95_max_ms=None, minimum=None, seuils=None) -> dict` (clés `version`, `mesures`, `derive`, `niveau`, `motif`, `rollback`) ; `_journaliser_une_fois(registry, evenement, fenetre_s, cles: dict, **details) -> dict | None`.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_surveiller.py` :

```python
"""Surveillance : dérive critique → rollback auto ; marge → alerte unique."""
from __future__ import annotations

import time

from app.llm_client import Bundle
from app.telemetry import Mesure
from ops.deploy import deployer_canary, surveiller


def _canary(registry):
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    deployer_canary("v2.0.0", 10, registry)


def _mesures(metriques, n, *, version="v2.0.0", score=0.9, latence=2000.0, erreur=False,
             ts=None):
    for _ in range(n):
        metriques.enregistrer(Mesure(ts=ts or time.time(), version=version, route="/analyse",
                                     latence_ms=latence, score=score, erreur=erreur))


def test_rollback_sur_score_avec_resume_metier(registry, metriques):
    _canary(registry)
    _mesures(metriques, 12, score=0.5)
    res = surveiller(registry, metriques)
    assert res["derive"] and res["rollback"] and res["niveau"] == "critique"
    entree = registry.journal()[-1]
    assert entree["evenement"] == "rollback" and entree["origine"] == "auto"
    assert entree["signal"] == "score_moyen" and entree["seuil"] == 0.70
    assert entree["resume"] == (
        "Version v2.0.0 retirée : 12 analyses sur 12 jugées peu fiables (score < 0,70).")
    assert registry.canary() == (None, 0)


def test_rollback_sur_erreurs_puis_sur_latence(registry, metriques):
    _canary(registry)
    _mesures(metriques, 10)
    _mesures(metriques, 3, erreur=True)
    assert surveiller(registry, metriques)["motif"].startswith("taux d'erreur")
    index = deployer_canary("v2.0.0", 10, registry)   # nouveau palier après le rollback
    assert index["canary"] == "v2.0.0"
    _mesures(metriques, 12, latence=9500)
    res = surveiller(registry, metriques)
    assert res["rollback"] and res["motif"].startswith("latence P95")


def test_marge_alerte_une_seule_fois(registry, metriques):
    _canary(registry)
    _mesures(metriques, 12, score=0.72)
    for _ in range(3):
        res = surveiller(registry, metriques)
    assert res["niveau"] == "marge" and not res["rollback"]
    alertes = [e for e in registry.journal() if e["evenement"] == "alerte"]
    assert len(alertes) == 1 and alertes[0]["origine"] == "auto"
    assert "à surveiller" in alertes[0]["resume"]
    assert registry.canary()[0] == "v2.0.0"


def test_echantillon_insuffisant(registry, metriques):
    _canary(registry)
    _mesures(metriques, 5, score=0.1)
    res = surveiller(registry, metriques)
    assert res["niveau"] == "echantillon_insuffisant" and not res["rollback"]


def test_mesures_d_un_palier_anterieur_ignorees(registry, metriques):
    _mesures(metriques, 12, score=0.1, ts=time.time() - 30)   # avant le canary
    _canary(registry)
    assert surveiller(registry, metriques)["niveau"] == "echantillon_insuffisant"


def test_refus_sans_retour_arriere(registry, metriques):
    _mesures(metriques, 12, version="v1.0.0", score=None, latence=9500)
    res = surveiller(registry, metriques)
    assert res["derive"] and not res["rollback"]
    refus = registry.journal()[-1]
    assert refus["evenement"] == "pilotage_refus" and "rien à annuler" in refus["raison"]
    surveiller(registry, metriques)
    assert sum(e["evenement"] == "pilotage_refus" for e in registry.journal()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_surveiller.py tests/acceptance/test_observabilite.py::test_journal_derive_et_rollback_automatique -v`
Expected: FAIL — `NotImplementedError: deploy.surveiller — détection de dérive + rollback automatique`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py`, ajouter `from dataclasses import replace` aux imports de la bibliothèque standard (après `from contextlib import contextmanager`), et après `from ops.registry import MOTIF_VERSION, ErreurRegistre, Registry` :

```python
from ops.pilotage import debut_palier, detecter_derive, resume_metier, version_surveillee
from ops.seuils import ErreurSeuilsPilotage, SeuilsPilotage, charger_seuils_pilotage
from ops.signaux import filtrer
```

Remplacer le stub `surveiller` par :

```python
# ---------------------------------------------------------------- pilotage
def _journaliser_une_fois(
    registry: Registry, evenement: str, fenetre_s: float, cles: dict[str, Any], **details: Any
) -> dict[str, Any] | None:
    """Journalise sauf si le même événement (mêmes ``cles``) date de moins de
    ``fenetre_s`` : une alerte ou un refus n'est pas répété à chaque tour."""
    limite = time.time() - fenetre_s
    for entree in reversed(registry.journal()):
        if entree.get("ts", 0) < limite:
            break
        if entree.get("evenement") == evenement and all(
            entree.get(k) == v for k, v in cles.items()
        ):
            return None
    return registry.journaliser(evenement, origine="auto", **cles, **details)


def surveiller(
    registry: Registry | None = None,
    metriques: MetricsStore | None = None,
    *,
    fenetre_s: float | None = None,
    score_min: float | None = None,
    taux_erreur_max: float | None = None,
    latence_p95_max_ms: float | None = None,
    minimum: int | None = None,
    seuils: SeuilsPilotage | None = None,
) -> dict[str, Any]:
    """Dérive critique de la version surveillée → rollback automatique, tracé.

    Les paramètres à ``None`` viennent de ``seuils`` (sinon de
    ``ops/seuils_pilotage.yaml``) ; les valeurs explicites l'emportent.
    """
    registry = registry or Registry()
    metriques = metriques or MetricsStore()
    s = seuils or charger_seuils_pilotage()
    fenetre = fenetre_s if fenetre_s is not None else s.fenetre_s
    mini = minimum if minimum is not None else s.minimum
    surcharges = {"score_min": score_min, "taux_erreur_max": taux_erreur_max,
                  "latence_p95_max_ms": latence_p95_max_ms}
    seuils_derive = replace(s.derive, **{k: v for k, v in surcharges.items() if v is not None})

    index = registry.index()
    version = version_surveillee(index)
    if version is None:
        return {"version": None, "mesures": 0, "derive": False, "niveau": "aucune",
                "motif": "aucune version active", "rollback": False}
    debut = debut_palier(registry.journal(), version) if index.get("canary") == version else None
    mesures = filtrer(metriques.lire(depuis_s=fenetre), version, depuis_ts=debut)
    derive = detecter_derive(version, mesures, seuils_derive, minimum=mini)
    resultat = {"version": version, "mesures": derive.mesures, "derive": derive.critique,
                "niveau": derive.niveau, "motif": derive.motif, "rollback": False}

    if derive.niveau == "marge":
        score = next(c for c in derive.constats if c.signal == "score_moyen")
        _journaliser_une_fois(
            registry, "alerte", fenetre, {"version": version, "signal": "score_moyen"},
            valeur=score.valeur, seuil=score.seuil, motif=derive.motif,
            resume=resume_metier("alerte", version=version, valeur=score.valeur,
                                 seuil=score.seuil),
        )
    elif derive.critique:
        c = derive.principal
        details = {
            "signal": c.signal, "valeur": c.valeur, "seuil": c.seuil,
            "mesures": derive.mesures, "constats": [x.to_dict() for x in derive.constats],
            "resume": resume_metier("rollback", version=version, signal=c.signal,
                                    valeur=c.valeur, seuil=c.seuil, mesures=derive.mesures,
                                    sous_seuil=derive.sous_seuil),
        }
        try:
            rollback(registry, motif=derive.motif, origine="auto", **details)
            resultat["rollback"] = True
        except ErreurDeploiement as exc:
            _journaliser_une_fois(
                registry, "pilotage_refus", fenetre,
                {"version": version, "action": "rollback"},
                raison=str(exc), motif=derive.motif,
                resume=resume_metier("pilotage_refus", action="rollback", raison=str(exc)),
            )
    return resultat
```

Dans `main`, remplacer `s.add_argument("--fenetre", type=float, default=120)` par `s.add_argument("--fenetre", type=float, default=None)` (la fenêtre vient des seuils), et insérer un `except ErreurSeuilsPilotage` entre les deux `except` existants du sous-projet 3 (`ErreurSeuilsPilotage` hérite de `ValueError`, pas d'`OSError` : il lui faut sa branche). La fin du `try` devient :

```python
    except ErreurDeploiement as exc:
        print(f"REFUSÉ : {exc}", file=sys.stderr)
        return 1
    except ErreurSeuilsPilotage as exc:
        print(f"SEUILS INVALIDES : {exc}", file=sys.stderr)
        return 1
    except (ErreurRegistre, OSError, json.JSONDecodeError) as exc:
        print(f"ÉCHEC : {exc}", file=sys.stderr)
        return 1
    return 0
```

Mettre à jour la docstring du module : `surveiller` lit ses valeurs par défaut dans `ops/seuils_pilotage.yaml` et journalise `alerte` / `pilotage_refus`.

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_surveiller.py tests/acceptance/test_observabilite.py -v && uv run ruff check ops/deploy.py`
Expected: PASS (6 tests) ; **les 5 tests de `test_observabilite.py` verts**, dont `test_journal_derive_et_rollback_automatique`.

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/integration/test_surveiller.py
git commit -m "feat(deploy): surveiller — dérive critique → rollback automatique tracé, alerte de marge

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Le pilote — `tour`, `piloter` et CLI

**Files:**
- Modify: `ops/deploy.py` (imports `chemin_seuils_gate`, `chemin_seuils_pilotage`, `changements_seuils`, `evaluer_palier` ; fonctions `fichiers_seuils`, `tour`, `piloter` ; sous-commande `piloter`)
- Test: `tests/integration/test_piloter.py`

**Interfaces:**
- Consumes: `surveiller`, `_journaliser_une_fois` (tâche 14) ; `evaluer_palier`, `debut_palier`, `changements_seuils`, `resume_metier` (tâches 5–6) ; `deployer_canary`, `promouvoir` avec `**details` (tâche 13) ; `chemin_seuils_pilotage`, `chemin_seuils_gate` (tâche 1).
- Produces: `fichiers_seuils() -> dict[str, Path]` ; `tour(registry, metriques, seuils) -> dict` (clés `surveillance`, `palier`) ; `piloter(registry=None, metriques=None, *, tours=None, attendre=time.sleep, charger=charger_seuils_pilotage) -> int` (nombre de tours joués ; lève `ErreurSeuilsPilotage` si les seuils sont invalides au démarrage) ; CLI `python -m ops.deploy piloter [--tours N]`.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_piloter.py` :

```python
"""Le pilote : une tour = seuils → surveillance → palier. Sans état."""
from __future__ import annotations

import time
from dataclasses import replace

import pytest

from app.llm_client import Bundle
from app.telemetry import Mesure
from ops.deploy import deployer_canary, main, piloter
from ops.seuils import CHEMIN_SEUILS_PILOTAGE_DEFAUT, ErreurSeuilsPilotage, charger_seuils_pilotage

BASE = charger_seuils_pilotage()
RAPIDES = replace(BASE, promotion=replace(BASE.promotion, duree_min_s=0, requetes_min=5))


def _rapides():
    return RAPIDES


def _canary(registry):
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    deployer_canary("v2.0.0", 10, registry)


def _trafic(metriques, n=12, *, score=0.9):
    for _ in range(n):
        ts = time.time()
        metriques.enregistrer(Mesure(ts=ts, version="v2.0.0", route="/analyse",
                                     latence_ms=2000, score=score))
        metriques.enregistrer(Mesure(ts=ts, version="v1.0.0", route="/analyse",
                                     latence_ms=900))


def _evenements(registry, *noms):
    return [e for e in registry.journal() if e["evenement"] in noms]


def test_trafic_conforme_promu_par_paliers(registry, metriques):
    _canary(registry)
    _trafic(metriques)
    assert piloter(registry, metriques, tours=1, charger=_rapides) == 1
    assert registry.canary() == ("v2.0.0", 50)
    piloter(registry, metriques, tours=1, charger=_rapides)      # redémarrage : palier relu
    assert registry.canary() == ("v2.0.0", 50)                   # aucune mesure du palier 50
    _trafic(metriques)
    piloter(registry, metriques, tours=1, charger=_rapides)
    assert registry.active() == "v2.0.0" and registry.canary() == (None, 0)
    auto = [e for e in _evenements(registry, "canary", "promotion") if e["origine"] == "auto"]
    assert [e["evenement"] for e in auto] == ["canary", "promotion"]
    assert auto[0]["resume"].startswith("Version v2.0.0 étendue à 50 %")
    assert auto[1]["resume"].startswith("Version v2.0.0 servie à tous les clients")


def test_derive_rollback_sans_promotion(registry, metriques):
    _canary(registry)
    _trafic(metriques, score=0.5)
    piloter(registry, metriques, tours=1, charger=_rapides)
    assert registry.canary() == (None, 0) and registry.active() == "v1.0.0"
    assert _evenements(registry, "promotion") == []
    assert _evenements(registry, "rollback")[-1]["origine"] == "auto"


def test_seuils_journalises_puis_modifies(registry, metriques, tmp_path, monkeypatch):
    chemin = tmp_path / "seuils_pilotage.yaml"
    chemin.write_text(CHEMIN_SEUILS_PILOTAGE_DEFAUT.read_text(encoding="utf-8"),
                      encoding="utf-8")
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(chemin))
    piloter(registry, metriques, tours=1)
    initiaux = _evenements(registry, "seuils")
    assert {e["fichier"] for e in initiaux} == {"ops/seuils_pilotage.yaml", "eval/seuils.yaml"}
    assert all(e["avant"] is None for e in initiaux)
    chemin.write_text(
        chemin.read_text(encoding="utf-8")
        .replace("score_min: 0.70", "score_min: 0.68")
        .replace('motif: "seuils initiaux', 'motif: "calibration du 22/09'),
        encoding="utf-8",
    )
    piloter(registry, metriques, tours=1)
    change = _evenements(registry, "seuils")[-1]
    assert change["fichier"] == "ops/seuils_pilotage.yaml"
    assert "derive.score_min 0,70 → 0,68" in change["resume"]


def test_seuils_casses_en_cours_de_route(registry, metriques):
    appels = {"n": 0}

    def charger():
        appels["n"] += 1
        if appels["n"] > 1:
            raise ErreurSeuilsPilotage("YAML invalide")
        return BASE

    attentes = []
    assert piloter(registry, metriques, tours=3, charger=charger,
                   attendre=attentes.append) == 3
    assert attentes == [5.0, 5.0]
    invalides = _evenements(registry, "seuils_invalides")
    assert len(invalides) == 1 and "YAML invalide" in invalides[0]["resume"]


def test_seuils_invalides_au_demarrage(registry, metriques, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(tmp_path / "absent.yaml"))
    with pytest.raises(ErreurSeuilsPilotage):
        piloter(registry, metriques, tours=1)
    assert main(["piloter", "--tours", "1"]) == 1
    assert "SEUILS INVALIDES" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_piloter.py -v`
Expected: FAIL — `ImportError: cannot import name 'piloter' from 'ops.deploy'`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py`, compléter les imports :

```python
from collections.abc import Callable
from pathlib import Path

from ops.pilotage import (
    changements_seuils,
    debut_palier,
    detecter_derive,
    evaluer_palier,
    resume_metier,
    version_surveillee,
)
from ops.seuils import (
    ErreurSeuilsPilotage,
    SeuilsPilotage,
    charger_seuils_pilotage,
    chemin_seuils_gate,
    chemin_seuils_pilotage,
)
```

(remplacer les deux lignes `from ops.pilotage …` et `from ops.seuils …` ajoutées à la tâche 14 ; `Callable` et `Path` sont déjà importés par le sous-projet 3.)

Ajouter après `surveiller` :

```python
def fichiers_seuils() -> dict[str, Path]:
    """Les deux fichiers de seuils suivis au journal (pilotage et gate)."""
    return {
        "ops/seuils_pilotage.yaml": chemin_seuils_pilotage(),
        "eval/seuils.yaml": chemin_seuils_gate(),
    }


def _journaliser_seuils(registry: Registry, seuils: SeuilsPilotage) -> None:
    try:
        evenements = changements_seuils(registry.journal(), fichiers_seuils())
    except ErreurSeuilsPilotage as exc:
        _journaliser_une_fois(
            registry, "seuils_invalides", seuils.fenetre_s, {"raison": str(exc)},
            fichier="seuils",
            resume=resume_metier("seuils_invalides", fichier="seuils", raison=str(exc)),
        )
        return
    for ev in evenements:
        registry.journaliser("seuils", origine="auto", **ev,
                             resume=resume_metier("seuils", **ev))


def tour(
    registry: Registry, metriques: MetricsStore, seuils: SeuilsPilotage
) -> dict[str, Any]:
    """Une tour du pilote : seuils → surveillance → palier. Relit tout, ne garde rien."""
    _journaliser_seuils(registry, seuils)
    surveillance = surveiller(registry, metriques, seuils=seuils)
    if surveillance["rollback"]:
        return {"surveillance": surveillance, "palier": None}
    index = registry.index()
    canary = index.get("canary")
    debut = debut_palier(registry.journal(), canary) if canary else None
    if canary is None or debut is None:
        return {"surveillance": surveillance, "palier": None}

    maintenant = time.time()
    depuis_s = maintenant - debut
    mesures = metriques.lire(depuis_s=depuis_s + 1)
    decision = evaluer_palier(
        canary,
        filtrer(mesures, canary, depuis_ts=debut),
        filtrer(mesures, index.get("active") or "", depuis_ts=debut),
        seuils,
        depuis_s=depuis_s,
        pourcentage=int(index.get("canary_percent") or 0),
    )
    details = {
        "motif": decision.motif,
        "constats": [c.to_dict() for c in decision.constats],
        "requetes": decision.requetes,
        "depuis_s": round(decision.depuis_s, 1),
    }
    try:
        if decision.action == "progresser":
            deployer_canary(
                canary, decision.pourcentage_suivant, registry, origine="auto",
                resume=resume_metier("canary", version=canary,
                                     pourcentage=decision.pourcentage_suivant,
                                     requetes=decision.requetes, depuis_s=decision.depuis_s),
                **details,
            )
        elif decision.action == "promouvoir":
            promouvoir(canary, registry, origine="auto",
                       resume=resume_metier("promotion", version=canary), **details)
    except ErreurDeploiement as exc:
        _journaliser_une_fois(
            registry, "pilotage_refus", seuils.fenetre_s,
            {"version": canary, "action": decision.action},
            raison=str(exc),
            resume=resume_metier("pilotage_refus", action=decision.action, raison=str(exc)),
        )
    return {"surveillance": surveillance,
            "palier": {"action": decision.action, "motif": decision.motif}}


def piloter(
    registry: Registry | None = None,
    metriques: MetricsStore | None = None,
    *,
    tours: int | None = None,
    attendre: Callable[[float], None] = time.sleep,
    charger: Callable[[], SeuilsPilotage] = charger_seuils_pilotage,
) -> int:
    """Boucle du pilote (service ``pilote``). Seuils invalides au démarrage →
    ``ErreurSeuilsPilotage`` ; en cours de route → derniers seuils valides."""
    registry = registry or Registry()
    metriques = metriques or MetricsStore()
    seuils = charger()
    joues = 0
    while tours is None or joues < tours:
        if joues:
            try:
                seuils = charger()
            except ErreurSeuilsPilotage as exc:
                _journaliser_une_fois(
                    registry, "seuils_invalides", seuils.fenetre_s, {"raison": str(exc)},
                    fichier="ops/seuils_pilotage.yaml",
                    resume=resume_metier("seuils_invalides",
                                         fichier="ops/seuils_pilotage.yaml", raison=str(exc)),
                )
        tour(registry, metriques, seuils)
        joues += 1
        if tours is None or joues < tours:
            attendre(seuils.intervalle_s)
    return joues
```

Dans `main`, ajouter la sous-commande après `surveiller` :

```python
    pl = sub.add_parser("piloter", help="boucle du pilote : surveillance + promotion")
    pl.add_argument("--tours", type=int, default=None, help="nombre de tours (défaut : infini)")
```

et la branche correspondante dans le `try` :

```python
        elif args.commande == "piloter":
            piloter(tours=args.tours)
```

(le `except ErreurSeuilsPilotage` ajouté à la tâche 14 renvoie 1).

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_piloter.py tests/integration/test_surveiller.py -v && uv run ruff check ops/deploy.py`
Expected: PASS (5 + 6 tests) ; ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/integration/test_piloter.py
git commit -m "feat(deploy): piloter — promotion canary pilotée par les métriques, seuils tracés

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Capture sur la gateway `POST /analyse`

**Files:**
- Modify: `app/gateway.py` (import `BackgroundTasks`, `Capture`, `capturer`, `get_capture` ; route `analyse`)
- Test: `tests/integration/test_capture.py` (ajouts)

**Interfaces:**
- Consumes: `Capture`, `capturer`, `get_capture` (tâche 8) ; `ReponseAnalyseV2` (déjà importé par la gateway pour `response_model`).
- Produces: `POST /analyse` capture les réponses v2 à faible confiance ; jamais les réponses v1.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/integration/test_capture.py` :

```python
from app.llm_client import Bundle
from ops.deploy import promouvoir


def test_gateway_capture_la_v2(client, registry, contrat, tmp_path):
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    promouvoir("v2.0.0", registry)
    chemin = tmp_path / "cand.jsonl"
    _brancher(client, Capture(chemin, score_max=1.01))
    r = client.post("/analyse", json={"texte": contrat("c02")})
    assert r.headers["x-mardik-version"] == "v2.0.0"
    assert len(_lignes(chemin)) == 1


def test_gateway_ne_capture_pas_la_v1(client, contrat, tmp_path):
    chemin = tmp_path / "cand.jsonl"
    _brancher(client, Capture(chemin, score_max=1.01))
    r = client.post("/analyse", json={"texte": contrat("c02")})
    assert r.headers["x-mardik-version"] == "v1.0.0"
    assert not chemin.exists()
```

(Placer les deux imports en tête du fichier avec les autres.)

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_capture.py -v`
Expected: FAIL — `test_gateway_capture_la_v2` : `FileNotFoundError` sur `cand.jsonl` (rien n'est capturé).

- [ ] **Step 3: Write minimal implementation**

Dans `app/gateway.py`, ajouter `BackgroundTasks` à l'import `fastapi`, ajouter `from app.capture import Capture, capturer, get_capture`, puis remplacer la route par :

```python
@router.post("/analyse", response_model=ReponseAnalyseV1 | ReponseAnalyseV2)
def analyse(
    requete: RequeteAnalyse,
    response: Response,
    taches: BackgroundTasks,
    registry: Registry = Depends(get_registry),
    telemetry: Telemetry = Depends(get_telemetry),
    tirage: float = Depends(get_tirage),
    fabrique_client: Callable[[Bundle], LLMClient] = Depends(get_fabrique_client),
    capture: Capture = Depends(get_capture),
) -> ReponseAnalyseV1 | ReponseAnalyseV2:
    try:
        version, reponse = routage.analyser(
            requete.texte, registry, telemetry, tirage, fabrique_client
        )
    except ErreurLLM as exc:
        raise HTTPException(
            status_code=503, detail=f"fournisseur LLM indisponible : {exc}"
        ) from exc
    response.headers["X-Mardik-Version"] = version
    if isinstance(reponse, ReponseAnalyseV2):   # la v1 ne produit pas de score
        taches.add_task(capturer, capture, requete.texte, reponse)
    return reponse
```

(Le corps est celui de la route fusionnée par le sous-projet 3 ; seuls s'ajoutent les paramètres `taches`, `capture` et les deux lignes de capture avant `return`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_capture.py tests/integration -q && uv run ruff check app/gateway.py`
Expected: PASS ; les tests de gateway du sous-projet 3 restent verts.

- [ ] **Step 5: Commit**

```bash
git add app/gateway.py tests/integration/test_capture.py
git commit -m "feat(gateway): capture des analyses v2 à faible confiance servies en production

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Service `pilote`, démo live et clôture

**Files:**
- Modify: `docker-compose.yml` (service `pilote`)
- Modify: `Makefile` (cible `pilote` ; commentaire `test-acceptance`)
- Modify: `docs/exploitation.md` (§4, dernier paragraphe avant « Refus » ; §7 « Preuve d'exécution »)
- Modify: `.github/workflows/promotion.yml` (commentaire d'en-tête uniquement)
- Modify: `README.md` (compteurs de tests, CLI, arborescence)

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: `docker compose up` lance le pilote ; `make pilote` en local ; la procédure de démo des trois boucles.

- [ ] **Step 1: `docker-compose.yml`**

Ajouter après le service `dashboard` :

```yaml
  # Le pilote : surveillance, rollback automatique, promotion canary pilotée
  # par les métriques (python -m ops.deploy piloter, une tour toutes les 5 s).
  pilote:
    build: .
    command: python -m ops.deploy piloter
    env_file: .env
    environment:
      METRICS_PATH: /app/ops/metrics.jsonl
      REGISTRY_PATH: /app/ops/registry
    volumes:
      - ./ops:/app/ops          # métriques, registre, journal, seuils partagés
      - ./eval:/app/eval        # eval/seuils.yaml suivi au journal
    depends_on:
      - app
```

Et remplacer le commentaire du service `dashboard` (`# Le tableau de bord (à implémenter : ops/dashboard.py).`) par `# Le tableau de bord (ops/dashboard.py) : http://localhost:8501`.

- [ ] **Step 2: `Makefile`**

Ajouter `pilote` à la ligne `.PHONY` (qui contient déjà `etat`, ajoutée par le sous-projet 3), puis la cible après `dashboard` :

```makefile
pilote:             ## boucle du pilote en local : surveillance, rollback auto, promotion canary
	uv run python -m ops.deploy piloter
```

Et remplacer le commentaire de `test-acceptance` par `## les 10 tests du brief : tous verts`.

- [ ] **Step 3: `docs/exploitation.md` §7**

Remplacer le commentaire HTML sous `## 7. Preuve d'exécution` par la procédure (puis, après l'avoir jouée, coller sous chaque scénario l'extrait réel du journal `ops/registry/journal.jsonl` et du dashboard) :

````markdown
Prérequis : `docker compose up -d` (app, proxy, dashboard, pilote) ; une v2
publiée (`python -m ops.deploy publier`) puis installée en canary à 10 %
(`python -m ops.deploy canary vX.Y.Z --pourcentage 10`). Dashboard :
http://localhost:8501 ; journal : `tail -f ops/registry/journal.jsonl`.

**Scénario 1 — dérive → rollback automatique.**
`make traffic MODE=derive-score DUREE=120 RPS=2` : le proxy fait chuter le
score de la v2 vers 0,5. En moins d'une fenêtre, le pilote journalise
`rollback` (`origine: auto`, `signal: score_moyen`, résumé « Version … retirée :
N analyses sur M jugées peu fiables ») ; la gateway sert 100 % v1.

```
(coller ici l'entrée rollback du journal)
```

**Scénario 2 — trafic conforme → promotion 10 → 50 → 100 %.**
Remettre le canary à 10 %, puis `make traffic MODE=normal DUREE=300 RPS=1`.
Après chaque palier (≥ 60 s, ≥ 20 requêtes, critères tenus), le pilote
journalise `canary` (50 %) puis `promotion`, `origine: auto`.

```
(coller ici les entrées canary et promotion)
```

**Scénario 3 — enrichissement.**
Un contrat ambigu envoyé à `/analyse` sous la v2 obtient un score < 0,70 →
`uv run python -m eval.enrichir lister` le montre (anonymisé) →
`uv run python -m eval.enrichir verser <id> --clauses "…"` après relecture →
`make eval` le rejoue (nouveau `cNN` dans le rapport).

```
(coller ici la sortie de lister, verser et la ligne du rapport)
```
````

- [ ] **Step 4: Renvois laissés par le sous-projet 3**

`docs/exploitation.md` §4 — remplacer le paragraphe :

```markdown
Avant chaque étape, regarder le tableau de bord de la version canary : taux
d'erreur ≤ v1, latence P95 < 8 s, score de confiance stable. Dans cette
version la décision est humaine ; les critères automatiques décrits en §6
(sous-projet 4) ne s'appliquent pas encore ici.
```

par :

```markdown
Le pilote (service `pilote`, `python -m ops.deploy piloter`) enchaîne 10 → 50 →
100 % tout seul quand les critères du §6 tiennent sur la fenêtre du palier, et
journalise chaque étape avec `origine: auto`. `promotion.yml` reste la voie
humaine pour forcer une étape (`origine: ci:<acteur>`) ; `rollback.yml` pour
l'interrompre.
```

`.github/workflows/promotion.yml` — remplacer les deux premières lignes de commentaire :

```yaml
# Promotion du canary : 50 % du trafic, puis 100 % (la version devient active).
# Voie humaine : le pilote (sous-projet 4) promeut automatiquement quand les
# métriques tiennent ; ce workflow force une étape à la main.
```

(la troisième ligne, « S'exécute sur le runner auto-hébergé de prod, jamais sur une PR. », est conservée ; rien d'autre ne change dans le workflow — `tests/unit/test_workflows.py` doit rester vert.)

`README.md` — remplacements exacts :

| Avant | Après |
|---|---|
| `make test-acceptance         # 8 verts, 2 rouges (sous-projet 4) ; 1 vert, 9 rouges au départ` | `make test-acceptance         # 10 verts ; 1 vert, 9 rouges au départ` |
| `\| \`make test-acceptance\` \| les 10 tests du brief (8 verts, 2 en attente du sous-projet 4) \|` | `\| \`make test-acceptance\` \| les 10 tests du brief (verts) \|` |
| `\| \`make dashboard\` \| tableau de bord (texte) ; \`DASH=serve\` pour la page HTML \|` | la même ligne, suivie de `\| \`make pilote\` \| boucle du pilote : surveillance, rollback automatique, promotion canary \|` |
| `… \| rollback [--motif M] [--origine ci:acteur] \| surveiller --boucle\`.` | `… \| rollback [--motif M] [--origine ci:acteur] \| surveiller [--boucle] \| piloter [--tours N]\`. Seuils : \`ops/seuils_pilotage.yaml\` ; calibration : \`python -m ops.seuils calibrer --version vX.Y.Z\` ; enrichissement : \`python -m eval.enrichir lister \| verser <id> --clauses …\`.` |
| `promotion, rollback FAIT — SP3 ; surveillance en STUB, SP4], dashboard.py [STUB]` | `promotion, rollback FAIT — SP3 ; surveiller, piloter FAIT — SP4], dashboard.py, pilotage.py, signaux.py, seuils.py [FAIT — SP4]` |
| `acceptance/ (10 tests du brief : 8 verts, 2 en attente du sous-projet 4)` | `acceptance/ (10 tests du brief : verts)` |

Ajouter aussi `capture.py [SP4]` à la ligne `app/` et `enrichir.py [SP4]` à la ligne `eval/` de l'arborescence, et « observabilité » à la liste `superpowers/specs/ et superpowers/plans/`.

- [ ] **Step 5: Vérification finale**

Run: `MOCK=on uv run pytest -q && MOCK=on uv run pytest -v tests/acceptance && uv run ruff check . && docker compose config --services`
Expected: toute la suite verte ; **les 10 tests d'acceptance verts** ; ruff propre ; `docker compose config --services` liste `app`, `proxy`, `dashboard`, `pilote`. Puis jouer les trois scénarios de l'étape 3 et coller les extraits réels dans `exploitation.md`.

- [ ] **Step 6: Commit et PR**

```bash
git add docker-compose.yml Makefile docs/exploitation.md .github/workflows/promotion.yml README.md
git commit -m "feat(ops): service pilote, cible make pilote et procédure de démo des trois boucles

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
git push
gh pr create --draft --title "Observabilité & boucles de rétroaction (sous-projet 4)" \
  --body "Sous-projet 4 (spec docs/superpowers/specs/2026-09-22-observabilite-design.md) : dashboard par version, surveillance avec rollback automatique, promotion canary pilotée par les métriques, capture et versement des cas à faible confiance, journal de pilotage en langage métier. Les 10 tests d'acceptance sont verts ; démo des trois boucles consignée dans docs/exploitation.md §7.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```
