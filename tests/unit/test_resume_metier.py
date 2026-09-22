"""Résumés en langage métier du journal de pilotage (B2.5)."""
from __future__ import annotations

import pytest

from ops.pilotage import resume_metier


def test_rollback_sur_score():
    assert resume_metier("rollback", version="v2.0.0", signal="score_moyen", valeur=0.52,
                         seuil=0.70, mesures=27, sous_seuil=15) == (
        "Version v2.0.0 retirée : 15 analyses sur 27 jugées peu fiables (score < 0,70).")


def test_rollback_sur_erreurs_et_latence():
    assert resume_metier("rollback", version="v2.0.0", signal="taux_erreur", valeur=0.12,
                         seuil=0.10, mesures=25) == (
        "Version v2.0.0 retirée : 12 % des analyses en échec (maximum toléré 10 %).")
    assert resume_metier("rollback", version="v2.0.0", signal="latence_p95_ms", valeur=9100,
                         seuil=8000, mesures=25) == (
        "Version v2.0.0 retirée : analyses trop lentes (P95 9,1 s, maximum 8,0 s).")


def test_canary_promotion_alerte():
    assert resume_metier("canary", version="v2.0.0", pourcentage=50, requetes=23,
                         depuis_s=64.2) == (
        "Version v2.0.0 étendue à 50 % des clients : 23 analyses conformes en 64 s.")
    assert resume_metier("promotion", version="v2.0.0") == (
        "Version v2.0.0 servie à tous les clients : tous les critères tenus.")
    assert resume_metier("alerte", version="v2.0.0", valeur=0.72, seuil=0.70) == (
        "Version v2.0.0 à surveiller : score moyen 0,72, proche du seuil 0,70.")


def test_refus_enrichissement():
    assert resume_metier("pilotage_refus", action="rollback", raison="rien à annuler") == (
        "Action automatique « rollback » impossible : rien à annuler.")
    assert resume_metier("enrichissement", contrat_id="c13", score=0.48) == (
        "Contrat c13 ajouté au jeu d'évaluation (score en production 0,48).")


def test_seuils():
    assert resume_metier("seuils", fichier="ops/seuils_pilotage.yaml", avant=None,
                         apres={"motif": "m"}, motif="m") == (
        "Seuils en vigueur (ops/seuils_pilotage.yaml) : m.")
    assert resume_metier(
        "seuils", fichier="ops/seuils_pilotage.yaml",
        avant={"derive": {"score_min": 0.7}, "motif": "a"},
        apres={"derive": {"score_min": 0.68}, "motif": "b"}, motif="b",
    ) == "Seuils modifiés (ops/seuils_pilotage.yaml) : derive.score_min 0,70 → 0,68 (motif : b)."
    assert resume_metier("seuils_invalides", fichier="f.yaml", raison="YAML invalide") == (
        "Seuils illisibles (f.yaml) : derniers seuils valides conservés — YAML invalide.")


def test_evenement_inconnu():
    with pytest.raises(ValueError, match="sans gabarit"):
        resume_metier("inconnu")
