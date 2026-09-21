"""Tests unitaires — orchestration de l'analyse v2 (LLM remplacé par FauxClient)."""
from __future__ import annotations

import time

import pytest

from app.api_v2 import analyser_v2, construire_warnings
from app.llm_client import ErreurLLM
from app.pipeline import Clause, DocumentTropLong
from tests.unit.doublures import FauxClient

CONTRAT = "".join(
    f"Article {i} — Résiliation\nLe contrat peut être résilié par chaque partie.\n"
    for i in range(1, 4)
)


def test_reponse_complete_et_totaux(telemetry, metriques):
    client = FauxClient(tokens=100)
    reponse = analyser_v2(CONTRAT, client, telemetry)
    assert reponse.version == "v2.0.0"
    assert reponse.model_version == f"v2.0.0-{client.bundle.empreinte()}"
    assert reponse.sections == reponse.appels_llm == client.appels == 1
    assert [c.type for c in reponse.clauses] == ["résiliation"]
    assert reponse.cout_eur == pytest.approx(0.0002)
    assert reponse.warnings == []
    [mesure] = metriques.lire()
    assert mesure.route == "/v2/analyse" and mesure.erreur is False
    assert mesure.score == reponse.confiance_globale
    assert mesure.tokens == 100 and mesure.appels_llm == 1


def test_refuse_un_document_trop_long_sans_appel_llm(telemetry, metriques):
    client = FauxClient()
    with pytest.raises(DocumentTropLong, match="limite 200 000"):
        analyser_v2("a " * 100_001, client, telemetry)
    assert client.appels == 0
    assert metriques.lire()[-1].erreur is True


def test_refuse_trop_de_sections_sans_appel_llm(telemetry, contrat):
    client = FauxClient()
    client.bundle.parametres["sections_max"] = 2
    with pytest.raises(DocumentTropLong, match="limite 2 "):
        analyser_v2(contrat("c12"), client, telemetry)
    assert client.appels == 0


def test_appels_llm_en_parallele(telemetry):
    client = FauxClient(delai_s=0.2)
    client.bundle.parametres.update(contexte_max_caracteres=100, parallelisme=8)
    texte = "".join(
        f"Article {i} — Durée\nLe contrat est conclu pour une durée de un an.\n"
        for i in range(1, 9)
    )
    debut = time.perf_counter()
    reponse = analyser_v2(texte, client, telemetry)
    assert reponse.sections == 8 and client.appels == 8
    assert time.perf_counter() - debut < 1.0  # 8 × 0,2 s en série = 1,6 s


def test_erreur_llm_pendant_le_map_journalisee(telemetry, metriques):
    client = FauxClient(erreur=ErreurLLM("fournisseur ollama injoignable"))
    with pytest.raises(ErreurLLM):
        analyser_v2(CONTRAT, client, telemetry)
    assert metriques.lire()[-1].erreur is True


def test_reponse_hors_schema_produit_des_warnings(telemetry):
    client = FauxClient(repondre=lambda _: "pas du json")
    reponse = analyser_v2(CONTRAT, client, telemetry)
    assert reponse.clauses == [] and reponse.confiance_globale == 0.0
    assert reponse.warnings == [
        "aucune clause détectée — relecture conseillée",
        "1 clause(s) ignorée(s) : réponse LLM hors schéma"
        " (sections : Article 1 — Résiliation (+2))",
    ]


def test_warning_clause_sous_le_seuil():
    clause = Clause("garantie", "x", 0.5, [0], confiance=0.42)
    assert construire_warnings([clause], [], 0.6) == [
        "clause « garantie » : confiance 0,42 < 0,60 — relecture conseillée"
    ]


def test_warning_clauses_ignorees_regroupe_les_sections():
    clause = Clause("durée", "x", 0.9, [0], confiance=0.9)
    assert construire_warnings([clause], [("Article 7", 2), ("Article 9", 1)], 0.6) == [
        "3 clause(s) ignorée(s) : réponse LLM hors schéma (sections : Article 7, Article 9)"
    ]


def test_pas_de_warning_quand_tout_va_bien():
    assert construire_warnings([Clause("durée", "x", 0.9, [0], confiance=0.9)], [], 0.6) == []
