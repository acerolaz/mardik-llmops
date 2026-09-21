"""Score de confiance composite, par clause puis global.

Contrat :

    scorer(clauses: list[Clause], texte: str) -> tuple[list[Clause], float]

* Deux signaux indépendants du modèle s'ajoutent à ce qu'il déclare :
  l'**ancrage** (l'extrait cité figure-t-il vraiment dans le contrat ?) et la
  **récurrence** (la clause a-t-elle été vue dans plusieurs sections ?).
* ``confiance = ancrage × (0,7 × confiance_llm + 0,3 × récurrence)`` : une
  citation inventée (ancrage ≈ 0) obtient un score bas, même si le modèle se
  déclare très sûr de lui.
* Le score global est la moyenne des scores par clause (0 si aucune clause) ;
  c'est lui que le client lit « pour savoir quand relire ».
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

POIDS_LLM = 0.7
POIDS_RECURRENCE = 0.3
RECURRENCE_UNIQUE = 0.5  # clause vue dans une seule section
_MOT = re.compile(r"\w+")


@dataclass
class Clause:
    type: str
    extrait: str
    confiance_llm: float
    sections: list[int] = field(default_factory=list)
    confiance: float = 0.0

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "extrait": self.extrait,
            "confiance": round(self.confiance, 3),
            "sections": self.sections,
        }


def scorer(clauses: list[Clause], texte: str) -> tuple[list[Clause], float]:
    index = _Index(texte)
    notees = [replace(c, confiance=_score(c, index)) for c in clauses]
    globale = sum(c.confiance for c in notees) / len(notees) if notees else 0.0
    return notees, globale


def ancrage(extrait: str, texte: str) -> float:
    """Part de l'extrait (en trigrammes de mots) retrouvée dans ``texte``, entre 0 et 1."""
    return _Index(texte).ancrage(extrait)


def _mots(texte: str) -> list[str]:
    # \w+ en minuscules : neutralise ponctuation, apostrophes typographiques et blancs.
    return _MOT.findall(texte.lower())


def _trigrammes(mots: list[str]) -> set[tuple[str, ...]]:
    return {tuple(mots[i : i + 3]) for i in range(len(mots) - 2)}


class _Index:
    """Index du contrat, calculé une fois par analyse (coût linéaire)."""

    def __init__(self, texte: str) -> None:
        mots = _mots(texte)
        self._phrase = f" {' '.join(mots)} "
        self._trigrammes = _trigrammes(mots)

    def ancrage(self, extrait: str) -> float:
        mots = _mots(extrait)
        if not mots:
            return 0.0
        if len(mots) < 3:
            return 1.0 if f" {' '.join(mots)} " in self._phrase else 0.0
        cherches = _trigrammes(mots)
        return len(cherches & self._trigrammes) / len(cherches)


def _score(clause: Clause, index: _Index) -> float:
    recurrence = 1.0 if len(set(clause.sections)) >= 2 else RECURRENCE_UNIQUE
    brut = index.ancrage(clause.extrait) * (
        POIDS_LLM * clause.confiance_llm + POIDS_RECURRENCE * recurrence
    )
    return min(1.0, max(0.0, brut))
