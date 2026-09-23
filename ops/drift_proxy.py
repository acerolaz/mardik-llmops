"""Proxy de dérive — [FOURNI]. Placé entre l'application et le fournisseur LLM.

Le modèle reste vrai ; la panne est commandée. Selon ``DRIFT`` :

* ``off``      transparent (défaut) ;
* ``latence``  chaque appel dure ×4 (on attend 3 fois la durée réelle en plus) ;
* ``erreurs``  10 % des appels renvoient HTTP 502 ;
* ``score``    les scores de confiance renvoyés par le modèle sont dégradés
               vers ~0,5 (le JSON est réécrit à la volée) — la sortie reste
               valide, mais « le modèle n'est plus sûr de lui ».

Le mode se change à chaud, sans redémarrage :

    GET  /_drift            → {"mode": "off"}
    POST /_drift {"mode": "score"}
    POST /_drift {"mode": "off"}

C'est ce que pilote ``scripts/traffic_sim.py --mode derive-score``.

Routage amont : l'en-tête ``x-mardik-provider`` (posé par ``app/llm_client.py``)
choisit l'amont — ``azure`` → ``AZURE_AI_INFERENCE_ENDPOINT`` (+ ``api-version``),
``ollama`` → ``OLLAMA_URL`` (défaut http://localhost:11434).

    python -m ops.drift_proxy            # écoute sur LLM_PROXY_PORT (8080)
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sys
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel

MODES = ("off", "latence", "erreurs", "score")
_etat = {"mode": (os.environ.get("DRIFT", "off").strip().lower() or "off")}
MOTIF_SCORE = re.compile(r'("confiance"\s*:\s*)(-?\d+(?:\.\d+)?)')

app = FastAPI(title="Mardik drift proxy")


class ModeDrift(BaseModel):
    mode: str


@app.get("/_drift")
def lire_mode() -> dict[str, str]:
    return {"mode": _etat["mode"]}


@app.post("/_drift")
def changer_mode(m: ModeDrift) -> dict[str, str]:
    mode = m.mode.strip().lower()
    if mode not in MODES:
        return {"erreur": f"mode inconnu {mode!r}, attendu : {', '.join(MODES)}"}
    _etat["mode"] = mode
    return {"mode": mode}


def _amont(provider: str, chemin: str) -> tuple[str, dict[str, str]]:
    if provider == "azure":
        base = os.environ.get("AZURE_AI_INFERENCE_ENDPOINT", "").rstrip("/")
        if not base:
            raise ValueError("AZURE_AI_INFERENCE_ENDPOINT non définie")
        return f"{base}/{chemin}", {}
    base = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    return f"{base}/{chemin}", {}


def degrader_scores(corps: bytes) -> bytes:
    """Réécrit ``"confiance": 0.93`` → ``"confiance": 0.5±0.1`` dans le contenu du modèle."""
    try:
        data = json.loads(corps)
    except json.JSONDecodeError:
        return corps

    def _degrade(texte: str) -> str:
        return MOTIF_SCORE.sub(lambda m: f"{m.group(1)}{random.uniform(0.4, 0.6):.2f}", texte)

    if isinstance(data, dict):
        if "choices" in data:  # format OpenAI / Azure
            for choix in data.get("choices", []):
                msg = choix.get("message", {})
                if isinstance(msg.get("content"), str):
                    msg["content"] = _degrade(msg["content"])
        elif isinstance(data.get("message"), dict):  # format Ollama
            if isinstance(data["message"].get("content"), str):
                data["message"]["content"] = _degrade(data["message"]["content"])
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


@app.api_route("/{chemin:path}", methods=["GET", "POST"])
async def relayer(chemin: str, request: Request) -> Response:
    mode = _etat["mode"]
    if mode == "erreurs" and random.random() < 0.10:
        return Response(
            content=json.dumps({"error": "dérive injectée : erreur amont simulée"}),
            status_code=502,
            media_type="application/json",
        )
    provider = request.headers.get("x-mardik-provider", os.environ.get("LLM_PROVIDER", "ollama"))
    try:
        url, extra = _amont(provider, chemin)
    except ValueError as exc:
        return Response(
            content=json.dumps({"error": f"configuration Azure manquante : {exc}"}),
            status_code=502,
            media_type="application/json",
        )
    entetes = {
        k: v
        for k, v in request.headers.items()
        if k.lower() in {"content-type", "api-key", "authorization", "accept"}
    }
    entetes.update(extra)
    corps = await request.body()
    debut = time.perf_counter()
    timeout = float(os.environ.get("LLM_TIMEOUT_S", "60"))
    async with httpx.AsyncClient(timeout=timeout) as http:
        try:
            r = await http.request(request.method, url, content=corps, headers=entetes)
        except httpx.HTTPError as exc:
            return Response(
                content=json.dumps({"error": f"amont injoignable : {exc}"}),
                status_code=502,
                media_type="application/json",
            )
    duree = time.perf_counter() - debut
    contenu = r.content
    if mode == "latence":
        await asyncio.sleep(min(duree * 3, 30))
    if mode == "score" and r.status_code == 200:
        contenu = degrader_scores(contenu)
    entetes_retour = {"x-mardik-drift": mode}
    if "retry-after" in r.headers:  # délai demandé sur un 429, lu par app/llm_client.py
        entetes_retour["retry-after"] = r.headers["retry-after"]
    return Response(
        content=contenu,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
        headers=entetes_retour,
    )


def main() -> int:
    port = int(os.environ.get("LLM_PROXY_PORT", "8080"))
    print(f"proxy de dérive sur :{port} — mode initial DRIFT={_etat['mode']}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
