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


def test_nombre_d_essais_invalide():
    with pytest.raises(ValueError, match="nombre d'essais invalide"):
        evaluer("v1", sous_ensemble=["c01"], n_essais=0, historique=None)


def test_rapport_depuis_dict_aller_retour(historique):
    rapport = evaluer("v2", sous_ensemble=["c01"], historique=historique)
    donnees = json.loads(json.dumps(rapport.to_dict()))
    assert Rapport.depuis_dict({**donnees, "cle_inconnue": 1}) == rapport


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

    assert main([*commun, "--contrats", "c01", "--essais", "0"]) == 2

    invalide = tmp_path / "seuils.yaml"
    invalide.write_text("note_min: 0.75\n", encoding="utf-8")
    monkeypatch.setenv("SEUILS_PATH", str(invalide))
    assert main([*commun, "--contrats", "c01"]) == 2
    assert "latence_p95_max_ms" in capsys.readouterr().err

    assert len(historique.read_text(encoding="utf-8").splitlines()) == 2
