"""Tests unitaires — invariants de sécurité des workflows qui touchent la prod."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
FICHIERS = ["ci.yml", "promotion.yml", "rollback.yml"]
CONCURRENCE = {"group": "mardik-production", "cancel-in-progress": False}


class _LoaderSansDoublons(yaml.SafeLoader):
    """SafeLoader qui refuse les clés dupliquées dans un mapping."""


def _construire_mapping_sans_doublons(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict:
    donnees: dict = {}
    for cle_node, valeur_node in node.value:
        cle = loader.construct_object(cle_node, deep=True)
        if cle in donnees:
            raise ValueError(f"clé dupliquée : {cle!r}")
        donnees[cle] = loader.construct_object(valeur_node, deep=True)
    return donnees


_LoaderSansDoublons.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construire_mapping_sans_doublons
)


def _charger(nom: str) -> dict:
    return yaml.safe_load((WORKFLOWS / nom).read_text(encoding="utf-8"))


def _declencheurs(workflow: dict) -> set[str]:
    # PyYAML (YAML 1.1) lit la clé « on » comme le booléen True
    return set(workflow.get("on", workflow.get(True)))


def _jobs_de_prod(workflow: dict) -> dict[str, dict]:
    def labels(job: dict) -> list[str]:
        runs_on = job["runs-on"]
        return runs_on if isinstance(runs_on, list) else [runs_on]

    return {nom: job for nom, job in workflow["jobs"].items() if "self-hosted" in labels(job)}


@pytest.mark.parametrize("nom", FICHIERS)
def test_jobs_de_prod_serialises_et_cibles(nom):
    jobs = _jobs_de_prod(_charger(nom))
    assert jobs, f"{nom} : aucun job sur le runner de prod"
    for job in jobs.values():
        assert job["runs-on"] == ["self-hosted", "mardik"]
        assert job["concurrency"] == CONCURRENCE
        assert job["environment"] == "production"
        assert job["env"]["REGISTRY_PATH"] == "${{ vars.MARDIK_REGISTRY_PATH }}"


@pytest.mark.parametrize("nom", FICHIERS)
def test_aucun_job_de_prod_sur_pull_request(nom):
    workflow = _charger(nom)
    if not _declencheurs(workflow) & {"pull_request", "pull_request_target"}:
        return
    for job in _jobs_de_prod(workflow).values():
        condition = job.get("if", "")
        assert "github.ref == 'refs/heads/main'" in condition
        assert "startsWith(github.ref, 'refs/tags/v')" in condition


@pytest.mark.parametrize("nom", ["promotion.yml", "rollback.yml"])
def test_pilotage_uniquement_manuel(nom):
    assert _declencheurs(_charger(nom)) == {"workflow_dispatch"}


def test_publication_expose_la_version():
    publication = _charger("ci.yml")["jobs"]["publication"]
    assert publication["outputs"]["version"] == "${{ steps.etiquetage.outputs.version }}"
    ids = [etape.get("id") for etape in publication["steps"]]
    assert "etiquetage" in ids


def test_rollback_exige_un_motif():
    entree = _charger("rollback.yml")[True]["workflow_dispatch"]["inputs"]["motif"]
    assert entree["required"] is True


@pytest.mark.parametrize("nom", FICHIERS)
def test_aucune_cle_dupliquee(nom):
    """Une étape à deux `if:`/`uses:`/`with:` (name manquant) passerait YAML mais
    serait rejetée par GitHub Actions — on le détecte ici avant coup."""
    texte = (WORKFLOWS / nom).read_text(encoding="utf-8")
    yaml.load(texte, Loader=_LoaderSansDoublons)
