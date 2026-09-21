"""Gateway : routeur canary entre les versions livrées. [STUB]

Contrat attendu :

    POST /analyse  {"texte": "..."}         → réponse de la version choisie,
                                              + en-tête ``X-Mardik-Version``
    GET  /gateway/etat                      → {"active": "v1.0.0", "canary": "v2.0.0",
                                               "canary_percent": 10}

    choisir_version(active, canary, canary_percent, tirage) -> str
        fonction pure : ``tirage`` ∈ [0, 100[ ; renvoie ``canary`` si un canary
        est déployé et ``tirage < canary_percent``, sinon ``active``.

Règles :
* la gateway lit ``ops/registry/index.json`` (via ``Registry``) à **chaque**
  requête : une promotion ou un rollback doit prendre effet sans redémarrage ;
* ``CANARY_PERCENT`` dans ``.env`` force le pourcentage (sinon celui de
  l'index) — pratique pour la démo ;
* le bundle de chaque version vient du registre (``registry.bundle(version)``),
  pas de ``models/`` : on sert ce qui a été livré, pas ce qui est en chantier ;
* la stratégie du bundle décide du moteur : ``monolithique`` → ``analyser_v1``,
  ``map_reduce_clauses`` → ``analyser_v2`` ;
* les erreurs restent explicites (422 / 503), comme sur ``/v1`` et ``/v2``.
"""
from __future__ import annotations

import os
import random

from app.api_v1 import analyser_v1
from app.api_v2 import analyser_v2
from app.llm_client import ErreurLLM, LLMClient
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.telemetry import Telemetry, build_default_telemetry
from ops.registry import Registry

router = APIRouter(tags=["gateway"])


class RequeteAnalyse(BaseModel):
    texte: str = Field(..., min_length=20)
    contrat_id: str | None = None


def choisir_version(
    active: str, canary: str | None, canary_percent: int, tirage: float
) -> str:
    if canary and tirage < canary_percent:
        return canary
    return active


def get_registry() -> Registry:
    return Registry()


def get_telemetry() -> Telemetry:
    return build_default_telemetry()


@router.get("/gateway/etat")
def etat(registry: Registry = Depends(get_registry)) -> dict:
    idx = registry.index()
    return {
        "active": idx.get("active"),
        "canary": idx.get("canary"),
        "canary_percent": int(idx.get("canary_percent") or 0),
    }


@router.post("/analyse")
def analyse(
    requete: RequeteAnalyse,
    response: Response,
    registry: Registry = Depends(get_registry),
    telemetry: Telemetry = Depends(get_telemetry),
) -> dict:
    idx = registry.index()
    active = idx.get("active")
    canary = idx.get("canary")
    canary_percent = _canary_percent(os.environ.get("CANARY_PERCENT"), idx.get("canary_percent"))
    if not active:
        raise HTTPException(status_code=503, detail="aucune version active dans le registre")
    version = choisir_version(active, canary, canary_percent, random.random() * 100)
    bundle = registry.bundle(version)
    client_llm = LLMClient(bundle)
    try:
        if bundle.strategie == "monolithique":
            corps = analyser_v1(requete.texte, client_llm, telemetry).model_dump()
        elif bundle.strategie == "map_reduce_clauses":
            corps = analyser_v2(requete.texte, client_llm, telemetry).model_dump()
        else:
            raise HTTPException(
                status_code=501, detail=f"stratégie inconnue: {bundle.strategie}"
            )
    except ErreurLLM as exc:
        raise HTTPException(status_code=503, detail=f"fournisseur LLM indisponible : {exc}") from exc
    response.headers["X-Mardik-Version"] = version
    return corps


def _canary_percent(force: str | None, index_value: object) -> int:
    brut = force if force is not None else index_value
    try:
        valeur = float(brut)
    except (TypeError, ValueError):
        return 0
    return int(max(0, min(100, valeur)))
