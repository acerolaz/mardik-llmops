"""Extraction des clauses d'une section (un appel LLM, sortie JSON contrainte).

Contrat :

    extraire(section: Section, client: LLMClient) -> tuple[list[Clause], ReponseLLM]

* Un appel ``client.completer(..., json_mode=True)`` par section. Le prompt
  utilisateur contient le texte de la section **puis** son intitulé.
* ``ErreurLLM`` levée par le fournisseur (transport, HTTP) est propagée : c'est
  un 503 pour l'appelant.
* Une réponse hors schéma ne fait jamais planter l'analyse : JSON illisible →
  aucune clause ; élément invalide (type inconnu, extrait vide, confiance hors
  [0, 1]) → ignoré. Le nombre d'éléments ignorés est rangé dans
  ``reponse.metadonnees["clauses_ignorees"]`` pour que l'appelant le signale.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.llm_client import TYPES_CLAUSES, ErreurLLM, LLMClient, ReponseLLM
from app.pipeline.confiance import Clause
from app.pipeline.decoupage import Section


class ClauseExtraite(BaseModel):
    """Un élément de la réponse du modèle, tel qu'on accepte de le lire."""

    model_config = ConfigDict(str_strip_whitespace=True)

    type: str
    extrait: str = Field(min_length=1)
    confiance: float = Field(ge=0.0, le=1.0)

    @field_validator("type")
    @classmethod
    def _type_connu(cls, valeur: str) -> str:
        normalise = valeur.lower()
        if normalise not in TYPES_CLAUSES:
            raise ValueError(f"type de clause inconnu : {valeur!r}")
        return normalise


def prompt_section(section: Section) -> str:
    # Le texte d'abord : l'intitulé n'est qu'un repère, jamais une source d'extrait.
    return f"{section.texte}\n\n---\nSection analysée : {section.titre}"


def extraire(section: Section, client: LLMClient) -> tuple[list[Clause], ReponseLLM]:
    reponse = client.completer(prompt_section(section), json_mode=True)
    elements, invalide = _elements(reponse)
    ignorees = 1 if invalide else 0
    clauses: list[Clause] = []
    for element in elements:
        try:
            valide = ClauseExtraite.model_validate(element)
        except ValidationError:
            ignorees += 1
            continue
        clauses.append(
            Clause(
                type=valide.type,
                extrait=valide.extrait,
                confiance_llm=valide.confiance,
                sections=[section.indice],
            )
        )
    reponse.metadonnees["clauses_ignorees"] = ignorees
    reponse.metadonnees["reponse_invalide"] = invalide
    return clauses, reponse


def _elements(reponse: ReponseLLM) -> tuple[list[Any], bool]:
    """Les éléments de ``{"clauses": [...]}`` (ou d'une liste nue) ; ``True`` si illisible."""
    try:
        donnees = reponse.json()
    except ErreurLLM:
        return [], True
    if isinstance(donnees, dict):
        donnees = donnees.get("clauses")
    if not isinstance(donnees, list):
        return [], True
    return donnees, False
