"""Réessai sur HTTP 429 (quota fournisseur) dans ``LLMClient._appeler``.

Seul le 429 est réessayé : les 5xx restent visibles, car le proxy de dérive
(mode ``erreurs``) en injecte volontairement pour déclencher le rollback.
"""
from __future__ import annotations

import httpx
import pytest

from app import llm_client
from app.llm_client import Bundle, ErreurLLM, LLMClient

SUCCES = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1}}


@pytest.fixture
def attentes(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    durees: list[float] = []
    monkeypatch.setattr(llm_client.time, "sleep", durees.append)
    return durees


def _client(monkeypatch: pytest.MonkeyPatch, reponses: list[httpx.Response]) -> LLMClient:
    """Client Azure dont le transport renvoie ``reponses`` dans l'ordre."""
    file = list(reponses)
    transport = httpx.MockTransport(lambda requete: file.pop(0))
    vrai_client = httpx.Client
    monkeypatch.setattr(
        llm_client.httpx, "Client", lambda **kw: vrai_client(transport=transport, **kw)
    )
    return LLMClient(Bundle.charger("v2"), provider="azure", mock="off", proxy_url="http://proxy")


def test_429_puis_succes_reessaie_une_fois(monkeypatch, attentes):
    client = _client(monkeypatch, [httpx.Response(429), httpx.Response(200, json=SUCCES)])

    texte, _ = client._appeler("système", "contrat", json_mode=True)

    assert texte == "ok"
    assert len(attentes) == 1


def test_429_persistant_leve_erreur_apres_trois_reessais(monkeypatch, attentes):
    client = _client(monkeypatch, [httpx.Response(429) for _ in range(4)])

    with pytest.raises(ErreurLLM, match="HTTP 429"):
        client._appeler("système", "contrat", json_mode=True)

    assert len(attentes) == 3
    # délai doublé à chaque essai (2, 4, 8 s) + aléa < 1 s
    for attente, base in zip(attentes, (2, 4, 8)):
        assert base <= attente < base + 1


def test_retry_after_du_fournisseur_est_respecte_et_plafonne(monkeypatch, attentes):
    client = _client(
        monkeypatch,
        [
            httpx.Response(429, headers={"retry-after": "5"}),
            httpx.Response(429, headers={"retry-after": "600"}),
            httpx.Response(200, json=SUCCES),
        ],
    )

    client._appeler("système", "contrat", json_mode=True)

    assert attentes == [5.0, 30.0]


def test_nombre_de_reessais_configurable(monkeypatch, attentes):
    monkeypatch.setenv("LLM_RETRY_429_MAX", "0")
    client = _client(monkeypatch, [httpx.Response(429)])

    with pytest.raises(ErreurLLM, match="HTTP 429"):
        client._appeler("système", "contrat", json_mode=True)

    assert attentes == []


def test_erreur_5xx_non_reessayee(monkeypatch, attentes):
    client = _client(monkeypatch, [httpx.Response(502)])

    with pytest.raises(ErreurLLM, match="HTTP 502"):
        client._appeler("système", "contrat", json_mode=True)

    assert attentes == []
