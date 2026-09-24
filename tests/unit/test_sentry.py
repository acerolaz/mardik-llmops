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
def sentry_inactif_apres(monkeypatch):
    yield
    monkeypatch.delenv("SENTRY_DSN", raising=False)   # sinon init() relit le DSN factice du test
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


SECRET = "CLAUSE_SECRETE_DU_CONTRAT"


def _envois_apres_503(monkeypatch, analyser) -> str:
    """Vraie app, vraie intégration FastAPI, 503 sur /v2/analyse : tout ce qui part vers Sentry."""
    import json

    return json.dumps(_evenements_apres_503(monkeypatch, analyser), ensure_ascii=False)


def _evenements_apres_503(monkeypatch, analyser=None, *, chemin="/v2/analyse",
                          configurer=None) -> list[dict]:
    from fastapi.testclient import TestClient

    from app import api_v2
    from app.main import create_app

    transports: list[TransportCapture] = []
    init_reel = sentry_sdk.init

    def init_capture(**options):
        transports.append(TransportCapture(options))
        return init_reel(transport=transports[-1], **options)

    monkeypatch.setattr(sentry_sdk, "init", init_capture)
    monkeypatch.setenv("SENTRY_DSN", DSN_FACTICE)
    if analyser is not None:
        monkeypatch.setattr(api_v2, "analyser_v2", analyser)
    app = create_app()
    if configurer is not None:
        configurer(app)
    client = TestClient(app, raise_server_exceptions=False)

    r = client.post(chemin, json={"texte": f"Article 1 — {SECRET}. " * 5})
    sentry_sdk.flush()

    assert r.status_code == 503
    assert transports and transports[-1].recus, "aucun événement capturé : le test ne prouve rien"
    return transports[-1].recus


def test_exception_reelle_sans_texte_du_contrat_dans_les_variables_locales(monkeypatch):
    from app.llm_client import ErreurLLM

    def analyser_en_panne(texte, client, telemetry):
        contrat = texte  # noqa: F841 — variable locale portant le texte
        raise ErreurLLM("fournisseur indisponible")

    assert SECRET not in _envois_apres_503(monkeypatch, analyser_en_panne)


def test_reponse_llm_non_json_sans_extrait_du_contrat(monkeypatch):
    from app.llm_client import extraire_json

    def analyser_reponse_illisible(texte, client, telemetry):
        extraire_json(texte)   # le LLM a « répondu » en recopiant le contrat, sans JSON

    assert SECRET not in _envois_apres_503(monkeypatch, analyser_reponse_illisible)


def test_logs_stdlib_sans_texte_du_contrat(monkeypatch):
    """Une bibliothèque tierce qui journalise le texte (breadcrumb, ou événement si ERROR)."""
    import logging

    from app.llm_client import ErreurLLM

    def analyser_bavard(texte, client, telemetry):
        logging.getLogger("tiers").warning("reçu : %s", texte)
        logging.getLogger("tiers").error("échec sur : %s", texte)
        raise ErreurLLM("fournisseur indisponible")

    assert SECRET not in _envois_apres_503(monkeypatch, analyser_bavard)


def test_erreur_etiquetee_avec_la_version(monkeypatch):
    """Les issues se filtrent par version comme les traces : le tag est sur l'erreur aussi."""
    from app import api_v2
    from app.llm_client import Bundle, ErreurLLM

    class ClientEnPanne:
        bundle = Bundle.charger("v2")

        def completer(self, *args, **kwargs):
            raise ErreurLLM("fournisseur indisponible")

    analyser_reel = api_v2.analyser_v2

    def analyser_en_panne(texte, client, telemetry):
        return analyser_reel(texte, ClientEnPanne(), telemetry)

    evenements = _evenements_apres_503(monkeypatch, analyser_en_panne)

    (erreur,) = [e for e in evenements if e.get("exception")]
    assert erreur["tags"]["mardik.version"] == ClientEnPanne.bundle.version
    assert erreur["tags"]["mardik.model_version"].startswith(ClientEnPanne.bundle.version)


class _ClientEnPanne:
    def __init__(self, bundle):
        self.bundle = bundle

    def completer(self, *args, **kwargs):
        from app.llm_client import ErreurLLM

        raise ErreurLLM("fournisseur indisponible")


def test_erreur_de_la_gateway_etiquetee_avec_la_version_servie(monkeypatch, registry):
    from app import gateway

    def configurer(app):
        app.dependency_overrides[gateway.get_registry] = lambda: registry
        app.dependency_overrides[gateway.get_fabrique_client] = lambda: _ClientEnPanne

    evenements = _evenements_apres_503(monkeypatch, chemin="/analyse", configurer=configurer)

    (erreur,) = [e for e in evenements if e.get("exception")]
    assert erreur["tags"]["mardik.version"] == "v1.0.0"      # active du registre de test


