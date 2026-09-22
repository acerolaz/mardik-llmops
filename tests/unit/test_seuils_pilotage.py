"""Seuils de pilotage : chargement strict, variable d'environnement, empreinte."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ops.seuils import (
    CHEMIN_SEUILS_PILOTAGE_DEFAUT,
    ErreurSeuilsPilotage,
    charger_seuils_pilotage,
    empreinte,
    lire_brut,
)


def _base() -> dict:
    return yaml.safe_load(CHEMIN_SEUILS_PILOTAGE_DEFAUT.read_text(encoding="utf-8"))


def _ecrire(tmp_path: Path, data: dict) -> Path:
    chemin = tmp_path / "seuils_pilotage.yaml"
    chemin.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return chemin


def test_fichier_du_depot_charge():
    s = charger_seuils_pilotage()
    assert s.fenetre_s == 300 and s.minimum == 10 and s.intervalle_s == 5
    assert s.derive.score_min == 0.70 and s.derive.marge == 0.05
    assert s.derive.taux_erreur_max == 0.10 and s.derive.latence_p95_max_ms == 8000
    assert s.promotion.paliers == (10, 50, 100)
    assert s.promotion.duree_min_s == 60 and s.promotion.requetes_min == 20
    assert s.promotion.score_moyen_min == 0.75 and s.promotion.score_p10_min == 0.65
    assert s.capture.score_max == 0.70
    assert s.motif
    assert s.to_dict()["promotion"]["paliers"] == [10, 50, 100]


def test_variable_environnement(tmp_path, monkeypatch):
    data = _base()
    data["fenetre_s"] = 60
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(_ecrire(tmp_path, data)))
    assert charger_seuils_pilotage().fenetre_s == 60


def _sans(data: dict, section: str, cle: str) -> dict:
    del data[section][cle]
    return data


@pytest.mark.parametrize(
    "modifier, attendu",
    [
        (lambda d: _sans(d, "derive", "score_min"), "derive.score_min"),
        (lambda d: {**d, "minimum": "dix"}, "minimum"),
        (lambda d: {**d, "derive": {**d["derive"], "marge": True}}, "derive.marge"),
        (lambda d: {**d, "promotion": {**d["promotion"], "paliers": [50, 10, 100]}}, "paliers"),
        (lambda d: {**d, "promotion": {**d["promotion"], "paliers": [10, 50]}}, "paliers"),
        (lambda d: {k: v for k, v in d.items() if k != "capture"}, "capture"),
        (lambda d: {**d, "motif": "  "}, "motif"),
    ],
)
def test_fichier_incoherent_refuse(tmp_path, modifier, attendu):
    chemin = _ecrire(tmp_path, modifier(_base()))
    with pytest.raises(ErreurSeuilsPilotage, match=attendu):
        charger_seuils_pilotage(chemin)


def test_fichier_absent_ou_yaml_invalide(tmp_path):
    with pytest.raises(ErreurSeuilsPilotage, match="introuvable"):
        charger_seuils_pilotage(tmp_path / "absent.yaml")
    casse = tmp_path / "casse.yaml"
    casse.write_text("derive: [", encoding="utf-8")
    with pytest.raises(ErreurSeuilsPilotage, match="YAML invalide"):
        charger_seuils_pilotage(casse)
    with pytest.raises(ErreurSeuilsPilotage, match="introuvable"):
        lire_brut(tmp_path / "absent.yaml")


def test_empreinte_stable_et_sensible(tmp_path):
    chemin = _ecrire(tmp_path, _base())
    e1 = empreinte(chemin)
    assert len(e1) == 12 and e1 == empreinte(chemin)
    data = _base()
    data["derive"]["score_min"] = 0.68
    _ecrire(tmp_path, data)
    assert empreinte(chemin) != e1
