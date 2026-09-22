"""Tests unitaires — seuils du gate (eval/seuils.yaml)."""
from __future__ import annotations

from pathlib import Path

import pytest

from eval.run_eval import ErreurSeuils, Seuils, charger_seuils

VALIDE = "note_min: 0.8\nlatence_p95_max_ms: 5000\ncout_moyen_max_eur: 0.1\nmotif: essai\n"


def _ecrire(tmp_path: Path, contenu: str) -> Path:
    chemin = tmp_path / "seuils.yaml"
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


def test_seuils_du_repo():
    seuils = charger_seuils()
    assert (seuils.note_min, seuils.latence_p95_max_ms, seuils.cout_moyen_max_eur) == (
        0.75,
        8000.0,
        0.15,
    )
    assert seuils.motif


def test_fichier_valide(tmp_path):
    seuils = charger_seuils(_ecrire(tmp_path, VALIDE))
    assert seuils == Seuils(0.8, 5000.0, 0.1, "essai")


def test_variable_d_environnement(tmp_path, monkeypatch):
    monkeypatch.setenv("SEUILS_PATH", str(_ecrire(tmp_path, VALIDE)))
    assert charger_seuils().note_min == 0.8


def test_cle_manquante(tmp_path):
    with pytest.raises(ErreurSeuils, match="cout_moyen_max_eur"):
        charger_seuils(_ecrire(tmp_path, "note_min: 0.8\nlatence_p95_max_ms: 5000\n"))


@pytest.mark.parametrize("valeur", ["'beaucoup'", "true", "[1]"])
def test_valeur_non_numerique(tmp_path, valeur):
    contenu = f"note_min: {valeur}\nlatence_p95_max_ms: 1\ncout_moyen_max_eur: 1\n"
    with pytest.raises(ErreurSeuils, match="note_min"):
        charger_seuils(_ecrire(tmp_path, contenu))


def test_fichier_absent(tmp_path):
    with pytest.raises(ErreurSeuils, match="introuvable"):
        charger_seuils(tmp_path / "absent.yaml")


@pytest.mark.parametrize("contenu", ["note_min: [", "- 0.75\n"])
def test_yaml_invalide_ou_pas_un_dictionnaire(tmp_path, contenu):
    with pytest.raises(ErreurSeuils):
        charger_seuils(_ecrire(tmp_path, contenu))


def test_erreur_seuils_est_une_value_error():
    assert issubclass(ErreurSeuils, ValueError)


def test_surcharge_ne_remplace_que_les_valeurs_fournies():
    seuils = Seuils(0.75, 8000.0, 0.15, "m")
    surcharge = seuils.surcharger(note_min=0.9, latence_p95_max_ms=None, cout_moyen_max_eur=0)
    assert surcharge == Seuils(0.9, 8000.0, 0.0, "m")
    assert seuils.note_min == 0.75
    assert surcharge.to_dict() == {
        "note_min": 0.9,
        "latence_p95_max_ms": 8000.0,
        "cout_moyen_max_eur": 0.0,
        "motif": "m",
    }
