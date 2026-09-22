"""Anonymisation des cas capturés avant tout stockage (B2.6)."""
from __future__ import annotations

import pytest

from app.capture import anonymiser


@pytest.mark.parametrize(
    "texte, attendu",
    [
        ("Contact : jean.dupont@acme-conseil.fr.", "Contact : [EMAIL]."),
        ("IBAN FR76 3000 6000 0112 3456 7890 189 ;", "IBAN [IBAN] ;"),
        ("Tél. 01 23 45 67 89 ou +33 6 12 34 56 78.", "Tél. [TELEPHONE] ou [TELEPHONE]."),
        ("SIRET 732 829 320 00074, SIREN 732829320.", "SIRET [SIRET], SIREN [SIRET]."),
        ("représentée par M. Jean Dupont, gérant", "représentée par [PERSONNE], gérant"),
        ("et Mme Claire Martin-Durand.", "et [PERSONNE]."),
        ("Maître : Me Lefèvre.", "Maître : [PERSONNE]."),
    ],
)
def test_motifs_masques(texte, attendu):
    assert anonymiser(texte) == attendu


def test_clauses_juridiques_intactes():
    texte = (
        "Article 3 — Durée. Le présent contrat est conclu pour une durée de 12 mois. "
        "Article 4 — Prix. Le prix est de 15 000 € HT, payable à 30 jours. "
        "Article 9 — Droit applicable. Le droit français est seul applicable."
    )
    assert anonymiser(texte) == texte
