"""Capture des analyses v2 à faible confiance, en tâche de fond."""
from __future__ import annotations

import json

from app.capture import Capture, candidats_en_attente, get_capture


def _brancher(client, capture: Capture) -> None:
    client.app.dependency_overrides[get_capture] = lambda: capture


def _lignes(chemin):
    return [json.loads(x) for x in chemin.read_text(encoding="utf-8").splitlines()]


def test_score_bas_capture_un_candidat_anonymise(client, contrat, tmp_path):
    chemin = tmp_path / "cand.jsonl"
    _brancher(client, Capture(chemin, score_max=1.01))   # tout est « bas »
    texte = contrat("c02") + "\nContact : juriste@client.fr"
    r = client.post("/v2/analyse", json={"texte": texte})
    assert r.status_code == 200
    [ligne] = _lignes(chemin)
    assert ligne["type"] == "candidat" and ligne["id"].startswith("cand-")
    assert ligne["version"] == r.json()["version"]
    assert ligne["score"] == r.json()["confiance_globale"]
    assert "[EMAIL]" in ligne["texte"] and "juriste@client.fr" not in ligne["texte"]
    assert ligne["clauses_trouvees"] == sorted({c["type"] for c in r.json()["clauses"]})
    assert [c["id"] for c in candidats_en_attente(chemin)] == [ligne["id"]]


def test_score_haut_ne_capture_rien(client, contrat, tmp_path):
    chemin = tmp_path / "cand.jsonl"
    _brancher(client, Capture(chemin, score_max=0.0))
    assert client.post("/v2/analyse", json={"texte": contrat("c02")}).status_code == 200
    assert not chemin.exists()


def test_meme_texte_capture_une_seule_fois(client, contrat, tmp_path):
    chemin = tmp_path / "cand.jsonl"
    _brancher(client, Capture(chemin, score_max=1.01))
    for _ in range(2):
        client.post("/v2/analyse", json={"texte": contrat("c02")})
    assert len(_lignes(chemin)) == 1


def test_echec_de_capture_sans_effet_sur_le_client(client, contrat, tmp_path):
    _brancher(client, Capture(tmp_path, score_max=1.01))   # un dossier : OSError
    r = client.post("/v2/analyse", json={"texte": contrat("c02")})
    assert r.status_code == 200 and "confiance_globale" in r.json()


def test_seuil_lu_dans_les_seuils_de_pilotage(client, contrat, tmp_path, monkeypatch):
    chemin = tmp_path / "cand.jsonl"
    seuils = tmp_path / "seuils.yaml"
    from ops.seuils import CHEMIN_SEUILS_PILOTAGE_DEFAUT
    seuils.write_text(
        CHEMIN_SEUILS_PILOTAGE_DEFAUT.read_text(encoding="utf-8").replace(
            "score_max: 0.70", "score_max: 1.01"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PILOTAGE_SEUILS_PATH", str(seuils))
    _brancher(client, Capture(chemin))            # score_max=None → lu dans le fichier
    client.post("/v2/analyse", json={"texte": contrat("c02")})
    assert len(_lignes(chemin)) == 1


def test_le_gate_ne_capture_rien(tmp_path, monkeypatch, historique):
    from eval.run_eval import evaluer

    chemin = tmp_path / "cand.jsonl"
    monkeypatch.setenv("CANDIDATS_PATH", str(chemin))
    evaluer("v2", sous_ensemble=["c01"], historique=historique)
    assert not chemin.exists()
