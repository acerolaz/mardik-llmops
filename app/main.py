"""Application FastAPI — [FOURNI].

* ``/v1`` est branché et fonctionnel (le contrat historique) ;
* ``/v2`` et ``/analyse`` (gateway) sont branchés sur des stubs : tant qu'un
  module lève ``NotImplementedError``, la route répond **501** avec le nom du
  chantier restant — jamais un 500 muet.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import api_v1, api_v2, gateway
from app.pipeline import DocumentTropLong
from app.telemetry import build_default_telemetry


def create_app() -> FastAPI:
    app = FastAPI(title="Mardik — analyse de contrats", version="2.0.0")
    build_default_telemetry()

    app.include_router(api_v1.router)
    app.include_router(api_v2.router)
    app.include_router(gateway.router)

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

    return app


app = create_app()
