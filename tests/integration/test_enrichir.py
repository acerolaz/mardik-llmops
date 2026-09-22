"""Versement humain d'un cas capturé dans le jeu d'évaluation (C2.7, B2.4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.enrichir import ErreurEnrichissement, main, prochain_id, verser

RACINE = Path(__file__).resolve().parents[2]


@pytest.fixture
def attendus(tmp_path) -> Path:
    lignes = (RACINE / "eval" / "attendus.jsonl").read_text(encoding="utf-8").splitlines()[:4]
    chemin = tmp_path / "attendus.jsonl"
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return chemin


@pytest.fixture
def candidats(tmp_path) -> Path:
    chemin = tmp_path / "candidats.jsonl"
    ligne = {"type": "candidat", "id": "cand-0123456789", "date": "2026-09-22T10:00:00+00:00",
             "version": "v2.0.0", "score": 0.48, "empreinte": "e",
             "texte": "Article 1 — Durée. Le contrat est conclu pour une durée de 12 mois. "
                      "Article 2 — Résiliation. Chaque partie peut résilier le contrat.",
             "clauses_trouvees": ["durée"]}
    chemin.write_text(json.dumps(ligne, ensure_ascii=False) + "\n", encoding="utf-8")
    return chemin


def _verser(candidats, contrats_courts, attendus, registry, **kw):
    return verser("cand-0123456789", kw.pop("clauses", ["durée", "résiliation"]),
                  candidats=candidats, contrats=contrats_courts, attendus=attendus,
                  registry=registry, **kw)


def test_prochain_id():
    assert prochain_id(["c01", "c09", "c12"]) == "c13"
    assert prochain_id([]) == "c01"


def test_verser_cree_le_contrat_annote(candidats, contrats_courts, attendus, registry):
    ligne = _verser(candidats, contrats_courts, attendus, registry)
    assert ligne["contrat_id"] == "c05" and ligne["origine"] == "production"
    assert ligne["clauses_attendues"] == ["durée", "résiliation"]
    assert (contrats_courts / "c05.txt").read_text(encoding="utf-8").startswith("Article 1")
    derniere = json.loads(attendus.read_text(encoding="utf-8").splitlines()[-1])
    assert derniere == ligne
    verse = json.loads(candidats.read_text(encoding="utf-8").splitlines()[-1])
    assert verse["type"] == "verse" and verse["contrat_id"] == "c05"
    entree = registry.journal()[-1]
    assert entree["evenement"] == "enrichissement" and entree["origine"] == "manuel"
    assert entree["contrat_id"] == "c05" and "c05" in entree["resume"]


@pytest.mark.parametrize(
    "identifiant, clauses, message",
    [
        ("cand-inconnu", ["durée"], "inconnu"),
        ("cand-0123456789", [], "au moins une clause"),
        ("cand-0123456789", ["durée", "clause magique"], "clause magique"),
    ],
)
def test_refus_sans_ecriture(candidats, contrats_courts, attendus, registry,
                             identifiant, clauses, message):
    avant = attendus.read_text(encoding="utf-8")
    with pytest.raises(ErreurEnrichissement, match=message):
        verser(identifiant, clauses, candidats=candidats, contrats=contrats_courts,
               attendus=attendus, registry=registry)
    assert attendus.read_text(encoding="utf-8") == avant
    assert not (contrats_courts / "c05.txt").exists()


def test_deja_verse(candidats, contrats_courts, attendus, registry):
    _verser(candidats, contrats_courts, attendus, registry)
    with pytest.raises(ErreurEnrichissement, match="déjà versé"):
        _verser(candidats, contrats_courts, attendus, registry)


def test_le_gate_rejoue_le_contrat_verse(candidats, contrats_courts, attendus, registry,
                                         historique):
    from eval.run_eval import evaluer

    _verser(candidats, contrats_courts, attendus, registry)
    rapport = evaluer("v2", contrats=contrats_courts, attendus=attendus, historique=historique)
    assert "c05" in rapport.par_contrat


def test_echec_d_attendus_ne_laisse_pas_de_contrat_orphelin(
    candidats, contrats_courts, attendus, registry, monkeypatch
):
    """Un versement interrompu ne doit pas bloquer les versements suivants."""
    from eval import enrichir

    def _echec(chemin, data):
        raise OSError("disque plein")

    monkeypatch.setattr(enrichir, "_ajouter_ligne", _echec)
    with pytest.raises(OSError):
        _verser(candidats, contrats_courts, attendus, registry)
    assert not (contrats_courts / "c05.txt").exists()
    assert not list(contrats_courts.glob("*.tmp"))

    monkeypatch.undo()
    ligne = _verser(candidats, contrats_courts, attendus, registry)
    assert ligne["contrat_id"] == "c05"
    assert (contrats_courts / "c05.txt").exists()


def test_cli(candidats, capsys, monkeypatch):
    monkeypatch.setenv("CANDIDATS_PATH", str(candidats))
    assert main(["lister"]) == 0
    assert "cand-0123456789" in capsys.readouterr().out
    assert main(["verser", "cand-0123456789", "--clauses", ""]) == 1
    assert "REFUSÉ" in capsys.readouterr().err
