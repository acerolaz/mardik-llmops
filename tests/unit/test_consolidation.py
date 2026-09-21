"""Tests unitaires — consolidation des clauses (reduce)."""
from __future__ import annotations

from app.pipeline.confiance import Clause
from app.pipeline.consolidation import consolider


def test_fusionne_les_clauses_de_meme_type():
    resultat = consolider(
        [
            [Clause("résiliation", "court", 0.6, [0])],
            [Clause("résiliation", "moyen extrait", 0.7, [1])],
            [Clause("résiliation", "extrait bien plus long", 0.9, [2])],
        ]
    )
    assert len(resultat) == 1
    clause = resultat[0]
    assert clause.extrait == "extrait bien plus long"
    assert clause.confiance_llm == 0.9
    assert clause.sections == [0, 1, 2]


def test_ordre_de_premiere_apparition():
    resultat = consolider(
        [
            [Clause("durée", "a", 0.8, [0]), Clause("prix et paiement", "b", 0.8, [0])],
            [Clause("résiliation", "c", 0.8, [1]), Clause("durée", "d", 0.8, [1])],
        ]
    )
    assert [c.type for c in resultat] == ["durée", "prix et paiement", "résiliation"]


def test_jamais_deux_clauses_de_meme_type():
    resultat = consolider([[Clause("durée", "a", 0.5, [i])] for i in range(5)])
    assert [c.type for c in resultat] == ["durée"]
    assert resultat[0].sections == [0, 1, 2, 3, 4]


def test_entree_vide():
    assert consolider([]) == []
    assert consolider([[], []]) == []


def test_ne_mute_pas_les_entrees():
    a = Clause("durée", "a", 0.5, [0])
    b = Clause("durée", "plus long", 0.9, [1])
    consolider([[a], [b]])
    assert a == Clause("durée", "a", 0.5, [0])
    assert b == Clause("durée", "plus long", 0.9, [1])
