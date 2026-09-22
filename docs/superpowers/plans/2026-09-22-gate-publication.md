# Gate d'évaluation & publication — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Toute fusion sur `main` joue un gate d'évaluation bloquant (note par contrat, P95, coût) puis publie une version étiquetée `vX.Y.Z` (registre + tag git + artefact de build) ; un gate en échec bloque la livraison.

**Architecture:** `eval/run_eval.py` mesure et juge : seuils lus dans `eval/seuils.yaml`, moteur choisi par la stratégie du bundle, notation et agrégation en fonctions pures, rapport écrit dans `eval/history.jsonl` et en JSON (`--sortie`). `ops/deploy.py::publier` décide de l'étiquetage à partir de ce rapport et numérote automatiquement le patch ; le registre fourni enregistre. `.github/workflows/ci.yml` (renommé depuis `llmops.yml`) enchaîne gate MOCK → gate de release (vrai modèle, sur tag ou à la main) → build → publication (rapport relu, artefact, tag git).

**Tech Stack:** Python 3.11, PyYAML, pytest, ruff, uv, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-21-gate-publication-design.md`

## Global Constraints

- Répertoire de travail : racine du dépôt git (le dossier qui contient `pyproject.toml`), branche `feature/gate-publication`. Toutes les commandes se lancent depuis cette racine.
- Tests : `MOCK=on uv run pytest …` (le `tests/conftest.py` fourni force déjà `MOCK=on`, `LLM_MODEL=modele-de-test`, `METRICS_PATH` et `REGISTRY_PATH` dans `tmp_path`).
- **Ne jamais modifier** : `ops/registry/__init__.py`, `app/llm_client.py`, `app/telemetry.py`, `tests/conftest.py`, `models/v1/`, `app/api_v1.py`, `app/pipeline/`, `app/api_v2.py`, `tests/acceptance/`.
- Signatures imposées (appelées par les tests d'acceptance) : `evaluer(version, *, n_essais=None, seuil=…, latence_max_ms=…, cout_max_eur=…, contrats=…, attendus=…, registry=None, telemetry=None, sous_ensemble=None, historique=…)` → `Rapport` ; `publier(version, *, bundle="v2", commit=None, registry=None, seuil=…, rapport=None)` → `dict` (manifeste).
- Seuils initiaux : `note_min: 0.75`, `latence_p95_max_ms: 8000`, `cout_moyen_max_eur: 0.15`. Seuil par contrat : `seuil_note` de `eval/attendus.jsonl` (0,75 ; 0,80 pour c07, c10, c12).
- `passe = motifs == []`. Motifs exacts : `"note {note:.3f} < seuil {note_min}"`, `"{cid} : note {note:.2f} < seuil_note {seuil_note:.2f}"`, `"latence P95 {p95:.0f} ms ≥ {max:.0f} ms"`, `"coût moyen {cout:.4f} € ≥ {max} €"`, `"{cid} : erreur LLM — {exc}"`, `"{cid} : document trop long — {exc}"`.
- `Rapport.version` = `bundle.version` (ex. `v2.0.0`) ; `mode_eval` = `"reel"` si `MOCK=off`, sinon `"mock"`.
- Codes de sortie `eval.run_eval` : 0 passé, 1 échec, 2 mal configuré. `ops.deploy` : 0 succès, 1 refus.
- Exceptions : jamais `except Exception` ; types précis (`FileNotFoundError`, `subprocess.CalledProcessError`, `yaml.YAMLError`, `json.JSONDecodeError`, `ErreurLLM`, `DocumentTropLong`, `ErreurRegistre`, `ValueError`).
- `uv run ruff check .` propre après chaque tâche (longueur de ligne 100).
- Messages et identifiants en français, comme le reste du dépôt.

---

## File Structure

| Fichier | Rôle | Tâches |
|---|---|---|
| `eval/seuils.yaml` | Seuils globaux du gate, versionnés, avec `motif` | 1 |
| `eval/run_eval.py` | Seuils, notation pure, moteurs, `evaluer`, `Rapport`, CLI | 1–5 |
| `ops/deploy.py` | Versions (pures), `publier`, `charger_rapport`, CLI `publier` | 6–8 |
| `.github/workflows/ci.yml` | Chaîne CI (renommé depuis `llmops.yml`) | 9 |
| `Makefile`, `README.md` | Cible `ci` avec gate ; références `ci.yml` | 9 |
| `tests/unit/test_seuils.py` | `charger_seuils`, `Seuils.surcharger` | 1 |
| `tests/unit/test_notation.py` | `noter_contrat`, `combiner_essais`, `agreger` | 2 |
| `tests/unit/test_moteurs.py` | `MOTEURS`, `moteur_pour` | 3 |
| `tests/integration/test_gate.py` | `evaluer` bout en bout + CLI | 4–5 |
| `tests/unit/test_versions.py` | `prochaine_version`, `valider_version`, `versions_connues`, `_tags_git` | 6 |
| `tests/integration/test_publication.py` | `publier` + CLI | 7–8 |

`eval/run_eval.py` reste un seul module (le stub fourni l'impose comme point d'entrée `python -m eval.run_eval`) ; il est organisé en sections : seuils, notation, moteurs, évaluation, CLI.

---

### Task 1: Seuils versionnés — `eval/seuils.yaml` et `charger_seuils`

**Files:**
- Create: `eval/seuils.yaml`
- Modify: `eval/run_eval.py` (imports ; nouvelle section après `CHEMIN_HISTORIQUE`)
- Test: `tests/unit/test_seuils.py`

**Interfaces:**
- Consumes: rien.
- Produces: `class ErreurSeuils(ValueError)` ; `@dataclass(frozen=True) class Seuils(note_min: float, latence_p95_max_ms: float, cout_moyen_max_eur: float, motif: str = "")` avec `surcharger(*, note_min=None, latence_p95_max_ms=None, cout_moyen_max_eur=None) -> Seuils` et `to_dict() -> dict` ; `chemin_seuils() -> Path` (lit `SEUILS_PATH` à l'appel) ; `charger_seuils(chemin: Path | str | None = None) -> Seuils`.

- [ ] **Step 1: Write the failing test**

Créer `tests/unit/test_seuils.py` :

```python
"""Tests unitaires — seuils du gate (eval/seuils.yaml)."""
from __future__ import annotations

from pathlib import Path

import pytest

from eval.run_eval import ErreurSeuils, Seuils, charger_seuils

VALIDE = "note_min: 0.8\nlatence_p95_max_ms: 5000\ncout_moyen_max_eur: 0.1\nmotif: essai\n"


def _ecrire(tmp_path: Path, contenu: str) -> Path:
    chemin = tmp_path / "seuils.yaml"
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


def test_seuils_du_repo():
    seuils = charger_seuils()
    assert (seuils.note_min, seuils.latence_p95_max_ms, seuils.cout_moyen_max_eur) == (
        0.75,
        8000.0,
        0.15,
    )
    assert seuils.motif


def test_fichier_valide(tmp_path):
    seuils = charger_seuils(_ecrire(tmp_path, VALIDE))
    assert seuils == Seuils(0.8, 5000.0, 0.1, "essai")


def test_variable_d_environnement(tmp_path, monkeypatch):
    monkeypatch.setenv("SEUILS_PATH", str(_ecrire(tmp_path, VALIDE)))
    assert charger_seuils().note_min == 0.8


def test_cle_manquante(tmp_path):
    with pytest.raises(ErreurSeuils, match="cout_moyen_max_eur"):
        charger_seuils(_ecrire(tmp_path, "note_min: 0.8\nlatence_p95_max_ms: 5000\n"))


@pytest.mark.parametrize("valeur", ["'beaucoup'", "true", "[1]"])
def test_valeur_non_numerique(tmp_path, valeur):
    contenu = f"note_min: {valeur}\nlatence_p95_max_ms: 1\ncout_moyen_max_eur: 1\n"
    with pytest.raises(ErreurSeuils, match="note_min"):
        charger_seuils(_ecrire(tmp_path, contenu))


def test_fichier_absent(tmp_path):
    with pytest.raises(ErreurSeuils, match="introuvable"):
        charger_seuils(tmp_path / "absent.yaml")


@pytest.mark.parametrize("contenu", ["note_min: [", "- 0.75\n"])
def test_yaml_invalide_ou_pas_un_dictionnaire(tmp_path, contenu):
    with pytest.raises(ErreurSeuils):
        charger_seuils(_ecrire(tmp_path, contenu))


def test_erreur_seuils_est_une_value_error():
    assert issubclass(ErreurSeuils, ValueError)


