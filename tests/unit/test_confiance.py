"""Tests unitaires — score de confiance composite."""
from __future__ import annotations

import pytest

from app.pipeline.confiance import Clause, ancrage, scorer

CONTRAT = (
    "Article 12 — Résiliation\n"
    "L'une ou l'autre des parties peut résilier le contrat par lettre recommandée "
    "avec un préavis de trois mois.\n"
)


def test_extrait_exact_ancrage_complet():
    assert ancrage("peut résilier le contrat par lettre recommandée", CONTRAT) == 1.0


def test_apostrophes_et_blancs_normalises():
    assert ancrage("L'une ou  l'autre des parties\npeut résilier", CONTRAT) == 1.0


def test_extrait_court():
    assert ancrage("Résiliation", CONTRAT) == 1.0
    assert ancrage("Exclusivité", CONTRAT) == 0.0
    assert ancrage("", CONTRAT) == 0.0


def test_citation_inventee_score_bas_meme_si_le_modele_est_sur():
    inventee = Clause(
        "résiliation", "Le client peut rompre sans préavis à tout moment et sans frais", 0.95, [0]
    )
    [notee], _ = scorer([inventee], CONTRAT)
    assert notee.confiance < 0.1


def test_recurrence_augmente_le_score():
    [une], _ = scorer([Clause("résiliation", "peut résilier le contrat", 0.8, [0])], CONTRAT)
    [deux], _ = scorer([Clause("résiliation", "peut résilier le contrat", 0.8, [0, 3])], CONTRAT)
    assert une.confiance == pytest.approx(0.7 * 0.8 + 0.3 * 0.5)
    assert deux.confiance == pytest.approx(0.7 * 0.8 + 0.3 * 1.0)


def test_score_borne_a_un():
    [c], globale = scorer([Clause("résiliation", "peut résilier le contrat", 1.0, [0, 1])], CONTRAT)
    assert c.confiance == pytest.approx(1.0)
    assert 0.0 <= globale <= 1.0


def test_score_global_est_la_moyenne():
    clauses = [
        Clause("résiliation", "peut résilier le contrat", 0.8, [0]),
        Clause("exclusivité", "texte absent du contrat ici", 0.9, [0]),
    ]
    notees, globale = scorer(clauses, CONTRAT)
    assert notees[1].confiance == 0.0
    assert globale == pytest.approx((notees[0].confiance + notees[1].confiance) / 2)


def test_aucune_clause():
    assert scorer([], CONTRAT) == ([], 0.0)


def test_ne_mute_pas_les_clauses_recues():
    clause = Clause("résiliation", "peut résilier le contrat", 0.8, [0])
    scorer([clause], CONTRAT)
    assert clause.confiance == 0.0
