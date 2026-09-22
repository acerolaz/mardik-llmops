"""Signaux — agrégats purs sur des ``Mesure`` (``ops/metrics.jsonl``).

Partagés par le tableau de bord et le pilote : ce qu'on voit à l'écran est
exactement ce qui a déclenché l'action. Les erreurs comptent dans le trafic et
le taux d'erreur, jamais dans les latences ni les scores.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

from app.telemetry import Mesure


def percentile(valeurs: Sequence[float], p: float) -> float | None:
    """Percentile ``p`` (0–100) par interpolation linéaire ; ``None`` si vide."""
    if not valeurs:
        return None
    tri = sorted(valeurs)
    rang = (len(tri) - 1) * p / 100
    bas, haut = math.floor(rang), math.ceil(rang)
    return float(tri[bas] + (tri[haut] - tri[bas]) * (rang - bas))


def _arrondi(valeur: float | None, decimales: int) -> float | None:
    return None if valeur is None else round(valeur, decimales)


@dataclass(frozen=True)
class Agregat:
    requetes: int
    erreurs: int
    taux_erreur: float
    latence_p50_ms: float | None
    latence_p95_ms: float | None
    score_moyen: float | None
    score_p10: float | None
    cout_total_eur: float
    cout_moyen_eur: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scores(mesures: Sequence[Mesure]) -> list[float]:
    """Scores des mesures sans erreur qui en portent un (la v1 n'en porte pas)."""
    return [m.score for m in mesures if not m.erreur and m.score is not None]


def agreger(mesures: Sequence[Mesure]) -> Agregat:
    n = len(mesures)
    erreurs = sum(1 for m in mesures if m.erreur)
    latences = [m.latence_ms for m in mesures if not m.erreur]
    valeurs = scores(mesures)
    cout = sum(m.cout_eur for m in mesures)
    return Agregat(
        requetes=n,
        erreurs=erreurs,
        taux_erreur=round(erreurs / n, 4) if n else 0.0,
        latence_p50_ms=_arrondi(percentile(latences, 50), 1),
        latence_p95_ms=_arrondi(percentile(latences, 95), 1),
        score_moyen=round(fmean(valeurs), 4) if valeurs else None,
        score_p10=_arrondi(percentile(valeurs, 10), 4),
        cout_total_eur=round(cout, 6),
        cout_moyen_eur=round(cout / n, 6) if n else 0.0,
    )


def histogramme(valeurs: Sequence[float], pas: float = 0.1) -> list[int]:
    """Comptes par intervalle de largeur ``pas`` sur [0 ; 1] ; 1,0 tombe dans le dernier."""
    n = round(1 / pas)
    comptes = [0] * n
    for v in valeurs:
        indice = min(max(int(v * n + 1e-9), 0), n - 1)
        comptes[indice] += 1
    return comptes


def serie_par_minute(mesures: Sequence[Mesure]) -> list[dict[str, Any]]:
    groupes: dict[int, list[Mesure]] = {}
    for m in mesures:
        groupes.setdefault(int(m.ts // 60) * 60, []).append(m)
    serie = []
    for minute in sorted(groupes):
        a = agreger(groupes[minute])
        serie.append(
            {
                "minute": minute,
                "requetes": a.requetes,
                "latence_p95_ms": a.latence_p95_ms,
                "taux_erreur": a.taux_erreur,
                "score_moyen": a.score_moyen,
            }
        )
    return serie


def filtrer(
    mesures: Sequence[Mesure], version: str, *, depuis_ts: float | None = None
) -> list[Mesure]:
    return [
        m for m in mesures
        if m.version == version and (depuis_ts is None or m.ts >= depuis_ts)
    ]
