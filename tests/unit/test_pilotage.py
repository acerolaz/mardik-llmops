"""Décisions pures du pilote : dérive, palier, changements de seuils."""
from __future__ import annotations

from app.telemetry import Mesure
from ops.pilotage import detecter_derive, version_surveillee
from ops.seuils import SeuilsDerive

DERIVE = SeuilsDerive(score_min=0.70, marge=0.05, taux_erreur_max=0.10, latence_p95_max_ms=8000)


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
