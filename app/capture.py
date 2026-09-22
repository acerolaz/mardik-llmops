"""Capture des analyses à faible confiance — boucle d'enrichissement (C2.6, B2.6).

Une analyse v2 servie dont la ``confiance_globale`` est sous
``capture.score_max`` (``ops/seuils_pilotage.yaml``) est anonymisée puis
ajoutée à ``eval/candidats.jsonl`` (hors git). Un humain la verse ensuite dans
le jeu d'évaluation (``python -m eval.enrichir verser``).

La capture tourne en tâche de fond : elle n'affecte jamais la réponse client.
Ce module n'importe pas ``app.api_v2`` (qui l'importe).
"""
from __future__ import annotations

import re

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
