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
