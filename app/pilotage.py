"""Résumé de pilotage pour l'interface web — route mince sur ``ops.dashboard.resume``.

    GET /pilotage/resume → ResumePilotage

Même calcul et même fenêtre que le pilote (``resume()`` sans argument) : aucune
agrégation n'est dupliquée ici. Les sources illisibles sont déjà dégradées en
``alertes`` par ``resume()`` — jamais de 500.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ops.dashboard import resume

router = APIRouter(prefix="/pilotage", tags=["pilotage"])


class PointMinute(BaseModel):
    model_config = ConfigDict(extra="allow")
    minute: int
    requetes: int
    latence_p95_ms: float | None
    taux_erreur: float


class StatsVersion(BaseModel):
    requetes: int
    trafic_pct: float
    latence_p50_ms: float | None
    latence_p95_ms: float | None
    taux_erreur: float
    score_moyen: float | None
    score_p10: float | None
    cout_total_eur: float
    histogramme_score: list[int]
    serie_minute: list[PointMinute]


class Palier(BaseModel):
    version: str
    pourcentage: int | None
    depuis_s: float | None
    requetes: int
    duree_min_s: float | None
    requetes_min: int | None


class EntreeJournal(BaseModel):
    """Les événements du registre sont hétérogènes : on garde tous leurs champs."""

    model_config = ConfigDict(extra="allow")
    date: str | None = None
    evenement: str | None = None
    origine: str | None = None
    resume: str | None = None
    motif: str | None = None


class ResumePilotage(BaseModel):
    fenetre_s: float
    total: int
    par_version: dict[str, StatsVersion]
    palier: Palier | None
    alertes: list[str]
    candidats: int
    journal: list[EntreeJournal]


def get_resume() -> dict[str, Any]:
    return resume()


@router.get("/resume", response_model=ResumePilotage)
def resume_pilotage(r: dict[str, Any] = Depends(get_resume)) -> ResumePilotage:
    return ResumePilotage.model_validate(r)
