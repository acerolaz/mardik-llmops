"""Pipeline v2 : découpage → extraction (map) → consolidation (reduce) → confiance."""
from .confiance import Clause, scorer  # noqa: F401
from .consolidation import consolider  # noqa: F401
from .decoupage import Section, decouper  # noqa: F401
from .erreurs import DocumentTropLong  # noqa: F401
from .extraction import extraire  # noqa: F401
