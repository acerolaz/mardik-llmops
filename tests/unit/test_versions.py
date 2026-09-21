"""Tests unitaires — numérotation des versions publiées."""
from __future__ import annotations

import subprocess

import pytest

from ops import deploy
from ops.deploy import ErreurDeploiement, prochaine_version, valider_version, versions_connues


@pytest.mark.parametrize(
    ("connues", "attendue"),
    [
        (set(), "v2.0.0"),
        ({"v2.0.0"}, "v2.0.1"),
        ({"v2.0.0", "v2.0.5"}, "v2.0.6"),
        ({"v1.0.0", "v2.1.0"}, "v2.0.0"),
        ({"v2.0.9", "v2.0.10"}, "v2.0.11"),
    ],
)
def test_prochaine_version(connues, attendue):
    assert prochaine_version("v2.0.0", connues) == attendue


def test_valider_version_acceptee():
    valider_version("v2.0.3", "v2.0.0", {"v1.0.0", "v2.0.0"})


@pytest.mark.parametrize(
    ("version", "motif"),
    [
        ("2.0.3", "invalide"),
        ("v2.0", "invalide"),
        ("v3.0.0", "ne correspond pas au bundle v2.0.0"),
        ("v2.0.0", "déjà publiée"),
    ],
)
def test_valider_version_refusee(version, motif):
    with pytest.raises(ErreurDeploiement, match=motif):
        valider_version(version, "v2.0.0", {"v2.0.0"})


def test_versions_connues_fusionne_registre_et_tags(registry, monkeypatch):
    monkeypatch.setattr(deploy, "_tags_git", lambda: {"v2.0.3"})
    assert versions_connues(registry) == {"v1.0.0", "v2.0.3"}


def test_tags_git_sans_git(monkeypatch):
    def git_absent(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "check_output", git_absent)
    assert deploy._tags_git() == set()


def test_tags_git_hors_depot(monkeypatch):
    def hors_depot(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(subprocess, "check_output", hors_depot)
    assert deploy._tags_git() == set()


def test_tags_git_ignore_le_commit_publie_et_filtre_le_motif(monkeypatch):
    appels = []

    def faux_git(commande, **kwargs):
        appels.append(commande)
        return "v2.0.0\nv2.0.1-rc\nvieux\nv2.0.2\n"

    monkeypatch.setattr(subprocess, "check_output", faux_git)
    assert deploy._tags_git() == {"v2.0.0", "v2.0.2"}
    assert appels == [["git", "tag", "-l", "v*", "--no-contains", "HEAD"]]
