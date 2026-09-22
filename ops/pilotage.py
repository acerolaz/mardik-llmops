"""Décisions du pilote — fonctions pures, aucune écriture.

Chaque fonction reçoit des mesures, des seuils et des entrées de journal, et
renvoie une décision. ``ops/deploy.py`` exécute ces décisions (rollback,
progression, promotion) ; ``ops/dashboard.py`` les affiche.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from app.telemetry import Mesure
from ops.seuils import SeuilsDerive
from ops.signaux import agreger, scores

NIVEAUX = ("aucune", "marge", "critique", "echantillon_insuffisant")


@dataclass(frozen=True)
class Constat:
    """Un critère évalué : le signal, sa valeur, le seuil, tenu ou non."""

    signal: str
    valeur: float | None
    seuil: float
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Derive:
    version: str
    mesures: int
    niveau: str
    constats: tuple[Constat, ...]
    motif: str
    sous_seuil: int = 0

    @property
    def critique(self) -> bool:
        return self.niveau == "critique"

    @property
    def principal(self) -> Constat | None:
        return next((c for c in self.constats if not c.ok), None)


def version_surveillee(index: dict[str, Any]) -> str | None:
    """Le canary s'il y en a un, sinon l'active."""
    return index.get("canary") or index.get("active")


def _motif_derive(c: Constat) -> str:
    if c.signal == "score_moyen":
        return f"score moyen {c.valeur:.2f} < {c.seuil:.2f}"
    if c.signal == "taux_erreur":
        return f"taux d'erreur {c.valeur:.0%} > {c.seuil:.0%}"
    return f"latence P95 {c.valeur:.0f} ms > {c.seuil:.0f} ms"


def detecter_derive(
    version: str, mesures: Sequence[Mesure], seuils: SeuilsDerive, *, minimum: int
) -> Derive:
    """Seuils durs → ``critique`` ; score dans la marge → ``marge``.

    Ordre des constats (et donc du motif) : score, erreurs, P95. Le score n'est
    pas évalué pour une version qui n'en produit pas (v1).
    """
    a = agreger(mesures)
    if a.requetes < minimum:
        return Derive(version, a.requetes, "echantillon_insuffisant", (),
                      f"échantillon insuffisant ({a.requetes}/{minimum})")
    constats: list[Constat] = []
    if a.score_moyen is not None:
        constats.append(Constat("score_moyen", a.score_moyen, seuils.score_min,
                                a.score_moyen >= seuils.score_min))
    constats.append(Constat("taux_erreur", a.taux_erreur, seuils.taux_erreur_max,
                            a.taux_erreur <= seuils.taux_erreur_max))
    if a.latence_p95_ms is not None:
        constats.append(Constat("latence_p95_ms", a.latence_p95_ms, seuils.latence_p95_max_ms,
                                a.latence_p95_ms <= seuils.latence_p95_max_ms))
    sous_seuil = sum(1 for s in scores(mesures) if s < seuils.score_min)
    echecs = [c for c in constats if not c.ok]
    if echecs:
        return Derive(version, a.requetes, "critique", tuple(constats),
                      _motif_derive(echecs[0]), sous_seuil)
    haut = seuils.score_min + seuils.marge
    if a.score_moyen is not None and a.score_moyen < haut:
        return Derive(
            version, a.requetes, "marge", tuple(constats),
            f"score moyen {a.score_moyen:.2f} dans la marge "
            f"[{seuils.score_min:.2f} ; {haut:.2f}[",
            sous_seuil,
        )
    return Derive(version, a.requetes, "aucune", tuple(constats), "", sous_seuil)
