"""Tests unitaires — fonction pure de routage canary."""
from __future__ import annotations

import pytest

from app.routage import choisir_version


def test_sans_canary_toujours_l_active():
    assert {choisir_version("v1.0.0", None, 30, t) for t in range(100)} == {"v1.0.0"}


@pytest.mark.parametrize(("pourcentage", "attendus"), [(0, 0), (30, 30), (99, 99)])
def test_le_canary_recoit_exactement_son_pourcentage(pourcentage, attendus):
    tirages = [choisir_version("v1.0.0", "v2.0.0", pourcentage, t) for t in range(100)]
    assert tirages.count("v2.0.0") == attendus


def test_borne_tirage_egal_au_pourcentage_va_a_l_active():
    assert choisir_version("v1.0.0", "v2.0.0", 30, 30.0) == "v1.0.0"
    assert choisir_version("v1.0.0", "v2.0.0", 30, 29.999) == "v2.0.0"
