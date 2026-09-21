"""Fusion et dédoublonnage des clauses extraites section par section (*reduce*).

Contrat :

    consolider(par_section: list[list[Clause]]) -> list[Clause]

* Deux clauses du même ``type`` trouvées dans des sections différentes sont
  **une seule** clause : extrait le plus long, ``sections`` unies et triées,
  ``confiance_llm`` maximale.
* L'ordre de sortie suit l'ordre d'apparition (``par_section`` est rangé dans
  l'ordre des sections).
* Fonction pure : les clauses reçues ne sont pas modifiées.
"""
from __future__ import annotations

from dataclasses import replace

from app.pipeline.confiance import Clause


def consolider(par_section: list[list[Clause]]) -> list[Clause]:
    fusion: dict[str, Clause] = {}
    for clauses in par_section:
        for clause in clauses:
            existante = fusion.get(clause.type)
            if existante is None:
                fusion[clause.type] = replace(clause, sections=sorted(set(clause.sections)))
                continue
            fusion[clause.type] = replace(
                existante,
                extrait=max(existante.extrait, clause.extrait, key=len),
                confiance_llm=max(existante.confiance_llm, clause.confiance_llm),
                sections=sorted(set(existante.sections) | set(clause.sections)),
            )
    return list(fusion.values())
