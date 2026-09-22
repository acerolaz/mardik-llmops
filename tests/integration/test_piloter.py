"""Le pilote : une tour = seuils → surveillance → palier. Sans état."""
from __future__ import annotations

import time
from dataclasses import replace

import pytest

from app.llm_client import Bundle
from app.telemetry import Mesure
from ops.deploy import deployer_canary, main, piloter
from ops.seuils import CHEMIN_SEUILS_PILOTAGE_DEFAUT, ErreurSeuilsPilotage, charger_seuils_pilotage

BASE = charger_seuils_pilotage()
RAPIDES = replace(BASE, promotion=replace(BASE.promotion, duree_min_s=0, requetes_min=5))


def _rapides():
    return RAPIDES


def _canary(registry):
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    deployer_canary("v2.0.0", 10, registry)


def _trafic(metriques, n=12, *, score=0.9):
    for _ in range(n):
        ts = time.time()
        metriques.enregistrer(Mesure(ts=ts, version="v2.0.0", route="/analyse",
                                     latence_ms=2000, score=score))
        metriques.enregistrer(Mesure(ts=ts, version="v1.0.0", route="/analyse",
                                     latence_ms=900))


def _evenements(registry, *noms):
    return [e for e in registry.journal() if e["evenement"] in noms]


def test_trafic_conforme_promu_par_paliers(registry, metriques):
    _canary(registry)
    _trafic(metriques)
    assert piloter(registry, metriques, tours=1, charger=_rapides) == 1
    assert registry.canary() == ("v2.0.0", 50)
    piloter(registry, metriques, tours=1, charger=_rapides)      # redémarrage : palier relu
    assert registry.canary() == ("v2.0.0", 50)                   # aucune mesure du palier 50
    _trafic(metriques)
    piloter(registry, metriques, tours=1, charger=_rapides)
    assert registry.active() == "v2.0.0" and registry.canary() == (None, 0)
    auto = [e for e in _evenements(registry, "canary", "promotion") if e["origine"] == "auto"]
    assert [e["evenement"] for e in auto] == ["canary", "promotion"]
    assert auto[0]["resume"].startswith("Version v2.0.0 étendue à 50 %")
    assert auto[1]["resume"].startswith("Version v2.0.0 servie à tous les clients")


def test_derive_rollback_sans_promotion(registry, metriques):
    _canary(registry)
    _trafic(metriques, score=0.5)
    piloter(registry, metriques, tours=1, charger=_rapides)
    assert registry.canary() == (None, 0) and registry.active() == "v1.0.0"
    assert _evenements(registry, "promotion") == []
    assert _evenements(registry, "rollback")[-1]["origine"] == "auto"


def test_seuils_journalises_puis_modifies(registry, metriques, tmp_path, monkeypatch):
    chemin = tmp_path / "seuils_pilotage.yaml"
    chemin.write_text(CHEMIN_SEUILS_PILOTAGE_DEFAUT.read_text(encoding="utf-8"),
                      encoding="utf-8")
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(chemin))
    piloter(registry, metriques, tours=1)
    initiaux = _evenements(registry, "seuils")
    assert {e["fichier"] for e in initiaux} == {"ops/seuils_pilotage.yaml", "eval/seuils.yaml"}
    assert all(e["avant"] is None for e in initiaux)
    chemin.write_text(
        chemin.read_text(encoding="utf-8")
        .replace("score_min: 0.70", "score_min: 0.68")
        .replace('motif: "seuils initiaux', 'motif: "calibration du 22/09'),
        encoding="utf-8",
    )
    piloter(registry, metriques, tours=1)
    change = _evenements(registry, "seuils")[-1]
    assert change["fichier"] == "ops/seuils_pilotage.yaml"
    assert "derive.score_min 0,70 → 0,68" in change["resume"]


def test_seuils_casses_en_cours_de_route(registry, metriques):
    appels = {"n": 0}

    def charger():
        appels["n"] += 1
        if appels["n"] > 1:
            raise ErreurSeuilsPilotage("YAML invalide")
        return BASE

    attentes = []
    assert piloter(registry, metriques, tours=3, charger=charger,
                   attendre=attentes.append) == 3
    assert attentes == [5.0, 5.0]
    invalides = _evenements(registry, "seuils_invalides")
    assert len(invalides) == 1 and "YAML invalide" in invalides[0]["resume"]


def test_seuils_invalides_au_demarrage(registry, metriques, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(tmp_path / "absent.yaml"))
    with pytest.raises(ErreurSeuilsPilotage):
        piloter(registry, metriques, tours=1)
    assert main(["piloter", "--tours", "1"]) == 1
    assert "SEUILS INVALIDES" in capsys.readouterr().err


class _MetriquesCassees:
    """Enveloppe un ``MetricsStore`` réel : lève ``OSError`` au premier ``lire``
    (panne transitoire simulée), délègue ensuite normalement."""

    def __init__(self, reelles):
        self._reelles = reelles
        self._appels = 0

    def lire(self, *args, **kwargs):
        self._appels += 1
        if self._appels == 1:
            raise OSError("panne disque simulée")
        return self._reelles.lire(*args, **kwargs)

    def enregistrer(self, *args, **kwargs):
        return self._reelles.enregistrer(*args, **kwargs)


def test_pilote_survit_a_une_panne_transitoire(registry, metriques):
    """Une ``OSError`` (ou ``ErreurRegistre``/``json.JSONDecodeError``) levée pendant une
    tour ne doit pas arrêter le pilote (aucun service n'a de politique de redémarrage
    autre que ``restart: unless-stopped`` — le service doit rester en vie tout seul) :
    seuls des seuils invalides AU DÉMARRAGE doivent l'arrêter."""
    fausses = _MetriquesCassees(metriques)
    assert piloter(registry, fausses, tours=2, charger=_rapides) == 2
    assert fausses._appels >= 2


def test_journal_illisible_pendant_un_rechargement_de_seuils(registry, metriques):
    """Seuils invalides + journal momentanément illisible : la boucle continue."""
    appels = {"n": 0}
    panne = {"armee": False}

    def charger():
        appels["n"] += 1
        if appels["n"] > 1:
            panne["armee"] = True            # le journal tombe au moment de tracer l'incident
            raise ErreurSeuilsPilotage("YAML invalide")
        return BASE

    vrai_journal = registry.journal

    def journal_capricieux():
        if panne["armee"]:
            panne["armee"] = False
            raise OSError("journal momentanément illisible")
        return vrai_journal()

    registry.journal = journal_capricieux            # type: ignore[method-assign]
    assert piloter(registry, metriques, tours=2, charger=charger, attendre=lambda _: None) == 2


def test_canary_sans_debut_de_palier_journalise_un_refus(registry, metriques):
    """Index avec canary mais journal sans entrée « canary » : le blocage est tracé."""
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    registry.definir_canary("v2.0.0", 10)          # aucune entrée « canary » au journal

    piloter(registry, metriques, tours=2, attendre=lambda _: None)
    refus = _evenements(registry, "pilotage_refus")
    assert len(refus) == 1                          # une seule fois par fenêtre
    assert refus[0]["action"] == "palier" and refus[0]["origine"] == "auto"
    assert "palier" in refus[0]["resume"]
