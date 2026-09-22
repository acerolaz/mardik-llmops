"""Décisions du pilote — fonctions pures, aucune écriture.

Chaque fonction reçoit des mesures, des seuils et des entrées de journal, et
renvoie une décision. ``ops/deploy.py`` exécute ces décisions (rollback,
progression, promotion) ; ``ops/dashboard.py`` les affiche.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.telemetry import Mesure
from ops.seuils import SeuilsDerive, SeuilsPilotage, empreinte, lire_brut
from ops.signaux import agreger, scores

NIVEAUX = ("aucune", "marge", "critique", "echantillon_insuffisant")


@dataclass(frozen=True)
class Constat:
    """Un critère évalué : le signal, sa valeur, le seuil, tenu ou non."""

    signal: str
    valeur: float | None
    seuil: float
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Derive:
    version: str
    mesures: int
    niveau: str
    constats: tuple[Constat, ...]
    motif: str
    sous_seuil: int = 0

    @property
    def critique(self) -> bool:
        return self.niveau == "critique"

    @property
    def principal(self) -> Constat | None:
        return next((c for c in self.constats if not c.ok), None)


def version_surveillee(index: dict[str, Any]) -> str | None:
    """Le canary s'il y en a un, sinon l'active."""
    return index.get("canary") or index.get("active")


def _motif_derive(c: Constat) -> str:
    if c.signal == "score_moyen":
        return f"score moyen {c.valeur:.2f} < {c.seuil:.2f}"
    if c.signal == "taux_erreur":
        return f"taux d'erreur {c.valeur:.0%} > {c.seuil:.0%}"
    return f"latence P95 {c.valeur:.0f} ms > {c.seuil:.0f} ms"


def detecter_derive(
    version: str, mesures: Sequence[Mesure], seuils: SeuilsDerive, *, minimum: int
) -> Derive:
    """Seuils durs → ``critique`` ; score dans la marge → ``marge``.

    Ordre des constats (et donc du motif) : score, erreurs, P95. Le score n'est
    pas évalué pour une version qui n'en produit pas (v1).
    """
    a = agreger(mesures)
    if a.requetes < minimum:
        return Derive(version, a.requetes, "echantillon_insuffisant", (),
                      f"échantillon insuffisant ({a.requetes}/{minimum})")
    constats: list[Constat] = []
    if a.score_moyen is not None:
        constats.append(Constat("score_moyen", a.score_moyen, seuils.score_min,
                                a.score_moyen >= seuils.score_min))
    constats.append(Constat("taux_erreur", a.taux_erreur, seuils.taux_erreur_max,
                            a.taux_erreur <= seuils.taux_erreur_max))
    if a.latence_p95_ms is not None:
        constats.append(Constat("latence_p95_ms", a.latence_p95_ms, seuils.latence_p95_max_ms,
                                a.latence_p95_ms <= seuils.latence_p95_max_ms))
    sous_seuil = sum(1 for s in scores(mesures) if s < seuils.score_min)
    echecs = [c for c in constats if not c.ok]
    if echecs:
        return Derive(version, a.requetes, "critique", tuple(constats),
                      _motif_derive(echecs[0]), sous_seuil)
    haut = seuils.score_min + seuils.marge
    if a.score_moyen is not None and a.score_moyen < haut:
        return Derive(
            version, a.requetes, "marge", tuple(constats),
            f"score moyen {a.score_moyen:.2f} dans la marge "
            f"[{seuils.score_min:.2f} ; {haut:.2f}[",
            sous_seuil,
        )
    return Derive(version, a.requetes, "aucune", tuple(constats), "", sous_seuil)


# ------------------------------------------------------------------- palier
@dataclass(frozen=True)
class DecisionPalier:
    version: str
    action: str                        # "attendre" | "progresser" | "promouvoir"
    pourcentage_suivant: int | None
    constats: tuple[Constat, ...]
    motif: str
    requetes: int
    depuis_s: float


def debut_palier(journal: list[dict[str, Any]], version: str) -> float | None:
    """``ts`` du dernier ``canary`` de ``version`` qu'aucun rollback ni aucune
    promotion n'a suivi (quelle que soit son ``origine``) ; sinon ``None``."""
    debut: float | None = None
    for entree in journal:
        evenement = entree.get("evenement")
        if evenement == "canary":
            debut = entree.get("ts") if entree.get("version") == version else None
        elif evenement in ("rollback", "promotion"):
            debut = None
    return debut


