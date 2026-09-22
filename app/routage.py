"""Routage canary : quelle version livrée sert une requête, et avec quel moteur.

Service sans HTTP (la gateway le délègue) :

    choisir_version(active, canary, canary_percent, tirage) -> str
        fonction pure : ``tirage`` ∈ [0, 100[ ; ``canary`` si un canary est
        déployé et ``tirage < canary_percent``, sinon ``active``.
"""
from __future__ import annotations


def choisir_version(
    active: str, canary: str | None, canary_percent: int, tirage: float
) -> str:
    if canary is not None and tirage < canary_percent:
        return canary
    return active
