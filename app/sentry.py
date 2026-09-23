"""Sentry — backend des traces OpenTelemetry et des erreurs, optionnel.

Sans DSN : rien n'est initialisé, les spans restent sur la console.
Avec DSN : les spans existants (``analyse.requete`` → ``llm.appel`` …) deviennent
des transactions Sentry via ``SentrySpanProcessor`` (``instrumenter="otel"``) ;
aucune instrumentation n'est réécrite.

Confidentialité : le texte d'un contrat ne quitte jamais le serveur
(``send_default_pii=False`` et ``filtrer_evenement``).
"""
from __future__ import annotations

from typing import Any

import sentry_sdk
from opentelemetry.sdk.trace import TracerProvider
from sentry_sdk.integrations.opentelemetry import SentrySpanProcessor

from app.config import SentrySettings

ATTRIBUTS_EN_TAGS = ("mardik.version", "mardik.model_version")
_providers_branches: set[int] = set()


def filtrer_evenement(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Retire le corps des requêtes (le texte du contrat) et recopie la version
    portée par le span en tag, pour filtrer les traces par version dans Sentry."""
    requete = event.get("request")
    if isinstance(requete, dict):
        requete.pop("data", None)
    attributs = ((event.get("contexts") or {}).get("otel") or {}).get("attributes") or {}
    tags = event.setdefault("tags", {})
    if isinstance(tags, dict):
        for cle in ATTRIBUTS_EN_TAGS:
            if cle in attributs:
                tags[cle] = attributs[cle]
    return event


def init_sentry(settings: SentrySettings, provider: TracerProvider | None) -> bool:
    """Initialise Sentry si un DSN est configuré ; renvoie ``True`` s'il est actif."""
    if not settings.dsn:
        return False
    sentry_sdk.init(
        dsn=settings.dsn,
        environment=settings.environment,
        traces_sample_rate=settings.traces_sample_rate,
        instrumenter="otel",
        send_default_pii=False,
        before_send=filtrer_evenement,
        before_send_transaction=filtrer_evenement,
    )
    if provider is not None and id(provider) not in _providers_branches:
        provider.add_span_processor(SentrySpanProcessor())
        _providers_branches.add(id(provider))
    return True