def _valeur(v: float | None) -> str:
    return "absente" if v is None else f"{v:g}"


def evaluer_palier(
    version: str,
    mesures_canary: Sequence[Mesure],
    mesures_active: Sequence[Mesure],
    seuils: SeuilsPilotage,
    *,
    depuis_s: float,
    pourcentage: int,
) -> DecisionPalier:
    """Le palier courant est-il tenu ? (C2.5, C2.17)

    Les erreurs du canary sont comparées à celles de l'active si elle a au moins
    ``seuils.minimum`` mesures, sinon au seuil absolu ``derive.taux_erreur_max``.
    Un canary sans score (ou sans latence mesurable) ne progresse jamais.
    """
    p = seuils.promotion
    c = agreger(mesures_canary)
    a = agreger(mesures_active)
    if depuis_s < p.duree_min_s or c.requetes < p.requetes_min:
        return DecisionPalier(
            version, "attendre", None, (),
            f"palier {pourcentage} % : {c.requetes}/{p.requetes_min} requêtes, "
            f"{depuis_s:.0f}/{p.duree_min_s:.0f} s",
            c.requetes, depuis_s,
        )
    if a.requetes >= seuils.minimum:
        ref_erreur = round(a.taux_erreur + p.ecart_erreur_max, 4)
    else:
        ref_erreur = seuils.derive.taux_erreur_max
    constats = (
        Constat("taux_erreur", c.taux_erreur, ref_erreur, c.taux_erreur <= ref_erreur),
        Constat("latence_p95_ms", c.latence_p95_ms, p.latence_p95_max_ms,
                c.latence_p95_ms is not None and c.latence_p95_ms < p.latence_p95_max_ms),
        Constat("score_moyen", c.score_moyen, p.score_moyen_min,
                c.score_moyen is not None and c.score_moyen >= p.score_moyen_min),
        Constat("score_p10", c.score_p10, p.score_p10_min,
                c.score_p10 is not None and c.score_p10 >= p.score_p10_min),
    )
    echec = next((x for x in constats if not x.ok), None)
    if echec is not None:
        return DecisionPalier(
            version, "attendre", None, constats,
            f"critère non tenu : {echec.signal} {_valeur(echec.valeur)} (seuil {echec.seuil:g})",
            c.requetes, depuis_s,
        )
    suivant = next((x for x in p.paliers if x > pourcentage), 100)
    return DecisionPalier(
        version, "promouvoir" if suivant == 100 else "progresser", suivant, constats,
        f"palier {pourcentage} % tenu : {c.requetes} analyses conformes en {depuis_s:.0f} s",
        c.requetes, depuis_s,
    )


# ------------------------------------------------------------------- seuils
def changements_seuils(
    journal: list[dict[str, Any]], fichiers: dict[str, Path]
) -> list[dict[str, Any]]:
    """Un événement par fichier dont l'empreinte diffère de sa dernière entrée
    ``seuils`` (état initial journalisé au premier passage : ``avant: None``).
    Lève ``ErreurSeuilsPilotage`` si un fichier est absent ou illisible."""
    evenements = []
    for nom, chemin in fichiers.items():
        courante = empreinte(chemin)
        derniere = next(
            (e for e in reversed(journal)
             if e.get("evenement") == "seuils" and e.get("fichier") == nom),
            None,
        )
        if derniere is not None and derniere.get("empreinte") == courante:
            continue
        valeurs = lire_brut(chemin)
        evenements.append(
            {
                "fichier": nom,
                "empreinte": courante,
                "avant": derniere.get("apres") if derniere else None,
                "apres": valeurs,
                "motif": str(valeurs.get("motif", "")),
            }
        )
    return evenements


