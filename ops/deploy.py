"""Déploiement : publication, canary, promotion, rollback, surveillance. [STUB]

Contrat attendu (le registre — ``ops/registry`` — enregistre ; ce module décide) :

    publier(version, *, bundle="v2", commit="local", registry=None, seuil=0.75,
            rapport=None) -> manifest
        Étiquette une version : joue le gate d'évaluation (``eval.run_eval.evaluer``)
        sur le bundle en chantier — sauf si un ``rapport`` est fourni — et
        REFUSE (``ErreurDeploiement``) si le gate échoue. Sinon dépose le bundle
        dans le registre avec commit + note d'éval, et journalise ``publication``.

    deployer_canary(version, pourcentage=None, registry=None) -> index
        Route ``pourcentage`` % du trafic vers ``version`` (défaut : CANARY_PERCENT
        de ``.env``, sinon 10). Journalise ``canary``.

    promouvoir(version, registry=None) -> index
        La version devient active pour 100 % du trafic ; l'ancienne active est
        conservée dans ``index["precedente"]`` ; le canary est retiré. Journalise
        ``promotion``.

    rollback(registry=None, motif="manuel") -> index
        Retour arrière en une opération : si un canary est en cours, il est
        retiré ; sinon l'active redevient ``precedente``. Journalise ``rollback``
        avec le motif et les versions avant/après.

    surveiller(registry=None, metriques=None, *, fenetre_s=120, score_min=0.7,
               taux_erreur_max=0.10, latence_p95_max_ms=8000, minimum=10) -> dict
        Lit les mesures récentes (``MetricsStore``) de la version sous
        surveillance (le canary s'il y en a un, sinon l'active). Dérive si
        score moyen < ``score_min``, ou taux d'erreur > ``taux_erreur_max``, ou
        P95 > ``latence_p95_max_ms`` — sur au moins ``minimum`` mesures.
        En cas de dérive : rollback automatique + entrée au journal.
        Renvoie {"version", "mesures", "derive", "motif", "rollback"}.

Ligne de commande : ``python -m ops.deploy publier v2.0.0 | canary v2.0.0 --pourcentage 10
| promouvoir v2.0.0 | rollback | surveiller [--boucle]``.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from typing import Any

from app.telemetry import MetricsStore
from ops.registry import Registry


class ErreurDeploiement(RuntimeError):
    pass


def _commit_courant() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "local"


def publier(
    version: str,
    *,
    bundle: str = "v2",
    commit: str | None = None,
    registry: Registry | None = None,
    seuil: float = 0.75,
    rapport: Any | None = None,
) -> dict[str, Any]:
    reg = registry or Registry()
    if rapport is None:
        from eval.run_eval import evaluer

        rapport = evaluer(bundle, seuil=seuil, registry=reg)
    if not rapport.passe:
        raise ErreurDeploiement("gate en échec : " + " ; ".join(rapport.motifs))
    from app.llm_client import Bundle

    manifest = reg.etiqueter(
        version,
        Bundle.charger(bundle),
        commit=commit or _commit_courant(),
        note_eval=rapport.note,
    )
    reg.journaliser("publication", version=version, bundle=bundle, note_eval=rapport.note)
    return manifest


def deployer_canary(
    version: str, pourcentage: int | None = None, registry: Registry | None = None
) -> dict[str, Any]:
    reg = registry or Registry()
    pct = pourcentage
    if pct is None:
        pct = int(os.environ.get("CANARY_PERCENT", "10") or 10)
    reg.definir_canary(version, int(pct))
    index = reg.index()
    reg.journaliser("canary", version=version, pourcentage=index["canary_percent"])
    return index


def promouvoir(version: str, registry: Registry | None = None) -> dict[str, Any]:
    reg = registry or Registry()
    reg.manifest(version)
    avant = reg.index()
    index = dict(avant)
    index["precedente"] = avant.get("active")
    index["active"] = version
    index["canary"] = None
    index["canary_percent"] = 0
    reg.ecrire_index(index)
    apres = reg.index()
    reg.journaliser("promotion", version=version, avant=avant, apres=apres)
    return apres


def rollback(registry: Registry | None = None, motif: str = "manuel") -> dict[str, Any]:
    reg = registry or Registry()
    avant = reg.index()
    index = dict(avant)
    if avant.get("canary"):
        index["canary"] = None
        index["canary_percent"] = 0
    elif avant.get("precedente"):
        index["active"] = avant["precedente"]
        index["precedente"] = None
    else:
        raise ErreurDeploiement("rollback impossible : aucune version précédente connue")
    reg.ecrire_index(index)
    apres = reg.index()
    reg.journaliser("rollback", motif=motif, avant=avant, apres=apres)
    return apres


def surveiller(
    registry: Registry | None = None,
    metriques: MetricsStore | None = None,
    *,
    fenetre_s: float = 120,
    score_min: float = 0.7,
    taux_erreur_max: float = 0.10,
    latence_p95_max_ms: float = 8000,
    minimum: int = 10,
) -> dict[str, Any]:
    reg = registry or Registry()
    store = metriques or MetricsStore()
    canary, _pct = reg.canary()
    version = canary or reg.active()
    if version is None:
        return {"version": None, "mesures": 0, "derive": False, "motif": "", "rollback": False}

    mesures = store.lire(depuis_s=fenetre_s, version=version)
    if len(mesures) < minimum:
        return {
            "version": version,
            "mesures": len(mesures),
            "derive": False,
            "motif": "",
            "rollback": False,
        }

    erreurs = [m for m in mesures if m.erreur]
    saines = [m for m in mesures if not m.erreur]
    scores = [m.score for m in saines if m.score is not None]
    latences = [m.latence_ms for m in saines]

    motifs: list[str] = []
    if scores and (sum(scores) / len(scores)) < score_min:
        motifs.append("score moyen sous le seuil")
    if (len(erreurs) / len(mesures)) > taux_erreur_max:
        motifs.append("taux d'erreur trop élevé")
    if _p95(latences) > latence_p95_max_ms:
        motifs.append("latence P95 trop élevée")

    derive = bool(motifs)
    a_rollback = False
    motif = " ; ".join(motifs)
    if derive:
        rollback(registry=reg, motif=motif)
        a_rollback = True
    return {
        "version": version,
        "mesures": len(mesures),
        "derive": derive,
        "motif": motif,
        "rollback": a_rollback,
    }


def _p95(valeurs: list[float]) -> float:
    if not valeurs:
        return 0.0
    tri = sorted(valeurs)
    return tri[min(len(tri) - 1, int(round(0.95 * len(tri) + 0.5)) - 1)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Déploiement Mardik")
    sub = parser.add_subparsers(dest="commande", required=True)
    p = sub.add_parser("publier")
    p.add_argument("version")
    p.add_argument("--bundle", default="v2")
    p.add_argument("--seuil", type=float, default=0.75)
    c = sub.add_parser("canary")
    c.add_argument("version")
    c.add_argument("--pourcentage", type=int, default=None)
    pr = sub.add_parser("promouvoir")
    pr.add_argument("version")
    r = sub.add_parser("rollback")
    r.add_argument("--motif", default="manuel")
    s = sub.add_parser("surveiller")
    s.add_argument("--boucle", action="store_true")
    s.add_argument("--intervalle", type=float, default=5.0)
    s.add_argument("--fenetre", type=float, default=120)
    args = parser.parse_args(argv)

    try:
        if args.commande == "publier":
            print(publier(args.version, bundle=args.bundle, seuil=args.seuil))
        elif args.commande == "canary":
            print(deployer_canary(args.version, args.pourcentage))
        elif args.commande == "promouvoir":
            print(promouvoir(args.version))
        elif args.commande == "rollback":
            print(rollback(motif=args.motif))
        elif args.commande == "surveiller":
            while True:
                res = surveiller(fenetre_s=args.fenetre)
                print(res)
                if not args.boucle or res["rollback"]:
                    break
                time.sleep(args.intervalle)
    except ErreurDeploiement as exc:
        print(f"REFUSÉ : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
