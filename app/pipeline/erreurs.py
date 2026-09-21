"""Erreurs métier du pipeline v2."""
from __future__ import annotations


class DocumentTropLong(ValueError):
    """Le document dépasse une limite absolue du bundle : refus explicite (413),
    jamais de troncature silencieuse."""