# -------------------------------------------------------- résumés métier
def _fr(valeur: float, decimales: int = 2) -> str:
    return f"{valeur:.{decimales}f}".replace(".", ",")


def _pct(valeur: float) -> str:
    return f"{valeur * 100:.0f} %"


def _valeur_fr(v: Any) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, int) or float(v).is_integer():
        return str(int(v))
    return _fr(v)


def _aplatir(d: dict[str, Any] | None, prefixe: str = "") -> dict[str, Any]:
    plat: dict[str, Any] = {}
    for cle, v in (d or {}).items():
        if isinstance(v, dict):
            plat.update(_aplatir(v, f"{prefixe}{cle}."))
        else:
            plat[f"{prefixe}{cle}"] = v
    return plat


def _differences(avant: dict[str, Any], apres: dict[str, Any]) -> list[str]:
    a, b = _aplatir(avant), _aplatir(apres)
    return [
        f"{cle} {_valeur_fr(a.get(cle))} → {_valeur_fr(b.get(cle))}"
        for cle in sorted(set(a) | set(b))
        if cle != "motif" and a.get(cle) != b.get(cle)
    ]


def _resume_rollback(d: dict[str, Any]) -> str:
    version, signal = d["version"], d["signal"]
    if signal == "score_moyen":
        return (f"Version {version} retirée : {d.get('sous_seuil', 0)} analyses sur "
                f"{d['mesures']} jugées peu fiables (score < {_fr(d['seuil'])}).")
    if signal == "taux_erreur":
        return (f"Version {version} retirée : {_pct(d['valeur'])} des analyses en échec "
                f"(maximum toléré {_pct(d['seuil'])}).")
    return (f"Version {version} retirée : analyses trop lentes "
            f"(P95 {_fr(d['valeur'] / 1000, 1)} s, maximum {_fr(d['seuil'] / 1000, 1)} s).")


def _resume_seuils(d: dict[str, Any]) -> str:
    if d.get("avant") is None:
        return f"Seuils en vigueur ({d['fichier']}) : {d['motif']}."
    diffs = ", ".join(_differences(d["avant"], d["apres"])) or "motif seul"
    return f"Seuils modifiés ({d['fichier']}) : {diffs} (motif : {d['motif']})."


_GABARITS = {
    "rollback": _resume_rollback,
    "canary": lambda d: (
        f"Version {d['version']} étendue à {d['pourcentage']} % des clients : "
        f"{d['requetes']} analyses conformes en {d['depuis_s']:.0f} s."
    ),
    "promotion": lambda d: (
        f"Version {d['version']} servie à tous les clients : tous les critères tenus."
    ),
    "alerte": lambda d: (
        f"Version {d['version']} à surveiller : score moyen {_fr(d['valeur'])}, "
        f"proche du seuil {_fr(d['seuil'])}."
    ),
    "pilotage_refus": lambda d: (
        f"Action automatique « {d['action']} » impossible : {d['raison']}."
    ),
    "enrichissement": lambda d: (
        f"Contrat {d['contrat_id']} ajouté au jeu d'évaluation "
        f"(score en production {_fr(d['score'])})."
    ),
    "seuils": _resume_seuils,
    "seuils_invalides": lambda d: (
        f"Seuils illisibles ({d['fichier']}) : derniers seuils valides conservés — "
        f"{d['raison']}."
    ),
}


def resume_metier(evenement: str, **details: Any) -> str:
    """La phrase lisible par un non-technicien (CTO, client) en cas d'audit."""
    gabarit = _GABARITS.get(evenement)
    if gabarit is None:
        raise ValueError(f"événement sans gabarit de résumé : {evenement}")
    return gabarit(details)
