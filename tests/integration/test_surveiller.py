"""Surveillance : dérive critique → rollback auto ; marge → alerte unique."""
from __future__ import annotations

import time

from app.llm_client import Bundle
from app.telemetry import Mesure
from ops.deploy import deployer_canary, surveiller


def _canary(registry):
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    deployer_canary("v2.0.0", 10, registry)


def _mesures(metriques, n, *, version="v2.0.0", score=0.9, latence=2000.0, erreur=False,
             ts=None):
    for _ in range(n):
        metriques.enregistrer(Mesure(ts=ts or time.time(), version=version, route="/analyse",
                                     latence_ms=latence, score=score, erreur=erreur))


def test_rollback_sur_score_avec_resume_metier(registry, metriques):
    _canary(registry)
    _mesures(metriques, 12, score=0.5)
    res = surveiller(registry, metriques)
    assert res["derive"] and res["rollback"] and res["niveau"] == "critique"
    entree = registry.journal()[-1]
    assert entree["evenement"] == "rollback" and entree["origine"] == "auto"
    assert entree["signal"] == "score_moyen" and entree["seuil"] == 0.70
    assert entree["resume"] == (
        "Version v2.0.0 retirée : 12 analyses sur 12 jugées peu fiables (score < 0,70).")
    assert registry.canary() == (None, 0)


def test_rollback_sur_erreurs_puis_sur_latence(registry, metriques):
    _canary(registry)
    _mesures(metriques, 10)
    _mesures(metriques, 3, erreur=True)
    assert surveiller(registry, metriques)["motif"].startswith("taux d'erreur")
    index = deployer_canary("v2.0.0", 10, registry)   # nouveau palier après le rollback
    assert index["canary"] == "v2.0.0"
    _mesures(metriques, 12, latence=9500)
    res = surveiller(registry, metriques)
    assert res["rollback"] and res["motif"].startswith("latence P95")


def test_marge_alerte_une_seule_fois(registry, metriques):
    _canary(registry)
    _mesures(metriques, 12, score=0.72)
    for _ in range(3):
        res = surveiller(registry, metriques)
    assert res["niveau"] == "marge" and not res["rollback"]
    alertes = [e for e in registry.journal() if e["evenement"] == "alerte"]
    assert len(alertes) == 1 and alertes[0]["origine"] == "auto"
    assert "à surveiller" in alertes[0]["resume"]
    assert registry.canary()[0] == "v2.0.0"


def test_echantillon_insuffisant(registry, metriques):
    _canary(registry)
    _mesures(metriques, 5, score=0.1)
    res = surveiller(registry, metriques)
    assert res["niveau"] == "echantillon_insuffisant" and not res["rollback"]


def test_mesures_d_un_palier_anterieur_ignorees(registry, metriques):
    _mesures(metriques, 12, score=0.1, ts=time.time() - 30)   # avant le canary
    _canary(registry)
    assert surveiller(registry, metriques)["niveau"] == "echantillon_insuffisant"


def test_refus_sans_retour_arriere(registry, metriques):
    _mesures(metriques, 12, version="v1.0.0", score=None, latence=9500)
    res = surveiller(registry, metriques)
    assert res["derive"] and not res["rollback"]
    refus = registry.journal()[-1]
    assert refus["evenement"] == "pilotage_refus" and "rien à annuler" in refus["raison"]
    surveiller(registry, metriques)
    assert sum(e["evenement"] == "pilotage_refus" for e in registry.journal()) == 1
