"""Agrégats purs sur des Mesure : ce que voient le dashboard et le pilote."""
from __future__ import annotations

import pytest

from app.telemetry import Mesure
from ops.signaux import agreger, filtrer, histogramme, percentile, scores, serie_par_minute


def m(version="v2.0.0", *, latence=1000.0, score=0.9, erreur=False, ts=0.0, cout=0.01):
    return Mesure(ts=ts, version=version, route="/analyse", latence_ms=latence,
                  erreur=erreur, score=score, cout_eur=cout)


def test_percentile():
    assert percentile([], 50) is None
    assert percentile([4.0], 95) == 4.0
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile(list(range(1, 101)), 95) == pytest.approx(95.05)


def test_agreger_exclut_les_erreurs_des_latences_et_scores():
    mesures = [
        m(latence=100, score=0.8),
        m(latence=200, score=0.9),
        m(latence=300, score=None),
        m(latence=9999, score=0.1, erreur=True),
    ]
    a = agreger(mesures)
    assert a.requetes == 4 and a.erreurs == 1 and a.taux_erreur == 0.25
    assert a.latence_p50_ms == 200.0
    assert a.latence_p95_ms == pytest.approx(290.0)
    assert a.score_moyen == pytest.approx(0.85)
    assert a.cout_total_eur == pytest.approx(0.04)
    assert a.cout_moyen_eur == pytest.approx(0.01)
    assert scores(mesures) == [0.8, 0.9]


def test_agreger_v1_sans_score():
    a = agreger([m("v1.0.0", score=None) for _ in range(3)])
    assert a.score_moyen is None and a.score_p10 is None


def test_agreger_vide():
    a = agreger([])
    assert a.requetes == 0 and a.taux_erreur == 0.0 and a.latence_p95_ms is None


def test_histogramme():
    assert histogramme([0.05, 0.3, 0.7, 0.95, 1.0]) == [1, 0, 0, 1, 0, 0, 0, 1, 0, 2]
    assert histogramme([]) == [0] * 10


def test_serie_par_minute():
    serie = serie_par_minute([m(ts=0), m(ts=30, erreur=True), m(ts=65)])
    assert [p["minute"] for p in serie] == [0, 60]
    assert serie[0]["requetes"] == 2 and serie[0]["taux_erreur"] == 0.5
    assert serie[1]["requetes"] == 1


def test_filtrer():
    mesures = [m("v1.0.0", ts=100), m(ts=10), m(ts=60)]
    assert [x.ts for x in filtrer(mesures, "v2.0.0")] == [10, 60]
    assert [x.ts for x in filtrer(mesures, "v2.0.0", depuis_ts=50)] == [60]
