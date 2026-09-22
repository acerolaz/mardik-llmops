"""Seuils de pilotage — config as code (``ops/seuils_pilotage.yaml``).

Même convention que ``eval/seuils.yaml`` (gate) : fichier versionné, ``motif``
obligatoire, relu à chaque appel, erreurs explicites qui nomment la clé.

Ce module n'importe ni ``app.api_*`` ni ``eval`` : ``app.capture`` l'importe,
et ``eval.run_eval`` importe ``app.api_v2`` (import circulaire sinon).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

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
    section: dict[str, Any], cle: str, chemin: Path, prefixe: str = ""
) -> float:
    nom = f"{prefixe}{cle}"
    if cle not in section:
        raise ErreurSeuilsPilotage(f"{chemin} : clé « {nom} » manquante")
    valeur = section[cle]
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        raise ErreurSeuilsPilotage(
            f"{chemin} : « {nom} » doit être un nombre, reçu {valeur!r}"
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
    return SeuilsPilotage(
        fenetre_s=_nombre(data, "fenetre_s", chemin),
        minimum=int(_nombre(data, "minimum", chemin)),
        intervalle_s=_nombre(data, "intervalle_s", chemin),
        derive=SeuilsDerive(
            score_min=_nombre(derive, "score_min", chemin, "derive."),
            marge=_nombre(derive, "marge", chemin, "derive."),
            taux_erreur_max=_nombre(derive, "taux_erreur_max", chemin, "derive."),
            latence_p95_max_ms=_nombre(derive, "latence_p95_max_ms", chemin, "derive."),
        ),
        promotion=SeuilsPromotion(
            paliers=_paliers(promotion, chemin),
            duree_min_s=_nombre(promotion, "duree_min_s", chemin, "promotion."),
            requetes_min=int(_nombre(promotion, "requetes_min", chemin, "promotion.")),
            ecart_erreur_max=_nombre(promotion, "ecart_erreur_max", chemin, "promotion."),
            latence_p95_max_ms=_nombre(promotion, "latence_p95_max_ms", chemin, "promotion."),
            score_moyen_min=_nombre(promotion, "score_moyen_min", chemin, "promotion."),
            score_p10_min=_nombre(promotion, "score_p10_min", chemin, "promotion."),
        ),
        capture=SeuilsCapture(score_max=_nombre(capture, "score_max", chemin, "capture.")),
        motif=motif,
    )
