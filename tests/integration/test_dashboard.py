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
    assert r["seuils"] is None and r["decision"] is None


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


def test_palier_compte_toutes_les_requetes_du_palier(metriques, registry, tmp_path, monkeypatch):
    """Le palier peut être plus long que la fenêtre : le compteur suit le palier."""
    vrai_time = time.time
    depart = vrai_time() - 600
    monkeypatch.setattr(time, "time", lambda: depart)   # le canary est daté d'il y a 600 s
    _canary(registry)
    monkeypatch.setattr(time, "time", vrai_time)

    _mesures(metriques, "v2.0.0", 5, score=0.9, ts=depart + 10, latence=2500)   # dans le palier, hors fenêtre
    _mesures(metriques, "v2.0.0", 3, score=0.9, latence=2500)                   # dans la fenêtre

    r = resume(metriques, registry=registry, fenetre_s=300, candidats=tmp_path / "absent.jsonl")
    assert r["total"] == 3                      # la fenêtre ne montre que les 3 récentes
    assert r["palier"]["requetes"] == 8         # le palier en compte 8, comme le pilote


def test_sources_illisibles_signalees_sans_500(metriques, registry, tmp_path, monkeypatch):
    """Un fichier de candidats illisible ne doit pas emporter toute la page."""
    from ops import dashboard

    _mesures(metriques, "v1.0.0", 3, score=None)

    def _illisible(chemin=None):
        raise OSError("eval/candidats.jsonl : permission refusée")

    monkeypatch.setattr(dashboard, "candidats_en_attente", _illisible)
    r = resume(metriques, registry=registry)
    assert r["par_version"]["v1.0.0"]["requetes"] == 3        # le reste est produit
    assert r["candidats"] == 0
    assert any("candidats illisibles" in a for a in r["alertes"])
    assert "candidats illisibles" in rendre_texte(r)


def test_metriques_illisibles_signalees_sans_500(metriques, registry, monkeypatch):
    """Les métriques illisibles ne doivent pas emporter toute la page."""
    _canary(registry)
    _mesures(metriques, "v2.0.0", 12, score=0.72)

    monkeypatch.setattr(metriques, "lire", lambda depuis_s: (_ for _ in ()).throw(OSError("metrics.jsonl illisible")))
    r = resume(metriques, registry=registry)
    assert r["palier"] is None
    assert any("métriques illisibles" in a for a in r["alertes"])
    assert "métriques illisibles" in rendre_texte(r)


def _resume_apres(metriques, registry, tmp_path, entree, secondes):
    return resume(metriques, registry=registry, candidats=tmp_path / "absent.jsonl",
                  maintenant=entree["ts"] + secondes)


def test_decision_attendre_faute_de_volume(metriques, registry, tmp_path):
    entree = _canary(registry)
    _mesures(metriques, "v2.0.0", 12, score=0.9)

    d = _resume_apres(metriques, registry, tmp_path, entree, 42)["decision"]

    assert (d["action"], d["version"], d["active"]) == ("attendre", "v2.0.0", "v1.0.0")
    assert d["motif"] == "palier 10 % : 12/20 requêtes, 42/60 s"
    assert d["constats"] == [] and d["pourcentage_suivant"] is None


def test_decision_attendre_critere_non_tenu(metriques, registry, tmp_path):
    entree = _canary(registry)
    _mesures(metriques, "v2.0.0", 25, score=0.72)    # ≥ 0,70 : pas de dérive ; < 0,75 : palier non tenu

    d = _resume_apres(metriques, registry, tmp_path, entree, 120)["decision"]

    assert d["action"] == "attendre"
    assert d["motif"].startswith("critère non tenu : score_moyen")
    assert {"signal": "score_moyen", "valeur": 0.72, "seuil": 0.75, "ok": False} in d["constats"]


def test_decision_progresser(metriques, registry, tmp_path):
    entree = _canary(registry, 10)
    _mesures(metriques, "v2.0.0", 25, score=0.9)

    d = _resume_apres(metriques, registry, tmp_path, entree, 120)["decision"]

    assert (d["action"], d["pourcentage_suivant"]) == ("progresser", 50)
    assert d["constats"] and all(c["ok"] for c in d["constats"])


def test_decision_promouvoir(metriques, registry, tmp_path):
    entree = _canary(registry, 50)
    _mesures(metriques, "v2.0.0", 25, score=0.9)

    d = _resume_apres(metriques, registry, tmp_path, entree, 120)["decision"]

    assert (d["action"], d["pourcentage_suivant"]) == ("promouvoir", 100)


def test_decision_rollback_prioritaire_sur_le_palier(metriques, registry, tmp_path):
    entree = _canary(registry)
    _mesures(metriques, "v2.0.0", 25, score=0.5)     # dérive critique : score < 0,70

    d = _resume_apres(metriques, registry, tmp_path, entree, 120)["decision"]

    assert d["action"] == "rollback" and d["pourcentage_suivant"] is None
    assert any(c["signal"] == "score_moyen" and not c["ok"] for c in d["constats"])


def test_sans_canary_pas_de_verdict_mais_seuils_exposes(metriques, registry, tmp_path):
    r = resume(metriques, registry=registry, candidats=tmp_path / "absent.jsonl")

    assert r["decision"] is None
    assert r["seuils"]["derive"]["score_min"] == 0.70
    assert r["seuils"]["promotion"]["paliers"] == [10, 50, 100]
    assert r["seuils"]["motif"]


def test_metrics_lues_une_seule_fois_par_resume(metriques, registry, tmp_path, monkeypatch):
    """Fenêtre, palier et verdict viennent de la même lecture de ``metrics.jsonl``."""
    entree = _canary(registry)
    _mesures(metriques, "v2.0.0", 25, score=0.9)
    lectures = []
    lire = type(metriques).lire
    monkeypatch.setattr(type(metriques), "lire", lambda self, *a, **k: lectures.append(1) or lire(self, *a, **k))

    r = _resume_apres(metriques, registry, tmp_path, entree, 120)

    assert len(lectures) == 1
    assert r["palier"]["requetes"] == 25 and r["decision"]["action"] == "progresser"


def test_canary_expose_meme_si_metriques_illisibles(metriques, registry, monkeypatch):
    """L'écran doit savoir qu'un canary tourne, même quand il ne peut pas en juger."""
    _canary(registry)
    monkeypatch.setattr(metriques, "lire", lambda depuis_s: (_ for _ in ()).throw(OSError("perm")))

    r = resume(metriques, registry=registry)

    assert r["canary"] == "v2.0.0"
    assert r["palier"] is None and r["decision"] is None


def test_sans_canary_champ_canary_nul(metriques, registry, tmp_path):
    assert resume(metriques, registry=registry, candidats=tmp_path / "absent.jsonl")["canary"] is None


def test_fenetre_zero_compte_tout_le_trafic(metriques, registry, tmp_path):
    """``--fenetre 0`` : toutes les mesures, comme ``MetricsStore.lire(depuis_s=0)``."""
    _mesures(metriques, "v1.0.0", 5, score=None, ts=time.time() - 3600)   # avant le palier
    entree = _canary(registry)
    _mesures(metriques, "v2.0.0", 3, score=0.9)

    r = resume(metriques, fenetre_s=0, registry=registry, candidats=tmp_path / "absent.jsonl",
               maintenant=entree["ts"] + 10)

    assert r["total"] == 8
    assert r["par_version"]["v1.0.0"]["requetes"] == 5
