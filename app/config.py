"""Réglages lus dans l'environnement (pydantic-settings).

``app/__init__.py`` charge déjà ``.env`` dans l'environnement : les réglages
le voient sans ``env_file`` (et les tests les neutralisent avec ``monkeypatch``).
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class SentrySettings(BaseSettings):
    """Sentry est optionnel : sans ``SENTRY_DSN``, rien n'est initialisé."""

    model_config = SettingsConfigDict(env_prefix="SENTRY_", extra="ignore")

    dsn: str | None = None
    environment: str = "local"
    traces_sample_rate: float = 1.0
    ui_url: str | None = None   # ex. https://<organisation>.sentry.io — liens de la page Observabilité
