"""Le proxy de dérive relaie ``Retry-After`` : sans lui, le client ne sait pas combien attendre sur un 429."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from ops import drift_proxy


def test_retry_after_amont_relaye_au_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AZURE_AI_INFERENCE_ENDPOINT", "http://azure.test/openai/v1")
    transport = httpx.MockTransport(
        lambda requete: httpx.Response(429, headers={"retry-after": "7"}, json={"error": "quota"})
    )
    vrai_client = httpx.AsyncClient
    monkeypatch.setattr(
        drift_proxy.httpx, "AsyncClient", lambda **kw: vrai_client(transport=transport, **kw)
    )

    r = TestClient(drift_proxy.app).post(
        "/chat/completions", json={}, headers={"x-mardik-provider": "azure"}
    )

    assert r.status_code == 429
    assert r.headers["retry-after"] == "7"
