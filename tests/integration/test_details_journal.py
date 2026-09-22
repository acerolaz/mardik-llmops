"""Les transitions acceptent des détails de pilotage, recopiés au journal (C2.8)."""
from __future__ import annotations

from app.llm_client import Bundle
from ops.deploy import deployer_canary, promouvoir, rollback


def _v2(registry):
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)


def test_details_recopies(registry):
    _v2(registry)
    deployer_canary("v2.0.0", 10, registry, origine="auto", resume="r1", requetes=23)
    rollback(registry, motif="score moyen 0.52 < 0.70", origine="auto",
             signal="score_moyen", valeur=0.52, seuil=0.70, resume="r2")
    canary, retour = registry.journal()[-2:]
    assert canary["resume"] == "r1" and canary["requetes"] == 23
    assert retour["signal"] == "score_moyen" and retour["valeur"] == 0.52
    assert retour["motif"] == "score moyen 0.52 < 0.70" and retour["origine"] == "auto"
    assert retour["avant"]["canary"] == "v2.0.0" and retour["apres"]["canary"] is None


def test_promotion_avec_details_et_appels_historiques_inchanges(registry):
    _v2(registry)
    promouvoir("v2.0.0", registry, origine="auto", resume="r3")
    assert registry.journal()[-1]["resume"] == "r3"
    rollback(registry)                                  # appel historique, sans détails
    assert registry.journal()[-1]["origine"] == "manuel"
