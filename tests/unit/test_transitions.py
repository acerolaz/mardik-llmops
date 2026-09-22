"""Tests unitaires — registre (écriture atomique) et transitions de déploiement."""
from __future__ import annotations

import pytest


def test_ecriture_atomique_de_l_index(registry, monkeypatch):
    avant = registry.index()

    def remplacement_en_echec(*args, **kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr("ops.registry.os.replace", remplacement_en_echec)
    with pytest.raises(OSError, match="disque plein"):
        registry.ecrire_index({**avant, "active": "v9.9.9"})
    assert registry.index() == avant


def test_ecriture_ne_laisse_pas_de_fichier_temporaire(registry):
    registry.ecrire_index({**registry.index(), "canary": None})
    assert not (registry.root / "index.json.tmp").exists()
