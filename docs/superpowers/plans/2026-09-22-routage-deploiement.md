# Routage & déploiement (sous-projet 3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Router le trafic `/analyse` entre les versions livrées du registre (canary par pourcentage), et piloter canary, promotion, rollback et installation d'artefact via `ops/deploy.py` et des workflows GitHub exécutés sur un runner auto-hébergé.

**Architecture:** `app/gateway.py` reste une couche HTTP mince ; `app/routage.py` lit l'index du registre à chaque requête, choisit la version, charge le bundle **livré** et le moteur selon la stratégie. `ops/deploy.py` décide : chaque transition passe par `_transition` (verrou `fcntl`, état avant/après, index écrit atomiquement, entrée de journal). Les jobs qui touchent la prod tournent sur `[self-hosted, mardik]` et appellent la CLI `python -m ops.deploy`.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, PyYAML, pytest, ruff, uv, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-22-routage-deploiement-design.md`

## Global Constraints

- Tous les tests tournent en `MOCK=on` : `MOCK=on uv run pytest -q …` ; aucun appel réseau.
- `uv run ruff check .` doit rester propre (ligne ≤ 100, règles par défaut E/F).
- Intouchables : `app/api_v1.py`, `models/v1/`, `app/llm_client.py`, `app/telemetry.py`, `tests/conftest.py`, `tests/acceptance/`.
- `ops/registry/__init__.py` : seule modification autorisée = `ecrire_index` atomique (Task 1).
- Jamais de 500 brut : toute erreur est explicite (422 / 413 / 503 côté HTTP ; `REFUSÉ : …` + code 1 côté CLI).
- Ne jamais attraper `Exception` : types précis uniquement.
- Pas de `import *`. Français pour les noms, messages, docstrings et messages de commit (convention du repo).
- Fixtures fournies par `tests/conftest.py` (autouse `environnement` : `MOCK=on`, `REGISTRY_PATH=tmp_path/registry`, `CANARY_PERCENT` supprimé) : `registry` (v1.0.0 livrée et active, même dossier que `REGISTRY_PATH`), `telemetry`, `metriques`, `client` (TestClient avec `gateway.get_registry` et `get_telemetry` surchargés).
- Chaque commit se termine par la ligne : `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Commandes lancées depuis la racine du repo (le dossier qui contient `pyproject.toml`).

## Structure des fichiers

| Fichier | Rôle | Tâche |
|---|---|---|
| `ops/registry/__init__.py` | `ecrire_index` atomique | 1 |
| `.gitignore` | `index.lock`, `index.json.tmp` | 1 |
| `app/routage.py` (nouveau) | `choisir_version`, `MOTEURS_HTTP`, `Cible`, `resoudre`, `analyser`, erreurs de routage | 2, 3 |
| `app/gateway.py` | routes `/analyse`, `/gateway/etat`, dépendances | 4 |
| `app/main.py` | gestionnaire `ErreurRoutage` → 503 | 4 |
| `ops/deploy.py` | `_verrou`, `_transition`, `deployer_canary`, `promouvoir`, `rollback`, `installer`, CLI | 5, 6, 7 |
| `.github/workflows/ci.yml` | `outputs.version` de `publication`, job `deploiement-canary` | 8 |
| `.github/workflows/promotion.yml`, `rollback.yml` (nouveaux) | pilotage manuel de la prod | 8 |
| `docs/exploitation.md` | §4 déploiement progressif, §5 rollback + runner | 9 |
| `tests/unit/test_transitions.py` | écriture atomique, transitions, `installer` | 1, 5, 6 |
| `tests/unit/test_choisir_version.py` | fonction pure de routage | 2 |
| `tests/unit/test_routage.py` | service de routage | 3 |
| `tests/integration/test_gateway.py` | routes HTTP | 4 |
| `tests/integration/test_cli_deploy.py` | CLI de déploiement | 7 |
| `tests/unit/test_workflows.py` | invariants de sécurité des workflows | 8 |

**Écart assumé à la spec (§4.1)** : `choisir_version` est définie dans `app/routage.py` (le service en a besoin, et `gateway` importe `routage` : la définir dans `gateway` créerait un import circulaire). `app/gateway.py` la **réexporte** (`from app.routage import choisir_version as choisir_version`), donc `from app.gateway import choisir_version` — utilisé par le test d'acceptance — fonctionne. Les deux erreurs de routage partagent une base `ErreurRoutage` : un seul gestionnaire dans `main.py` au lieu de deux.

---

### Task 1: Écriture atomique de l'index du registre

**Files:**
- Modify: `ops/registry/__init__.py` (méthode `ecrire_index`)
- Modify: `.gitignore` (section « Généré à l'exécution »)
- Create: `tests/unit/test_transitions.py`

**Interfaces:**
- Consumes: `Registry` (fourni).
- Produces: `Registry.ecrire_index(index)` écrit `<root>/index.json.tmp` puis `os.replace` vers `<root>/index.json`. Signature inchangée.

- [ ] **Step 1: Write the failing test**

Créer `tests/unit/test_transitions.py` :

```python
"""Tests unitaires — registre (écriture atomique) et transitions de déploiement."""
from __future__ import annotations

import pytest


def test_ecriture_atomique_de_l_index(registry, monkeypatch):
    avant = registry.index()

    def remplacement_en_echec(*args, **kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr("ops.registry.os.replace", remplacement_en_echec)
    with pytest.raises(OSError, match="disque plein"):
        registry.ecrire_index({**avant, "active": "v9.9.9"})
    assert registry.index() == avant


def test_ecriture_ne_laisse_pas_de_fichier_temporaire(registry):
    registry.ecrire_index({**registry.index(), "canary": None})
    assert not (registry.root / "index.json.tmp").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_transitions.py -v`
Expected: `test_ecriture_atomique_de_l_index` FAIL avec `DID NOT RAISE <class 'OSError'>` (l'écriture actuelle n'appelle pas `os.replace`) ; l'autre test passe.

- [ ] **Step 3: Write minimal implementation**

Dans `ops/registry/__init__.py`, remplacer le corps de `ecrire_index` (le module importe déjà `os`) :

```python
    def ecrire_index(self, index: dict[str, Any]) -> None:
        """Écrit l'index de façon atomique : la gateway le relit à chaque requête
        et ne doit jamais tomber sur un JSON à moitié écrit."""
        index = {**index, "mis_a_jour": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        temporaire = self._chemin_index.with_name("index.json.tmp")
        temporaire.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporaire, self._chemin_index)
```

Dans `.gitignore`, sous `ops/registry/journal.jsonl`, ajouter :

```
ops/registry/index.lock
ops/registry/index.json.tmp
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_transitions.py -v && MOCK=on uv run pytest -q tests/integration`
Expected: 2 passed ; intégration toujours verte.

- [ ] **Step 5: Commit**

```bash
git add ops/registry/__init__.py .gitignore tests/unit/test_transitions.py
git commit -m "feat(registre): écriture atomique de l'index (tmp + os.replace)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Fonction pure `choisir_version`

**Files:**
- Create: `app/routage.py`
- Create: `tests/unit/test_choisir_version.py`

**Interfaces:**
- Produces: `app.routage.choisir_version(active: str, canary: str | None, canary_percent: int, tirage: float) -> str` — `canary` si `canary is not None and tirage < canary_percent`, sinon `active`.

- [ ] **Step 1: Write the failing test**

Créer `tests/unit/test_choisir_version.py` :

```python
"""Tests unitaires — fonction pure de routage canary."""
from __future__ import annotations

import pytest

from app.routage import choisir_version


def test_sans_canary_toujours_l_active():
    assert {choisir_version("v1.0.0", None, 30, t) for t in range(100)} == {"v1.0.0"}


@pytest.mark.parametrize(("pourcentage", "attendus"), [(0, 0), (30, 30), (99, 99)])
def test_le_canary_recoit_exactement_son_pourcentage(pourcentage, attendus):
    tirages = [choisir_version("v1.0.0", "v2.0.0", pourcentage, t) for t in range(100)]
    assert tirages.count("v2.0.0") == attendus


def test_borne_tirage_egal_au_pourcentage_va_a_l_active():
    assert choisir_version("v1.0.0", "v2.0.0", 30, 30.0) == "v1.0.0"
    assert choisir_version("v1.0.0", "v2.0.0", 30, 29.999) == "v2.0.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_choisir_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routage'`.

- [ ] **Step 3: Write minimal implementation**

Créer `app/routage.py` :

```python
"""Routage canary : quelle version livrée sert une requête, et avec quel moteur.

Service sans HTTP (la gateway le délègue) :

    choisir_version(active, canary, canary_percent, tirage) -> str
        fonction pure : ``tirage`` ∈ [0, 100[ ; ``canary`` si un canary est
        déployé et ``tirage < canary_percent``, sinon ``active``.
"""
from __future__ import annotations


def choisir_version(
    active: str, canary: str | None, canary_percent: int, tirage: float
) -> str:
    if canary is not None and tirage < canary_percent:
        return canary
    return active
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_choisir_version.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/routage.py tests/unit/test_choisir_version.py
git commit -m "feat(routage): fonction pure choisir_version

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Service de routage — `resoudre` et `analyser`

**Files:**
- Modify: `app/routage.py`
- Create: `tests/unit/test_routage.py`

**Interfaces:**
- Consumes: `choisir_version` (Task 2) ; `Registry.index()`, `Registry.bundle(version)` ; `app.api_v1.analyser_v1`, `app.api_v2.analyser_v2` (signature `(texte: str, client: LLMClient, telemetry: Telemetry) -> BaseModel`).
- Produces (dans `app.routage`) :
  - `Moteur = Callable[[str, LLMClient, Telemetry], BaseModel]`
  - `FabriqueClient = Callable[[Bundle], LLMClient]`
  - `MOTEURS_HTTP: dict[str, Moteur]` = `{"monolithique": analyser_v1, "map_reduce_clauses": analyser_v2}`
  - `class ErreurRoutage(RuntimeError)` ; `class AucuneVersionActive(ErreurRoutage)` (message « aucune version active dans le registre ») ; `class StrategieInconnue(ErreurRoutage)` (`__init__(version, strategie)`, message « version vX.Y.Z : stratégie 'rag' non routable »)
  - `@dataclass(frozen=True) class Cible: version: str; bundle: Bundle; moteur: Moteur`
  - `resoudre(registry: Registry, tirage: float) -> Cible`
  - `analyser(texte: str, registry: Registry, telemetry: Telemetry, tirage: float, fabrique_client: FabriqueClient) -> tuple[str, BaseModel]`

- [ ] **Step 1: Write the failing test**

Créer `tests/unit/test_routage.py` :

```python
"""Tests unitaires — service de routage (registre isolé en tmp_path)."""
from __future__ import annotations

import pytest

from app.api_v1 import analyser_v1
from app.api_v2 import analyser_v2
from app.llm_client import Bundle
from app.routage import (
    MOTEURS_HTTP,
    AucuneVersionActive,
    StrategieInconnue,
    analyser,
    resoudre,
)
from ops.registry import Registry
from tests.unit.doublures import FauxClient

CONTRAT = "".join(
    f"Article {i} — Résiliation\nLe contrat peut être résilié par chaque partie.\n"
    for i in range(1, 4)
)


def _livrer_v2(registry: Registry) -> None:
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)


