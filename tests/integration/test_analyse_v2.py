"""Tests d'intégration — la v2 de bout en bout : vrai bundle, vrai client LLM (MOCK=on),
traces OpenTelemetry, métriques et route HTTP. Mêmes signaux que la v1."""
from __future__ import annotations

import pytest

from app.api_v2 import analyser_v2
from app.llm_client import Bundle, ErreurLLM, LLMClient
from app.pipeline import DocumentTropLong


def _analyser(telemetry, texte):
    return analyser_v2(texte, LLMClient(Bundle.charger("v2")), telemetry)


def _fournisseur_en_panne(monkeypatch):
    monkeypatch.setenv("MOCK", "off")
    monkeypatch.setenv("LLM_PROXY_URL", "http://127.0.0.1:9")  # port fermé
    monkeypatch.setenv("LLM_TIMEOUT_S", "2")


def test_contrat_long_analyse_sans_troncature(telemetry, contrat):
    reponse = _analyser(telemetry, contrat("c12"))
    assert reponse.sections > 1 and reponse.appels_llm == reponse.sections
    types = {c.type for c in reponse.clauses}
    assert {"résiliation", "droit applicable"} <= types
    assert len(types) == len(reponse.clauses)
    assert all(0.0 <= c.confiance <= 1.0 for c in reponse.clauses)
    assert 0.0 <= reponse.confiance_globale <= 1.0
    assert reponse.model_version == f"v2.0.0-{Bundle.charger('v2').empreinte()}"


def test_spans_llm_enfants_de_la_requete(telemetry, span_exporter, contrat):
    reponse = _analyser(telemetry, contrat("c07"))
    spans = span_exporter.get_finished_spans()
    [racine] = [s for s in spans if s.name == "analyse.requete"]
    appels = [s for s in spans if s.name == "llm.appel"]
    assert len(appels) == reponse.sections
    for span in appels:
        assert span.context.trace_id == racine.context.trace_id
        assert span.parent.span_id == racine.context.span_id
    assert racine.attributes["mardik.version"] == "v2.0.0"
    assert racine.attributes["mardik.sections"] == reponse.sections
    assert racine.attributes["mardik.model_version"] == reponse.model_version


def test_mesure_journalisee(telemetry, metriques, contrat):
    reponse = _analyser(telemetry, contrat("c01"))
    [mesure] = metriques.lire()
    assert mesure.version == "v2.0.0" and mesure.route == "/v2/analyse"
    assert mesure.erreur is False
    assert mesure.score == reponse.confiance_globale
    assert mesure.appels_llm == reponse.sections
    assert mesure.cout_eur > 0


def test_fournisseur_injoignable_journalise_une_erreur(telemetry, metriques, contrat, monkeypatch):
    _fournisseur_en_panne(monkeypatch)
    with pytest.raises(ErreurLLM):
        _analyser(telemetry, contrat("c01"))
    assert metriques.lire()[-1].erreur is True


def test_document_trop_long_refuse_sans_appel(telemetry, metriques):
    with pytest.raises(DocumentTropLong):
        _analyser(telemetry, "a " * 100_001)
    [mesure] = metriques.lire()
    assert mesure.erreur is True and mesure.appels_llm == 0


def test_http_200_respecte_le_contrat_v2(client, contrat):
    r = client.post("/v2/analyse", json={"texte": contrat("c01"), "contrat_id": "c01"})
    assert r.status_code == 200, r.text
    corps = r.json()
    attendus = {"clauses", "confiance_globale", "modele", "version", "model_version",
                "sections", "appels_llm", "latence_ms", "cout_eur", "warnings"}
    assert attendus <= set(corps)
    assert all({"type", "extrait", "confiance", "sections"} <= set(c) for c in corps["clauses"])


def test_http_413_explicite(client):
    r = client.post("/v2/analyse", json={"texte": "a " * 100_001})
    assert r.status_code == 413
    assert "limite 200 000" in r.json()["detail"]


@pytest.mark.parametrize("corps", [{"pas_le_bon_champ": 1}, {"texte": "trop court"}])
def test_http_422_corps_invalide(client, corps):
    r = client.post("/v2/analyse", json=corps)
    assert r.status_code == 422
    assert "detail" in r.json()


def test_http_503_fournisseur_en_panne(client, contrat, monkeypatch):
    _fournisseur_en_panne(monkeypatch)
    r = client.post("/v2/analyse", json={"texte": contrat("c01")})
    assert r.status_code == 503
    assert "LLM" in r.json()["detail"]


def test_v1_inchangee_pendant_que_v2_est_active(client, contrat):
    assert client.post("/v2/analyse", json={"texte": contrat("c01")}).status_code == 200
    corps = client.post("/v1/analyse", json={"texte": contrat("c01")}).json()
    assert set(corps) == {"clauses", "modele", "version", "tronque"}
    assert corps["version"] == "v1.0.0"
