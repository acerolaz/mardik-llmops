"""Observabilité : agrégats par route, erreurs récentes, état Sentry."""
from __future__ import annotations

import time

from app.config import SentrySettings
from app.observabilite import ResumeObservabilite
from app.telemetry import Mesure
from ops.observabilite import liens_sentry, resume_observabilite


def test_resume_par_route_et_erreurs_recentes(metriques):
    maintenant = time.time()
    for i in range(3):
        metriques.enregistrer(Mesure(ts=maintenant, version="v1.0.0", route="/v1/analyse",
                                     latence_ms=1000.0 + i, appels_llm=1, tokens=4800,
                                     tronque=True, cout_eur=0.01))
    for i in range(12):
        metriques.enregistrer(Mesure(ts=maintenant + i, version="v2.0.0", route="/v2/analyse",
                                     latence_ms=5000.0, erreur=True))

    r = resume_observabilite(metriques, settings=SentrySettings(), fenetre_s=300)

    v1 = next(s for s in r["par_route"] if s["route"] == "/v1/analyse")
    assert (v1["version"], v1["requetes"], v1["taux_erreur"]) == ("v1.0.0", 3, 0.0)
    assert (v1["appels_llm_moyen"], v1["tokens_moyen"], v1["taux_tronque"]) == (1.0, 4800.0, 1.0)
    assert v1["latence_p50_ms"] == 1001.0 and v1["cout_total_eur"] == 0.03
    v2 = next(s for s in r["par_route"] if s["route"] == "/v2/analyse")
    assert v2["taux_erreur"] == 1.0 and v2["latence_p95_ms"] is None
    assert len(r["erreurs_recentes"]) == 10
    assert r["erreurs_recentes"][0]["route"] == "/v2/analyse"
    assert r["sentry"] == {"actif": False, "environnement": "local", "liens": None}


def test_liens_sentry_seulement_si_actif_et_url_connue():
    actif = SentrySettings(dsn="https://cle@o0.ingest.sentry.io/1",
                           ui_url="https://mardik.sentry.io/", environment="production")

    assert liens_sentry(actif) == {
        "issues": "https://mardik.sentry.io/issues/?environment=production",
        "traces": "https://mardik.sentry.io/traces/?environment=production",
        "performance": "https://mardik.sentry.io/insights/backend/?environment=production",
    }
    assert liens_sentry(SentrySettings(ui_url="https://mardik.sentry.io")) is None
    assert liens_sentry(SentrySettings(dsn="https://cle@o0.ingest.sentry.io/1")) is None


def test_endpoint_resume_observabilite(client):
    r = client.get("/observabilite/resume")

    assert r.status_code == 200
    corps = ResumeObservabilite.model_validate(r.json())
    assert corps.par_route == [] and corps.erreurs_recentes == []
    assert corps.sentry.actif is False
