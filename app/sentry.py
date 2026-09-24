"""Sentry — backend des traces OpenTelemetry et des erreurs, optionnel.

Sans DSN : rien n'est initialisé, les spans restent sur la console.
Avec DSN : les spans existants (``analyse.requete`` → ``llm.appel`` …) deviennent
des transactions Sentry via ``SentrySpanProcessor`` (``instrumenter="otel"``) ;
aucune instrumentation n'est réécrite.

Confidentialité : le texte d'un contrat ne quitte jamais le serveur
(``send_default_pii=False``, ni variables locales, ni corps de requête, ni logs
``logging``, et ``filtrer_evenement``). Règle du projet : un message d'exception
ne cite jamais le texte du contrat ni la réponse du LLM — il part tel quel.
"""
from __future__ import annotations

from typing import Any
from weakref import WeakSet

import sentry_sdk
from opentelemetry.sdk.trace import TracerProvider
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.opentelemetry import SentrySpanProcessor

from app.config import SentrySettings
from app.llm_client import Bundle

# Référence faible : un provider collecté sort de l'ensemble, et un nouveau provider
# n'est jamais confondu avec un ancien (pas de réutilisation d'``id``).
_providers_branches: WeakSet[TracerProvider] = WeakSet()


def filtrer_evenement(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Retire le corps des requêtes : le texte du contrat. Les tags de version
    viennent des routes (``etiqueter_version``)."""
    requete = event.get("request")
    if isinstance(requete, dict):
        requete.pop("data", None)
    return event


def etiqueter_version(bundle: Bundle) -> None:
    """Tags de version sur le scope de la requête : ils suivent aussi l'erreur,
    capturée après la fermeture du span. À appeler depuis une route HTTP
    seulement — hors requête, le scope est celui du processus. Sans Sentry actif,
    rien : le scope du processus reste vierge si Sentry est initialisé plus tard."""
    if not sentry_sdk.get_client().dsn:   # is_active() est vrai même sans DSN
        return
    sentry_sdk.set_tags({
        "mardik.version": bundle.version,
        "mardik.model_version": f"{bundle.version}-{bundle.empreinte()}",
    })


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
        include_local_variables=False,   # les frames portent `texte` (le contrat)
        max_request_body_size="never",
        # Les logs passent par structlog (console) ; ceux de `logging` (bibliothèques
        # tierces) pourraient citer le contrat : ni breadcrumbs ni événements.
        integrations=[LoggingIntegration(level=None, event_level=None)],
        before_send=filtrer_evenement,
        before_send_transaction=filtrer_evenement,
    )
    if provider is not None and provider not in _providers_branches:
        provider.add_span_processor(SentrySpanProcessor())
        _providers_branches.add(provider)
    return True
