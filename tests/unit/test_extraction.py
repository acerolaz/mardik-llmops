"""Tests unitaires — extraction des clauses d'une section."""
from __future__ import annotations

import json

import pytest

from app.llm_client import ErreurLLM
from app.pipeline.confiance import Clause
from app.pipeline.decoupage import Section
from app.pipeline.extraction import extraire
from tests.unit.doublures import FauxClient

SECTION = Section(
    indice=4,
    titre="Article 12 — Résiliation",
    texte="Article 12 — Résiliation\nLe contrat peut être résilié par chaque partie.",
)


def _json(*elements) -> str:
    return json.dumps({"clauses": list(elements)}, ensure_ascii=False)


def _clause(type_="résiliation", extrait="Le contrat peut être résilié.", confiance=0.9):
    return {"type": type_, "extrait": extrait, "confiance": confiance}


def test_extrait_les_clauses_valides():
    client = FauxClient(repondre=lambda _: _json(_clause()))
    clauses, reponse = extraire(SECTION, client)
    assert clauses == [Clause("résiliation", "Le contrat peut être résilié.", 0.9, [4])]
    assert reponse.metadonnees["clauses_ignorees"] == 0
    assert reponse.metadonnees["reponse_invalide"] is False


def test_prompt_texte_puis_titre_en_mode_json():
    client = FauxClient(repondre=lambda _: _json())
    extraire(SECTION, client)
    [prompt] = client.prompts
    assert prompt.startswith(SECTION.texte)
    assert prompt.rstrip().endswith(SECTION.titre)
    assert client.json_modes == [True]


def test_type_normalise():
    client = FauxClient(repondre=lambda _: _json(_clause(type_="  Résiliation ")))
    clauses, _ = extraire(SECTION, client)
    assert [c.type for c in clauses] == ["résiliation"]


def test_elements_invalides_ignores_et_comptes():
    client = FauxClient(
        repondre=lambda _: _json(
            _clause(),
            _clause(type_="clause inventée"),
            _clause(type_="durée", extrait="   "),
            _clause(type_="durée", confiance=1.4),
            "pas un objet",
        )
    )
    clauses, reponse = extraire(SECTION, client)
    assert [c.type for c in clauses] == ["résiliation"]
    assert reponse.metadonnees["clauses_ignorees"] == 4


def test_forme_liste_acceptee():
    client = FauxClient(repondre=lambda _: json.dumps([_clause()], ensure_ascii=False))
    clauses, _ = extraire(SECTION, client)
    assert [c.type for c in clauses] == ["résiliation"]


@pytest.mark.parametrize("texte", ["pas du json du tout", '{"autre": 1}'])
def test_reponse_invalide_ne_plante_pas(texte):
    client = FauxClient(repondre=lambda _: texte)
    clauses, reponse = extraire(SECTION, client)
    assert clauses == []
    assert reponse.metadonnees["clauses_ignorees"] == 1
    assert reponse.metadonnees["reponse_invalide"] is True


def test_erreur_fournisseur_propagee():
    client = FauxClient(erreur=ErreurLLM("fournisseur ollama injoignable"))
    with pytest.raises(ErreurLLM):
        extraire(SECTION, client)
