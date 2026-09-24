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


def test_bundle_illisible_503(client, registry):
    (registry.root / "v1.0.0" / "config.yaml").unlink()
    reponse = client.post("/analyse", json={"texte": TEXTE})
    assert reponse.status_code == 503
    assert "version v1.0.0 : bundle illisible" in reponse.json()["detail"]


def test_fournisseur_indisponible_503(client):
    client.app.dependency_overrides[gateway.get_fabrique_client] = lambda: (
        lambda bundle: FauxClient(bundle, erreur=ErreurLLM("délai dépassé"))
    )
    reponse = client.post("/analyse", json={"texte": TEXTE})
    assert reponse.status_code == 503
    assert reponse.json()["detail"] == "fournisseur LLM indisponible : délai dépassé"


def test_la_fabrique_recoit_le_bundle_livre(client, registry):
    _livrer_v2(registry)
    registry.definir_actif("v2.0.0")
    recus: list[str] = []

    def fabrique(bundle: Bundle) -> FauxClient:
        recus.append(bundle.version)
        return FauxClient(bundle)

    client.app.dependency_overrides[gateway.get_fabrique_client] = lambda: fabrique
    reponse = client.post("/analyse", json={"texte": TEXTE})

    assert reponse.status_code == 200, reponse.text
    assert recus == ["v2.0.0"]
    assert reponse.headers["x-mardik-version"] == "v2.0.0"


def test_corps_invalide_422(client):
    assert client.post("/analyse", json={"texte": "court"}).status_code == 422