def test_table_des_moteurs():
    assert MOTEURS_HTTP == {"monolithique": analyser_v1, "map_reduce_clauses": analyser_v2}


def test_l_active_est_servie_depuis_le_registre(registry):
    cible = resoudre(registry, tirage=50.0)
    assert cible.version == "v1.0.0"
    assert cible.bundle.chemin == registry.root / "v1.0.0" / "config.yaml"
    assert cible.moteur is analyser_v1


def test_le_canary_est_servi_selon_le_tirage(registry):
    _livrer_v2(registry)
    registry.definir_canary("v2.0.0", 30)
    canary = resoudre(registry, tirage=29.9)
    assert canary.version == "v2.0.0" and canary.moteur is analyser_v2
    assert resoudre(registry, tirage=30.0).version == "v1.0.0"


def test_aucune_version_active(tmp_path):
    with pytest.raises(AucuneVersionActive, match="aucune version active dans le registre"):
        resoudre(Registry(tmp_path / "vide"), tirage=0.0)


def test_strategie_inconnue(registry):
    chemin = registry.root / "v1.0.0" / "config.yaml"
    contenu = chemin.read_text(encoding="utf-8")
    chemin.write_text(
        contenu.replace("strategie: monolithique", "strategie: rag"), encoding="utf-8"
    )
    with pytest.raises(StrategieInconnue, match="version v1.0.0 : stratégie 'rag' non routable"):
        resoudre(registry, tirage=0.0)


def test_analyser_passe_le_bundle_livre_a_la_fabrique(registry, telemetry):
    _livrer_v2(registry)
    registry.definir_actif("v2.0.0")
    recus: list[str] = []

    def fabrique(bundle: Bundle) -> FauxClient:
        recus.append(bundle.version)
        return FauxClient(bundle)

    version, reponse = analyser(CONTRAT, registry, telemetry, 50.0, fabrique)
    assert version == "v2.0.0"
    assert recus == ["v2.0.0"]
    assert reponse.version == "v2.0.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_routage.py -v`
Expected: FAIL — `ImportError: cannot import name 'MOTEURS_HTTP' from 'app.routage'`.

- [ ] **Step 3: Write minimal implementation**

Remplacer tout le contenu de `app/routage.py` par :

```python
"""Routage canary : quelle version livrée sert une requête, et avec quel moteur.

Service sans HTTP (la gateway le délègue) :

    choisir_version(active, canary, canary_percent, tirage) -> str
        fonction pure : ``tirage`` ∈ [0, 100[ ; ``canary`` si un canary est
        déployé et ``tirage < canary_percent``, sinon ``active``.

    resoudre(registry, tirage) -> Cible
        relit l'index à CHAQUE appel (une promotion ou un rollback prend effet
        sans redémarrage), charge le bundle depuis le registre — ce qui a été
        livré, pas ``models/`` — et choisit le moteur selon sa stratégie.

    analyser(texte, registry, telemetry, tirage, fabrique_client) -> (version, réponse)

Ajouter une stratégie = ajouter une entrée à ``MOTEURS_HTTP``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from app.api_v1 import analyser_v1
from app.api_v2 import analyser_v2
from app.llm_client import Bundle, LLMClient
from app.telemetry import Telemetry
from ops.registry import Registry

Moteur = Callable[[str, LLMClient, Telemetry], BaseModel]
FabriqueClient = Callable[[Bundle], LLMClient]

MOTEURS_HTTP: dict[str, Moteur] = {
    "monolithique": analyser_v1,
    "map_reduce_clauses": analyser_v2,
}


class ErreurRoutage(RuntimeError):
    """La gateway ne peut servir aucune version : 503 explicite."""


class AucuneVersionActive(ErreurRoutage):
    def __init__(self) -> None:
        super().__init__("aucune version active dans le registre")


class StrategieInconnue(ErreurRoutage):
    def __init__(self, version: str, strategie: str) -> None:
        super().__init__(f"version {version} : stratégie {strategie!r} non routable")
        self.version = version
        self.strategie = strategie


@dataclass(frozen=True)
class Cible:
    version: str
    bundle: Bundle
    moteur: Moteur


def choisir_version(
    active: str, canary: str | None, canary_percent: int, tirage: float
) -> str:
    if canary is not None and tirage < canary_percent:
        return canary
    return active


def resoudre(registry: Registry, tirage: float) -> Cible:
    index = registry.index()
    active = index.get("active")
    if active is None:
        raise AucuneVersionActive()
    version = choisir_version(
        active, index.get("canary"), int(index.get("canary_percent") or 0), tirage
    )
    bundle = registry.bundle(version)
    moteur = MOTEURS_HTTP.get(bundle.strategie)
    if moteur is None:
        raise StrategieInconnue(version, bundle.strategie)
    return Cible(version=version, bundle=bundle, moteur=moteur)


def analyser(
    texte: str,
    registry: Registry,
    telemetry: Telemetry,
    tirage: float,
    fabrique_client: FabriqueClient,
) -> tuple[str, BaseModel]:
    cible = resoudre(registry, tirage)
    return cible.version, cible.moteur(texte, fabrique_client(cible.bundle), telemetry)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_routage.py tests/unit/test_choisir_version.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add app/routage.py tests/unit/test_routage.py
git commit -m "feat(routage): résolution version → bundle livré → moteur

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Gateway HTTP — `/gateway/etat`, `/analyse`, erreurs 503

**Files:**
- Modify: `app/gateway.py` (réécriture complète)
- Modify: `app/main.py` (import, gestionnaire, docstring)
- Create: `tests/integration/test_gateway.py`

**Interfaces:**
- Consumes: `app.routage` (Task 3) : `analyser`, `choisir_version`, `ErreurRoutage`.
- Produces (dans `app.gateway`) : `choisir_version` (réexport), `RequeteAnalyse`, `EtatGateway(active: str | None, canary: str | None, canary_percent: int)`, `get_registry() -> Registry`, `get_telemetry() -> Telemetry`, `get_tirage() -> float`, `get_fabrique_client() -> Callable[[Bundle], LLMClient]`, `GET /gateway/etat`, `POST /analyse` (en-tête `X-Mardik-Version`).

- [ ] **Step 1: Write the failing test**

Créer `tests/integration/test_gateway.py` :

```python
"""Tests d'intégration — gateway /analyse et /gateway/etat (registre isolé)."""
from __future__ import annotations