def test_surcharge_ne_remplace_que_les_valeurs_fournies():
    seuils = Seuils(0.75, 8000.0, 0.15, "m")
    surcharge = seuils.surcharger(note_min=0.9, latence_p95_max_ms=None, cout_moyen_max_eur=0)
    assert surcharge == Seuils(0.9, 8000.0, 0.0, "m")
    assert seuils.note_min == 0.75
    assert surcharge.to_dict() == {
        "note_min": 0.9,
        "latence_p95_max_ms": 8000.0,
        "cout_moyen_max_eur": 0.0,
        "motif": "m",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_seuils.py -q`
Expected: FAIL — `ImportError: cannot import name 'ErreurSeuils' from 'eval.run_eval'`

- [ ] **Step 3: Write minimal implementation**

Créer `eval/seuils.yaml` :

```yaml
# Seuils du gate d'évaluation — config as code.
#
# Toute modification passe par un commit (jamais en silence) et met à jour
# « motif » : pourquoi ce seuil, sur quelle observation. Chaque rapport du
# gate recopie les seuils appliqués (eval/history.jsonl).
# Le seuil par contrat reste « seuil_note » dans eval/attendus.jsonl.

note_min: 0.75              # note globale minimale (rappel moyen des clauses attendues)
latence_p95_max_ms: 8000    # contrainte client : P95 < 8 s par analyse
cout_moyen_max_eur: 0.15    # contrainte client : < 0,15 € par analyse
motif: "seuils initiaux — contraintes client (P95 < 8 s, < 0,15 € par analyse), note = seuil_note de base"
```

Dans `eval/run_eval.py`, remplacer le bloc d'imports par :

```python
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from app.llm_client import Bundle
from app.telemetry import Telemetry
from ops.registry import MOTIF_VERSION, Registry
```

Puis, juste après la ligne `CHEMIN_HISTORIQUE = RACINE / "eval" / "history.jsonl"`, ajouter :

```python
CHEMIN_SEUILS_DEFAUT = RACINE / "eval" / "seuils.yaml"
CLES_SEUILS = ("note_min", "latence_p95_max_ms", "cout_moyen_max_eur")


# ------------------------------------------------------------------- seuils
class ErreurSeuils(ValueError):
    """Le fichier de seuils du gate est absent ou invalide."""


@dataclass(frozen=True)
class Seuils:
    note_min: float
    latence_p95_max_ms: float
    cout_moyen_max_eur: float
    motif: str = ""

    def surcharger(
        self,
        *,
        note_min: float | None = None,
        latence_p95_max_ms: float | None = None,
        cout_moyen_max_eur: float | None = None,
    ) -> Seuils:
        """Remplace les seuils fournis (usage local et tests) ; ``None`` = inchangé."""
        valeurs = {
            "note_min": note_min,
            "latence_p95_max_ms": latence_p95_max_ms,
            "cout_moyen_max_eur": cout_moyen_max_eur,
        }
        return replace(self, **{k: float(v) for k, v in valeurs.items() if v is not None})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def chemin_seuils() -> Path:
    """``SEUILS_PATH`` si défini, sinon ``eval/seuils.yaml`` (lu à chaque appel)."""
    return Path(os.environ.get("SEUILS_PATH") or CHEMIN_SEUILS_DEFAUT)


def charger_seuils(chemin: Path | str | None = None) -> Seuils:
    chemin = Path(chemin) if chemin else chemin_seuils()
    try:
        data = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ErreurSeuils(f"fichier de seuils introuvable : {chemin}") from exc
    except yaml.YAMLError as exc:
        raise ErreurSeuils(f"{chemin} : YAML invalide ({exc})") from exc
    if not isinstance(data, dict):
        raise ErreurSeuils(f"{chemin} : attendu un dictionnaire de seuils")
    valeurs: dict[str, float] = {}
    for cle in CLES_SEUILS:
        if cle not in data:
            raise ErreurSeuils(f"{chemin} : clé « {cle} » manquante")
        valeur = data[cle]
        if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
            raise ErreurSeuils(f"{chemin} : « {cle} » doit être un nombre, reçu {valeur!r}")
        valeurs[cle] = float(valeur)
    return Seuils(**valeurs, motif=str(data.get("motif") or ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_seuils.py -q && uv run ruff check .`
Expected: `12 passed`, ruff `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add eval/seuils.yaml eval/run_eval.py tests/unit/test_seuils.py
git commit -m "feat(gate): seuils versionnés dans eval/seuils.yaml (charger_seuils)"
```

---

### Task 2: Notation pure — `noter_contrat`, `combiner_essais`, `agreger`

**Files:**
- Modify: `eval/run_eval.py` (import `Iterable` ; nouvelle section après `_p95`)
- Test: `tests/unit/test_notation.py`

**Interfaces:**
- Consumes: `Seuils` (Task 1) ; `_p95(valeurs: list[float]) -> float` (existant dans `eval/run_eval.py`).
- Produces:
  - `noter_contrat(trouves: Iterable[str], attendu: dict) -> dict` → `{"note", "seuil_note", "passe", "trouvees", "manquantes"}` ;
  - `combiner_essais(essais: list[dict]) -> dict` — chaque essai = sortie de `noter_contrat` + `"latence_ms"`, `"cout_eur"` ; renvoie le même format (note moyenne, `passe` recalculé, trouvées/manquantes du dernier essai, latence et coût moyens) ;
  - `agreger(par_contrat: dict[str, dict], latences: list[float], couts: list[float], seuils: Seuils, erreurs: Iterable[str] = ()) -> tuple[float, float, float, list[str]]` → `(note, p95, cout_moyen, motifs)`.

- [ ] **Step 1: Write the failing test**

Créer `tests/unit/test_notation.py` :

```python
"""Tests unitaires — notation du gate (fonctions pures)."""
from __future__ import annotations

import pytest

from eval.run_eval import Seuils, agreger, combiner_essais, noter_contrat

COURT = {
    "contrat_id": "c02",
    "clauses_attendues": [
        "durée",
        "prix et paiement",
        "pénalité de retard",
        "résiliation",
        "garantie",
    ],
    "seuil_note": 0.75,
}
LONG = {
    "contrat_id": "c07",
    "clauses_attendues": ["durée", "résiliation", "garantie", "confidentialité"],
    "seuil_note": 0.80,
}
SEUILS = Seuils(note_min=0.75, latence_p95_max_ms=8000.0, cout_moyen_max_eur=0.15)


def _contrat(note: float, seuil: float = 0.75) -> dict:
    return {"note": note, "seuil_note": seuil, "passe": note >= seuil, "trouvees": [],
            "manquantes": []}


def test_tout_trouve():
    assert noter_contrat(COURT["clauses_attendues"], COURT) == {
        "note": 1.0,
        "seuil_note": 0.75,
        "passe": True,
        "trouvees": COURT["clauses_attendues"],
        "manquantes": [],
    }


def test_rappel_partiel_dans_l_ordre_attendu_et_types_en_trop_ignores():
    resultat = noter_contrat(["garantie", "durée", "résiliation", "exclusivité"], COURT)
    assert resultat["note"] == 0.6
    assert resultat["trouvees"] == ["durée", "résiliation", "garantie"]
    assert resultat["manquantes"] == ["prix et paiement", "pénalité de retard"]
    assert resultat["passe"] is False


def test_seuil_note_des_contrats_longs():
    trois_sur_quatre = noter_contrat(["durée", "résiliation", "garantie"], LONG)
    assert trois_sur_quatre["note"] == 0.75
    assert trois_sur_quatre["passe"] is False  # 0,75 < 0,80
    assert noter_contrat(LONG["clauses_attendues"], LONG)["passe"] is True


def test_combiner_essais_fait_la_moyenne():
    premier = {**noter_contrat(["durée", "résiliation", "garantie"], LONG),
               "latence_ms": 100.0, "cout_eur": 0.01}
    second = {**noter_contrat(LONG["clauses_attendues"], LONG),
              "latence_ms": 300.0, "cout_eur": 0.03}
    combine = combiner_essais([premier, second])
    assert combine["note"] == pytest.approx(0.875)
    assert combine["passe"] is True
    assert combine["manquantes"] == []  # dernier essai
    assert combine["latence_ms"] == pytest.approx(200.0)
    assert combine["cout_eur"] == pytest.approx(0.02)


def test_agreger_sans_motif():
    note, p95, cout, motifs = agreger(
        {"c01": _contrat(1.0), "c02": _contrat(0.8)}, [100.0, 200.0], [0.01, 0.03], SEUILS
    )
    assert note == pytest.approx(0.9)
    assert p95 == 200.0
    assert cout == pytest.approx(0.02)
    assert motifs == []


def test_agreger_note_globale_sous_le_seuil():
    _, _, _, motifs = agreger(
        {"c01": _contrat(0.6, 0.5), "c02": _contrat(0.7, 0.5)}, [1.0], [0.0], SEUILS
    )
    assert motifs == ["note 0.650 < seuil 0.75"]


def test_agreger_contrat_sous_son_seuil():
    _, _, _, motifs = agreger(
        {"c01": _contrat(1.0), "c10": _contrat(0.75, 0.80)}, [1.0], [0.0], SEUILS
    )
    assert motifs == ["c10 : note 0.75 < seuil_note 0.80"]


def test_agreger_latence_et_cout():
    _, _, _, motifs = agreger({"c01": _contrat(1.0)}, [9120.0], [0.18], SEUILS)
    assert motifs == ["latence P95 9120 ms ≥ 8000 ms", "coût moyen 0.1800 € ≥ 0.15 €"]


def test_agreger_erreurs_en_tete():
    _, _, _, motifs = agreger(
        {"c01": _contrat(1.0)}, [1.0], [0.0], SEUILS, erreurs=["c03 : erreur LLM — timeout"]
    )
    assert motifs == ["c03 : erreur LLM — timeout"]


def test_agreger_sans_mesure():
    note, p95, cout, _ = agreger({"c01": _contrat(0.0)}, [], [], SEUILS)
    assert (note, p95, cout) == (0.0, 0.0, 0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_notation.py -q`
Expected: FAIL — `ImportError: cannot import name 'agreger' from 'eval.run_eval'`

- [ ] **Step 3: Write minimal implementation**

Dans `eval/run_eval.py`, ajouter à la liste des imports (ordre alphabétique des modules de la bibliothèque standard) :

```python
from collections.abc import Iterable
```

Puis, juste après la fonction `_p95` existante, ajouter :

```python
# ----------------------------------------------------------------- notation
def noter_contrat(trouves: Iterable[str], attendu: dict[str, Any]) -> dict[str, Any]:
    """Rappel des clauses attendues d'un contrat (fonction pure)."""
    presentes = set(trouves)
    attendues: list[str] = list(attendu["clauses_attendues"])
    trouvees = [c for c in attendues if c in presentes]
    manquantes = [c for c in attendues if c not in presentes]
    note = round(len(trouvees) / len(attendues), 4) if attendues else 1.0
    seuil_note = float(attendu["seuil_note"])
    return {
        "note": note,
        "seuil_note": seuil_note,
        "passe": note >= seuil_note,
        "trouvees": trouvees,
        "manquantes": manquantes,
    }


def combiner_essais(essais: list[dict[str, Any]]) -> dict[str, Any]:
    """Moyenne des passes d'un contrat ; trouvées / manquantes du dernier essai."""
    dernier = essais[-1]
    n = len(essais)
    note = round(sum(e["note"] for e in essais) / n, 4)
    return {
        **dernier,
        "note": note,
        "passe": note >= dernier["seuil_note"],
        "latence_ms": round(sum(e["latence_ms"] for e in essais) / n, 1),
        "cout_eur": round(sum(e["cout_eur"] for e in essais) / n, 6),
    }


def agreger(
    par_contrat: dict[str, dict[str, Any]],
    latences: list[float],
    couts: list[float],
    seuils: Seuils,
    erreurs: Iterable[str] = (),
) -> tuple[float, float, float, list[str]]:
    """Note globale, P95, coût moyen et motifs d'échec (liste vide = gate passé)."""
    notes = [c["note"] for c in par_contrat.values()]
    note = round(sum(notes) / len(notes), 4) if notes else 0.0
    p95 = _p95(latences)
    cout = round(sum(couts) / len(couts), 6) if couts else 0.0
    motifs = list(erreurs)
    if note < seuils.note_min:
        motifs.append(f"note {note:.3f} < seuil {seuils.note_min}")
    for cid, c in par_contrat.items():
        if not c["passe"]:
            motifs.append(f"{cid} : note {c['note']:.2f} < seuil_note {c['seuil_note']:.2f}")
    if p95 >= seuils.latence_p95_max_ms:
        motifs.append(f"latence P95 {p95:.0f} ms ≥ {seuils.latence_p95_max_ms:.0f} ms")
    if cout >= seuils.cout_moyen_max_eur:
        motifs.append(f"coût moyen {cout:.4f} € ≥ {seuils.cout_moyen_max_eur} €")
    return note, p95, cout, motifs
```

Note : `test_agreger_sans_mesure` a une note 0,0 → le motif « note … < seuil » est produit ; le test ne vérifie que les trois valeurs numériques.

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_notation.py -q && uv run ruff check .`
Expected: `10 passed`, ruff propre.

- [ ] **Step 5: Commit**

```bash
git add eval/run_eval.py tests/unit/test_notation.py
git commit -m "feat(gate): notation par contrat, moyenne des essais et agrégation des motifs"
```

---

### Task 3: Moteur choisi par la stratégie du bundle

**Files:**
- Modify: `eval/run_eval.py` (imports ; nouvelle section après `agreger`)
- Test: `tests/unit/test_moteurs.py`

**Interfaces:**
- Consumes: `analyser_v1(texte, client, telemetry) -> ReponseAnalyseV1` (`.clauses: list[str]`) de `app/api_v1.py` ; `analyser_v2(texte, client, telemetry) -> ReponseAnalyseV2` (`.clauses: list[ClauseV2]`, chaque `ClauseV2.type: str`) de `app/api_v2.py`.
- Produces: `Moteur = Callable[[str, LLMClient, Telemetry], list[str]]` ; `MOTEURS: dict[str, Moteur]` avec les clés `"monolithique"` et `"map_reduce_clauses"` ; `moteur_pour(strategie: str) -> Moteur` (lève `ValueError` si inconnue).

- [ ] **Step 1: Write the failing test**

Créer `tests/unit/test_moteurs.py` :

```python
"""Tests unitaires — choix du moteur d'évaluation selon la stratégie du bundle."""
from __future__ import annotations

import pytest

from app.llm_client import Bundle, LLMClient
from eval.run_eval import MOTEURS, moteur_pour
from tests.unit.doublures import FauxClient

CONTRAT = "".join(
    f"Article {i} — Résiliation\nLe contrat peut être résilié par chaque partie.\n"
    for i in range(1, 4)
)


def test_strategies_connues():
    assert set(MOTEURS) == {"monolithique", "map_reduce_clauses"}


def test_moteur_v2_renvoie_les_types(telemetry):
    types = moteur_pour("map_reduce_clauses")(CONTRAT, FauxClient(), telemetry)
    assert types == ["résiliation"]


def test_moteur_v1_renvoie_les_types(telemetry):
    types = moteur_pour("monolithique")(CONTRAT, LLMClient(Bundle.charger("v1")), telemetry)
    assert "résiliation" in types


def test_strategie_inconnue():
    with pytest.raises(ValueError, match="stratégie 'rag' sans moteur d'évaluation"):
        moteur_pour("rag")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_moteurs.py -q`
Expected: FAIL — `ImportError: cannot import name 'MOTEURS' from 'eval.run_eval'`

- [ ] **Step 3: Write minimal implementation**

Dans `eval/run_eval.py`, remplacer la ligne `from collections.abc import Iterable` par :

```python
from collections.abc import Callable, Iterable
```

et remplacer les imports applicatifs par :

```python
from app.api_v1 import analyser_v1
from app.api_v2 import analyser_v2
from app.llm_client import Bundle, LLMClient
from app.telemetry import Telemetry
from ops.registry import MOTIF_VERSION, Registry
```

Puis, juste après `agreger`, ajouter :

```python
# ------------------------------------------------------------------ moteurs
Moteur = Callable[[str, LLMClient, Telemetry], list[str]]


def _types_v1(texte: str, client: LLMClient, telemetry: Telemetry) -> list[str]:
    return list(analyser_v1(texte, client, telemetry).clauses)


def _types_v2(texte: str, client: LLMClient, telemetry: Telemetry) -> list[str]:
    return [c.type for c in analyser_v2(texte, client, telemetry).clauses]


# Une nouvelle stratégie (v3…) = une nouvelle entrée ; evaluer ne change pas.
MOTEURS: dict[str, Moteur] = {"monolithique": _types_v1, "map_reduce_clauses": _types_v2}


def moteur_pour(strategie: str) -> Moteur:
    try:
        return MOTEURS[strategie]
    except KeyError:
        raise ValueError(f"stratégie {strategie!r} sans moteur d'évaluation") from None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_moteurs.py -q && uv run ruff check .`
Expected: `4 passed`, ruff propre.

- [ ] **Step 5: Commit**

```bash
git add eval/run_eval.py tests/unit/test_moteurs.py
git commit -m "feat(gate): moteur d'évaluation choisi par la stratégie du bundle"
```

---

### Task 4: `evaluer` et `Rapport` enrichi

**Files:**
- Modify: `eval/run_eval.py` (imports ; `Rapport` ; `evaluer` ; nouvelles fonctions privées)
- Test: `tests/integration/test_gate.py`

**Interfaces:**
- Consumes: `charger_seuils`, `Seuils.surcharger`, `Seuils.to_dict` (Task 1) ; `noter_contrat`, `combiner_essais`, `agreger` (Task 2) ; `moteur_pour` (Task 3) ; `charger_attendus`, `charger_bundle`, `_p95` (existants) ; `mode_mock()` de `app.llm_client` ; `build_telemetry(*, span_exporter, metrics_path, level)` et `NoopSpanExporter` de `app.telemetry` ; `telemetry.metriques.lire() -> list[Mesure]` (`Mesure.latence_ms`, `Mesure.cout_eur`).
- Produces:
  - `Rapport` avec les champs existants plus `mode_eval: str = "mock"`, `seuils: dict = {}`, `bundle_empreinte: str = ""`, et `Rapport.depuis_dict(data: dict) -> Rapport` (ignore les clés inconnues) ;
  - `mode_eval() -> str` ;
  - `evaluer(version, *, n_essais=None, seuil=None, latence_max_ms=None, cout_max_eur=None, contrats=DOSSIER_CONTRATS, attendus=CHEMIN_ATTENDUS, registry=None, telemetry=None, sous_ensemble=None, historique=CHEMIN_HISTORIQUE, seuils=None) -> Rapport`.

- [ ] **Step 1: Write the failing test**

Créer `tests/integration/test_gate.py` :

```python
"""Tests d'intégration — gate d'évaluation (vrais bundles, LLMClient en MOCK)."""
from __future__ import annotations

import json

import pytest

from app.llm_client import Bundle
from eval.run_eval import Rapport, evaluer

COURTS = ["c01", "c02", "c03", "c04"]


@pytest.fixture(autouse=True)
def metriques_eval(tmp_path, monkeypatch):
    """Le gate écrit ses mesures à part — jamais dans le dépôt pendant les tests."""
    monkeypatch.setenv("METRICS_EVAL_PATH", str(tmp_path / "metrics_eval.jsonl"))


def test_rapport_v1_trace_dans_l_historique(historique):
    rapport = evaluer("v1", sous_ensemble=COURTS, historique=historique)

    assert set(rapport.par_contrat) == set(COURTS)
    assert rapport.version == "v1.0.0"
    assert rapport.mode_eval == "mock"
    assert rapport.seuils["note_min"] == 0.75 and rapport.seuils["motif"]
    assert rapport.bundle_empreinte == Bundle.charger("v1").empreinte()
    assert all(c["latence_ms"] >= 0 and c["cout_eur"] >= 0 for c in rapport.par_contrat.values())
    [ligne] = historique.read_text(encoding="utf-8").splitlines()
    trace = json.loads(ligne)
    assert trace["version"] == "v1.0.0"
    assert trace["mode_eval"] == "mock"
    assert trace["seuils"]["note_min"] == 0.75
    assert trace["bundle_empreinte"] == rapport.bundle_empreinte


def test_v2_passe_et_bat_la_v1_sur_un_contrat_long(historique):
    v1 = evaluer("v1", sous_ensemble=["c07"], historique=historique)
    v2 = evaluer("v2", sous_ensemble=["c07"], historique=historique)
    assert v2.passe, v2.motifs
    assert not v1.passe
    assert "c07 : note" in " ".join(v1.motifs)
    assert v2.par_contrat["c07"]["note"] > v1.par_contrat["c07"]["note"]


def test_surcharge_du_seuil_tracee(historique):
    rapport = evaluer("v2", sous_ensemble=["c01"], seuil=0.9, historique=historique)
    assert rapport.seuil == 0.9
    assert rapport.seuils["note_min"] == 0.9


def test_essais_multiples(historique):
    rapport = evaluer("v2", sous_ensemble=["c01"], n_essais=2, historique=historique)
    assert rapport.essais == 2


def test_telemetrie_injectee(telemetry, metriques, historique):
    evaluer("v2", sous_ensemble=["c01", "c02"], telemetry=telemetry, historique=historique)
    assert len(metriques.lire()) == 2


def test_fournisseur_injoignable_fait_echouer_le_gate_sans_planter(historique, monkeypatch):
    monkeypatch.setenv("MOCK", "off")
    monkeypatch.setenv("LLM_PROXY_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("LLM_TIMEOUT_S", "1")

    rapport = evaluer("v1", sous_ensemble=["c01"], historique=historique)

    assert rapport.passe is False
    assert rapport.mode_eval == "reel"
    assert rapport.par_contrat["c01"]["note"] == 0.0
    assert any(m.startswith("c01 : erreur LLM") for m in rapport.motifs)


def test_dossier_de_contrats_incoherent(contrats_courts):
    with pytest.raises(FileNotFoundError, match="c05"):
        evaluer("v1", contrats=contrats_courts, historique=None)


def test_contrat_non_annote():
    with pytest.raises(ValueError, match="c99"):
        evaluer("v1", sous_ensemble=["c99"], historique=None)


def test_rapport_depuis_dict_aller_retour(historique):
    rapport = evaluer("v2", sous_ensemble=["c01"], historique=historique)
    donnees = json.loads(json.dumps(rapport.to_dict()))
    assert Rapport.depuis_dict({**donnees, "cle_inconnue": 1}) == rapport
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_gate.py -q`
Expected: FAIL — `NotImplementedError: eval.run_eval.evaluer — le gate d'évaluation` (et `AttributeError` sur `Rapport.depuis_dict`).

- [ ] **Step 3: Write minimal implementation**

Dans `eval/run_eval.py`, remplacer le bloc d'imports complet par :

```python
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.api_v1 import analyser_v1
from app.api_v2 import analyser_v2
from app.llm_client import Bundle, ErreurLLM, LLMClient, mode_mock
from app.pipeline import DocumentTropLong
from app.telemetry import NoopSpanExporter, Telemetry, build_telemetry
from ops.registry import MOTIF_VERSION, Registry
```

Sous `CHEMIN_SEUILS_DEFAUT`, ajouter :

```python
CHEMIN_METRIQUES_EVAL_DEFAUT = RACINE / "eval" / ".metrics_eval.jsonl"
```

Remplacer la classe `Rapport` par :

```python
@dataclass
class Rapport:
    version: str
    date: str
    essais: int
    note: float
    par_contrat: dict[str, dict[str, Any]]
    latence_p95_ms: float
    cout_moyen_eur: float
    passe: bool
    motifs: list[str] = field(default_factory=list)
    seuil: float = 0.75
    mode_eval: str = "mock"
    seuils: dict[str, Any] = field(default_factory=dict)
    bundle_empreinte: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def depuis_dict(cls, data: dict[str, Any]) -> Rapport:
        """Relit un rapport sérialisé (``--sortie``) ; les clés inconnues sont ignorées."""
        noms = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in noms})
```

Remplacer la fonction `evaluer` (le stub `raise NotImplementedError`) par le bloc suivant, placé après `moteur_pour` (section « évaluation ») :

```python
# --------------------------------------------------------------- évaluation
def mode_eval() -> str:
    """``reel`` seulement quand le vrai modèle répond sans enregistrement (``MOCK=off``)."""
    return "reel" if mode_mock() == "off" else "mock"


def _telemetry_eval() -> Telemetry:
    """Télémétrie du gate : pas de spans, mesures à part de ``ops/metrics.jsonl``."""
    chemin = os.environ.get("METRICS_EVAL_PATH") or CHEMIN_METRIQUES_EVAL_DEFAUT
    return build_telemetry(
        span_exporter=NoopSpanExporter(),
        metrics_path=chemin,
        level=os.environ.get("LOG_LEVEL", "INFO"),
    )


def _charger_contrats(
    dossier: Path, annotes: dict[str, dict[str, Any]], sous_ensemble: list[str] | None
) -> dict[str, str]:
    """Textes des contrats retenus ; un golden dataset incohérent ne passe jamais."""
    ids = sous_ensemble or sorted(annotes)
    inconnus = [cid for cid in ids if cid not in annotes]
    if inconnus:
        raise ValueError(f"contrat(s) sans annotation dans attendus.jsonl : {', '.join(inconnus)}")
    textes: dict[str, str] = {}
    for cid in ids:
        chemin = Path(dossier) / f"{cid}.txt"
        if not chemin.exists():
            raise FileNotFoundError(f"contrat annoté introuvable : {chemin}")
        textes[cid] = chemin.read_text(encoding="utf-8")
    return textes


def _essai_en_erreur(attendu: dict[str, Any]) -> dict[str, Any]:
    return {**noter_contrat([], attendu), "latence_ms": 0.0, "cout_eur": 0.0}


def evaluer(
    version: str,
    *,
    n_essais: int | None = None,
    seuil: float | None = None,
    latence_max_ms: float | None = None,
    cout_max_eur: float | None = None,
    contrats: Path = DOSSIER_CONTRATS,
    attendus: Path = CHEMIN_ATTENDUS,
    registry: Registry | None = None,
    telemetry: Telemetry | None = None,
    sous_ensemble: list[str] | None = None,
    historique: Path | None = CHEMIN_HISTORIQUE,
    seuils: Path | None = None,
) -> Rapport:
    bundle = charger_bundle(version, registry)
    moteur = moteur_pour(bundle.strategie)
    appliques = charger_seuils(seuils).surcharger(
        note_min=seuil, latence_p95_max_ms=latence_max_ms, cout_moyen_max_eur=cout_max_eur
    )
    annotes = charger_attendus(attendus)
    textes = _charger_contrats(contrats, annotes, sous_ensemble)
    telemetry = telemetry or _telemetry_eval()
    n = n_essais or int(bundle.parametres.get("essais_eval") or 1)

    essais: dict[str, list[dict[str, Any]]] = {cid: [] for cid in textes}
    latences: list[float] = []
    couts: list[float] = []
    erreurs: list[str] = []
    for _ in range(n):
        for cid, texte in textes.items():
            try:
                trouves = moteur(texte, LLMClient(bundle), telemetry)
            except ErreurLLM as exc:
                erreurs.append(f"{cid} : erreur LLM — {exc}")
                essais[cid].append(_essai_en_erreur(annotes[cid]))
                continue
            except DocumentTropLong as exc:
                erreurs.append(f"{cid} : document trop long — {exc}")
                essais[cid].append(_essai_en_erreur(annotes[cid]))
                continue
            mesure = telemetry.metriques.lire()[-1]  # le gate est séquentiel
            latences.append(mesure.latence_ms)
            couts.append(mesure.cout_eur)
            essais[cid].append(
                {
                    **noter_contrat(trouves, annotes[cid]),
                    "latence_ms": mesure.latence_ms,
                    "cout_eur": mesure.cout_eur,
                }
            )

    par_contrat = {cid: combiner_essais(e) for cid, e in essais.items()}
    note, p95, cout, motifs = agreger(par_contrat, latences, couts, appliques, erreurs)
    rapport = Rapport(
        version=bundle.version,
        date=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        essais=n,
        note=note,
        par_contrat=par_contrat,
        latence_p95_ms=p95,
        cout_moyen_eur=cout,
        passe=not motifs,
        motifs=motifs,
        seuil=appliques.note_min,
        mode_eval=mode_eval(),
        seuils=appliques.to_dict(),
        bundle_empreinte=bundle.empreinte(),
    )
    if historique is not None:
        historique = Path(historique)
        historique.parent.mkdir(parents=True, exist_ok=True)
        with historique.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rapport.to_dict(), ensure_ascii=False) + "\n")
    return rapport
```

Mettre à jour la docstring du module : dans le bloc « Contrat attendu », remplacer `seuil=0.75, latence_max_ms=8000, cout_max_eur=0.15` par `seuil=None, latence_max_ms=None, cout_max_eur=None` et ajouter, après la puce « chaque exécution ajoute une ligne à ``eval/history.jsonl`` (le rapport) ; », la puce :

```
* les seuils viennent de ``eval/seuils.yaml`` (``SEUILS_PATH``) ; les
  arguments ``seuil`` / ``latence_max_ms`` / ``cout_max_eur`` les surchargent ;
  le rapport recopie les seuils appliqués et ``mode_eval`` (``mock`` | ``reel``) ;
```

Retirer aussi le marqueur `[STUB]` de la première ligne de la docstring.

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_gate.py -q && uv run ruff check .`
Expected: `9 passed`, ruff propre.

Puis les deux tests d'acceptance du gate :

Run: `MOCK=on uv run pytest -q tests/acceptance -k "gate_evaluation_note_par_version or evaluation_enrichie"`
Expected: `2 passed`

Puis non-régression complète :

Run: `MOCK=on uv run pytest -q`
Expected: seuls échouent `test_etiquetage_version_apres_gate` (`NotImplementedError: deploy.publier`) et les 4 tests des sous-projets 3 et 4 (`test_rollback_en_une_operation`, `test_promotion_canary_puis_totale`, `test_dashboard_par_version`, `test_journal_derive_et_rollback_automatique`).

- [ ] **Step 5: Commit**

```bash
git add eval/run_eval.py tests/integration/test_gate.py
git commit -m "feat(gate): evaluer — note par contrat, P95, coût, seuils tracés dans history.jsonl"
```

---

### Task 5: CLI du gate — `--sortie`, `--historique`, codes 0 / 1 / 2

**Files:**
- Modify: `eval/run_eval.py` (fonction `main`)
- Test: `tests/integration/test_gate.py` (ajout)

**Interfaces:**
- Consumes: `evaluer`, `afficher`, `Rapport.to_dict`, `Rapport.depuis_dict` (Task 4).
- Produces: `main(argv: list[str] | None = None) -> int` avec les options `--version`, `--essais`, `--contrats`, `--seuil`, `--latence-max-ms`, `--cout-max-eur` (défaut `None` = valeur du fichier), `--sortie CHEMIN` (JSON du rapport), `--historique CHEMIN` (défaut `eval/history.jsonl`). La CI (Task 9) appelle `python -m eval.run_eval --version v2 --sortie eval/rapport.json`.

- [ ] **Step 1: Write the failing test**

Ajouter à la fin de `tests/integration/test_gate.py` :

```python
def test_cli_codes_de_sortie_et_rapport_json(tmp_path, monkeypatch, capsys):
    from eval.run_eval import main

    historique = tmp_path / "h.jsonl"
    sortie = tmp_path / "gate" / "rapport.json"
    commun = ["--version", "v2", "--historique", str(historique)]

    assert main([*commun, "--contrats", "c01,c02", "--sortie", str(sortie)]) == 0
    donnees = json.loads(sortie.read_text(encoding="utf-8"))
    relu = Rapport.depuis_dict(donnees)
    assert relu.passe and set(relu.par_contrat) == {"c01", "c02"}
    assert relu.to_dict() == donnees

    assert main([*commun, "--contrats", "c01", "--cout-max-eur", "0"]) == 1

    invalide = tmp_path / "seuils.yaml"
    invalide.write_text("note_min: 0.75\n", encoding="utf-8")
    monkeypatch.setenv("SEUILS_PATH", str(invalide))
    assert main([*commun, "--contrats", "c01"]) == 2
    assert "latence_p95_max_ms" in capsys.readouterr().err

    assert len(historique.read_text(encoding="utf-8").splitlines()) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_gate.py::test_cli_codes_de_sortie_et_rapport_json -q`
Expected: FAIL — `SystemExit: 2` (argparse : `unrecognized arguments: --historique`).

- [ ] **Step 3: Write minimal implementation**

Remplacer la fonction `main` de `eval/run_eval.py` par :

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate d'évaluation Mardik")
    parser.add_argument("--version", default="v2")
    parser.add_argument("--essais", type=int, default=None)
    parser.add_argument("--contrats", default=None, help="liste c01,c02,… (défaut : tous)")
    parser.add_argument("--seuil", type=float, default=None,
                        help="surcharge note_min (défaut : eval/seuils.yaml)")
    parser.add_argument("--latence-max-ms", type=float, default=None,
                        help="surcharge latence_p95_max_ms (défaut : eval/seuils.yaml)")
    parser.add_argument("--cout-max-eur", type=float, default=None,
                        help="surcharge cout_moyen_max_eur (défaut : eval/seuils.yaml)")
    parser.add_argument("--sortie", default=None, help="écrit le rapport en JSON")
    parser.add_argument("--historique", default=str(CHEMIN_HISTORIQUE))
    args = parser.parse_args(argv)
    sous_ensemble = re.split(r"[,\s]+", args.contrats.strip()) if args.contrats else None
    try:
        rapport = evaluer(
            args.version,
            n_essais=args.essais,
            seuil=args.seuil,
            latence_max_ms=args.latence_max_ms,
            cout_max_eur=args.cout_max_eur,
            sous_ensemble=sous_ensemble,
            historique=Path(args.historique),
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"GATE MAL CONFIGURÉ : {exc}", file=sys.stderr)
        return 2
    afficher(rapport)
    if args.sortie:
        sortie = Path(args.sortie)
        sortie.parent.mkdir(parents=True, exist_ok=True)
        sortie.write_text(
            json.dumps(rapport.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if rapport.passe else 1
```

Dans la docstring du module, remplacer la ligne ``--essais N`` force le nombre de passes, ``--contrats c01,c07`` restreint. par :

```
  ``--essais N`` force le nombre de passes, ``--contrats c01,c07`` restreint,
  ``--sortie rapport.json`` écrit le rapport (relu par ``ops.deploy publier
  --rapport``), ``--historique`` change le fichier d'historique ; code 2 si le
  gate est mal configuré (seuils, golden dataset, stratégie).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_gate.py -q && uv run ruff check .`
Expected: `10 passed`, ruff propre.

Vérification manuelle de la CLI :

Run: `MOCK=on uv run python -m eval.run_eval --version v2 --historique "$CLAUDE_JOB_DIR/tmp/h.jsonl" --sortie "$CLAUDE_JOB_DIR/tmp/rapport.json"; echo "code=$?"` (hors session Claude : remplacer `$CLAUDE_JOB_DIR/tmp` par un dossier temporaire)
Expected: 12 lignes `OK`, `GATE : PASSE`, `code=0`.

- [ ] **Step 5: Commit**

```bash
git add eval/run_eval.py tests/integration/test_gate.py
git commit -m "feat(gate): CLI --sortie/--historique, code 2 pour un gate mal configuré"
```

---

### Task 6: Numérotation des versions — fonctions pures de `ops/deploy.py`

**Files:**
- Modify: `ops/deploy.py` (imports ; `_commit_courant` ; nouvelles fonctions avant `publier`)
- Test: `tests/unit/test_versions.py`

**Interfaces:**
- Consumes: `MOTIF_VERSION` (regex `^v\d+\.\d+\.\d+$`) et `Registry.versions() -> list[str]` de `ops/registry`.
- Produces:
  - `_tags_git() -> set[str]` — `git tag -l "v*" --no-contains HEAD` filtré par `MOTIF_VERSION` ; `set()` si git est absent ou hors dépôt ;
  - `versions_connues(registry: Registry) -> set[str]` = `set(registry.versions()) | _tags_git()` ;
  - `prochaine_version(version_bundle: str, connues: Iterable[str]) -> str` ;
  - `valider_version(version: str, version_bundle: str, connues: Iterable[str]) -> None` (lève `ErreurDeploiement`).

- [ ] **Step 1: Write the failing test**

Créer `tests/unit/test_versions.py` :

```python
"""Tests unitaires — numérotation des versions publiées."""
from __future__ import annotations

import subprocess

import pytest

from ops import deploy
from ops.deploy import ErreurDeploiement, prochaine_version, valider_version, versions_connues


@pytest.mark.parametrize(
    ("connues", "attendue"),
    [
        (set(), "v2.0.0"),
        ({"v2.0.0"}, "v2.0.1"),
        ({"v2.0.0", "v2.0.5"}, "v2.0.6"),
        ({"v1.0.0", "v2.1.0"}, "v2.0.0"),
        ({"v2.0.9", "v2.0.10"}, "v2.0.11"),
    ],
)
def test_prochaine_version(connues, attendue):
    assert prochaine_version("v2.0.0", connues) == attendue


def test_valider_version_acceptee():
    valider_version("v2.0.3", "v2.0.0", {"v1.0.0", "v2.0.0"})


@pytest.mark.parametrize(
    ("version", "motif"),
    [
        ("2.0.3", "invalide"),
        ("v2.0", "invalide"),
        ("v3.0.0", "ne correspond pas au bundle v2.0.0"),
        ("v2.0.0", "déjà publiée"),
    ],
)
def test_valider_version_refusee(version, motif):
    with pytest.raises(ErreurDeploiement, match=motif):
        valider_version(version, "v2.0.0", {"v2.0.0"})


def test_versions_connues_fusionne_registre_et_tags(registry, monkeypatch):
    monkeypatch.setattr(deploy, "_tags_git", lambda: {"v2.0.3"})
    assert versions_connues(registry) == {"v1.0.0", "v2.0.3"}


def test_tags_git_sans_git(monkeypatch):
    def git_absent(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "check_output", git_absent)
    assert deploy._tags_git() == set()


def test_tags_git_hors_depot(monkeypatch):
    def hors_depot(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(subprocess, "check_output", hors_depot)
    assert deploy._tags_git() == set()


def test_tags_git_ignore_le_commit_publie_et_filtre_le_motif(monkeypatch):
    appels = []

    def faux_git(commande, **kwargs):
        appels.append(commande)
        return "v2.0.0\nv2.0.1-rc\nvieux\nv2.0.2\n"

    monkeypatch.setattr(subprocess, "check_output", faux_git)
    assert deploy._tags_git() == {"v2.0.0", "v2.0.2"}
    assert appels == [["git", "tag", "-l", "v*", "--no-contains", "HEAD"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_versions.py -q`
Expected: FAIL — `ImportError: cannot import name 'prochaine_version' from 'ops.deploy'`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py`, remplacer le bloc d'imports par :

```python
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.telemetry import MetricsStore
from ops.registry import MOTIF_VERSION, Registry

RACINE = Path(__file__).resolve().parent.parent
```

Remplacer `_commit_courant` par (règle du projet : pas de `except Exception`) :

```python
def _commit_courant() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "local"
```

Puis ajouter, juste avant `def publier` :

```python
# ----------------------------------------------------------------- versions
def _tags_git() -> set[str]:
    """Tags ``vX.Y.Z`` du dépôt, **hors** ceux posés sur le commit publié.

    Un tag sur HEAD est le label de ce commit, pas une version concurrente :
    un run déclenché par le tag ``v2.0.3`` doit pouvoir publier ``v2.0.3``.
    """
    try:
        sortie = subprocess.check_output(
            ["git", "tag", "-l", "v*", "--no-contains", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            cwd=RACINE,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return set()
    return {t.strip() for t in sortie.splitlines() if MOTIF_VERSION.match(t.strip())}


def versions_connues(registry: Registry) -> set[str]:
    """Versions déjà livrées : registre local ∪ tags git (la mémoire en CI)."""
    return set(registry.versions()) | _tags_git()


def _base(version: str) -> tuple[int, int]:
    majeure, mineure, _ = version.lstrip("v").split(".")
    return int(majeure), int(mineure)


def prochaine_version(version_bundle: str, connues: Iterable[str]) -> str:
    """Patch suivant sur la base ``vX.Y`` du bundle ; la version du bundle si aucune."""
    base = _base(version_bundle)
    patchs = [
        int(v.rsplit(".", 1)[1]) for v in connues if MOTIF_VERSION.match(v) and _base(v) == base
    ]
    if not patchs:
        return version_bundle
    return f"v{base[0]}.{base[1]}.{max(patchs) + 1}"


def valider_version(version: str, version_bundle: str, connues: Iterable[str]) -> None:
    if not MOTIF_VERSION.match(version):
        raise ErreurDeploiement(f"version invalide : {version!r} (attendu vX.Y.Z)")
    if _base(version) != _base(version_bundle):
        raise ErreurDeploiement(f"le tag {version} ne correspond pas au bundle {version_bundle}")
    if version in set(connues):
        raise ErreurDeploiement(f"{version} déjà publiée")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_versions.py -q && uv run ruff check .`
Expected: `14 passed`, ruff propre.

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/unit/test_versions.py
git commit -m "feat(publication): numérotation automatique du patch (registre ∪ tags git)"
```

---

### Task 7: `publier` — gate puis étiquetage

**Files:**
- Modify: `ops/deploy.py` (imports ; fonction `publier`)
- Test: `tests/integration/test_publication.py`

**Interfaces:**
- Consumes: `prochaine_version`, `valider_version` (Task 6) ; `Rapport`, `evaluer` (Task 4) ; `Bundle.charger(nom) -> Bundle` (`.version`) ; `Registry.etiqueter(version, bundle, *, commit, note_eval, details) -> dict`, `Registry.journaliser(evenement, **details)`, `ErreurRegistre`.
- Produces: `publier(version: str | None = None, *, bundle: str = "v2", commit: str | None = None, registry: Registry | None = None, seuil: float | None = None, rapport: Rapport | None = None, versions: Iterable[str] | None = None) -> dict` — manifeste avec `version`, `commit`, `empreinte`, `note_eval`, `date`, `bundle_source`, `mode_eval`, `seuils`, `latence_p95_ms`, `cout_moyen_eur`, `essais` ; journal `publication` ou `publication_refusee`.

- [ ] **Step 1: Write the failing test**

Créer `tests/integration/test_publication.py` :

```python
"""Tests d'intégration — publication d'une version (registre isolé en tmp_path)."""
from __future__ import annotations

import pytest

from eval.run_eval import Rapport
from ops.deploy import ErreurDeploiement, publier


def _rapport(passe: bool = True, **modifs) -> Rapport:
    donnees = {
        "version": "v2.0.0",
        "date": "2026-09-22T00:00:00+00:00",
        "essais": 1,
        "note": 0.9,
        "par_contrat": {},
        "latence_p95_ms": 120.0,
        "cout_moyen_eur": 0.01,
        "passe": passe,
        "motifs": [] if passe else ["note 0.400 < seuil 0.75"],
        "seuil": 0.75,
        "mode_eval": "mock",
        "seuils": {"note_min": 0.75, "latence_p95_max_ms": 8000.0,
                   "cout_moyen_max_eur": 0.15, "motif": "m"},
        "bundle_empreinte": "x",
    }
    return Rapport(**{**donnees, **modifs})


def test_numerotation_automatique(registry):
    premier = publier(bundle="v2", commit="aaa1111", registry=registry, rapport=_rapport())
    second = publier(bundle="v2", commit="bbb2222", registry=registry, rapport=_rapport())
    assert (premier["version"], second["version"]) == ("v2.0.0", "v2.0.1")
    assert registry.versions() == ["v1.0.0", "v2.0.0", "v2.0.1"]


def test_versions_injectees(registry):
    manifeste = publier(
        bundle="v2", commit="c", registry=registry, rapport=_rapport(), versions={"v2.0.4"}
    )
    assert manifeste["version"] == "v2.0.5"


def test_manifeste_complet_et_journal(registry):
    manifeste = publier("v2.0.0", bundle="v2", commit="abc1234", registry=registry,
                        rapport=_rapport())
    assert manifeste["commit"] == "abc1234"
    assert manifeste["note_eval"] == 0.9
    assert manifeste["bundle_source"] == "v2"
    assert manifeste["mode_eval"] == "mock"
    assert manifeste["seuils"]["note_min"] == 0.75
    assert manifeste["latence_p95_ms"] == 120.0
    assert manifeste["cout_moyen_eur"] == 0.01
    assert manifeste["essais"] == 1
    assert registry.manifest("v2.0.0") == manifeste
    derniere = registry.journal()[-1]
    assert derniere["evenement"] == "publication"
    assert (derniere["version"], derniere["commit"], derniere["mode_eval"]) == (
        "v2.0.0", "abc1234", "mock"
    )


def test_gate_en_echec_bloque_et_se_journalise(registry):
    with pytest.raises(ErreurDeploiement, match="gate en échec : note 0.400"):
        publier(bundle="v2", commit="c", registry=registry, rapport=_rapport(passe=False))
    assert registry.versions() == ["v1.0.0"]
    refus = registry.journal()[-1]
    assert refus["evenement"] == "publication_refusee"
    assert refus["version"] == "v2.0.0"
    assert refus["motifs"] == ["note 0.400 < seuil 0.75"]


def test_tag_incoherent_avec_le_bundle(registry):
    with pytest.raises(ErreurDeploiement, match="ne correspond pas"):
        publier("v3.0.0", bundle="v2", commit="c", registry=registry, rapport=_rapport())
    assert registry.versions() == ["v1.0.0"]


def test_version_en_double(registry):
    publier("v2.0.0", bundle="v2", commit="c", registry=registry, rapport=_rapport())
    with pytest.raises(ErreurDeploiement, match="déjà publiée"):
        publier("v2.0.0", bundle="v2", commit="c", registry=registry, rapport=_rapport())


def test_gate_mal_configure(registry, tmp_path, monkeypatch):
    invalide = tmp_path / "seuils.yaml"
    invalide.write_text("note_min: 0.75\n", encoding="utf-8")
    monkeypatch.setenv("SEUILS_PATH", str(invalide))
    with pytest.raises(ErreurDeploiement, match="gate mal configuré"):
        publier(bundle="v2", commit="c", registry=registry)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_publication.py -q`
Expected: FAIL — `NotImplementedError: deploy.publier — gate puis étiquetage dans le registre`

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py`, remplacer les imports applicatifs par :

```python
from app.llm_client import Bundle
from app.telemetry import MetricsStore
from eval.run_eval import Rapport, evaluer
from ops.registry import MOTIF_VERSION, ErreurRegistre, Registry
```

Remplacer la fonction `publier` (le stub) par :

```python
def publier(
    version: str | None = None,
    *,
    bundle: str = "v2",
    commit: str | None = None,
    registry: Registry | None = None,
    seuil: float | None = None,
    rapport: Rapport | None = None,
    versions: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Étiquette ``version`` (ou le patch suivant) si le gate passe.

    Ne lit pas git : les versions connues sont le registre ∪ ``versions``
    (la ligne de commande y passe les tags git, cf. ``versions_connues``).
    """
    source = Bundle.charger(bundle)
    registry = registry or Registry()
    commit = commit or _commit_courant()
    connues = set(registry.versions()) | set(versions or ())
    if version is None:
        version = prochaine_version(source.version, connues)
    else:
        valider_version(version, source.version, connues)

    if rapport is None:
        try:
            rapport = evaluer(bundle, seuil=seuil)
        except (ValueError, FileNotFoundError) as exc:
            raise ErreurDeploiement(f"gate mal configuré : {exc}") from exc
    if not rapport.passe:
        registry.journaliser(
            "publication_refusee", version=version, commit=commit, motifs=list(rapport.motifs)
        )
        raise ErreurDeploiement("gate en échec : " + " ; ".join(rapport.motifs))

    details = {
        "bundle_source": bundle,
        "mode_eval": rapport.mode_eval,
        "seuils": rapport.seuils,
        "latence_p95_ms": rapport.latence_p95_ms,
        "cout_moyen_eur": rapport.cout_moyen_eur,
        "essais": rapport.essais,
    }
    try:
        manifest = registry.etiqueter(
            version, source, commit=commit, note_eval=rapport.note, details=details
        )
    except ErreurRegistre as exc:
        raise ErreurDeploiement(str(exc)) from exc
    registry.journaliser(
        "publication",
        version=version,
        commit=commit,
        note_eval=rapport.note,
        mode_eval=rapport.mode_eval,
    )
    return manifest
```

Dans la docstring du module, remplacer le paragraphe `publier(...)` par :

```
    publier(version=None, *, bundle="v2", commit=None, registry=None, seuil=None,
            rapport=None, versions=None) -> manifest
        Étiquette une version : sans ``version``, le patch suivant de la base
        ``vX.Y`` du bundle (registre ∪ ``versions``) ; avec, elle doit avoir la
        même base et ne pas être connue. Joue le gate (``eval.run_eval.evaluer``)
        sauf si un ``rapport`` est fourni, et REFUSE (``ErreurDeploiement``,
        journal ``publication_refusee``) s'il échoue. Sinon dépose le bundle dans
        le registre avec commit, note, mode et seuils du gate, et journalise
        ``publication``.
```

et retirer le marqueur `[STUB]` de sa première ligne en le remplaçant par `[STUB — canary, promotion, rollback, surveillance : sous-projet 3]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_publication.py -q && uv run ruff check .`
Expected: `7 passed`, ruff propre.

Puis le test d'acceptance de l'étiquetage :

Run: `MOCK=on uv run pytest -q tests/acceptance/test_chaine.py`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/integration/test_publication.py
git commit -m "feat(publication): publier — gate bloquant puis étiquetage et journal"
```

---

### Task 8: CLI de publication — version optionnelle, `--rapport`, manifeste JSON

**Files:**
- Modify: `ops/deploy.py` (imports ; `charger_rapport` ; `main`)
- Test: `tests/integration/test_publication.py` (ajout)

**Interfaces:**
- Consumes: `publier`, `versions_connues`, `_tags_git` (Tasks 6–7) ; `Rapport.depuis_dict` (Task 4).
- Produces: `charger_rapport(chemin: Path | str) -> Rapport` (lève `ErreurDeploiement` si absent, illisible ou incomplet) ; `python -m ops.deploy publier [vX.Y.Z] [--bundle v2] [--commit SHA] [--seuil X] [--rapport CHEMIN]` qui affiche le manifeste en JSON sur stdout (lu par `jq -r .version` en CI, Task 9).

- [ ] **Step 1: Write the failing test**

Ajouter à la fin de `tests/integration/test_publication.py` :

```python
def test_cli_publier_depuis_un_rapport(tmp_path, monkeypatch, capsys):
    import json

    from ops import deploy

    monkeypatch.setattr(deploy, "_tags_git", lambda: {"v2.0.2"})
    chemin = tmp_path / "rapport.json"
    chemin.write_text(json.dumps(_rapport().to_dict()), encoding="utf-8")

    assert deploy.main(["publier", "--commit", "abc1234", "--rapport", str(chemin)]) == 0
    manifeste = json.loads(capsys.readouterr().out)
    assert manifeste["version"] == "v2.0.3"
    assert manifeste["commit"] == "abc1234"
    assert manifeste["mode_eval"] == "mock"


@pytest.mark.parametrize(
    ("contenu", "motif"),
    [(None, "introuvable"), ("{pas du json", "illisible"), ('{"version": "v2.0.0"}', "incomplet")],
)
def test_cli_rapport_invalide(tmp_path, capsys, contenu, motif):
    from ops import deploy

    chemin = tmp_path / "rapport.json"
    if contenu is not None:
        chemin.write_text(contenu, encoding="utf-8")
    assert deploy.main(["publier", "--rapport", str(chemin)]) == 1
    assert motif in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_publication.py -q -k cli`
Expected: FAIL — `SystemExit: 2` (argparse : `the following arguments are required: version` / `unrecognized arguments: --commit`).

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py`, ajouter `import json` aux imports de la bibliothèque standard (après `import argparse`).

Ajouter, juste après `publier` :

```python
def charger_rapport(chemin: Path | str) -> Rapport:
    """Relit le rapport JSON écrit par ``eval.run_eval --sortie``."""
    chemin = Path(chemin)
    try:
        data = json.loads(chemin.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ErreurDeploiement(f"rapport de gate introuvable : {chemin}") from exc
    except json.JSONDecodeError as exc:
        raise ErreurDeploiement(f"rapport de gate illisible : {chemin} ({exc})") from exc
    try:
        return Rapport.depuis_dict(data)
    except TypeError as exc:
        raise ErreurDeploiement(f"rapport de gate incomplet : {chemin} ({exc})") from exc
```

Dans `main`, remplacer la définition du sous-parseur `publier` :

```python
    p = sub.add_parser("publier")
    p.add_argument("version", nargs="?", default=None,
                   help="vX.Y.Z (défaut : patch suivant de la base du bundle)")
    p.add_argument("--bundle", default="v2")
    p.add_argument("--commit", default=None)
    p.add_argument("--seuil", type=float, default=None)
    p.add_argument("--rapport", default=None,
                   help="rapport JSON du gate (eval.run_eval --sortie) ; sinon le gate est joué")
```

et la branche `publier` du bloc `try` :

```python
        if args.commande == "publier":
            registry = Registry()
            rapport = charger_rapport(args.rapport) if args.rapport else None
            manifest = publier(
                args.version,
                bundle=args.bundle,
                commit=args.commit,
                registry=registry,
                seuil=args.seuil,
                rapport=rapport,
                versions=versions_connues(registry),
            )
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
```

Dans la docstring du module, remplacer la ligne `Ligne de commande : ``python -m ops.deploy publier v2.0.0 | canary …` par :

```
Ligne de commande : ``python -m ops.deploy publier [v2.0.0] [--commit SHA] [--rapport r.json]
| canary v2.0.0 --pourcentage 10 | promouvoir v2.0.0 | rollback | surveiller [--boucle]``.
``publier`` affiche le manifeste en JSON et tient compte des tags git.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_publication.py -q && uv run ruff check .`
Expected: `11 passed`, ruff propre.

Vérification manuelle bout en bout (registre jetable) :

```bash
T="$CLAUDE_JOB_DIR/tmp"   # hors session Claude : un dossier temporaire quelconque
MOCK=on uv run python -m eval.run_eval --version v2 --historique "$T/h.jsonl" --sortie "$T/rapport.json"
REGISTRY_PATH="$T/registre" MOCK=on uv run python -m ops.deploy publier --commit abc1234 --rapport "$T/rapport.json" | jq -r .version
```

Expected: `GATE : PASSE`, puis `v2.0.0` (aucun tag `v2.0.*` dans le dépôt) — ou le patch suivant du plus haut tag `v2.0.*` existant.

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/integration/test_publication.py
git commit -m "feat(publication): CLI publier — version optionnelle, --rapport, manifeste JSON"
```

---

### Task 9: Chaîne CI — `ci.yml`, `Makefile`, `README.md`

**Files:**
- Rename: `.github/workflows/llmops.yml` → `.github/workflows/ci.yml` (`git mv`)
- Modify: `.github/workflows/ci.yml` (contenu complet ci-dessous)
- Modify: `Makefile:52-55` (cible `ci`)
- Modify: `README.md:92`, `README.md:105`
- Modify: `.gitignore` (ajout de `eval/rapport.json`)

**Interfaces:**
- Consumes: `python -m eval.run_eval --version v2 [--essais 3] --sortie eval/rapport.json` (Task 5) ; `python -m ops.deploy publier [vX.Y.Z] --commit SHA --rapport eval/rapport.json` → manifeste JSON sur stdout (Task 8) ; `python -m ops.drift_proxy` (fourni, écoute sur 8080, `GET /_drift`).
- Produces: artefacts de build `gate-mock`, `gate-reel`, `mardik-vX.Y.Z` ; tag git `vX.Y.Z`. Le job `deploiement-canary` (sous-projet 3) consommera `mardik-vX.Y.Z`.

- [ ] **Step 1: Renommer le workflow**

```bash
git mv .github/workflows/llmops.yml .github/workflows/ci.yml
```

- [ ] **Step 2: Écrire le workflow**

Remplacer tout le contenu de `.github/workflows/ci.yml` par :

```yaml
# Chaîne CI/CD Mardik — gates bloquants → build → artefact étiqueté → canary.
#
# Deux niveaux de gate d'évaluation :
#   * gate-evaluation : MOCK=on, les 12 contrats, à chaque PR et fusion — la CI
#     ne paie pas et ne dépend pas du fournisseur ;
#   * gate-release : vrai modèle (3 passes), sur tag v* ou lancement manuel.
# La publication relit le rapport du gate (un seul rapport fait foi), dépose
# la version dans le registre du runner, l'envoie en artefact, puis pose le
# tag git vX.Y.Z (les tags sont la mémoire des versions publiées).
#
# Équivalent local : `make ci`.

name: ci

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
  workflow_dispatch:

env:
  MOCK: "on"
  DRIFT: "off"
  LLM_PROVIDER: ollama
  LLM_MODEL: modele-ci
  OTEL_TRACES: "off"

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.11"
      - run: uv sync
      - run: uv run ruff check .

  tests:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.11"
      - run: uv sync
      - name: Tests unitaires (pipeline v2, gate, versions)
        run: uv run pytest -q tests/unit
      - name: Tests d'intégration (hérités de la remédiation + v2 + gate + publication)
        run: uv run pytest -q tests/integration
      - name: Tests d'acceptance (le brief)
        run: uv run pytest -q tests/acceptance

  gate-evaluation:
    needs: tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.11"
      - run: uv sync
      - name: Gate d'évaluation (MOCK, 12 contrats, seuils de eval/seuils.yaml)
        run: uv run python -m eval.run_eval --version v2 --sortie eval/rapport.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: gate-mock
          path: |
            eval/rapport.json
            eval/history.jsonl
          if-no-files-found: warn

  gate-release:
    needs: gate-evaluation
    if: startsWith(github.ref, 'refs/tags/v') || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    environment: release
    env:
      MOCK: "off"
      LLM_PROVIDER: ${{ secrets.LLM_PROVIDER }}
      LLM_MODEL: ${{ secrets.LLM_MODEL }}
      AZURE_AI_ENDPOINT: ${{ secrets.AZURE_AI_ENDPOINT }}
      AZURE_AI_API_KEY: ${{ secrets.AZURE_AI_API_KEY }}
      LLM_PROXY_URL: http://localhost:8080
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.11"
      - run: uv sync
      - name: Proxy LLM (l'app parle toujours au proxy, jamais au fournisseur)
        run: |
          nohup uv run python -m ops.drift_proxy > proxy.log 2>&1 &
          for _ in $(seq 30); do
            curl -sf http://localhost:8080/_drift && exit 0
            sleep 1
          done
          cat proxy.log
          exit 1
      - name: Gate de release (vrai modèle, 3 passes)
        run: uv run python -m eval.run_eval --version v2 --essais 3 --sortie eval/rapport.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: gate-reel
          path: |
            eval/rapport.json
            eval/history.jsonl
          if-no-files-found: warn

  build:
    needs: [gate-evaluation, gate-release]
    if: >-
      always()
      && needs.gate-evaluation.result == 'success'
      && needs.gate-release.result != 'failure'
      && needs.gate-release.result != 'cancelled'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Image de l'application
        run: docker build -t mardik:${{ github.sha }} .

  publication:
    needs: [build, gate-release]
    if: always() && needs.build.result == 'success' && github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    concurrency:
      group: publication
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.11"
      - run: uv sync
      - name: Rapport du gate de release (vrai modèle)
        if: needs.gate-release.result == 'success'
        uses: actions/download-artifact@v4
        with:
          name: gate-reel
          path: eval
      - name: Rapport du gate CI (MOCK)
        if: needs.gate-release.result != 'success'
        uses: actions/download-artifact@v4
        with:
          name: gate-mock
          path: eval
      - name: Étiquetage dans le registre
        env:
          REF_TYPE: ${{ github.ref_type }}
          REF_NAME: ${{ github.ref_name }}
        run: |
          VERSION_ARG=""
          if [ "$REF_TYPE" = "tag" ]; then VERSION_ARG="$REF_NAME"; fi
          uv run python -m ops.deploy publier $VERSION_ARG \
            --commit "${GITHUB_SHA::7}" --rapport eval/rapport.json > manifest.json
          cat manifest.json
          echo "VERSION=$(jq -r .version manifest.json)" >> "$GITHUB_ENV"
      - uses: actions/upload-artifact@v4
        with:
          name: mardik-${{ env.VERSION }}
          path: |
            ops/registry/${{ env.VERSION }}/
            ops/registry/journal.jsonl
            manifest.json
          if-no-files-found: error
      - name: Tag git de la version (après l'artefact)
        if: github.ref_type != 'tag'
        run: |
          if git rev-parse -q --verify "refs/tags/$VERSION" > /dev/null; then
            echo "tag $VERSION déjà posé sur ce commit (relance) — rien à faire"
          else
            git tag "$VERSION"
            git push origin "$VERSION"
          fi

  deploiement-canary:
    needs: publication
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')
    steps:
      - uses: actions/checkout@v4
      # TODO déploiement canary (sous-projet 3) : récupérer l'artefact mardik-vX.Y.Z,
      #      `python -m ops.deploy canary vX.Y.Z --pourcentage 10`, puis surveillance
      #      (`python -m ops.deploy surveiller`) et promotion ou rollback.
      - run: echo "TODO déploiement canary"
```

- [ ] **Step 3: Mettre à jour `Makefile` et `README.md`**

Dans `Makefile`, remplacer la cible `ci` entière par (le gate passe **avant**
l'acceptance : tant que les sous-projets 3 et 4 ne sont pas livrés, l'étape
acceptance échoue et `make` s'arrêterait avant le gate) :

```make
ci:                 ## l'équivalent local du workflow GitHub (MOCK=on)
	uv run ruff check .
	MOCK=on uv run pytest -q tests/unit
	MOCK=on uv run pytest -q tests/integration
	MOCK=on uv run python -m eval.run_eval --version v2
	MOCK=on uv run pytest -q tests/acceptance
	@echo "publication / canary : en CI uniquement, voir .github/workflows/ci.yml"
```

Dans `.gitignore`, ajouter sous la ligne `eval/history.jsonl` :

```
eval/rapport.json
```

Dans `README.md` :
- ligne 92 : remplacer `.github/      workflows/llmops.yml [TEMPLATE] — étapes posées, gates en TODO` par `.github/      workflows/ci.yml — gates (MOCK + release), build, publication ; canary en TODO`
- ligne 105 : remplacer `` `.github/workflows/llmops.yml` `` par `` `.github/workflows/ci.yml` ``

Vérifier qu'il ne reste aucune référence :

Run: `git grep -n "llmops.yml" -- . ":(exclude)docs/superpowers"`
Expected: aucune sortie (code 1).

- [ ] **Step 4: Valider le workflow**

Run: `uv run python -c "import yaml, pathlib; d = yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text()); print(list(d['jobs']))"`
Expected: `['lint', 'tests', 'gate-evaluation', 'gate-release', 'build', 'publication', 'deploiement-canary']`

Si `actionlint` est disponible (`command -v actionlint` ou `uvx --from actionlint-py actionlint --version`) :

Run: `uvx --from actionlint-py actionlint .github/workflows/ci.yml`
Expected: aucune erreur. (Si l'outil n'est pas disponible, le noter dans le compte rendu ; le vrai contrôle est le premier run GitHub.)

Run: `make ci`
Expected: ruff propre, unitaires et intégration verts, `GATE : PASSE`, puis l'étape acceptance échoue sur exactement 4 tests — `test_rollback_en_une_operation`, `test_promotion_canary_puis_totale`, `test_dashboard_par_version`, `test_journal_derive_et_rollback_automatique` (sous-projets 3 et 4) — ce qui fait sortir `make` en erreur : c'est l'état attendu à ce stade.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml Makefile README.md .gitignore
git commit -m "ci: ci.yml — gate MOCK, gate de release, build, publication (artefact + tag)"
```

---

### Task 10: Vérification finale

**Files:** aucun (sauf corrections révélées).

- [ ] **Step 1: Suite complète**

Run: `MOCK=on uv run pytest -q`
Expected: tout vert sauf exactement 4 échecs : `test_rollback_en_une_operation`, `test_promotion_canary_puis_totale`, `test_dashboard_par_version`, `test_journal_derive_et_rollback_automatique` (sous-projets 3 et 4). En particulier verts : `test_gate_evaluation_note_par_version`, `test_evaluation_enrichie_latence_et_cout`, `test_etiquetage_version_apres_gate`, `test_contrat_v2_long_analyse_sans_troncature`, `test_erreurs_explicites_jamais_de_500`, `test_client_v1_fonctionne`.

- [ ] **Step 2: Lint**

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 3: Fichiers intouchables**

Run: `git diff --stat main -- ops/registry/__init__.py app/llm_client.py app/telemetry.py tests/conftest.py models/v1 app/api_v1.py app/pipeline app/api_v2.py tests/acceptance`
Expected: aucune sortie.

- [ ] **Step 4: Aucun fichier généré dans le dépôt**

Run: `git status --short`
Expected: aucune sortie (`eval/history.jsonl`, `eval/.metrics_eval.jsonl`, `eval/rapport.json` sont ignorés par git).

- [ ] **Step 5: Commit (si corrections)**

```bash
git add -A
git commit -m "chore: vérification finale du sous-projet 2"
```
