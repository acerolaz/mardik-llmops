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
