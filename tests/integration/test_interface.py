"""Interface web : résumé de pilotage et pages servies."""
from __future__ import annotations

import time

import pytest

from app.llm_client import Bundle
from app.pilotage import ResumePilotage
from app.telemetry import Mesure


def _canary(registry, pourcentage=10):
    registry.etiqueter("v2.0.0", Bundle.charger("v2"), commit="abc1234", note_eval=0.9)
    registry.definir_canary("v2.0.0", pourcentage)
    registry.journaliser("canary", version="v2.0.0", pourcentage=pourcentage, origine="manuel")


def _mesures(metriques, version, n, latence_ms=900.0, **champs):
    for _ in range(n):
        metriques.enregistrer(Mesure(ts=time.time(), version=version, route="/analyse",
                                     latence_ms=latence_ms, **champs))


def test_resume_avec_canary(client, registry, metriques):
    _canary(registry)
    _mesures(metriques, "v1.0.0", 5)
    _mesures(metriques, "v2.0.0", 3, latence_ms=2000.0, score=0.9, cout_eur=0.02)

    r = client.get("/pilotage/resume")

    assert r.status_code == 200
    corps = ResumePilotage.model_validate(r.json())
    assert set(corps.par_version) == {"v1.0.0", "v2.0.0"}
    assert corps.par_version["v1.0.0"].score_moyen is None
    assert sum(p.requetes for p in corps.par_version["v2.0.0"].serie_minute) == 3
    assert corps.palier is not None
    assert (corps.palier.version, corps.palier.pourcentage) == ("v2.0.0", 10)
    assert corps.journal[-1].evenement == "canary"
    assert corps.decision is not None
    assert (corps.decision.action, corps.decision.version) == ("attendre", "v2.0.0")
    assert corps.seuils is not None and corps.seuils.promotion["paliers"] == [10, 50, 100]
    assert r.json()["journal"][-1]["pourcentage"] == 10     # champs en plus conservés


def test_resume_sans_trafic(client, registry):
    r = client.get("/pilotage/resume")

    assert r.status_code == 200
    corps = r.json()
    assert (corps["total"], corps["par_version"], corps["palier"]) == (0, {}, None)


def test_resume_version_toute_en_erreur(client, registry, metriques):
    _mesures(metriques, "v1.0.0", 4, erreur=True)

    v1 = client.get("/pilotage/resume").json()["par_version"]["v1.0.0"]

    assert v1["latence_p95_ms"] is None
    assert v1["taux_erreur"] == 1.0


def test_resume_registre_illisible(client, registry):
    (registry.root / "index.json").write_text("{", encoding="utf-8")

    r = client.get("/pilotage/resume")

    assert r.status_code == 200
    assert any("registre illisible" in a for a in r.json()["alertes"])


@pytest.mark.parametrize("chemin, marqueur", [
    ("/", 'id="form-analyse"'),
    ("/pilotage", 'id="verdict"'),
    ("/observabilite", 'id="observabilite"'),
])
def test_pages_servies(client, chemin, marqueur):
    r = client.get(chemin)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert marqueur in r.text
    assert "/static/styles.css" in r.text


@pytest.mark.parametrize("chemin", ["/", "/pilotage", "/observabilite"])
def test_navigation_trois_onglets(client, chemin):
    page = client.get(chemin).text

    for cible in ('href="/"', 'href="/pilotage"', 'href="/observabilite"'):
        assert cible in page
    assert page.count('aria-current="page"') == 1
    assert f'href="{chemin}" aria-current="page"' in page


@pytest.mark.parametrize("chemin", ["/static/styles.css", "/static/observabilite.js",
                                    "/exemples/c02.txt", "/exemples/c07.txt"])
def test_fichiers_statiques(client, chemin):
    assert client.get(chemin).status_code == 200


def test_exemples_hors_dossier_refuses(client):
    assert client.get("/exemples/..%2f..%2fapp%2fmain.py").status_code == 404


def test_page_analyse_modes_et_sans_kpi(client):
    page = client.get("/").text

    for mode in ('value="v1"', 'value="v2"', 'value="comparer"'):
        assert mode in page
    assert 'value="gateway"' not in page      # la gateway n'est plus proposée ici
    assert 'id="kpi-p95"' not in page and 'id="kpi-canary"' not in page
    assert 'id="suivi"' in page                # zone des timers


def test_page_pilotage_lecture_seule(client):
    page = client.get("/pilotage").text

    for zone in ('id="verdict"', 'id="comparaison"', 'id="retroactions"', 'id="seuils"', 'id="journal"'):
        assert zone in page
    assert "<form" not in page             # aucune action possible depuis la page


def test_page_observabilite_zones(client):
    page = client.get("/observabilite").text

    for zone in ('id="etat-sentry"', 'id="routes"', 'id="erreurs"', 'id="liens"'):
        assert zone in page
