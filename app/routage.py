"""Routage canary : quelle version livrée sert une requête, et avec quel moteur.

Service sans HTTP ; la gateway résout la cible puis appelle son moteur :

    choisir_version(active, canary, canary_percent, tirage) -> str
        fonction pure : ``tirage`` ∈ [0, 100[ ; ``canary`` si un canary est
        déployé et ``tirage < canary_percent``, sinon ``active``.

    resoudre(registry, tirage) -> Cible
        relit l'index à CHAQUE appel (une promotion ou un rollback prend effet
        sans redémarrage), charge le bundle depuis le registre — ce qui a été
        livré, pas ``models/`` — et choisit le moteur selon sa stratégie.

Ajouter une stratégie = ajouter une entrée à ``MOTEURS_HTTP``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import yaml
from pydantic import BaseModel

from app.api_v1 import analyser_v1
from app.api_v2 import analyser_v2
from app.llm_client import Bundle, LLMClient
from app.telemetry import Telemetry
from ops.registry import Registry

Moteur = Callable[[str, LLMClient, Telemetry], BaseModel]

MOTEURS_HTTP: dict[str, Moteur] = {
    "monolithique": analyser_v1,
    "map_reduce_clauses": analyser_v2,
}


class ErreurRoutage(RuntimeError):
    """La gateway ne peut servir aucune version : 503 explicite."""


class AucuneVersionActive(ErreurRoutage):
    def __init__(self) -> None:
        super().__init__("aucune version active dans le registre")


class StrategieInconnue(ErreurRoutage):
    def __init__(self, version: str, strategie: str) -> None:
        super().__init__(f"version {version} : stratégie {strategie!r} non routable")
        self.version = version
        self.strategie = strategie


class BundleIllisible(ErreurRoutage):
    """``config.yaml`` (ou son ``prompt_fichier``) est absent ou illisible."""

    def __init__(self, version: str, erreur: Exception) -> None:
        super().__init__(f"version {version} : bundle illisible ({erreur})")
        self.version = version


@dataclass(frozen=True)
class Cible:
    version: str
    bundle: Bundle
    moteur: Moteur


def choisir_version(
    active: str, canary: str | None, canary_percent: int, tirage: float
) -> str:
    if canary is not None and tirage < canary_percent:
        return canary
    return active


def resoudre(registry: Registry, tirage: float) -> Cible:
    index = registry.index()
    active = index.get("active")
    if active is None:
        raise AucuneVersionActive()
    version = choisir_version(
        active, index.get("canary"), int(index.get("canary_percent") or 0), tirage
    )
    try:
        bundle = registry.bundle(version)
    except (OSError, yaml.YAMLError, ValueError, TypeError, AttributeError) as exc:
        raise BundleIllisible(version, exc) from exc
    moteur = MOTEURS_HTTP.get(bundle.strategie)
    if moteur is None:
        raise StrategieInconnue(version, bundle.strategie)
    return Cible(version=version, bundle=bundle, moteur=moteur)

