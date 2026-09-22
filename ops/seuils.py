"""Seuils de pilotage — config as code (``ops/seuils_pilotage.yaml``).

Même convention que ``eval/seuils.yaml`` (gate) : fichier versionné, ``motif``
obligatoire, relu à chaque appel, erreurs explicites qui nomment la clé.

Ce module n'importe ni ``app.api_*`` ni ``eval`` : ``app.capture`` l'importe,
et ``eval.run_eval`` importe ``app.api_v2`` (import circulaire sinon).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

import yaml

from app.telemetry import MetricsStore
from ops.signaux import percentile, scores

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_SEUILS_PILOTAGE_DEFAUT = RACINE / "ops" / "seuils_pilotage.yaml"
CHEMIN_SEUILS_GATE_DEFAUT = RACINE / "eval" / "seuils.yaml"


class ErreurSeuilsPilotage(ValueError):
    """Fichier de seuils de pilotage absent, illisible ou incohérent."""


@dataclass(frozen=True)
class SeuilsDerive:
    score_min: float
    marge: float
    taux_erreur_max: float
    latence_p95_max_ms: float


@dataclass(frozen=True)
class SeuilsPromotion:
    paliers: tuple[int, ...]
    duree_min_s: float
    requetes_min: int
    ecart_erreur_max: float
    latence_p95_max_ms: float
    score_moyen_min: float
    score_p10_min: float


@dataclass(frozen=True)
class SeuilsCapture:
    score_max: float


@dataclass(frozen=True)
class SeuilsPilotage:
    fenetre_s: float
    minimum: int
    intervalle_s: float
    derive: SeuilsDerive
    promotion: SeuilsPromotion
    capture: SeuilsCapture
    motif: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["promotion"]["paliers"] = list(self.promotion.paliers)
        return data


def chemin_seuils_pilotage() -> Path:
    """``PILOTAGE_SEUILS_PATH`` si défini, sinon ``ops/seuils_pilotage.yaml``."""
    return Path(os.environ.get("PILOTAGE_SEUILS_PATH") or CHEMIN_SEUILS_PILOTAGE_DEFAUT)


def chemin_seuils_gate() -> Path:
    """Le fichier du gate (SP2) : ``SEUILS_PATH`` si défini, sinon ``eval/seuils.yaml``."""
    return Path(os.environ.get("SEUILS_PATH") or CHEMIN_SEUILS_GATE_DEFAUT)


def lire_brut(chemin: Path | str) -> dict[str, Any]:
    """Le YAML tel quel (pour journaliser avant/après), sans validation des clés."""
    chemin = Path(chemin)
    try:
        contenu = chemin.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ErreurSeuilsPilotage(f"fichier de seuils introuvable : {chemin}") from exc
    except OSError as exc:
        raise ErreurSeuilsPilotage(f"fichier de seuils illisible : {chemin} ({exc})") from exc
    try:
        data = yaml.safe_load(contenu)
    except yaml.YAMLError as exc:
        raise ErreurSeuilsPilotage(f"{chemin} : YAML invalide ({exc})") from exc
    if not isinstance(data, dict):
        raise ErreurSeuilsPilotage(f"{chemin} : attendu un dictionnaire de seuils")
    return data


def empreinte(chemin: Path | str) -> str:
    chemin = Path(chemin)
    try:
        contenu = chemin.read_bytes()
    except FileNotFoundError as exc:
        raise ErreurSeuilsPilotage(f"fichier de seuils introuvable : {chemin}") from exc
    except OSError as exc:
        raise ErreurSeuilsPilotage(f"fichier de seuils illisible : {chemin} ({exc})") from exc
    return hashlib.sha256(contenu).hexdigest()[:12]


def _section(data: dict[str, Any], cle: str, chemin: Path) -> dict[str, Any]:
    section = data.get(cle)
    if not isinstance(section, dict):
        raise ErreurSeuilsPilotage(f"{chemin} : section « {cle} » manquante")
    return section


def _nombre(
    section: dict[str, Any], cle: str, chemin: Path, prefixe: str = "", *, positif: bool = False
) -> float:
    nom = f"{prefixe}{cle}"
    if cle not in section:
        raise ErreurSeuilsPilotage(f"{chemin} : clé « {nom} » manquante")
    valeur = section[cle]
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        raise ErreurSeuilsPilotage(
            f"{chemin} : « {nom} » doit être un nombre, reçu {valeur!r}"
        )
    if positif and valeur <= 0:
        raise ErreurSeuilsPilotage(
            f"{chemin} : « {nom} » doit être strictement positif, reçu {valeur!r}"
        )
    return float(valeur)


def _paliers(section: dict[str, Any], chemin: Path) -> tuple[int, ...]:
    paliers = section.get("paliers")
    valide = (
        isinstance(paliers, list)
        and bool(paliers)
        and all(isinstance(p, int) and not isinstance(p, bool) for p in paliers)
        and paliers == sorted(set(paliers))
        and paliers[0] >= 1
        and paliers[-1] == 100
    )
    if not valide:
        raise ErreurSeuilsPilotage(
            f"{chemin} : « promotion.paliers » doit être une liste croissante d'entiers "
            f"finissant par 100, reçu {paliers!r}"
        )
    return tuple(paliers)


def charger_seuils_pilotage(chemin: Path | str | None = None) -> SeuilsPilotage:
    chemin = Path(chemin) if chemin else chemin_seuils_pilotage()
    data = lire_brut(chemin)
    derive = _section(data, "derive", chemin)
    promotion = _section(data, "promotion", chemin)
    capture = _section(data, "capture", chemin)
    motif = str(data.get("motif") or "").strip()
    if not motif:
        raise ErreurSeuilsPilotage(f"{chemin} : clé « motif » manquante ou vide")
    minimum_f = _nombre(data, "minimum", chemin, positif=True)
    if not minimum_f.is_integer():
        raise ErreurSeuilsPilotage(
            f"{chemin} : « minimum » doit être un entier, reçu {data.get('minimum')!r}"
        )
    requetes_min_f = _nombre(promotion, "requetes_min", chemin, "promotion.")
    if not requetes_min_f.is_integer():
        raise ErreurSeuilsPilotage(
            f"{chemin} : « promotion.requetes_min » doit être un entier, reçu {promotion.get('requetes_min')!r}"
        )
    return SeuilsPilotage(
        fenetre_s=_nombre(data, "fenetre_s", chemin, positif=True),
        minimum=int(minimum_f),
        intervalle_s=_nombre(data, "intervalle_s", chemin, positif=True),
        derive=SeuilsDerive(
            score_min=_nombre(derive, "score_min", chemin, "derive."),
            marge=_nombre(derive, "marge", chemin, "derive."),
            taux_erreur_max=_nombre(derive, "taux_erreur_max", chemin, "derive."),
            latence_p95_max_ms=_nombre(derive, "latence_p95_max_ms", chemin, "derive."),
        ),
        promotion=SeuilsPromotion(
            paliers=_paliers(promotion, chemin),
            duree_min_s=_nombre(promotion, "duree_min_s", chemin, "promotion."),
            requetes_min=int(requetes_min_f),
            ecart_erreur_max=_nombre(promotion, "ecart_erreur_max", chemin, "promotion."),
            latence_p95_max_ms=_nombre(promotion, "latence_p95_max_ms", chemin, "promotion."),
            score_moyen_min=_nombre(promotion, "score_moyen_min", chemin, "promotion."),
            score_p10_min=_nombre(promotion, "score_p10_min", chemin, "promotion."),
        ),
        capture=SeuilsCapture(score_max=_nombre(capture, "score_max", chemin, "capture.")),
        motif=motif,
    )


# ---------------------------------------------------------------- calibration
def calibrer(
    metriques: MetricsStore,
    version: str,
    *,
    fenetre_s: float = 3600,
    k: float = 1.5,
    minimum: int = 30,
    marge_p10: float = 0.05,
) -> dict[str, Any]:
    """Propose des seuils à partir de la distribution de production (C2.2, D7).

    N'écrit rien : la proposition est reportée à la main dans
    ``ops/seuils_pilotage.yaml`` par un commit dont le ``motif`` la cite.
    """
    valeurs = scores(metriques.lire(depuis_s=fenetre_s, version=version))
    if len(valeurs) < minimum:
        raise ErreurSeuilsPilotage(
            f"échantillon insuffisant pour calibrer {version} : "
            f"{len(valeurs)} scores < {minimum}"
        )
    moyenne = fmean(valeurs)
    ecart = pstdev(valeurs)
    p10 = percentile(valeurs, 10) or 0.0
    return {
        "version": version,
        "mesures": len(valeurs),
        "moyenne": round(moyenne, 4),
        "ecart_type": round(ecart, 4),
        "p10": round(p10, 4),
        "k": k,
        "score_min_propose": round(moyenne - k * ecart, 2),
        "score_p10_min_propose": round(p10 - marge_p10, 2),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seuils de pilotage Mardik")
    sub = parser.add_subparsers(dest="commande", required=True)
    c = sub.add_parser("calibrer", help="propose des seuils (n'écrit rien)")
    c.add_argument("--version", required=True)
    c.add_argument("--fenetre", type=float, default=3600)
    c.add_argument("--k", type=float, default=1.5)
    args = parser.parse_args(argv)
    try:
        r = calibrer(MetricsStore(), args.version, fenetre_s=args.fenetre, k=args.k)
    except ErreurSeuilsPilotage as exc:
        print(f"CALIBRATION IMPOSSIBLE : {exc}", file=sys.stderr)
        return 1
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print("\nProposition à reporter dans ops/seuils_pilotage.yaml (ce script n'écrit rien) :")
    print(f"derive:\n  score_min: {r['score_min_propose']}")
    print(f"promotion:\n  score_p10_min: {r['score_p10_min_propose']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
