"""Sentry : optionnel, branché sur les spans OTel existants, sans texte de contrat."""
from __future__ import annotations

import pytest
import sentry_sdk
from opentelemetry.sdk.trace import TracerProvider
from sentry_sdk.transport import Transport

from app.config import SentrySettings
from app.sentry import filtrer_evenement, init_sentry

DSN_FACTICE = "https://cle@o0.ingest.sentry.io/1"


class TransportCapture(Transport):
    """Garde les envois en mémoire : aucun appel réseau."""

    def __init__(self, options=None):
        super().__init__(options)
        self.recus: list[dict] = []

    def capture_envelope(self, envelope):
        self.recus.extend(item.payload.json for item in envelope.items if item.payload.json)


@pytest.fixture(autouse=True)
def sentry_inactif_apres():
    yield
    sentry_sdk.init()          # client sans DSN : inactif pour les tests suivants


def test_sans_dsn_rien_n_est_initialise():
    assert init_sentry(SentrySettings(), TracerProvider()) is False
    assert not sentry_sdk.get_client().is_active()


def test_filtre_retire_le_texte_du_contrat_et_etiquette_la_version():
    event = {
        "request": {"url": "http://mardik/v2/analyse", "data": {"texte": "CONTRAT CONFIDENTIEL"}},
        "contexts": {"otel": {"attributes": {"mardik.version": "v2", "mardik.model_version": "v2.0.3"}}},
    }

    sortie = filtrer_evenement(event, {})

    assert "data" not in sortie["request"]
    assert "CONTRAT CONFIDENTIEL" not in repr(sortie)
    assert sortie["tags"] == {"mardik.version": "v2", "mardik.model_version": "v2.0.3"}


def test_spans_otel_envoyes_comme_transaction(monkeypatch):
    transports: list[TransportCapture] = []
    init_reel = sentry_sdk.init

    def init_capture(**options):
        transports.append(TransportCapture(options))
        return init_reel(transport=transports[-1], **options)

    monkeypatch.setattr(sentry_sdk, "init", init_capture)
    provider = TracerProvider()

    assert init_sentry(SentrySettings(dsn=DSN_FACTICE, environment="test"), provider) is True
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("analyse.requete") as span:
        span.set_attribute("mardik.version", "v2")
        with tracer.start_as_current_span("llm.appel"):
            pass
    sentry_sdk.flush()

    (transaction,) = [e for e in transports[0].recus if e.get("type") == "transaction"]
    assert transaction["transaction"] == "analyse.requete"
    assert transaction["environment"] == "test"
    assert transaction["tags"]["mardik.version"] == "v2"
    assert any("llm.appel" in (s.get("description"), s.get("op")) for s in transaction["spans"])
