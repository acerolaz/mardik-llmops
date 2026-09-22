"""Tests unitaires — notation du gate (fonctions pures)."""
from __future__ import annotations

import pytest

from eval.run_eval import Seuils, agreger, combiner_essais, noter_contrat

COURT = {
    "contrat_id": "c02",
    "clauses_attendues": [
        "durée",
        "prix et paiement",
        "pénalité de retard",
        "résiliation",
        "garantie",
    ],
    "seuil_note": 0.75,
}
LONG = {
    "contrat_id": "c07",
    "clauses_attendues": ["durée", "résiliation", "garantie", "confidentialité"],
    "seuil_note": 0.80,
}
SEUILS = Seuils(note_min=0.75, latence_p95_max_ms=8000.0, cout_moyen_max_eur=0.15)


def _contrat(note: float, seuil: float = 0.75) -> dict:
    return {"note": note, "seuil_note": seuil, "passe": note >= seuil, "trouvees": [],
            "manquantes": []}


def test_tout_trouve():
    assert noter_contrat(COURT["clauses_attendues"], COURT) == {
        "note": 1.0,
        "seuil_note": 0.75,
        "passe": True,
        "trouvees": COURT["clauses_attendues"],
        "manquantes": [],
    }


def test_rappel_partiel_dans_l_ordre_attendu_et_types_en_trop_ignores():
    resultat = noter_contrat(["garantie", "durée", "résiliation", "exclusivité"], COURT)
    assert resultat["note"] == 0.6
    assert resultat["trouvees"] == ["durée", "résiliation", "garantie"]
    assert resultat["manquantes"] == ["prix et paiement", "pénalité de retard"]
    assert resultat["passe"] is False


def test_seuil_note_des_contrats_longs():
    trois_sur_quatre = noter_contrat(["durée", "résiliation", "garantie"], LONG)
    assert trois_sur_quatre["note"] == 0.75
    assert trois_sur_quatre["passe"] is False  # 0,75 < 0,80
    assert noter_contrat(LONG["clauses_attendues"], LONG)["passe"] is True


def test_combiner_essais_fait_la_moyenne():
    premier = {**noter_contrat(["durée", "résiliation", "garantie"], LONG),
               "latence_ms": 100.0, "cout_eur": 0.01}
    second = {**noter_contrat(LONG["clauses_attendues"], LONG),
              "latence_ms": 300.0, "cout_eur": 0.03}
    combine = combiner_essais([premier, second])
    assert combine["note"] == pytest.approx(0.875)
    assert combine["passe"] is True
    assert combine["manquantes"] == []  # dernier essai
    assert combine["latence_ms"] == pytest.approx(200.0)
    assert combine["cout_eur"] == pytest.approx(0.02)


def test_agreger_sans_motif():
    note, p95, cout, motifs = agreger(
        {"c01": _contrat(1.0), "c02": _contrat(0.8)}, [100.0, 200.0], [0.01, 0.03], SEUILS
    )
    assert note == pytest.approx(0.9)
    assert p95 == 200.0
    assert cout == pytest.approx(0.02)
    assert motifs == []


def test_agreger_note_globale_sous_le_seuil():
    _, _, _, motifs = agreger(
        {"c01": _contrat(0.6, 0.5), "c02": _contrat(0.7, 0.5)}, [1.0], [0.0], SEUILS
    )
    assert motifs == ["note 0.650 < seuil 0.75"]


def test_agreger_contrat_sous_son_seuil():
    _, _, _, motifs = agreger(
        {"c01": _contrat(1.0), "c10": _contrat(0.75, 0.80)}, [1.0], [0.0], SEUILS
    )
    assert motifs == ["c10 : note 0.75 < seuil_note 0.80"]


def test_agreger_latence_et_cout():
    _, _, _, motifs = agreger({"c01": _contrat(1.0)}, [9120.0], [0.18], SEUILS)
    assert motifs == ["latence P95 9120 ms ≥ 8000 ms", "coût moyen 0.1800 € ≥ 0.15 €"]


def test_agreger_erreurs_en_tete():
    _, _, _, motifs = agreger(
        {"c01": _contrat(1.0)}, [1.0], [0.0], SEUILS, erreurs=["c03 : erreur LLM — timeout"]
    )
    assert motifs == ["c03 : erreur LLM — timeout"]


def test_agreger_sans_mesure():
    note, p95, cout, _ = agreger({"c01": _contrat(0.0)}, [], [], SEUILS)
    assert (note, p95, cout) == (0.0, 0.0, 0.0)
