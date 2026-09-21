"""Découpage d'un contrat par clauses (étape *map* du map-reduce).

Contrat :

    decouper(texte: str, taille_max: int = 6000) -> list[Section]

* Le texte est d'abord coupé aux intitulés d'articles (« Article 3 — Résiliation »,
  « 3. Résiliation », « ARTICLE 3 : … ») ; ce qui précède le premier intitulé
  forme le ``préambule``.
* Un article plus long que ``taille_max`` est scindé en fin de phrase (une
  phrase plus longue que ``taille_max`` est coupée au dernier blanc).
* Les morceaux consécutifs sont regroupés tant que leur longueur cumulée reste
  ≤ ``taille_max`` : un appel LLM par section, pas par article. Le titre d'un
  groupe est celui de son premier article, suffixé de ``(+k)``.
* Invariant : ``"".join(s.texte for s in sections) == texte`` — rien n'est perdu.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TITRE_PREAMBULE = "préambule"
_INTITULE = re.compile(
    r"^[ \t]*(?:article[ \t]+\d+|\d+\.[ \t]+\w)[^\n]*", re.IGNORECASE | re.MULTILINE
)
_FIN_PHRASE = re.compile(r"[.!?;](?=\s)")


@dataclass
class Section:
    indice: int
    titre: str
    texte: str


def decouper(texte: str, taille_max: int = 6000) -> list[Section]:
    morceaux = [
        (titre, morceau)
        for titre, contenu in _unites(texte)
        for morceau in _scinder(contenu, taille_max)
    ]
    groupes: list[tuple[list[str], str]] = []
    for titre, morceau in morceaux:
        if groupes and len(groupes[-1][1]) + len(morceau) <= taille_max:
            titres, contenu = groupes[-1]
            groupes[-1] = ([*titres, titre], contenu + morceau)
        else:
            groupes.append(([titre], morceau))
    return [
        Section(indice=i, titre=_titre_groupe(titres), texte=contenu)
        for i, (titres, contenu) in enumerate(groupes)
    ]


def _unites(texte: str) -> list[tuple[str, str]]:
    """Coupe le texte aux intitulés d'articles : ``(titre, contenu)``, sans perte."""
    debuts = [m.start() for m in _INTITULE.finditer(texte)]
    if not debuts or debuts[0] != 0:
        debuts.insert(0, 0)
    bornes = [*debuts, len(texte)]
    unites = []
    for debut, fin in zip(bornes, bornes[1:]):
        intitule = _INTITULE.match(texte, debut)
        titre = intitule.group(0).strip() if intitule else TITRE_PREAMBULE
        unites.append((titre, texte[debut:fin]))
    return unites


def _scinder(texte: str, taille_max: int) -> list[str]:
    """Scinde ``texte`` en morceaux ≤ ``taille_max``, en fin de phrase si possible."""
    morceaux = []
    while len(texte) > taille_max:
        fenetre = texte[:taille_max]
        fins = [m.end() for m in _FIN_PHRASE.finditer(fenetre)]
        if fins:
            coupe = fins[-1]
        else:
            blanc = max(fenetre.rfind(" "), fenetre.rfind("\n"))
            coupe = blanc + 1 if blanc >= 0 else taille_max
        morceaux.append(texte[:coupe])
        texte = texte[coupe:]
    if texte:
        morceaux.append(texte)
    return morceaux


def _titre_groupe(titres: list[str]) -> str:
    return titres[0] if len(titres) == 1 else f"{titres[0]} (+{len(titres) - 1})"
