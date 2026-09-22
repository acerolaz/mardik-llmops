"""Tableau de bord : résumé enrichi, rendus texte et HTML."""
from __future__ import annotations

import json
import time

from app.llm_client import Bundle
from app.telemetry import Mesure
from ops.dashboard import rendre_html, rendre_texte, resume


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
    assert r["alertes"] == ["Version v2.0.0 à surveiller : score moyen 0,72, proche du seuil 0,70."]
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
    assert r["fenetre_s"] == 300           # seuils invalides → repli sur 300 s


def test_fenetre_par_defaut_suit_les_seuils(metriques, registry, monkeypatch, tmp_path):
    """``fenetre_s`` non précisée : le tableau de bord suit ``ops/seuils_pilotage.yaml``,
    comme le pilote (``ops.deploy.surveiller``) — même fenêtre affichée que celle décidée."""
    from ops.seuils import CHEMIN_SEUILS_PILOTAGE_DEFAUT

    chemin = tmp_path / "seuils_pilotage.yaml"
    chemin.write_text(
        CHEMIN_SEUILS_PILOTAGE_DEFAUT.read_text(encoding="utf-8")
        .replace("fenetre_s: 300", "fenetre_s: 120"),
        encoding="utf-8",
    )
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(chemin))
    r = resume(metriques, registry=registry)
    assert r["fenetre_s"] == 120


def test_fenetre_explicite_l_emporte_sur_les_seuils(metriques, registry, monkeypatch, tmp_path):
    from ops.seuils import CHEMIN_SEUILS_PILOTAGE_DEFAUT

    chemin = tmp_path / "seuils_pilotage.yaml"
    chemin.write_text(
        CHEMIN_SEUILS_PILOTAGE_DEFAUT.read_text(encoding="utf-8")
        .replace("fenetre_s: 300", "fenetre_s: 120"),
        encoding="utf-8",
    )
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(chemin))
    r = resume(metriques, fenetre_s=60, registry=registry)
    assert r["fenetre_s"] == 60


def test_alerte_derive_critique_en_langage_metier(metriques, registry):
    """La dérive critique (rollback déclenché par ops.deploy.surveiller) est aussi
    signalée ici, en phrase métier — pas le motif technique brut."""
    _canary(registry)
    _mesures(metriques, "v2.0.0", 12, score=0.3)     # sous le seuil dur (0.70)
    r = resume(metriques, registry=registry)
    assert len(r["alertes"]) == 1
    assert r["alertes"][0].startswith("Version v2.0.0 en dérive critique : score moyen")
    assert "0,30" in r["alertes"][0]
    assert "[" not in r["alertes"][0]                # plus la forme technique "dans la marge [...]"


def test_rendre_texte(metriques, registry):
    _canary(registry)
    _mesures(metriques, "v2.0.0", 12, score=0.72)
    texte = rendre_texte(resume(metriques, registry=registry))
    assert "v2.0.0" in texte and "palier : v2.0.0 à 10 %" in texte
    assert "alertes :" in texte and "journal :" in texte


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
