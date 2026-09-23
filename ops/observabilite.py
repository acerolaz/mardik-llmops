"""Observabilité locale : agrégats par route depuis ``ops/metrics.jsonl``.

Les traces et les erreurs détaillées sont dans Sentry ; ici, les chiffres qui
disent où regarder. Même fenêtre que le pilote (``ops/seuils_pilotage.yaml``).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from app.config import SentrySettings
from app.telemetry import Mesure, MetricsStore
from ops.seuils import ErreurSeuilsPilotage, charger_seuils_pilotage
from ops.signaux import agreger

NB_ERREURS_RECENTES = 10
FENETRE_DEFAUT_S = 300


def _stats_route(route: str, version: str, mesures: Sequence[Mesure]) -> dict[str, Any]:
    a = agreger(mesures)
    n = len(mesures)
    return {
        "route": route,
        "version": version,
        "requetes": a.requetes,
        "taux_erreur": a.taux_erreur,
        "latence_p50_ms": a.latence_p50_ms,
        "latence_p95_ms": a.latence_p95_ms,
        "appels_llm_moyen": round(sum(m.appels_llm for m in mesures) / n, 2),
        "tokens_moyen": round(sum(m.tokens for m in mesures) / n, 1),
        "taux_tronque": round(sum(1 for m in mesures if m.tronque) / n, 4),
        "cout_total_eur": a.cout_total_eur,
    }


def liens_sentry(settings: SentrySettings) -> dict[str, str] | None:
    """Liens profonds vers l'organisation Sentry, filtrés par environnement."""
    if not settings.dsn or not settings.ui_url:
        return None
    base = settings.ui_url.rstrip("/")
    env = quote(settings.environment)
    return {
        "issues": f"{base}/issues/?environment={env}",
        "traces": f"{base}/traces/?environment={env}",
        "performance": f"{base}/insights/backend/?environment={env}",
    }


def resume_observabilite(
    metriques: MetricsStore | None = None,
    *,
    settings: SentrySettings | None = None,
    fenetre_s: float | None = None,
) -> dict[str, Any]:
    metriques = metriques or MetricsStore()
    settings = settings or SentrySettings()
    if fenetre_s is None:
        try:
            fenetre_s = charger_seuils_pilotage().fenetre_s
        except ErreurSeuilsPilotage:
            fenetre_s = FENETRE_DEFAUT_S
    mesures = metriques.lire(depuis_s=fenetre_s)
    groupes: dict[tuple[str, str], list[Mesure]] = {}
    for m in mesures:
        groupes.setdefault((m.route, m.version), []).append(m)
    erreurs = sorted((m for m in mesures if m.erreur), key=lambda m: m.ts, reverse=True)
    return {
        "fenetre_s": fenetre_s,
        "par_route": [_stats_route(route, version, du) for (route, version), du in sorted(groupes.items())],
        "erreurs_recentes": [
            {
                "date": datetime.fromtimestamp(m.ts, timezone.utc).isoformat(timespec="seconds"),
                "route": m.route,
                "version": m.version,
                "latence_ms": round(m.latence_ms, 1),
            }
            for m in erreurs[:NB_ERREURS_RECENTES]
        ],
        "sentry": {
            "actif": bool(settings.dsn),
            "environnement": settings.environment,
            "liens": liens_sentry(settings),
        },
    }
