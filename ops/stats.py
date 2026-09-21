from __future__ import annotations

import math


def p95(valeurs: list[float]) -> float:
    if not valeurs:
        return 0.0
    tri = sorted(valeurs)
    index = max(0, math.ceil(0.95 * len(tri)) - 1)
    return tri[index]