def test_analyse_hors_requete_ne_laisse_pas_de_tags(monkeypatch, telemetry):
    """Script ou gate d'éval avec un DSN actif : pas de version périmée sur les événements suivants."""
    from app import api_v2
    from app.llm_client import Bundle, ErreurLLM

    transports: list[TransportCapture] = []
    init_reel = sentry_sdk.init

    def init_capture(**options):
        transports.append(TransportCapture(options))
        return init_reel(transport=transports[-1], **options)

    monkeypatch.setattr(sentry_sdk, "init", init_capture)
    with sentry_sdk.isolation_scope() as scope:
        scope.clear()                       # le fork copie les tags posés par d'autres tests
        assert init_sentry(SentrySettings(dsn=DSN_FACTICE), None) is True
        with pytest.raises(ErreurLLM):
            api_v2.analyser_v2(f"Article 1 — {SECRET}. " * 5, _ClientEnPanne(Bundle.charger("v2")), telemetry)
        sentry_sdk.capture_message("événement suivant, sans rapport")
        sentry_sdk.flush()

    (message,) = [e for e in transports[-1].recus if e.get("message") or e.get("logentry")]
    assert not any(cle.startswith("mardik.") for cle in message.get("tags", {}))


def test_sans_sentry_actif_aucun_tag_pose(monkeypatch):
    """Sentry initialisé tard dans un processus qui a déjà servi des requêtes : pas de version périmée."""
    from app.llm_client import Bundle
    from app.sentry import etiqueter_version

    transports: list[TransportCapture] = []
    init_reel = sentry_sdk.init

    def init_capture(**options):
        transports.append(TransportCapture(options))
        return init_reel(transport=transports[-1], **options)

    monkeypatch.setattr(sentry_sdk, "init", init_capture)
    with sentry_sdk.isolation_scope() as scope:
        scope.clear()
        assert not sentry_sdk.get_client().dsn          # Sentry pas encore configuré
        etiqueter_version(Bundle.charger("v1"))          # une requête servie sans Sentry
        assert init_sentry(SentrySettings(dsn=DSN_FACTICE), None) is True
        sentry_sdk.capture_message("premier événement après l'initialisation")
        sentry_sdk.flush()

    (message,) = [e for e in transports[-1].recus if e.get("message") or e.get("logentry")]
    assert not any(cle.startswith("mardik.") for cle in message.get("tags", {}))


def test_erreur_de_la_route_v1_etiquetee_avec_la_version(monkeypatch):
    from app import api_v1
    from app.llm_client import Bundle

    def configurer(app):
        app.dependency_overrides[api_v1.get_client_v1] = lambda: _ClientEnPanne(Bundle.charger("v1"))

    evenements = _evenements_apres_503(monkeypatch, chemin="/v1/analyse", configurer=configurer)

    (erreur,) = [e for e in evenements if e.get("exception")]
    assert erreur["tags"]["mardik.version"] == Bundle.charger("v1").version


def _processeurs_sentry(provider: TracerProvider) -> int:
    from sentry_sdk.integrations.opentelemetry import SentrySpanProcessor

    return sum(isinstance(p, SentrySpanProcessor)
               for p in provider._active_span_processor._span_processors)


def test_processeur_sentry_ajoute_une_seule_fois_par_provider(monkeypatch):
    transport = TransportCapture()
    monkeypatch.setattr(sentry_sdk, "init", lambda **o: sentry_sdk.api.init(transport=transport, **o))
    provider = TracerProvider()

    init_sentry(SentrySettings(dsn=DSN_FACTICE), provider)
    init_sentry(SentrySettings(dsn=DSN_FACTICE), provider)

    assert _processeurs_sentry(provider) == 1


def test_provider_collecte_ne_bloque_pas_un_nouveau_provider(monkeypatch):
    """Le suivi des providers est un WeakSet : un provider collecté disparaît, et un nouveau provider reste branchable."""
    import gc
    import weakref
    import app.sentry as module

    transport = TransportCapture()
    monkeypatch.setattr(sentry_sdk, "init", lambda **o: sentry_sdk.api.init(transport=transport, **o))

    monkeypatch.setattr(module, "_providers_branches", weakref.WeakSet())   # sans les providers des autres tests
    ancien = TracerProvider(shutdown_on_exit=False)   # sinon atexit le garde en vie
    module._providers_branches.add(ancien)
    ref = weakref.ref(ancien)
    del ancien
    gc.collect()
    assert ref() is None and len(module._providers_branches) == 0

    provider = TracerProvider()
    init_sentry(SentrySettings(dsn=DSN_FACTICE), provider)
    assert _processeurs_sentry(provider) == 1