import pytest

from app import gateway
from app.llm_client import Bundle, ErreurLLM
from ops.registry import Registry
from tests.unit.doublures import FauxClient

TEXTE = "".join(
    f"Article {i} — Résiliation\nLe contrat peut être résilié par chaque partie.\n"
    for i in range(1, 4)
)


def _livrer_v2(registry: Registry) -> None:
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)


@pytest.fixture
def tirage(client):
    """Tirage déterministe : modifier ``tirage["valeur"]`` avant chaque requête."""
    valeur = {"valeur": 50.0}
    client.app.dependency_overrides[gateway.get_tirage] = lambda: valeur["valeur"]
    return valeur


def _version(client) -> str:
    reponse = client.post("/analyse", json={"texte": TEXTE})
    assert reponse.status_code == 200, reponse.text
    return reponse.headers["x-mardik-version"]


def test_etat_initial(client):
    assert client.get("/gateway/etat").json() == {
        "active": "v1.0.0",
        "canary": None,
        "canary_percent": 0,
    }


def test_l_active_repond_au_format_v1(client, tirage):
    reponse = client.post("/analyse", json={"texte": TEXTE})
    assert reponse.status_code == 200
    assert reponse.headers["x-mardik-version"] == "v1.0.0"
    assert set(reponse.json()) == {"clauses", "modele", "version", "tronque"}


def test_le_canary_repond_au_format_v2_selon_le_tirage(client, registry, tirage):
    _livrer_v2(registry)
    registry.definir_canary("v2.0.0", 30)
    tirage["valeur"] = 10.0
    reponse = client.post("/analyse", json={"texte": TEXTE})
    assert reponse.headers["x-mardik-version"] == "v2.0.0"
    assert "confiance_globale" in reponse.json()
    tirage["valeur"] = 30.0
    assert _version(client) == "v1.0.0"


def test_bascule_sans_redemarrage(client, registry, tirage):
    _livrer_v2(registry)
    assert _version(client) == "v1.0.0"
    registry.definir_actif("v2.0.0")
    assert _version(client) == "v2.0.0"
    registry.definir_actif("v1.0.0")
    assert _version(client) == "v1.0.0"
    assert client.get("/gateway/etat").json()["active"] == "v1.0.0"


def test_aucune_version_active_503(client, tmp_path):
    client.app.dependency_overrides[gateway.get_registry] = lambda: Registry(tmp_path / "vide")
    reponse = client.post("/analyse", json={"texte": TEXTE})
    assert reponse.status_code == 503
    assert reponse.json()["detail"] == "aucune version active dans le registre"


def test_strategie_inconnue_503(client, registry):
    chemin = registry.root / "v1.0.0" / "config.yaml"
    contenu = chemin.read_text(encoding="utf-8")
    chemin.write_text(
        contenu.replace("strategie: monolithique", "strategie: rag"), encoding="utf-8"
    )
    reponse = client.post("/analyse", json={"texte": TEXTE})
    assert reponse.status_code == 503
    assert reponse.json()["detail"] == "version v1.0.0 : stratégie 'rag' non routable"


def test_fournisseur_indisponible_503(client):
    client.app.dependency_overrides[gateway.get_fabrique_client] = lambda: (
        lambda bundle: FauxClient(bundle, erreur=ErreurLLM("délai dépassé"))
    )
    reponse = client.post("/analyse", json={"texte": TEXTE})
    assert reponse.status_code == 503
    assert reponse.json()["detail"] == "fournisseur LLM indisponible : délai dépassé"


def test_corps_invalide_422(client):
    assert client.post("/analyse", json={"texte": "court"}).status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_gateway.py -v`
Expected: FAIL — `AttributeError: module 'app.gateway' has no attribute 'get_tirage'` pour les tests qui utilisent `tirage` ; les autres échouent sur un statut 501 (« à implémenter : gateway… »).

- [ ] **Step 3: Write minimal implementation**

Remplacer tout le contenu de `app/gateway.py` par :

```python
"""Gateway : routeur canary entre les versions livrées.

    POST /analyse  {"texte": "..."}   → réponse de la version choisie,
                                        + en-tête ``X-Mardik-Version``
    GET  /gateway/etat                → {"active": "v1.0.0", "canary": "v2.0.0",
                                         "canary_percent": 10}

Route mince : le choix de la version, du bundle livré et du moteur est dans
``app.routage``. L'index du registre est relu à chaque requête : une promotion
ou un rollback prend effet sans redémarrage. ``CANARY_PERCENT`` n'est que la
valeur par défaut de ``ops.deploy.deployer_canary`` ; la gateway suit l'index.
Erreurs explicites : 422 (corps), 413 (document trop long, moteur v2),
503 (fournisseur LLM, aucune version routable).
"""
from __future__ import annotations

import random
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app import routage
from app.api_v1 import ReponseAnalyseV1
from app.api_v2 import ReponseAnalyseV2
from app.llm_client import Bundle, ErreurLLM, LLMClient
from app.routage import choisir_version as choisir_version
from app.telemetry import Telemetry, build_default_telemetry
from ops.registry import Registry

router = APIRouter(tags=["gateway"])


class RequeteAnalyse(BaseModel):
    texte: str = Field(..., min_length=20)
    contrat_id: str | None = None


class EtatGateway(BaseModel):
    active: str | None
    canary: str | None
    canary_percent: int


def get_registry() -> Registry:
    return Registry()


def get_telemetry() -> Telemetry:
    return build_default_telemetry()


def get_tirage() -> float:
    return random.uniform(0, 100)


def get_fabrique_client() -> Callable[[Bundle], LLMClient]:
    return LLMClient


@router.get("/gateway/etat", response_model=EtatGateway)
def etat(registry: Registry = Depends(get_registry)) -> EtatGateway:
    index = registry.index()
    return EtatGateway(
        active=index.get("active"),
        canary=index.get("canary"),
        canary_percent=int(index.get("canary_percent") or 0),
    )


