"""Capture des analyses à faible confiance — boucle d'enrichissement (C2.6, B2.6).

Une analyse v2 servie dont la ``confiance_globale`` est sous
``capture.score_max`` (``ops/seuils_pilotage.yaml``) est anonymisée puis
ajoutée à ``eval/candidats.jsonl`` (hors git). Un humain la verse ensuite dans
le jeu d'évaluation (``python -m eval.enrichir verser``).

La capture tourne en tâche de fond : elle n'affecte jamais la réponse client.
Ce module n'importe pas ``app.api_v2`` (qui l'importe).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Protocol

import structlog

from ops.seuils import ErreurSeuilsPilotage, charger_seuils_pilotage

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_CANDIDATS_DEFAUT = RACINE / "eval" / "candidats.jsonl"

_NOM = r"[A-ZÀ-Ý][\w'-]+"
_MOTIFS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"\bFR\d{2}(?:\s?[A-Z0-9]{4}){5}\s?[A-Z0-9]{3}\b"),
     "[IBAN]"),
    (re.compile(r"(?:\+33\s?|\b0)[1-9](?:[\s.-]?\d{2}){4}\b"),
     "[TELEPHONE]"),
    (re.compile(r"\b\d{3}\s?\d{3}\s?\d{3}(?:\s?\d{5})?\b"), "[SIRET]"),
    (re.compile(rf"\b(?:M\.|Mme|Monsieur|Madame|Me)\s+{_NOM}(?:\s+{_NOM})*"),
     "[PERSONNE]"),
)


def anonymiser(texte: str) -> str:
    """Masque e-mails, IBAN, téléphones, SIRET/SIREN et personnes (civilité + nom).

    Les raisons sociales et les adresses ne sont pas masquées : le juriste le
    vérifie avant tout versement dans le jeu d'évaluation.
    """
    for motif, remplacement in _MOTIFS:
        texte = motif.sub(remplacement, texte)
    return texte


# ------------------------------------------------------------------ capture
class _Clause(Protocol):
    type: str


class ReponseScoree(Protocol):
    """Ce que la capture lit d'une réponse v2 (``ReponseAnalyseV2`` convient)."""

    confiance_globale: float
    version: str
    clauses: Sequence[_Clause]


def chemin_candidats() -> Path:
    """``CANDIDATS_PATH`` si défini, sinon ``eval/candidats.jsonl``."""
    return Path(os.environ.get("CANDIDATS_PATH") or CHEMIN_CANDIDATS_DEFAUT)


@dataclass(frozen=True)
class Capture:
    """Où capturer et sous quel score ; ``score_max=None`` → lu dans les seuils
    de pilotage au moment de la capture (jamais à la résolution de la dépendance :
    un fichier de seuils cassé ne doit pas faire échouer la requête)."""

    chemin: Path
    score_max: float | None = None


def get_capture() -> Capture:
    return Capture(chemin=chemin_candidats())


@contextmanager
def _verrouille(chemin: Path) -> Iterator[IO[str]]:
    """Ouvre ``chemin`` en ajout, sous verrou exclusif.

    Le tampon est vidé **avant** le déverrouillage : sinon une ligne restée
    dans le tampon partirait hors verrou et pourrait s'entrelacer avec l'écriture
    d'un autre processus (l'app et le versement humain écrivent le même fichier).
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("a+", encoding="utf-8") as fichier:
        fcntl.flock(fichier, fcntl.LOCK_EX)
        try:
            yield fichier
        finally:
            fichier.flush()
            os.fsync(fichier.fileno())
            fcntl.flock(fichier, fcntl.LOCK_UN)


def _lignes(fichier: IO[str]) -> Iterator[dict[str, Any]]:
    for ligne in fichier:
        ligne = ligne.strip()
        if ligne:
            try:
                yield json.loads(ligne)
            except json.JSONDecodeError:
                continue


def capturer(capture: Capture, texte: str, reponse: ReponseScoree) -> str | None:
    """Ajoute un candidat si le score est bas ; renvoie son id (``None`` sinon).

    Dédoublonnage sur l'empreinte du texte **brut**. Toute erreur d'écriture ou
    de seuils est journalisée (``capture.echec``) et avalée.
    """
    try:
        score_max = capture.score_max
        if score_max is None:
            score_max = charger_seuils_pilotage().capture.score_max
        if reponse.confiance_globale >= score_max:
            return None
        empreinte = hashlib.sha256(texte.encode("utf-8")).hexdigest()
        identifiant = f"cand-{empreinte[:10]}"
        with _verrouille(capture.chemin) as fichier:
            fichier.seek(0)
            if any(e.get("empreinte") == empreinte for e in _lignes(fichier)):
                return None
            fichier.seek(0, os.SEEK_END)
            ligne = {
                "type": "candidat",
                "id": identifiant,
                "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "version": reponse.version,
                "score": reponse.confiance_globale,
                "texte": anonymiser(texte),
                "clauses_trouvees": sorted({c.type for c in reponse.clauses}),
                "empreinte": empreinte,
            }
            fichier.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        return identifiant
    except (OSError, ErreurSeuilsPilotage) as exc:
        structlog.get_logger("mardik").warning("capture.echec", cause=str(exc))
        return None


def lire_candidats(
    chemin: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Rejoue ``candidats.jsonl`` (append-only) : (candidats par id, versements par id)."""
    chemin = chemin or chemin_candidats()
    candidats: dict[str, dict[str, Any]] = {}
    verses: dict[str, dict[str, Any]] = {}
    if not chemin.exists():
        return candidats, verses
    with chemin.open(encoding="utf-8") as fichier:
        for entree in _lignes(fichier):
            if entree.get("type") == "candidat":
                candidats[entree["id"]] = entree
            elif entree.get("type") == "verse":
                verses[entree["id"]] = entree
    return candidats, verses


def candidats_en_attente(chemin: Path | None = None) -> list[dict[str, Any]]:
    candidats, verses = lire_candidats(chemin)
    return [c for identifiant, c in candidats.items() if identifiant not in verses]
