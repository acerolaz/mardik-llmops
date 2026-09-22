"""Décisions pures du pilote : dérive, palier, changements de seuils."""
from __future__ import annotations

import yaml

from app.telemetry import Mesure
from ops.pilotage import changements_seuils, debut_palier, detecter_derive, evaluer_palier, version_surveillee
from ops.seuils import SeuilsDerive, charger_seuils_pilotage

DERIVE = SeuilsDerive(score_min=0.70, marge=0.05, taux_erreur_max=0.10, latence_p95_max_ms=8000)
SEUILS = charger_seuils_pilotage()   # duree_min_s 60, requetes_min 20, minimum 10


def _canary(n=25, *, score=0.9, latence=2000.0, erreurs=0):
    return [m(score=score, latence=latence) for _ in range(n - erreurs)] + [
        m(erreur=True) for _ in range(erreurs)
    ]


def _active(n=25, erreurs=0):
    return [m("v1.0.0", score=None) for _ in range(n - erreurs)] + [
        m("v1.0.0", score=None, erreur=True) for _ in range(erreurs)
    ]


def m(version="v2.0.0", *, latence=1000.0, score=0.9, erreur=False, ts=0.0):
    return Mesure(ts=ts, version=version, route="/analyse", latence_ms=latence,
                  erreur=erreur, score=score)


def test_version_surveillee():
    assert version_surveillee({"active": "v1.0.0", "canary": "v2.0.0"}) == "v2.0.0"
    assert version_surveillee({"active": "v1.0.0", "canary": None}) == "v1.0.0"
    assert version_surveillee({}) is None


def test_echantillon_insuffisant():
    d = detecter_derive("v2.0.0", [m(score=0.1)] * 5, DERIVE, minimum=10)
    assert d.niveau == "echantillon_insuffisant" and not d.critique
    assert d.motif == "échantillon insuffisant (5/10)"


def test_derive_critique_sur_le_score():
    d = detecter_derive("v2.0.0", [m(score=0.5)] * 12, DERIVE, minimum=10)
    assert d.critique and d.niveau == "critique"
    assert d.motif == "score moyen 0.50 < 0.70"
    assert d.principal.signal == "score_moyen" and d.sous_seuil == 12


def test_derive_critique_sur_les_erreurs():
    d = detecter_derive("v2.0.0", [m()] * 10 + [m(erreur=True)] * 2, DERIVE, minimum=10)
    assert d.critique and d.motif.startswith("taux d'erreur 17%")


def test_derive_critique_sur_la_latence():
    d = detecter_derive("v2.0.0", [m(latence=9000)] * 12, DERIVE, minimum=10)
    assert d.critique and d.motif == "latence P95 9000 ms > 8000 ms"


def test_le_score_est_cite_en_premier():
    d = detecter_derive("v2.0.0", [m(score=0.5, latence=9000)] * 12, DERIVE, minimum=10)
    assert d.motif.startswith("score")


def test_marge_sans_action():
    d = detecter_derive("v2.0.0", [m(score=0.72)] * 12, DERIVE, minimum=10)
    assert d.niveau == "marge" and not d.critique
    assert d.motif == "score moyen 0.72 dans la marge [0.70 ; 0.75["


def test_aucune_derive():
    d = detecter_derive("v2.0.0", [m(score=0.9)] * 12, DERIVE, minimum=10)
    assert d.niveau == "aucune" and d.motif == "" and d.principal is None


def test_v1_sans_score_jugee_sur_erreurs_et_latence():
    d = detecter_derive("v1.0.0", [m("v1.0.0", score=None)] * 12, DERIVE, minimum=10)
    assert d.niveau == "aucune"
    assert "score_moyen" not in {c.signal for c in d.constats}


def test_debut_palier():
    j = [{"evenement": "canary", "version": "v2.0.0", "ts": 1.0},
         {"evenement": "canary", "version": "v2.0.0", "ts": 2.0}]
    assert debut_palier(j, "v2.0.0") == 2.0
    assert debut_palier(j + [{"evenement": "rollback", "ts": 3.0}], "v2.0.0") is None
    assert debut_palier(j + [{"evenement": "promotion", "ts": 3.0}], "v2.0.0") is None
    assert debut_palier(j, "v3.0.0") is None


def test_attendre_la_duree_minimale():
    d = evaluer_palier("v2.0.0", _canary(), _active(), SEUILS, depuis_s=30, pourcentage=10)
    assert d.action == "attendre" and "30/60 s" in d.motif


def test_attendre_le_nombre_de_requetes():
    d = evaluer_palier("v2.0.0", _canary(5), _active(), SEUILS, depuis_s=120, pourcentage=10)
    assert d.action == "attendre" and "5/20 requêtes" in d.motif


def test_progresser_puis_promouvoir():
    d = evaluer_palier("v2.0.0", _canary(), _active(), SEUILS, depuis_s=120, pourcentage=10)
    assert d.action == "progresser" and d.pourcentage_suivant == 50
    assert d.motif == "palier 10 % tenu : 25 analyses conformes en 120 s"
    assert all(c.ok for c in d.constats) and d.requetes == 25
    d = evaluer_palier("v2.0.0", _canary(), _active(), SEUILS, depuis_s=120, pourcentage=50)
    assert d.action == "promouvoir" and d.pourcentage_suivant == 100


def test_erreurs_superieures_a_la_v1():
    d = evaluer_palier("v2.0.0", _canary(erreurs=3), _active(), SEUILS,
                       depuis_s=120, pourcentage=10)
    assert d.action == "attendre" and "taux_erreur" in d.motif


def test_queue_basse_du_score():
    canary = [m(score=0.95) for _ in range(20)] + [m(score=0.5) for _ in range(5)]
    d = evaluer_palier("v2.0.0", canary, _active(), SEUILS, depuis_s=120, pourcentage=10)
    assert d.action == "attendre" and "score_p10" in d.motif


def test_repli_sur_le_seuil_absolu_si_active_peu_servie():
    canary = _canary(erreurs=2)   # 8 % d'erreurs
    assert evaluer_palier("v2.0.0", canary, _active(3), SEUILS,
                          depuis_s=120, pourcentage=10).action == "progresser"
    assert evaluer_palier("v2.0.0", canary, _active(25), SEUILS,
                          depuis_s=120, pourcentage=10).action == "attendre"


def test_changements_seuils(tmp_path):
    chemin = tmp_path / "s.yaml"
    chemin.write_text(yaml.safe_dump({"derive": {"score_min": 0.7}, "motif": "a"}),
                      encoding="utf-8")
    fichiers = {"ops/seuils_pilotage.yaml": chemin}
    [initial] = changements_seuils([], fichiers)
    assert initial["fichier"] == "ops/seuils_pilotage.yaml" and initial["avant"] is None
    assert initial["apres"]["derive"]["score_min"] == 0.7 and initial["motif"] == "a"
    journal = [{"evenement": "seuils", **initial}]
    assert changements_seuils(journal, fichiers) == []
    chemin.write_text(yaml.safe_dump({"derive": {"score_min": 0.68}, "motif": "b"}),
                      encoding="utf-8")
    [change] = changements_seuils(journal, fichiers)
    assert change["avant"] == initial["apres"] and change["apres"]["derive"]["score_min"] == 0.68
    assert change["empreinte"] != initial["empreinte"]