@router.post("/analyse", response_model=ReponseAnalyseV1 | ReponseAnalyseV2)
def analyse(
    requete: RequeteAnalyse,
    response: Response,
    registry: Registry = Depends(get_registry),
    telemetry: Telemetry = Depends(get_telemetry),
    tirage: float = Depends(get_tirage),
    fabrique_client: Callable[[Bundle], LLMClient] = Depends(get_fabrique_client),
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
    return reponse
```

Dans `app/main.py` :

1. Remplacer la docstring du module par :

```python
"""Application FastAPI — [FOURNI].

* ``/v1`` est branché et fonctionnel (le contrat historique) ;
* ``/v2`` est implémenté (``DocumentTropLong`` → **413**) ;
* ``/analyse`` (gateway) route entre les versions livrées du registre ; une
  ``ErreurRoutage`` (aucune version active, stratégie non routable) → **503**.
Un module encore en chantier lève ``NotImplementedError`` → **501** explicite —
jamais un 500 muet.
"""
```

2. Ajouter l'import sous `from app.pipeline import DocumentTropLong` :

```python
from app.routage import ErreurRoutage
```

3. Ajouter le gestionnaire après celui de `DocumentTropLong`, avant `return app` :

```python
    @app.exception_handler(ErreurRoutage)
    async def _routage_impossible(request: Request, exc: ErreurRoutage) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_gateway.py -v && MOCK=on uv run pytest -q tests/acceptance/test_chaine.py`
Expected: 8 passed ; `test_chaine.py` toujours vert (dont `test_client_v1_fonctionne`).

- [ ] **Step 5: Commit**

```bash
git add app/gateway.py app/main.py tests/integration/test_gateway.py
git commit -m "feat(gateway): POST /analyse routé par l'index, GET /gateway/etat, 503 explicites

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Transitions `deployer_canary`, `promouvoir`, `rollback`

**Files:**
- Modify: `ops/deploy.py` (imports ; remplacer les trois stubs)
- Modify: `tests/unit/test_transitions.py` (ajouter les tests)

**Interfaces:**
- Consumes: `Registry.index()`, `ecrire_index()`, `manifest()`, `journaliser()` ; `ErreurRegistre`.
- Produces (dans `ops.deploy`) :
  - `CANARY_PERCENT_DEFAUT = 10`
  - `_verrou(registry: Registry) -> ContextManager[None]` (flock exclusif sur `<root>/index.lock`)
  - `_transition(registry, evenement: str, calcul: Callable[[dict], dict], *, origine: str, **details) -> dict` (renvoie `registry.index()`)
  - `deployer_canary(version: str, pourcentage: int | None = None, registry: Registry | None = None, *, origine: str = "manuel") -> dict`
  - `promouvoir(version: str, registry: Registry | None = None, *, origine: str = "manuel") -> dict`
  - `rollback(registry: Registry | None = None, motif: str = "manuel", *, origine: str = "manuel") -> dict`
  - Entrée de journal : `{ts, date, evenement, avant, apres, origine, version?, pourcentage?, motif?}` ; `avant`/`apres` sans `mis_a_jour`.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/unit/test_transitions.py` — compléter les imports en tête de fichier :

```python
from app.llm_client import Bundle
from ops.deploy import ErreurDeploiement, deployer_canary, promouvoir, rollback
```

puis ajouter à la fin du fichier :

```python
def _livrer(registry, version="v2.0.0"):
    registry.etiqueter(version, Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    return version


# ------------------------------------------------------------------ canary
def test_canary_route_le_pourcentage_et_journalise(registry):
    _livrer(registry)
    index = deployer_canary("v2.0.0", 30, registry, origine="ci:bot")
    assert (index["active"], index["canary"], index["canary_percent"]) == ("v1.0.0", "v2.0.0", 30)
    entree = registry.journal()[-1]
    assert entree["evenement"] == "canary"
    assert (entree["version"], entree["pourcentage"], entree["origine"]) == ("v2.0.0", 30, "ci:bot")
    assert entree["avant"]["canary"] is None and "mis_a_jour" not in entree["avant"]
    assert entree["apres"]["canary"] == "v2.0.0" and entree["apres"]["canary_percent"] == 30
    assert (registry.root / "index.lock").exists()


def test_canary_pourcentage_par_defaut(registry, monkeypatch):
    _livrer(registry)
    assert deployer_canary("v2.0.0", registry=registry)["canary_percent"] == 10
    monkeypatch.setenv("CANARY_PERCENT", "25")
    assert deployer_canary("v2.0.0", registry=registry)["canary_percent"] == 25


def test_canary_pourcentage_d_environnement_invalide(registry, monkeypatch):
    _livrer(registry)
    monkeypatch.setenv("CANARY_PERCENT", "dix")
    with pytest.raises(ErreurDeploiement, match="CANARY_PERCENT invalide"):
        deployer_canary("v2.0.0", registry=registry)


def test_canary_progression(registry):
    _livrer(registry)
    deployer_canary("v2.0.0", 10, registry)
    index = deployer_canary("v2.0.0", 50, registry)
    assert index["canary_percent"] == 50
    entree = registry.journal()[-1]
    assert entree["avant"]["canary_percent"] == 10 and entree["apres"]["canary_percent"] == 50


@pytest.mark.parametrize(
    ("version", "pourcentage", "motif"),
    [
        ("v9.9.9", 10, "inconnue"),
        ("v1.0.0", 10, "déjà la version active"),
        ("v2.0.0", 0, r"hors de \[1, 99\]"),
        ("v2.0.0", 100, r"hors de \[1, 99\]"),
    ],
)
def test_canary_refuse(registry, version, pourcentage, motif):
    _livrer(registry)
    avant, journal = registry.index(), registry.journal()
    with pytest.raises(ErreurDeploiement, match=motif):
        deployer_canary(version, pourcentage, registry)
    assert registry.index() == avant and registry.journal() == journal


def test_canary_refuse_si_un_autre_canary_est_en_cours(registry):
    _livrer(registry)
    _livrer(registry, "v2.0.1")
    deployer_canary("v2.0.0", 10, registry)
    with pytest.raises(ErreurDeploiement, match="canary v2.0.0 déjà en cours"):
        deployer_canary("v2.0.1", 10, registry)


# --------------------------------------------------------------- promotion
def test_promotion_du_canary(registry):
    _livrer(registry)
    deployer_canary("v2.0.0", 30, registry)
    index = promouvoir("v2.0.0", registry, origine="ci:bot")
    assert index["active"] == "v2.0.0" and index["precedente"] == "v1.0.0"
    assert index["canary"] is None and index["canary_percent"] == 0
    entree = registry.journal()[-1]
    assert (entree["evenement"], entree["version"], entree["origine"]) == (
        "promotion", "v2.0.0", "ci:bot"
    )


def test_promotion_directe_sans_canary(registry):
    _livrer(registry)
    assert promouvoir("v2.0.0", registry)["active"] == "v2.0.0"


@pytest.mark.parametrize(("version", "motif"), [("v9.9.9", "inconnue"), ("v1.0.0", "déjà")])
def test_promotion_refusee(registry, version, motif):
    with pytest.raises(ErreurDeploiement, match=motif):
        promouvoir(version, registry)


def test_promotion_refusee_par_dessus_un_autre_canary(registry):
    _livrer(registry)
    _livrer(registry, "v2.0.1")
    deployer_canary("v2.0.0", 10, registry)
    with pytest.raises(ErreurDeploiement, match="canary v2.0.0 en cours"):
        promouvoir("v2.0.1", registry)


# ---------------------------------------------------------------- rollback
def test_rollback_retire_le_canary(registry):
    _livrer(registry)
    deployer_canary("v2.0.0", 20, registry)
    index = rollback(registry, motif="dérive du score")
    assert index["active"] == "v1.0.0"
    assert index["canary"] is None and index["canary_percent"] == 0
    entree = registry.journal()[-1]
    assert (entree["evenement"], entree["motif"], entree["origine"]) == (
        "rollback", "dérive du score", "manuel"
    )
    assert entree["avant"]["canary"] == "v2.0.0" and entree["apres"]["canary"] is None


def test_rollback_revient_a_la_precedente_une_seule_fois(registry):
    _livrer(registry)
    promouvoir("v2.0.0", registry)
    index = rollback(registry, motif="test")
    assert index["active"] == "v1.0.0" and index["precedente"] is None
    with pytest.raises(ErreurDeploiement, match="rien à annuler"):
        rollback(registry)


def test_rollback_sans_rien_a_annuler(registry):
    with pytest.raises(ErreurDeploiement, match="rien à annuler"):
        rollback(registry)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_transitions.py -v`
Expected: les nouveaux tests FAIL avec `NotImplementedError: deploy.deployer_canary …` / `deploy.promouvoir …` / `deploy.rollback …` ; les 2 tests de la Task 1 passent.

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py`, remplacer le bloc d'imports par :

```python
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.llm_client import Bundle
from app.telemetry import MetricsStore
from eval.run_eval import Rapport, evaluer
from ops.registry import MOTIF_VERSION, ErreurRegistre, Registry

RACINE = Path(__file__).resolve().parent.parent
CANARY_PERCENT_DEFAUT = 10
```

(la ligne `RACINE = …` existe déjà : ne pas la dupliquer ; ajouter seulement `CANARY_PERCENT_DEFAUT` dessous.)

Remplacer les trois stubs `deployer_canary`, `promouvoir`, `rollback` par :

```python
# ------------------------------------------------------------- transitions
@contextmanager
def _verrou(registry: Registry) -> Iterator[None]:
    """Verrou exclusif inter-processus : la CI et la surveillance (SP4) écrivent
    toutes deux l'index ; une seule transition à la fois."""
    with (registry.root / "index.lock").open("a") as fichier:
        fcntl.flock(fichier, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fichier, fcntl.LOCK_UN)


def _etat(index: dict[str, Any]) -> dict[str, Any]:
    return {cle: valeur for cle, valeur in index.items() if cle != "mis_a_jour"}


def _transition(
    registry: Registry,
    evenement: str,
    calcul: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    origine: str,
    **details: Any,
) -> dict[str, Any]:
    """Sous verrou : état avant → ``calcul`` (lève si refus) → index écrit → journal."""
    with _verrou(registry):
        avant = _etat(registry.index())
        try:
            apres = calcul(dict(avant))
        except ErreurRegistre as exc:
            raise ErreurDeploiement(str(exc)) from exc
        registry.ecrire_index(apres)
        registry.journaliser(evenement, avant=avant, apres=apres, origine=origine, **details)
    return registry.index()


def _pourcentage_par_defaut() -> int:
    brut = os.environ.get("CANARY_PERCENT", "").strip()
    if not brut:
        return CANARY_PERCENT_DEFAUT
    try:
        return int(brut)
    except ValueError as exc:
        raise ErreurDeploiement(f"CANARY_PERCENT invalide : {brut!r} (attendu un entier)") from exc


def deployer_canary(
    version: str,
    pourcentage: int | None = None,
    registry: Registry | None = None,
    *,
    origine: str = "manuel",
) -> dict[str, Any]:
    """Route ``pourcentage`` % du trafic vers ``version`` ; même version = étape suivante."""
    registry = registry or Registry()
    if pourcentage is None:
        pourcentage = _pourcentage_par_defaut()

    def calcul(index: dict[str, Any]) -> dict[str, Any]:
        registry.manifest(version)
        if index.get("active") == version:
            raise ErreurDeploiement(f"{version} est déjà la version active")
        if not 1 <= pourcentage <= 99:
            raise ErreurDeploiement(
                f"pourcentage canary {pourcentage} hors de [1, 99] (100 % : promouvoir)"
            )
        en_cours = index.get("canary")
        if en_cours not in (None, version):
            raise ErreurDeploiement(
                f"canary {en_cours} déjà en cours : rollback ou promotion d'abord"
            )
        return {**index, "canary": version, "canary_percent": pourcentage}

    return _transition(
        registry, "canary", calcul, origine=origine, version=version, pourcentage=pourcentage
    )


def promouvoir(
    version: str, registry: Registry | None = None, *, origine: str = "manuel"
) -> dict[str, Any]:
    """``version`` devient active à 100 % ; l'ancienne active devient ``precedente``."""
    registry = registry or Registry()

    def calcul(index: dict[str, Any]) -> dict[str, Any]:
        registry.manifest(version)
        if index.get("active") == version:
            raise ErreurDeploiement(f"{version} est déjà la version active")
        en_cours = index.get("canary")
        if en_cours not in (None, version):
            raise ErreurDeploiement(
                f"canary {en_cours} en cours : on ne promeut pas {version} par-dessus"
            )
        return {
            **index,
            "precedente": index.get("active"),
            "active": version,
            "canary": None,
            "canary_percent": 0,
        }

    return _transition(registry, "promotion", calcul, origine=origine, version=version)


def rollback(
    registry: Registry | None = None, motif: str = "manuel", *, origine: str = "manuel"
) -> dict[str, Any]:
    """Retour arrière en une opération, sans rebuild : retire le canary s'il y en a
    un, sinon revient à ``precedente`` (un seul niveau)."""
    registry = registry or Registry()

    def calcul(index: dict[str, Any]) -> dict[str, Any]:
        if index.get("canary") is not None:
            return {**index, "canary": None, "canary_percent": 0}
        precedente = index.get("precedente")
        if precedente is None:
            raise ErreurDeploiement("rien à annuler : ni canary en cours ni version précédente")
        return {**index, "active": precedente, "precedente": None}

    return _transition(registry, "rollback", calcul, origine=origine, motif=motif)
```

Note : `registry.manifest(version)` lève `ErreurRegistre("version inconnue du registre : …")`, convertie en `ErreurDeploiement` par `_transition` (d'où `match="inconnue"`).

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_transitions.py -v && MOCK=on uv run pytest -q tests/acceptance/test_observabilite.py::test_rollback_en_une_operation tests/acceptance/test_observabilite.py::test_promotion_canary_puis_totale`
Expected: tous les tests de `test_transitions.py` passent ; les **deux tests d'acceptance visés passent**.

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/unit/test_transitions.py
git commit -m "feat(deploy): canary, promotion et rollback sous verrou, journalisés avant/après

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `installer` — version publiée par la CI → registre de prod

**Files:**
- Modify: `ops/deploy.py` (import `shutil`, nouvelle fonction après `rollback`)
- Modify: `tests/unit/test_transitions.py`

**Interfaces:**
- Consumes: `_verrou` (Task 5) ; `Registry.versions()`, `manifest()`, `journaliser()` ; `MOTIF_VERSION`.
- Produces: `installer(version: str, depuis: Path | str, registry: Registry | None = None, *, origine: str = "manuel") -> dict` — renvoie le manifeste de la version installée ; journal `installation` (`version`, `commit`, `note_eval`, `empreinte`, `origine`) uniquement lors d'une copie.

- [ ] **Step 1: Write the failing test**

Dans `tests/unit/test_transitions.py`, compléter les imports :

```python
from ops.deploy import ErreurDeploiement, deployer_canary, installer, promouvoir, rollback
from ops.registry import Registry
```

puis ajouter à la fin :

```python
# --------------------------------------------------------------- installer
def _artefact(tmp_path, version="v2.0.0"):
    """Dossier de version tel que la CI l'envoie en artefact (registre du runner)."""
    runner = Registry(tmp_path / "runner")
    runner.etiqueter(version, Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    return runner.root / version


def test_installer_copie_et_journalise(registry, tmp_path):
    avant = registry.index()
    manifeste = installer("v2.0.0", _artefact(tmp_path), registry, origine="ci:bot")
    assert manifeste["version"] == "v2.0.0"
    assert "v2.0.0" in registry.versions()
    assert registry.bundle("v2.0.0").strategie == "map_reduce_clauses"
    entree = registry.journal()[-1]
    assert entree["evenement"] == "installation"
    assert (entree["version"], entree["commit"], entree["origine"]) == (
        "v2.0.0", "abc1234", "ci:bot"
    )
    assert registry.index() == avant


def test_installer_est_idempotent(registry, tmp_path):
    dossier = _artefact(tmp_path)
    installer("v2.0.0", dossier, registry)
    journal = registry.journal()
    assert installer("v2.0.0", dossier, registry)["version"] == "v2.0.0"
    assert registry.journal() == journal


def test_installer_refuse_une_autre_empreinte(registry, tmp_path):
    registry.etiqueter("v2.0.0", Bundle.charger("v1"), commit="autre", note_eval=None)
    with pytest.raises(ErreurDeploiement, match="immuable"):
        installer("v2.0.0", _artefact(tmp_path), registry)


def test_installer_sans_manifeste(registry, tmp_path):
    with pytest.raises(ErreurDeploiement, match="manifeste introuvable"):
        installer("v2.0.0", tmp_path, registry)


def test_installer_version_incoherente(registry, tmp_path):
    with pytest.raises(ErreurDeploiement, match="décrit 'v2.0.0', pas v2.0.1"):
        installer("v2.0.1", _artefact(tmp_path), registry)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_transitions.py -v`
Expected: FAIL à la collecte — `ImportError: cannot import name 'installer' from 'ops.deploy'`.

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py`, ajouter `import shutil` dans les imports (ordre alphabétique, après `import os`), puis ajouter après `rollback` :

```python
def installer(
    version: str,
    depuis: Path | str,
    registry: Registry | None = None,
    *,
    origine: str = "manuel",
) -> dict[str, Any]:
    """Installe dans ce registre une version publiée ailleurs (artefact CI
    ``mardik-vX.Y.Z``). Idempotent à empreinte égale ; un tag reste immuable."""
    registry = registry or Registry()
    depuis = Path(depuis)
    if not MOTIF_VERSION.match(version):
        raise ErreurDeploiement(f"version invalide : {version!r} (attendu vX.Y.Z)")
    chemin = depuis / "manifest.json"
    try:
        manifeste = json.loads(chemin.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ErreurDeploiement(f"manifeste introuvable : {chemin}") from exc
    except json.JSONDecodeError as exc:
        raise ErreurDeploiement(f"manifeste illisible : {chemin} ({exc})") from exc
    if not isinstance(manifeste, dict) or manifeste.get("version") != version:
        decrit = manifeste.get("version") if isinstance(manifeste, dict) else None
        raise ErreurDeploiement(f"le manifeste de {depuis} décrit {decrit!r}, pas {version}")

    with _verrou(registry):
        if version in registry.versions():
            installe = registry.manifest(version)
            if installe.get("empreinte") != manifeste.get("empreinte"):
                raise ErreurDeploiement(
                    f"{version} déjà installée avec l'empreinte {installe.get('empreinte')} "
                    f"≠ {manifeste.get('empreinte')} : un tag est immuable"
                )
            return installe
        shutil.copytree(depuis, registry.root / version)
        registry.journaliser(
            "installation",
            version=version,
            commit=manifeste.get("commit"),
            note_eval=manifeste.get("note_eval"),
            empreinte=manifeste.get("empreinte"),
            origine=origine,
        )
    return manifeste
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_transitions.py -v`
Expected: tous passent.

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/unit/test_transitions.py
git commit -m "feat(deploy): installer une version publiée dans le registre de prod

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: CLI — `installer`, `--origine`, sortie JSON

**Files:**
- Modify: `ops/deploy.py` (docstring du module, fonction `main`)
- Create: `tests/integration/test_cli_deploy.py`

**Interfaces:**
- Consumes: `deployer_canary`, `promouvoir`, `rollback`, `installer` (Tasks 5–6).
- Produces: CLI

```
python -m ops.deploy installer vX.Y.Z --depuis DOSSIER [--origine O]
python -m ops.deploy canary vX.Y.Z [--pourcentage N] [--origine O]
python -m ops.deploy promouvoir vX.Y.Z [--origine O]
python -m ops.deploy rollback [--motif M] [--origine O]
```

succès → JSON (index, ou manifeste pour `installer`) sur stdout, code 0 ; `ErreurDeploiement` → `REFUSÉ : …` sur stderr, code 1.

- [ ] **Step 1: Write the failing test**

Créer `tests/integration/test_cli_deploy.py` :

```python
"""Tests d'intégration — CLI de déploiement (REGISTRY_PATH = registre isolé)."""
from __future__ import annotations

import json

from app.llm_client import Bundle
from ops import deploy
from ops.registry import Registry


def _sortie(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_cli_installer_canary_promotion_rollback(registry, tmp_path, capsys):
    runner = Registry(tmp_path / "runner")
    runner.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)

    assert deploy.main(
        ["installer", "v2.0.0", "--depuis", str(runner.root / "v2.0.0"), "--origine", "ci:bot"]
    ) == 0
    assert _sortie(capsys)["version"] == "v2.0.0"

    assert deploy.main(["canary", "v2.0.0", "--pourcentage", "10", "--origine", "ci:bot"]) == 0
    assert _sortie(capsys)["canary"] == "v2.0.0"
    assert registry.journal()[-1]["origine"] == "ci:bot"

    assert deploy.main(["promouvoir", "v2.0.0"]) == 0
    assert _sortie(capsys)["active"] == "v2.0.0"

    assert deploy.main(["rollback", "--motif", "test", "--origine", "ci:bot"]) == 0
    assert _sortie(capsys)["active"] == "v1.0.0"
    entree = registry.journal()[-1]
    assert (entree["evenement"], entree["motif"], entree["origine"]) == (
        "rollback", "test", "ci:bot"
    )


def test_cli_rollback_refuse(registry, capsys):
    assert deploy.main(["rollback"]) == 1
    erreur = capsys.readouterr().err
    assert "REFUSÉ" in erreur and "rien à annuler" in erreur


def test_cli_canary_version_inconnue(registry, capsys):
    assert deploy.main(["canary", "v9.9.9"]) == 1
    assert "inconnue" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/integration/test_cli_deploy.py -v`
Expected: `test_cli_installer_canary_promotion_rollback` FAIL (`SystemExit: 2`, argparse : `invalid choice: 'installer'`) ; les deux autres passent déjà (vérifier).

- [ ] **Step 3: Write minimal implementation**

Dans `ops/deploy.py`, remplacer la docstring du module par :

```python
"""Déploiement : publication, installation, canary, promotion, rollback. [surveillance : SP4]

Le registre (``ops/registry``) enregistre ; ce module décide. Chaque transition
d'index passe par ``_transition`` : verrou exclusif (``index.lock``), état
avant → calcul (refus = ``ErreurDeploiement``) → index écrit atomiquement →
entrée de journal ``{evenement, avant, apres, origine, …}``.

    publier(version=None, *, bundle="v2", commit=None, registry=None, seuil=None,
            rapport=None, versions=None) -> manifest
        Étiquette une version : sans ``version``, le patch suivant de la base
        ``vX.Y`` du bundle (registre ∪ ``versions``) ; avec, elle doit avoir la
        même base et ne pas être connue. Joue le gate (``eval.run_eval.evaluer``)
        sauf si un ``rapport`` est fourni, et REFUSE (``ErreurDeploiement``,
        journal ``publication_refusee``) s'il échoue. Sinon dépose le bundle dans
        le registre avec commit, note, mode et seuils du gate, et journalise
        ``publication``.

    installer(version, depuis, registry=None, *, origine="manuel") -> manifest
        Copie un dossier de version publié par la CI (artefact ``mardik-vX.Y.Z``)
        dans ce registre. Idempotent à empreinte égale ; empreinte différente =
        refus (un tag est immuable). Journalise ``installation``.

    deployer_canary(version, pourcentage=None, registry=None, *, origine="manuel") -> index
        ``pourcentage`` % du trafic (défaut : CANARY_PERCENT, sinon 10 ; 1 à 99)
        vers ``version``. Même version = étape de progression (10 → 50).
        Journalise ``canary``.

    promouvoir(version, registry=None, *, origine="manuel") -> index
        La version devient active à 100 % ; l'ancienne active devient
        ``index["precedente"]`` ; le canary est retiré. Journalise ``promotion``.

    rollback(registry=None, motif="manuel", *, origine="manuel") -> index
        Retour arrière en une opération, sans rebuild : retire le canary s'il y en
        a un, sinon l'active redevient ``precedente`` (un seul niveau).
        Journalise ``rollback`` avec le motif.

    surveiller(...) -> dict                                   [sous-projet 4]

``origine`` : ``manuel``, ``ci:<acteur GitHub>`` ou ``auto`` (surveillance).

Ligne de commande (JSON sur stdout ; refus → ``REFUSÉ : …`` sur stderr, code 1) :
``python -m ops.deploy publier [vX.Y.Z] [--commit SHA] [--rapport r.json]
| installer vX.Y.Z --depuis DOSSIER | canary vX.Y.Z [--pourcentage N]
| promouvoir vX.Y.Z | rollback [--motif M] | surveiller [--boucle]`` ;
``--origine`` sur installer, canary, promouvoir et rollback.
"""
```

Dans `main`, remplacer la déclaration des sous-commandes `canary`, `promouvoir`, `rollback` par :

```python
    i = sub.add_parser("installer")
    i.add_argument("version")
    i.add_argument("--depuis", required=True, help="dossier de version (artefact CI)")
    c = sub.add_parser("canary")
    c.add_argument("version")
    c.add_argument("--pourcentage", type=int, default=None)
    pr = sub.add_parser("promouvoir")
    pr.add_argument("version")
    r = sub.add_parser("rollback")
    r.add_argument("--motif", default="manuel")
    for commande in (i, c, pr, r):
        commande.add_argument("--origine", default="manuel",
                              help="manuel | ci:<acteur> | auto")
```

et remplacer les branches `canary`, `promouvoir`, `rollback` du `try` par :

```python
        elif args.commande == "installer":
            _afficher(installer(args.version, args.depuis, origine=args.origine))
        elif args.commande == "canary":
            _afficher(deployer_canary(args.version, args.pourcentage, origine=args.origine))
        elif args.commande == "promouvoir":
            _afficher(promouvoir(args.version, origine=args.origine))
        elif args.commande == "rollback":
            _afficher(rollback(motif=args.motif, origine=args.origine))
```

Ajouter juste avant `def main` :

```python
def _afficher(donnees: dict[str, Any]) -> None:
    print(json.dumps(donnees, ensure_ascii=False, indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/integration/test_cli_deploy.py tests/integration/test_publication.py -v`
Expected: tous passent (la CLI `publier` n'a pas changé).

- [ ] **Step 5: Commit**

```bash
git add ops/deploy.py tests/integration/test_cli_deploy.py
git commit -m "feat(deploy): CLI installer, option --origine, sortie JSON

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Workflows — canary CI, promotion, rollback sur le runner auto-hébergé

**Files:**
- Modify: `.github/workflows/ci.yml` (job `publication` : `outputs` + `id` d'étape ; job `deploiement-canary`)
- Create: `.github/workflows/promotion.yml`
- Create: `.github/workflows/rollback.yml`
- Create: `tests/unit/test_workflows.py`

**Interfaces:**
- Consumes: CLI de la Task 7.
- Produces: job `publication` avec `outputs.version` ; tout job de prod : `runs-on: [self-hosted, mardik]`, `environment: production`, `concurrency: {group: mardik-production, cancel-in-progress: false}`, `env.REGISTRY_PATH: ${{ vars.MARDIK_REGISTRY_PATH }}`. Variables de dépôt attendues : `MARDIK_REGISTRY_PATH` (obligatoire), `MARDIK_APP_URL` (optionnelle, défaut `http://localhost:8000`).

- [ ] **Step 1: Write the failing test**

Créer `tests/unit/test_workflows.py` :

```python
"""Tests unitaires — invariants de sécurité des workflows qui touchent la prod."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
FICHIERS = ["ci.yml", "promotion.yml", "rollback.yml"]
CONCURRENCE = {"group": "mardik-production", "cancel-in-progress": False}


def _charger(nom: str) -> dict:
    return yaml.safe_load((WORKFLOWS / nom).read_text(encoding="utf-8"))


def _declencheurs(workflow: dict) -> set[str]:
    # PyYAML (YAML 1.1) lit la clé « on » comme le booléen True
    return set(workflow.get("on", workflow.get(True)))


def _jobs_de_prod(workflow: dict) -> dict[str, dict]:
    def labels(job: dict) -> list[str]:
        runs_on = job["runs-on"]
        return runs_on if isinstance(runs_on, list) else [runs_on]

    return {nom: job for nom, job in workflow["jobs"].items() if "self-hosted" in labels(job)}


@pytest.mark.parametrize("nom", FICHIERS)
def test_jobs_de_prod_serialises_et_cibles(nom):
    jobs = _jobs_de_prod(_charger(nom))
    assert jobs, f"{nom} : aucun job sur le runner de prod"
    for job in jobs.values():
        assert job["runs-on"] == ["self-hosted", "mardik"]
        assert job["concurrency"] == CONCURRENCE
        assert job["environment"] == "production"
        assert job["env"]["REGISTRY_PATH"] == "${{ vars.MARDIK_REGISTRY_PATH }}"


@pytest.mark.parametrize("nom", FICHIERS)
def test_aucun_job_de_prod_sur_pull_request(nom):
    workflow = _charger(nom)
    if not _declencheurs(workflow) & {"pull_request", "pull_request_target"}:
        return
    for job in _jobs_de_prod(workflow).values():
        condition = job.get("if", "")
        assert "github.ref == 'refs/heads/main'" in condition
        assert "startsWith(github.ref, 'refs/tags/v')" in condition


@pytest.mark.parametrize("nom", ["promotion.yml", "rollback.yml"])
def test_pilotage_uniquement_manuel(nom):
    assert _declencheurs(_charger(nom)) == {"workflow_dispatch"}


def test_publication_expose_la_version():
    publication = _charger("ci.yml")["jobs"]["publication"]
    assert publication["outputs"]["version"] == "${{ steps.etiquetage.outputs.version }}"
    ids = [etape.get("id") for etape in publication["steps"]]
    assert "etiquetage" in ids


def test_rollback_exige_un_motif():
    entree = _charger("rollback.yml")[True]["workflow_dispatch"]["inputs"]["motif"]
    assert entree["required"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MOCK=on uv run pytest tests/unit/test_workflows.py -v`
Expected: FAIL — `ci.yml : aucun job sur le runner de prod`, `FileNotFoundError` pour `promotion.yml` / `rollback.yml`, `KeyError: 'outputs'`.

- [ ] **Step 3: Write minimal implementation**

**3a — `ci.yml`, job `publication`.** Ajouter sous `runs-on: ubuntu-latest` du job `publication` (avant `permissions:`) :

```yaml
    outputs:
      version: ${{ steps.etiquetage.outputs.version }}
```

Remplacer l'étape « Étiquetage dans le registre » par :

```yaml
      - name: Étiquetage dans le registre
        id: etiquetage
        env:
          REF_TYPE: ${{ github.ref_type }}
          REF_NAME: ${{ github.ref_name }}
        run: |
          VERSION_ARG=""
          if [ "$REF_TYPE" = "tag" ]; then VERSION_ARG="$REF_NAME"; fi
          uv run python -m ops.deploy publier $VERSION_ARG \
            --commit "${GITHUB_SHA::7}" --rapport eval/rapport.json > manifest.json
          cat manifest.json
          VERSION=$(jq -r .version manifest.json)
          echo "VERSION=$VERSION" >> "$GITHUB_ENV"
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"
```

**3b — `ci.yml`, job `deploiement-canary`.** Remplacer tout le job par :

```yaml
  # Prod : runner auto-hébergé sur la machine de démo, qui écrit dans le registre
  # monté par le docker compose (variable de dépôt MARDIK_REGISTRY_PATH).
  deploiement-canary:
    needs: publication
    if: >-
      always()
      && needs.publication.result == 'success'
      && (github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v'))
    runs-on: [self-hosted, mardik]
    environment: production
    concurrency:
      group: mardik-production
      cancel-in-progress: false
    env:
      REGISTRY_PATH: ${{ vars.MARDIK_REGISTRY_PATH }}
      MARDIK_APP_URL: ${{ vars.MARDIK_APP_URL || 'http://localhost:8000' }}
      VERSION: ${{ needs.publication.outputs.version }}
      ORIGINE: ci:${{ github.actor }}
    steps:
      - name: Préconditions
        run: |
          if [ -z "$VERSION" ]; then echo "::error::publication n'a exposé aucune version"; exit 1; fi
          if [ -z "$REGISTRY_PATH" ]; then echo "::error::variable MARDIK_REGISTRY_PATH absente"; exit 1; fi
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.11"
      - run: uv sync
      - uses: actions/download-artifact@v4
        with:
          name: mardik-${{ needs.publication.outputs.version }}
          path: ${{ runner.temp }}/mardik
      - name: Installation dans le registre de prod
        run: |
          uv run python -m ops.deploy installer "$VERSION" \
            --depuis "$RUNNER_TEMP/mardik/ops/registry/$VERSION" --origine "$ORIGINE"
      - name: Canary 10 %
        run: uv run python -m ops.deploy canary "$VERSION" --pourcentage 10 --origine "$ORIGINE"
      - name: État vu par la gateway
        run: curl -sf "$MARDIK_APP_URL/gateway/etat"
```

Mettre à jour l'en-tête de `ci.yml` : remplacer la ligne `# Chaîne CI/CD Mardik — gates bloquants → build → artefact étiqueté → canary.` par :

```yaml
# Chaîne CI/CD Mardik — gates bloquants → build → artefact étiqueté → canary.
# Le canary tourne sur le runner auto-hébergé de prod ; la suite du pilotage
# (50 %, 100 %, rollback) passe par promotion.yml et rollback.yml.
```

**3c — créer `.github/workflows/promotion.yml`** :

```yaml
# Promotion du canary : 50 % du trafic, puis 100 % (la version devient active).
# Décision humaine dans le sous-projet 3 ; le sous-projet 4 la branchera sur les
# métriques. S'exécute sur le runner auto-hébergé de prod, jamais sur une PR.

name: promotion

on:
  workflow_dispatch:
    inputs:
      version:
        description: "Version en canary (vX.Y.Z)"
        required: true
        type: string
      etape:
        description: "50 : canary à 50 % ; 100 : promotion totale"
        required: true
        type: choice
        options: ["50", "100"]

jobs:
  promotion:
    runs-on: [self-hosted, mardik]
    environment: production
    concurrency:
      group: mardik-production
      cancel-in-progress: false
    env:
      REGISTRY_PATH: ${{ vars.MARDIK_REGISTRY_PATH }}
      MARDIK_APP_URL: ${{ vars.MARDIK_APP_URL || 'http://localhost:8000' }}
      VERSION: ${{ inputs.version }}
      ETAPE: ${{ inputs.etape }}
      ORIGINE: ci:${{ github.actor }}
    steps:
      - name: Préconditions
        run: |
          if [ -z "$REGISTRY_PATH" ]; then echo "::error::variable MARDIK_REGISTRY_PATH absente"; exit 1; fi
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.11"
      - run: uv sync
      - name: Étape de promotion
        run: |
          if [ "$ETAPE" = "100" ]; then
            uv run python -m ops.deploy promouvoir "$VERSION" --origine "$ORIGINE"
          else
            uv run python -m ops.deploy canary "$VERSION" --pourcentage 50 --origine "$ORIGINE"
          fi
      - name: État vu par la gateway
        run: curl -sf "$MARDIK_APP_URL/gateway/etat"
```

**3d — créer `.github/workflows/rollback.yml`** :

```yaml
# Rollback en une action : retire le canary en cours, sinon revient à la version
# précédente (N-1, déjà dans le registre de prod : ni artefact ni rebuild).
#   gh workflow run rollback.yml -f motif="dérive du score de confiance"

name: rollback

on:
  workflow_dispatch:
    inputs:
      motif:
        description: "Pourquoi revenir en arrière (inscrit au journal)"
        required: true
        type: string

jobs:
  rollback:
    runs-on: [self-hosted, mardik]
    environment: production
    concurrency:
      group: mardik-production
      cancel-in-progress: false
    env:
      REGISTRY_PATH: ${{ vars.MARDIK_REGISTRY_PATH }}
      MARDIK_APP_URL: ${{ vars.MARDIK_APP_URL || 'http://localhost:8000' }}
      MOTIF: ${{ inputs.motif }}
      ORIGINE: ci:${{ github.actor }}
    steps:
      - name: Préconditions
        run: |
          if [ -z "$REGISTRY_PATH" ]; then echo "::error::variable MARDIK_REGISTRY_PATH absente"; exit 1; fi
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.11"
      - run: uv sync
      - name: Rollback
        run: uv run python -m ops.deploy rollback --motif "$MOTIF" --origine "$ORIGINE"
      - name: État vu par la gateway
        run: curl -sf "$MARDIK_APP_URL/gateway/etat"
```

Toutes les valeurs venant du contexte GitHub (entrées, acteur) passent par `env:` puis `"$VAR"` — jamais `${{ … }}` directement dans `run:` (injection de script).

- [ ] **Step 4: Run test to verify it passes**

Run: `MOCK=on uv run pytest tests/unit/test_workflows.py -v`
Expected: tous passent.
Si `actionlint` est installé : `actionlint .github/workflows/*.yml` → aucune erreur. Sinon, le noter dans le rapport de tâche.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/promotion.yml .github/workflows/rollback.yml tests/unit/test_workflows.py
git commit -m "ci: canary, promotion et rollback sur le runner auto-hébergé de prod

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Documentation d'exploitation et vérification finale

**Files:**
- Modify: `docs/exploitation.md` (§4 et §5 uniquement ; §6 et §7 restent pour SP4)

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: procédure opérationnelle lisible « à 3 h du matin ».

- [ ] **Step 1: Rédiger §4**

Dans `docs/exploitation.md`, remplacer le commentaire HTML sous `## 4. Déploiement progressif` par :

```markdown
La gateway (`POST /analyse`) relit `ops/registry/index.json` à chaque requête
et envoie `canary_percent` % du trafic au canary, le reste à la version active.
L'en-tête `X-Mardik-Version` dit quelle version a répondu ; `GET /gateway/etat`
donne la répartition courante. `/v1/analyse` reste servi à l'identique pendant
toute la transition.

| Étape | Déclencheur | Commande exécutée par la chaîne |
|---|---|---|
| Installation + 10 % | automatique après `publication` sur `main` ou tag (`ci.yml`, job `deploiement-canary`) | `ops.deploy installer vX.Y.Z` puis `ops.deploy canary vX.Y.Z --pourcentage 10` |
| 50 % | `promotion.yml`, `etape=50` | `ops.deploy canary vX.Y.Z --pourcentage 50` |
| 100 % | `promotion.yml`, `etape=100` | `ops.deploy promouvoir vX.Y.Z` (l'ancienne active devient `precedente`) |

Avant chaque étape, regarder le tableau de bord de la version canary : taux
d'erreur ≤ v1, latence P95 < 8 s, score de confiance stable. Dans cette
version la décision est humaine ; les critères automatiques de promotion sont
décrits en §6.

Refus (job rouge, rien n'est modifié) : version absente du registre, version
déjà active, pourcentage hors de 1 à 99, autre canary déjà en cours.
`CANARY_PERCENT` (`.env`) ne fixe que le pourcentage par défaut d'un canary.
```

- [ ] **Step 2: Rédiger §5**

Remplacer le commentaire HTML sous `## 5. Procédure de rollback` par :

```markdown
**Une action**, depuis l'onglet *Actions* → *rollback* → *Run workflow*, ou :

    gh workflow run rollback.yml -f motif="dérive du score de confiance"

- **Qui** : tout collaborateur ayant le droit d'écriture sur le dépôt.
- **Effet** : si un canary est en cours, il est retiré (100 % sur l'active) ;
  sinon l'active redevient la version précédente. Aucun rebuild : la version
  N-1 est déjà dans le registre. Un seul niveau : un second rollback consécutif
  est refusé.
- **Vérifier** : la dernière étape du job affiche `GET /gateway/etat` ; la
  dernière ligne de `ops/registry/journal.jsonl` est l'entrée `rollback` avec
  le motif, l'origine (`ci:<acteur>`) et l'index `avant`/`apres`. Les réponses
  de `/analyse` portent l'en-tête `X-Mardik-Version` de la version restaurée.
- **Jamais à la main** : le rollback passe par la chaîne. Si le runner est hors
  ligne, le redémarrer d'abord (ci-dessous) ; en dernier recours seulement,
  `uv run python -m ops.deploy rollback --motif "…"` sur la machine de prod
  (tracé avec l'origine `manuel`).

### Runner de prod (installation, une fois)

1. *Settings* → *Actions* → *Runners* → *New self-hosted runner* sur la machine
   qui fait tourner `docker compose` ; labels : `self-hosted`, `mardik`.
2. Installer `uv` sur la machine (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
3. *Settings* → *Secrets and variables* → *Actions* → *Variables* :
   `MARDIK_REGISTRY_PATH` = chemin absolu du dossier `ops/registry` monté par
   le docker compose (ex. `/home/demo/mardik/ops/registry`) ; optionnel :
   `MARDIK_APP_URL` (défaut `http://localhost:8000`).
4. *Settings* → *Environments* : créer `production` (historique des déploiements).
5. Redémarrer le runner : `./svc.sh stop && ./svc.sh start` dans son dossier
   d'installation.

Aucun job du runner de prod ne s'exécute sur une pull request : le code d'un
fork ne tourne jamais sur cette machine.
```

- [ ] **Step 3: Vérification complète**

Run: `uv run ruff check . && MOCK=on uv run pytest -q`
Expected: ruff propre ; **seuls** `test_dashboard_par_version` et `test_journal_derive_et_rollback_automatique` (SP4) échouent. Tout le reste passe, dont `test_rollback_en_une_operation`, `test_promotion_canary_puis_totale` et `test_client_v1_fonctionne`.

Run: `git status --short`
Expected: seul `docs/exploitation.md` modifié ; aucun `index.lock` / `index.json.tmp` / `journal.jsonl` suivi (ignorés par `.gitignore`).

- [ ] **Step 4: Commit**

```bash
git add docs/exploitation.md
git commit -m "docs(exploitation): déploiement progressif, procédure de rollback, runner de prod

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Couverture de la spec

| Spec | Tâche |
|---|---|
| D5 (pas de forçage `CANARY_PERCENT` dans la gateway) | 4 (docstring, routage via l'index), 5 (défaut de `deployer_canary`) |
| D6 / §5.1 écriture atomique | 1 |
| §4.1 gateway, §4.2 service, §4.3 erreurs | 2, 3, 4 |
| §5.2 verrou et transition, §5.3 transitions | 5 |
| §5.4 `installer` | 6 |
| §5.5 CLI | 7 |
| §6.1–6.4 chaîne CI | 8 |
| §6.5 `docs/exploitation.md` | 9 |
| §7 tests, §7.4 critères de fin | 1–9, vérification finale en 9 |
