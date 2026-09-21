"""Tests unitaires — le bundle v2 est complet et cohérent avec le vocabulaire partagé."""
from __future__ import annotations

from app.llm_client import TYPES_CLAUSES, Bundle


def test_bundle_v2_declare_la_strategie_et_les_limites():
    bundle = Bundle.charger("v2")
    assert bundle.version == "v2.0.0"
    assert bundle.strategie == "map_reduce_clauses"
    p = bundle.parametres
    assert p["taille_max_document"] == 200000 and p["sections_max"] == 40
    assert p["contexte_max_caracteres"] == 6000 and p["parallelisme"] == 8
    assert p["seuil_relecture"] == 0.6 and p["temperature"] == 0.0 and p["seed"] == 42


def test_schema_et_prompt_imposent_les_libelles_partages():
    bundle = Bundle.charger("v2")
    items = bundle.schema_sortie["properties"]["clauses"]["items"]
    assert items["properties"]["type"]["enum"] == list(TYPES_CLAUSES)
    assert set(items["required"]) == {"type", "extrait", "confiance"}
    for libelle in TYPES_CLAUSES:
        assert libelle in bundle.prompt
