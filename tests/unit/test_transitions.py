"""Tests unitaires — registre (écriture atomique) et transitions de déploiement."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.llm_client import Bundle
from ops.deploy import ErreurDeploiement, deployer_canary, installer, promouvoir, rollback
from ops.registry import Registry


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


def test_canary_refuse_sans_version_active(tmp_path):
    registry = Registry(tmp_path / "vide")
    _livrer(registry)
    with pytest.raises(ErreurDeploiement, match="aucune version active : promouvoir d'abord"):
        deployer_canary("v2.0.0", 10, registry)


def test_canary_version_invalide(registry):
    with pytest.raises(ErreurDeploiement, match="version invalide : '2.0.0' \\(attendu vX.Y.Z\\)"):
        deployer_canary("2.0.0", 10, registry)


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


def test_promotion_version_invalide(registry):
    with pytest.raises(ErreurDeploiement, match="version invalide : '2.0.0' \\(attendu vX.Y.Z\\)"):
        promouvoir("2.0.0", registry)


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


# --------------------------------------------------------------- installer
def _artefact(tmp_path, version="v2.0.0"):
    """Dossier de version tel que la CI l'envoie en artefact (registre du runner)."""
    runner = Registry(tmp_path / "runner")
    runner.etiqueter(version, Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    return runner.root / version


def test_installer_copie_et_journalise(registry, tmp_path):
    avant = registry.index()
    manifeste = installer("v2.0.0", _artefact(tmp_path), registry, origine="ci:bot")
    assert manifeste["version"] == "v2.0.0"
    assert "v2.0.0" in registry.versions()
    assert registry.bundle("v2.0.0").strategie == "map_reduce_clauses"
    entree = registry.journal()[-1]
    assert entree["evenement"] == "installation"
    assert (entree["version"], entree["commit"], entree["origine"]) == (
        "v2.0.0", "abc1234", "ci:bot"
    )
    assert registry.index() == avant


def test_installer_est_idempotent(registry, tmp_path):
    dossier = _artefact(tmp_path)
    installer("v2.0.0", dossier, registry)
    journal = registry.journal()
    assert installer("v2.0.0", dossier, registry)["version"] == "v2.0.0"
    assert registry.journal() == journal


def test_installer_refuse_une_autre_empreinte(registry, tmp_path):
    registry.etiqueter("v2.0.0", Bundle.charger("v1"), commit="autre", note_eval=None)
    with pytest.raises(ErreurDeploiement, match="immuable"):
        installer("v2.0.0", _artefact(tmp_path), registry)


def test_installer_sans_manifeste(registry, tmp_path):
    with pytest.raises(ErreurDeploiement, match="manifeste introuvable"):
        installer("v2.0.0", tmp_path, registry)


def test_installer_version_incoherente(registry, tmp_path):
    with pytest.raises(ErreurDeploiement, match="décrit 'v2.0.0', pas v2.0.1"):
        installer("v2.0.1", _artefact(tmp_path), registry)


def test_installer_sans_config_yaml(registry, tmp_path):
    dossier = _artefact(tmp_path)
    (dossier / "config.yaml").unlink()
    with pytest.raises(ErreurDeploiement, match=f"config.yaml absent de {re.escape(str(dossier))}"):
        installer("v2.0.0", dossier, registry)


def test_installer_depuis_fichier_refuse(registry, tmp_path):
    """Si depuis est un fichier (pas un dossier), OSError est attrapée."""
    fichier = tmp_path / "fichier.txt"
    fichier.write_text("contenu")
    with pytest.raises(ErreurDeploiement, match="manifeste introuvable"):
        installer("v2.0.0", fichier, registry)


def test_installer_copytree_failure_nettoie(registry, tmp_path, monkeypatch):
    """Si copytree échoue, temp folder est nettoyé, version n'est pas ajoutée."""
    dossier = _artefact(tmp_path)
    journal_avant = registry.journal()
    versions_avant = set(registry.versions())

    def copytree_qui_echoue(src, dst, *args, **kwargs):
        # Créer un dossier partiel
        Path(dst).mkdir(parents=True, exist_ok=True)
        (Path(dst) / "partial.txt").write_text("partiel")
        raise OSError("disque plein")

    monkeypatch.setattr("ops.deploy.shutil.copytree", copytree_qui_echoue)

    with pytest.raises(ErreurDeploiement, match="installation de v2.0.0 impossible"):
        installer("v2.0.0", dossier, registry)

    # Vérifier que v2.0.0 n'a pas été ajoutée
    assert "v2.0.0" not in registry.versions()
    assert set(registry.versions()) == versions_avant
    # Vérifier que le journal n'a pas changé
    assert registry.journal() == journal_avant
    # Vérifier qu'il n'y a pas de dossier temporaire orphelin
    temp_files = [f for f in registry.root.iterdir() if f.name.startswith(".")]
    assert len(temp_files) == 0, f"Dossier temporaire orphelin trouvé : {temp_files}"
