"""Le verrou d'écriture ne doit être relâché qu'une fois la ligne sur le disque."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path

from app import capture


def test_le_fichier_est_complet_avant_le_deverrouillage(tmp_path: Path, monkeypatch):
    chemin = tmp_path / "candidats.jsonl"
    tailles: list[int] = []
    vrai_flock = fcntl.flock

    def espion(fichier, operation):
        if operation == fcntl.LOCK_UN:
            tailles.append(chemin.stat().st_size)
        return vrai_flock(fichier, operation)

    monkeypatch.setattr(capture.fcntl, "flock", espion)
    # Plus courte que le tampon texte : sans vidage explicite, elle n'atteint le disque qu'à la fermeture,
    # donc après le déverrouillage (taille mesurée : 0).
    ligne = json.dumps({"texte": "x" * 200}, ensure_ascii=False) + "\n"
    with capture._verrouille(chemin) as fichier:
        fichier.write(ligne)
    assert tailles == [len(ligne.encode("utf-8"))]
