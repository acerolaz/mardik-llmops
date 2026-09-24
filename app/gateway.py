"""Gateway : routeur canary entre les versions livrées.

    POST /analyse  {"texte": "..."}   → réponse de la version choisie,
                                        + en-tête ``X-Mardik-Version``
    GET  /gateway/etat                → {"active": "v1.0.0", "canary": "v2.0.0",
                                         "canary_percent": 10}

Route mince : le choix de la version, du bundle livré et du moteur est dans
``app.routage``. L'index du registre est relu à chaque requête : une promotion
ou un rollback prend effet sans redémarrage. ``CANARY_PERCENT`` n'est que la
valeur par défaut de ``ops.deploy.deployer_canary`` ; la gateway suit l'index.
Erreurs explicites : 422 (corps), 413 (document trop long, moteur v2),
503 (fournisseur LLM, aucune version routable).
"""
from __future__ import annotations

import random
from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app import routage
from app.api_v1 import ReponseAnalyseV1
from app.api_v2 import ReponseAnalyseV2
from app.capture import Capture, capturer, get_capture
from app.llm_client import Bundle, ErreurLLM, LLMClient
from app.routage import choisir_version as choisir_version
from app.sentry import etiqueter_version
from app.telemetry import Telemetry, build_default_telemetry
from ops.registry import Registry

router = APIRouter(tags=["gateway"])


class RequeteAnalyse(BaseModel):
    texte: str = Field(..., min_length=20)
    contrat_id: str | None = None


class EtatGateway(BaseModel):
    active: str | None
    canary: str | None
    canary_percent: int


def get_registry() -> Registry:
    return Registry()


def get_telemetry() -> Telemetry:
    return build_default_telemetry()


def get_tirage() -> float:
    return random.uniform(0, 100)


def get_fabrique_client() -> Callable[[Bundle], LLMClient]:
    return LLMClient


@router.get("/gateway/etat", response_model=EtatGateway)
def etat(registry: Registry = Depends(get_registry)) -> EtatGateway:
    index = registry.index()
    return EtatGateway(
        active=index.get("active"),
        canary=index.get("canary"),
        canary_percent=int(index.get("canary_percent") or 0),
    )


@router.post("/analyse", response_model=ReponseAnalyseV1 | ReponseAnalyseV2)
def analyse(
    requete: RequeteAnalyse,
    response: Response,
    taches: BackgroundTasks,
    registry: Registry = Depends(get_registry),
    telemetry: Telemetry = Depends(get_telemetry),
    tirage: float = Depends(get_tirage),
    fabrique_client: Callable[[Bundle], LLMClient] = Depends(get_fabrique_client),
    capture: Capture = Depends(get_capture),
) -> ReponseAnalyseV1 | ReponseAnalyseV2:
    cible = routage.resoudre(registry, tirage)
    etiqueter_version(cible.bundle)
    version = cible.version
    try:
        reponse = cible.moteur(requete.texte, fabrique_client(cible.bundle), telemetry)
    except ErreurLLM as exc:
        raise HTTPException(
            status_code=503, detail=f"fournisseur LLM indisponible : {exc}"
        ) from exc
    response.headers["X-Mardik-Version"] = version
    if isinstance(reponse, ReponseAnalyseV2):   # la v1 ne produit pas de score
        taches.add_task(capturer, capture, requete.texte, reponse)
    return reponse
