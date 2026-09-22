"""Enrichissement du jeu d'évaluation — versement humain des cas capturés.

    python -m eval.enrichir lister
    python -m eval.enrichir verser cand-0123456789 --clauses "durée,résiliation"

``verser`` crée ``eval/contrats/cNN.txt`` (texte déjà anonymisé) et ajoute sa
ligne à ``eval/attendus.jsonl`` ; le gate le rejoue à la fusion suivante. Les
clauses attendues sont celles que le juriste valide (B2.4), jamais celles que
la v2 a trouvées (ce serait évaluer le modèle sur ses propres sorties).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.capture import _verrouille, candidats_en_attente, chemin_candidats, lire_candidats
from app.llm_client import TYPES_CLAUSES
from eval.run_eval import CHEMIN_ATTENDUS, DOSSIER_CONTRATS, charger_attendus
from ops.pilotage import resume_metier
from ops.registry import Registry


class ErreurEnrichissement(ValueError):
    """Versement refusé : rien n'a été écrit."""


def prochain_id(identifiants: Iterable[str]) -> str:
    numeros = [int(m.group(1)) for i in identifiants if (m := re.fullmatch(r"c(\d+)", i))]
    return f"c{max(numeros, default=0) + 1:02d}"


def _ajouter_ligne(chemin: Path, data: dict[str, Any]) -> None:
    contenu = chemin.read_text(encoding="utf-8") if chemin.exists() else ""
    separateur = "" if not contenu or contenu.endswith("\n") else "\n"
    with chemin.open("a", encoding="utf-8") as f:
        f.write(separateur + json.dumps(data, ensure_ascii=False) + "\n")


def _ajouter_ligne_verrouillee(chemin: Path, data: dict[str, Any]) -> None:
    """Comme ``_ajouter_ligne``, mais sous le même verrou (``fcntl``) que la
    capture (``app.capture._verrouille``) : ``candidats.jsonl`` est écrit par
    les deux à la fois."""
    with _verrouille(chemin) as fichier:
        fichier.seek(0, os.SEEK_END)
        fichier.write(json.dumps(data, ensure_ascii=False) + "\n")


def verser(
    id_candidat: str,
    clauses: list[str],
    *,
    seuil_note: float = 0.75,
    candidats: Path | None = None,
    contrats: Path = DOSSIER_CONTRATS,
    attendus: Path = CHEMIN_ATTENDUS,
    registry: Registry | None = None,
) -> dict[str, Any]:
    chemin = candidats or chemin_candidats()
    tous, verses = lire_candidats(chemin)
    if id_candidat not in tous:
        raise ErreurEnrichissement(f"candidat inconnu : {id_candidat}")
    if id_candidat in verses:
        raise ErreurEnrichissement(
            f"candidat déjà versé : {id_candidat} ({verses[id_candidat]['contrat_id']})"
        )
    clauses = [c.strip() for c in clauses if c.strip()]
    if not clauses:
        raise ErreurEnrichissement("au moins une clause attendue est requise (--clauses)")
    inconnues = [c for c in clauses if c not in TYPES_CLAUSES]
    if inconnues:
        raise ErreurEnrichissement(
            f"type(s) de clause inconnu(s) : {', '.join(inconnues)} "
            "— voir app/llm_client.py::TYPES_CLAUSES"
        )
    candidat = tous[id_candidat]
    contrat_id = prochain_id(charger_attendus(Path(attendus)))
    fichier = Path(contrats) / f"{contrat_id}.txt"
    if fichier.exists():
        raise ErreurEnrichissement(f"le contrat {fichier} existe déjà")

    texte = candidat["texte"]
    ligne = {
        "contrat_id": contrat_id,
        "pages": max(1, round(len(texte) / 3000)),
        "clauses_attendues": clauses,
        "seuil_note": seuil_note,
        "origine": "production",
    }
    fichier.write_text(texte, encoding="utf-8")
    _ajouter_ligne(Path(attendus), ligne)
    _ajouter_ligne_verrouillee(chemin, {
        "type": "verse",
        "id": id_candidat,
        "contrat_id": contrat_id,
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    (registry or Registry()).journaliser(
        "enrichissement",
        origine="manuel",
        candidat=id_candidat,
        contrat_id=contrat_id,
        score=candidat["score"],
        resume=resume_metier("enrichissement", contrat_id=contrat_id, score=candidat["score"]),
    )
    return ligne


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrichissement du jeu d'évaluation Mardik")
    sub = parser.add_subparsers(dest="commande", required=True)
    sub.add_parser("lister", help="candidats en attente de versement")
    v = sub.add_parser("verser", help="verse un candidat dans le jeu d'évaluation")
    v.add_argument("id")
    v.add_argument("--clauses", required=True, help="types attendus, séparés par des virgules")
    v.add_argument("--seuil-note", type=float, default=0.75)
    args = parser.parse_args(argv)

    if args.commande == "lister":
        attente = candidats_en_attente()
        if not attente:
            print("aucun candidat en attente")
        for c in attente:
            apercu = c["texte"][:200].replace("\n", " ")
            print(f"{c['id']}  {c['date']}  {c['version']}  score {c['score']:.2f}  "
                  f"trouvées : {', '.join(c['clauses_trouvees']) or '—'}\n    {apercu}…")
        return 0
    try:
        ligne = verser(args.id, args.clauses.split(","), seuil_note=args.seuil_note)
    except ErreurEnrichissement as exc:
        print(f"REFUSÉ : {exc}", file=sys.stderr)
        return 1
    print(json.dumps(ligne, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
