"""Tests unitaires — registre (écriture atomique) et transitions de déploiement."""
from __future__ import annotations

import pytest

from app.llm_client import Bundle
from ops.deploy import ErreurDeploiement, deployer_canary, promouvoir, rollback


def test_ecriture_atomique_de_l_index(registry, monkeypatch):
    avant = registry.index()

    def remplacement_en_echec(*args, **kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr("ops.registry.os.replace", remplacement_en_echec)
    with pytest.raises(OSError, match="disque plein"):
        registry.ecrire_index({**avant, "active": "v9.9.9"})
    assert registry.index() == avant


def test_ecriture_ne_laisse_pas_de_fichier_temporaire(registry):
    registry.ecrire_index({**registry.index(), "canary": None})
    assert not (registry.root / "index.json.tmp").exists()


def _livrer(registry, version="v2.0.0"):
    registry.etiqueter(version, Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    return version


# ------------------------------------------------------------------ canary
def test_canary_route_le_pourcentage_et_journalise(registry):
    _livrer(registry)
    index = deployer_canary("v2.0.0", 30, registry, origine="ci:bot")
    assert (index["active"], index["canary"], index["canary_percent"]) == ("v1.0.0", "v2.0.0", 30)
    entree = registry.journal()[-1]
    assert entree["evenement"] == "canary"
    assert (entree["version"], entree["pourcentage"], entree["origine"]) == ("v2.0.0", 30, "ci:bot")
    assert entree["avant"]["canary"] is None and "mis_a_jour" not in entree["avant"]
    assert entree["apres"]["canary"] == "v2.0.0" and entree["apres"]["canary_percent"] == 30
    assert (registry.root / "index.lock").exists()


def test_canary_pourcentage_par_defaut(registry, monkeypatch):
    _livrer(registry)
    assert deployer_canary("v2.0.0", registry=registry)["canary_percent"] == 10
    monkeypatch.setenv("CANARY_PERCENT", "25")
    assert deployer_canary("v2.0.0", registry=registry)["canary_percent"] == 25


def test_canary_pourcentage_d_environnement_invalide(registry, monkeypatch):
    _livrer(registry)
    monkeypatch.setenv("CANARY_PERCENT", "dix")
    with pytest.raises(ErreurDeploiement, match="CANARY_PERCENT invalide"):
        deployer_canary("v2.0.0", registry=registry)


def test_canary_progression(registry):
    _livrer(registry)
    deployer_canary("v2.0.0", 10, registry)
    index = deployer_canary("v2.0.0", 50, registry)
    assert index["canary_percent"] == 50
    entree = registry.journal()[-1]
    assert entree["avant"]["canary_percent"] == 10 and entree["apres"]["canary_percent"] == 50


@pytest.mark.parametrize(
    ("version", "pourcentage", "motif"),
    [
        ("v9.9.9", 10, "inconnue"),
        ("v1.0.0", 10, "déjà la version active"),
        ("v2.0.0", 0, r"hors de \[1, 99\]"),
        ("v2.0.0", 100, r"hors de \[1, 99\]"),
    ],
)
def test_canary_refuse(registry, version, pourcentage, motif):
    _livrer(registry)
    avant, journal = registry.index(), registry.journal()
    with pytest.raises(ErreurDeploiement, match=motif):
        deployer_canary(version, pourcentage, registry)
    assert registry.index() == avant and registry.journal() == journal


def test_canary_refuse_si_un_autre_canary_est_en_cours(registry):
    _livrer(registry)
    _livrer(registry, "v2.0.1")
    deployer_canary("v2.0.0", 10, registry)
    with pytest.raises(ErreurDeploiement, match="canary v2.0.0 déjà en cours"):
        deployer_canary("v2.0.1", 10, registry)


# --------------------------------------------------------------- promotion
def test_promotion_du_canary(registry):
    _livrer(registry)
    deployer_canary("v2.0.0", 30, registry)
    index = promouvoir("v2.0.0", registry, origine="ci:bot")
    assert index["active"] == "v2.0.0" and index["precedente"] == "v1.0.0"
    assert index["canary"] is None and index["canary_percent"] == 0
    entree = registry.journal()[-1]
    assert (entree["evenement"], entree["version"], entree["origine"]) == (
        "promotion", "v2.0.0", "ci:bot"
    )


def test_promotion_directe_sans_canary(registry):
    _livrer(registry)
    assert promouvoir("v2.0.0", registry)["active"] == "v2.0.0"


@pytest.mark.parametrize(("version", "motif"), [("v9.9.9", "inconnue"), ("v1.0.0", "déjà")])
def test_promotion_refusee(registry, version, motif):
    with pytest.raises(ErreurDeploiement, match=motif):
        promouvoir(version, registry)


def test_promotion_refusee_par_dessus_un_autre_canary(registry):
    _livrer(registry)
    _livrer(registry, "v2.0.1")
    deployer_canary("v2.0.0", 10, registry)
    with pytest.raises(ErreurDeploiement, match="canary v2.0.0 en cours"):
        promouvoir("v2.0.1", registry)


# ---------------------------------------------------------------- rollback
def test_rollback_retire_le_canary(registry):
    _livrer(registry)
    deployer_canary("v2.0.0", 20, registry)
    index = rollback(registry, motif="dérive du score")
    assert index["active"] == "v1.0.0"
    assert index["canary"] is None and index["canary_percent"] == 0
    entree = registry.journal()[-1]
    assert (entree["evenement"], entree["motif"], entree["origine"]) == (
        "rollback", "dérive du score", "manuel"
    )
    assert entree["avant"]["canary"] == "v2.0.0" and entree["apres"]["canary"] is None


def test_rollback_revient_a_la_precedente_une_seule_fois(registry):
    _livrer(registry)
    promouvoir("v2.0.0", registry)
    index = rollback(registry, motif="test")
    assert index["active"] == "v1.0.0" and index["precedente"] is None
    with pytest.raises(ErreurDeploiement, match="rien à annuler"):
        rollback(registry)


def test_rollback_sans_rien_a_annuler(registry):
    with pytest.raises(ErreurDeploiement, match="rien à annuler"):
        rollback(registry)
