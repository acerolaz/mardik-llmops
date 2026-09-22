"""Tests unitaires — service de routage (registre isolé en tmp_path)."""
from __future__ import annotations

import pytest

from app.api_v1 import analyser_v1
from app.api_v2 import analyser_v2
from app.llm_client import Bundle
from app.routage import (
    MOTEURS_HTTP,
    AucuneVersionActive,
    BundleIllisible,
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


def test_bundle_illisible(registry):
    (registry.root / "v1.0.0" / "config.yaml").unlink()
    with pytest.raises(BundleIllisible, match="version v1.0.0 : bundle illisible"):
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
