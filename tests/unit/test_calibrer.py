"""calibrer : moyenne − k·σ sur la distribution de production (D7)."""
from __future__ import annotations

import time

import pytest

from app.telemetry import Mesure
from ops.seuils import ErreurSeuilsPilotage, calibrer, main


def _remplir(metriques, scores, version="v2.0.0", erreur=False):
    for s in scores:
        metriques.enregistrer(Mesure(ts=time.time(), version=version, route="/analyse",
                                     latence_ms=1000, erreur=erreur, score=s))


def test_proposition_moyenne_moins_k_ecarts_types(metriques):
    _remplir(metriques, [0.7] * 15 + [0.9] * 15)
    _remplir(metriques, [0.1] * 5, erreur=True)          # erreurs exclues
    _remplir(metriques, [None] * 5)                      # sans score exclues
    _remplir(metriques, [0.2] * 30, version="v3.0.0")    # autre version exclue
    r = calibrer(metriques, "v2.0.0")
    assert r["mesures"] == 30
    assert r["moyenne"] == pytest.approx(0.8)
    assert r["ecart_type"] == pytest.approx(0.1)
    assert r["score_min_propose"] == pytest.approx(0.65)
    assert r["p10"] == pytest.approx(0.7)
    assert r["score_p10_min_propose"] == pytest.approx(0.65)


def test_echantillon_insuffisant(metriques):
    _remplir(metriques, [0.8] * 10)
    with pytest.raises(ErreurSeuilsPilotage, match="échantillon insuffisant"):
        calibrer(metriques, "v2.0.0")


def test_cli(metriques, capsys):
    _remplir(metriques, [0.7] * 15 + [0.9] * 15)
    assert main(["calibrer", "--version", "v2.0.0"]) == 0
    sortie = capsys.readouterr().out
    assert "score_min: 0.65" in sortie and "n'écrit rien" in sortie
    assert main(["calibrer", "--version", "v9.9.9"]) == 1
    assert "CALIBRATION IMPOSSIBLE" in capsys.readouterr().err
