"""Application FastAPI — [FOURNI].

* ``/v1`` est branché et fonctionnel (le contrat historique) ;
* ``/v2`` est implémenté (``DocumentTropLong`` → **413**) ;
* ``/analyse`` (gateway) route entre les versions livrées du registre ; une
  ``ErreurRoutage`` (aucune version active, stratégie non routable) → **503** ;
* ``/pilotage/resume`` expose le résumé du tableau de bord (``ops.dashboard``)
  à l'interface web ; ``/`` et ``/pilotage`` servent ses deux pages.
Un module encore en chantier lève ``NotImplementedError`` → **501** explicite —
jamais un 500 muet.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import api_v1, api_v2, gateway, pilotage
from app.pipeline import DocumentTropLong
from app.routage import ErreurRoutage
from app.telemetry import build_default_telemetry

RACINE = Path(__file__).resolve().parent.parent
WEB = RACINE / "app" / "web"
EXEMPLES = RACINE / "eval" / "contrats"


def create_app() -> FastAPI:
    app = FastAPI(title="Mardik — analyse de contrats", version="2.0.0")
    build_default_telemetry()

    app.include_router(api_v1.router)
    app.include_router(api_v2.router)
    app.include_router(gateway.router)
    app.include_router(pilotage.router)
    app.mount("/static", StaticFiles(directory=WEB), name="static")
    app.mount("/exemples", StaticFiles(directory=EXEMPLES), name="exemples")

    @app.get("/", include_in_schema=False)
    def page_analyse() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/pilotage", include_in_schema=False)
    def page_pilotage() -> FileResponse:
        return FileResponse(WEB / "pilotage.html")

    @app.get("/observabilite", include_in_schema=False)
    def page_observabilite() -> FileResponse:
        return FileResponse(WEB / "observabilite.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "provider": os.environ.get("LLM_PROVIDER", "ollama"),
            "mock": os.environ.get("MOCK", "off"),
        }

    @app.exception_handler(NotImplementedError)
    async def _non_implemente(request: Request, exc: NotImplementedError) -> JSONResponse:
        return JSONResponse(
            status_code=501,
            content={"detail": f"à implémenter : {exc or 'module non implémenté'}"},
        )

    @app.exception_handler(DocumentTropLong)
    async def _document_trop_long(request: Request, exc: DocumentTropLong) -> JSONResponse:
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    @app.exception_handler(ErreurRoutage)
    async def _routage_impossible(request: Request, exc: ErreurRoutage) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    return app


app = create_app()
