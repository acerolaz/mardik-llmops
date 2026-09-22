"""Tests d'intégration — CLI de déploiement (REGISTRY_PATH = registre isolé)."""
from __future__ import annotations

import json

from app.llm_client import Bundle
from ops import deploy
from ops.registry import Registry


def _sortie(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_cli_installer_canary_promotion_rollback(registry, tmp_path, capsys):
    runner = Registry(tmp_path / "runner")
    runner.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)

    assert deploy.main(
        ["installer", "v2.0.0", "--depuis", str(runner.root / "v2.0.0"), "--origine", "ci:bot"]
    ) == 0
    assert _sortie(capsys)["version"] == "v2.0.0"

    assert deploy.main(["canary", "v2.0.0", "--pourcentage", "10", "--origine", "ci:bot"]) == 0
    assert _sortie(capsys)["canary"] == "v2.0.0"
    assert registry.journal()[-1]["origine"] == "ci:bot"

    assert deploy.main(["promouvoir", "v2.0.0"]) == 0
    assert _sortie(capsys)["active"] == "v2.0.0"

    assert deploy.main(["rollback", "--motif", "test", "--origine", "ci:bot"]) == 0
    assert _sortie(capsys)["active"] == "v1.0.0"
    entree = registry.journal()[-1]
    assert (entree["evenement"], entree["motif"], entree["origine"]) == (
        "rollback", "test", "ci:bot"
    )


def test_cli_rollback_refuse(registry, capsys):
    assert deploy.main(["rollback"]) == 1
    erreur = capsys.readouterr().err
    assert "REFUSÉ" in erreur and "rien à annuler" in erreur


def test_cli_canary_version_inconnue(registry, capsys):
    assert deploy.main(["canary", "v9.9.9"]) == 1
    assert "inconnue" in capsys.readouterr().err


def test_cli_echec_os_error_hors_erreur_deploiement(registry, tmp_path, monkeypatch, capsys):
    """Une OSError qui n'est pas déjà enveloppée en ErreurDeploiement (ex. échec
    de os.replace lors de l'écriture de l'index pendant un canary) donne
    « ÉCHEC » et le code 1, jamais un traceback brut."""
    runner = Registry(tmp_path / "runner")
    runner.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    assert deploy.main(
        ["installer", "v2.0.0", "--depuis", str(runner.root / "v2.0.0")]
    ) == 0
    capsys.readouterr()

    def remplacement_en_echec(*args, **kwargs):
        raise PermissionError("accès refusé")

    monkeypatch.setattr("ops.registry.os.replace", remplacement_en_echec)

    assert deploy.main(["canary", "v2.0.0", "--pourcentage", "10"]) == 1
    erreur = capsys.readouterr().err
    assert "ÉCHEC" in erreur and "accès refusé" in erreur
