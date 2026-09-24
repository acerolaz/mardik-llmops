"""README, « Règles du jeu » : ``app/api_v1.py`` est intouchable.

Empreinte du fichier tel que livré sur ``main`` (dépôt de départ). Toute
modification, même d'une ligne, fait échouer ce test : l'instrumentation
de la v1 se branche depuis ``app/main.py``, jamais dans ce fichier.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

EMPREINTE_MAIN = "ef75c1202f52a3a56b7a44fe63540e0cf67d93d2adbe1fb5daf9ea66e0ee7065"


def test_api_v1_identique_a_main():
    contenu = (Path(__file__).parents[2] / "app" / "api_v1.py").read_bytes()
    assert hashlib.sha256(contenu).hexdigest() == EMPREINTE_MAIN
