"""``GET /observabilite/resume`` : métriques par route, erreurs récentes, état Sentry."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ops.observabilite import resume_observabilite

router = APIRouter(prefix="/observabilite", tags=["observabilite"])


class StatsRoute(BaseModel):
    route: str
    version: str
    requetes: int
    taux_erreur: float
    latence_p50_ms: float | None
    latence_p95_ms: float | None
    appels_llm_moyen: float
    tokens_moyen: float
    taux_tronque: float
    cout_total_eur: float


class ErreurRecente(BaseModel):
    date: str
    route: str
    version: str
    latence_ms: float


class LiensSentry(BaseModel):
    issues: str
    traces: str
    performance: str


class EtatSentry(BaseModel):
    actif: bool
    environnement: str
    liens: LiensSentry | None


class ResumeObservabilite(BaseModel):
    fenetre_s: float
    par_route: list[StatsRoute]
    erreurs_recentes: list[ErreurRecente]
    sentry: EtatSentry


def get_resume_observabilite() -> dict[str, Any]:
    return resume_observabilite()


@router.get("/resume", response_model=ResumeObservabilite)
def resume(r: dict[str, Any] = Depends(get_resume_observabilite)) -> ResumeObservabilite:
    return ResumeObservabilite.model_validate(r)
