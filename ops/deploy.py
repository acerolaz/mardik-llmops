"""Déploiement : publication, canary, promotion, rollback, surveillance. [STUB — canary, promotion, rollback, surveillance : sous-projet 3]

Contrat attendu (le registre — ``ops/registry`` — enregistre ; ce module décide) :

    publier(version=None, *, bundle="v2", commit=None, registry=None, seuil=None,
            rapport=None, versions=None) -> manifest
        Étiquette une version : sans ``version``, le patch suivant de la base
        ``vX.Y`` du bundle (registre ∪ ``versions``) ; avec, elle doit avoir la
        même base et ne pas être connue. Joue le gate (``eval.run_eval.evaluer``)
        sauf si un ``rapport`` est fourni, et REFUSE (``ErreurDeploiement``,
        journal ``publication_refusee``) s'il échoue. Sinon dépose le bundle dans
        le registre avec commit, note, mode et seuils du gate, et journalise
        ``publication``.

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

Ligne de commande : ``python -m ops.deploy publier [v2.0.0] [--commit SHA] [--rapport r.json]
| canary v2.0.0 --pourcentage 10 | promouvoir v2.0.0 | rollback | surveiller [--boucle]``.
``publier`` affiche le manifeste en JSON et tient compte des tags git.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.llm_client import Bundle
from app.telemetry import MetricsStore
from eval.run_eval import Rapport, evaluer
from ops.registry import MOTIF_VERSION, ErreurRegistre, Registry

RACINE = Path(__file__).resolve().parent.parent
CANARY_PERCENT_DEFAUT = 10


class ErreurDeploiement(RuntimeError):
    pass


# ----------------------------------------------------------------- versions
def _tags_git_motif(*args: str) -> set[str]:
    """Tags ``v*`` renvoyés par ``git tag -l "v*" <args>``, filtrés par ``MOTIF_VERSION``."""
    try:
        sortie = subprocess.check_output(
            ["git", "tag", "-l", "v*", *args],
            text=True,
            stderr=subprocess.DEVNULL,
            cwd=RACINE,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return set()
    return {t.strip() for t in sortie.splitlines() if MOTIF_VERSION.match(t.strip())}


def _tags_git() -> set[str]:
    """Tags ``vX.Y.Z`` du dépôt, **hors** ceux posés sur le commit publié.

    Un tag sur HEAD est le label de ce commit, pas une version concurrente :
    un run déclenché par le tag ``v2.0.3`` doit pouvoir publier ``v2.0.3``.
    On calcule donc tous les tags ``v*`` MOINS ceux qui pointent sur HEAD
    (``--points-at HEAD``) plutôt que ``--no-contains HEAD``, qui exclurait
    aussi les tags posés sur des commits **descendants** de HEAD.
    """
    return _tags_git_motif() - _tags_git_motif("--points-at", "HEAD")


def versions_connues(registry: Registry) -> set[str]:
    """Versions déjà livrées : registre local ∪ tags git (la mémoire en CI)."""
    return set(registry.versions()) | _tags_git()


def _base(version: str) -> tuple[int, int]:
    majeure, mineure, _ = version.lstrip("v").split(".")
    return int(majeure), int(mineure)


def _tag_de_head(version_bundle: str) -> str | None:
    """Tag ``vX.Y.Z`` posé sur HEAD, de même base que ``version_bundle``.

    Sert à rendre une relance idempotente : si le commit courant porte déjà
    un tag de la base publiée (run précédent qui avait posé le tag mais pas
    poussé l'artefact, ou relance manuelle), on republie CETTE version au
    lieu de calculer le patch suivant. Le plus grand patch si plusieurs tags
    de la base sont sur HEAD ; ``None`` si aucun ou si git est absent.
    """
    base = _base(version_bundle)
    tags = _tags_git_motif("--points-at", "HEAD")
    candidats = [t for t in tags if _base(t) == base]
    if not candidats:
        return None
    return max(candidats, key=lambda t: int(t.rsplit(".", 1)[1]))


def prochaine_version(version_bundle: str, connues: Iterable[str]) -> str:
    """Patch suivant sur la base ``vX.Y`` du bundle ; la version du bundle si aucune."""
    base = _base(version_bundle)
    patchs = [
        int(v.rsplit(".", 1)[1]) for v in connues if MOTIF_VERSION.match(v) and _base(v) == base
    ]
    if not patchs:
        return version_bundle
    return f"v{base[0]}.{base[1]}.{max(patchs) + 1}"


def valider_version(version: str, version_bundle: str, connues: Iterable[str]) -> None:
    if not MOTIF_VERSION.match(version):
        raise ErreurDeploiement(f"version invalide : {version!r} (attendu vX.Y.Z)")
    if _base(version) != _base(version_bundle):
        raise ErreurDeploiement(f"le tag {version} ne correspond pas au bundle {version_bundle}")
    if version in set(connues):
        raise ErreurDeploiement(f"{version} déjà publiée")


def _commit_courant() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            cwd=RACINE,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "local"


def publier(
    version: str | None = None,
    *,
    bundle: str = "v2",
    commit: str | None = None,
    registry: Registry | None = None,
    seuil: float | None = None,
    rapport: Rapport | None = None,
    versions: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Étiquette ``version`` (ou le patch suivant) si le gate passe.

    Ne lit pas git : les versions connues sont le registre ∪ ``versions``
    (la ligne de commande y passe les tags git, cf. ``versions_connues``).
    """
    source = Bundle.charger(bundle)
    registry = registry or Registry()
    commit = commit or _commit_courant()
    connues = set(registry.versions()) | set(versions or ())
    if version is None:
        version = prochaine_version(source.version, connues)
    else:
        valider_version(version, source.version, connues)

    if rapport is None:
        try:
            rapport = evaluer(bundle, seuil=seuil)
        except (ValueError, FileNotFoundError) as exc:
            raise ErreurDeploiement(f"gate mal configuré : {exc}") from exc
    empreinte_source = source.empreinte()
    if rapport.bundle_empreinte and rapport.bundle_empreinte != empreinte_source:
        raise ErreurDeploiement(
            f"rapport d'un autre bundle : empreinte {rapport.bundle_empreinte} "
            f"≠ {empreinte_source}"
        )
    if not rapport.passe:
        registry.journaliser(
            "publication_refusee", version=version, commit=commit, motifs=list(rapport.motifs)
        )
        raise ErreurDeploiement("gate en échec : " + " ; ".join(rapport.motifs))

    details = {
        "bundle_source": bundle,
        "mode_eval": rapport.mode_eval,
        "seuils": rapport.seuils,
        "latence_p95_ms": rapport.latence_p95_ms,
        "cout_moyen_eur": rapport.cout_moyen_eur,
        "essais": rapport.essais,
    }
    try:
        manifest = registry.etiqueter(
            version, source, commit=commit, note_eval=rapport.note, details=details
        )
    except ErreurRegistre as exc:
        raise ErreurDeploiement(str(exc)) from exc
    registry.journaliser(
        "publication",
        version=version,
        commit=commit,
        note_eval=rapport.note,
        mode_eval=rapport.mode_eval,
    )
    return manifest


def charger_rapport(chemin: Path | str) -> Rapport:
    """Relit le rapport JSON écrit par ``eval.run_eval --sortie``."""
    chemin = Path(chemin)
    try:
        contenu = chemin.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ErreurDeploiement(f"rapport de gate introuvable : {chemin}") from exc
    except OSError as exc:
        raise ErreurDeploiement(f"rapport de gate illisible : {chemin} ({exc})") from exc
    try:
        data = json.loads(contenu)
    except json.JSONDecodeError as exc:
        raise ErreurDeploiement(f"rapport de gate illisible : {chemin} ({exc})") from exc
    if not isinstance(data, dict):
        raise ErreurDeploiement(f"rapport de gate incomplet : {chemin} (attendu un objet JSON)")
    if not isinstance(data.get("passe"), bool):
        raise ErreurDeploiement(f"rapport de gate incomplet : {chemin} — passe doit être un booléen")
    try:
        return Rapport.depuis_dict(data)
    except TypeError as exc:
        raise ErreurDeploiement(f"rapport de gate incomplet : {chemin} ({exc})") from exc


# ------------------------------------------------------------- transitions
@contextmanager
def _verrou(registry: Registry) -> Iterator[None]:
    """Verrou exclusif inter-processus : la CI et la surveillance (SP4) écrivent
    toutes deux l'index ; une seule transition à la fois."""
    with (registry.root / "index.lock").open("a") as fichier:
        fcntl.flock(fichier, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fichier, fcntl.LOCK_UN)


def _etat(index: dict[str, Any]) -> dict[str, Any]:
    return {cle: valeur for cle, valeur in index.items() if cle != "mis_a_jour"}


def _transition(
    registry: Registry,
    evenement: str,
    calcul: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    origine: str,
    **details: Any,
) -> dict[str, Any]:
    """Sous verrou : état avant → ``calcul`` (lève si refus) → index écrit → journal."""
    with _verrou(registry):
        avant = _etat(registry.index())
        try:
            apres = calcul(dict(avant))
        except ErreurRegistre as exc:
            raise ErreurDeploiement(str(exc)) from exc
        registry.ecrire_index(apres)
        registry.journaliser(evenement, avant=avant, apres=apres, origine=origine, **details)
    return registry.index()


def _pourcentage_par_defaut() -> int:
    brut = os.environ.get("CANARY_PERCENT", "").strip()
    if not brut:
        return CANARY_PERCENT_DEFAUT
    try:
        return int(brut)
    except ValueError as exc:
        raise ErreurDeploiement(f"CANARY_PERCENT invalide : {brut!r} (attendu un entier)") from exc


def deployer_canary(
    version: str,
    pourcentage: int | None = None,
    registry: Registry | None = None,
    *,
    origine: str = "manuel",
) -> dict[str, Any]:
    """Route ``pourcentage`` % du trafic vers ``version`` ; même version = étape suivante."""
    registry = registry or Registry()
    if pourcentage is None:
        pourcentage = _pourcentage_par_defaut()

    def calcul(index: dict[str, Any]) -> dict[str, Any]:
        registry.manifest(version)
        if index.get("active") == version:
            raise ErreurDeploiement(f"{version} est déjà la version active")
        if not 1 <= pourcentage <= 99:
            raise ErreurDeploiement(
                f"pourcentage canary {pourcentage} hors de [1, 99] (100 % : promouvoir)"
            )
        en_cours = index.get("canary")
        if en_cours not in (None, version):
            raise ErreurDeploiement(
                f"canary {en_cours} déjà en cours : rollback ou promotion d'abord"
            )
        return {**index, "canary": version, "canary_percent": pourcentage}

    return _transition(
        registry, "canary", calcul, origine=origine, version=version, pourcentage=pourcentage
    )


def promouvoir(
    version: str, registry: Registry | None = None, *, origine: str = "manuel"
) -> dict[str, Any]:
    """``version`` devient active à 100 % ; l'ancienne active devient ``precedente``."""
    registry = registry or Registry()

    def calcul(index: dict[str, Any]) -> dict[str, Any]:
        registry.manifest(version)
        if index.get("active") == version:
            raise ErreurDeploiement(f"{version} est déjà la version active")
        en_cours = index.get("canary")
        if en_cours not in (None, version):
            raise ErreurDeploiement(
                f"canary {en_cours} en cours : on ne promeut pas {version} par-dessus"
            )
        return {
            **index,
            "precedente": index.get("active"),
            "active": version,
            "canary": None,
            "canary_percent": 0,
        }

    return _transition(registry, "promotion", calcul, origine=origine, version=version)


def rollback(
    registry: Registry | None = None, motif: str = "manuel", *, origine: str = "manuel"
) -> dict[str, Any]:
    """Retour arrière en une opération, sans rebuild : retire le canary s'il y en a
    un, sinon revient à ``precedente`` (un seul niveau)."""
    registry = registry or Registry()

    def calcul(index: dict[str, Any]) -> dict[str, Any]:
        if index.get("canary") is not None:
            return {**index, "canary": None, "canary_percent": 0}
        precedente = index.get("precedente")
        if precedente is None:
            raise ErreurDeploiement("rien à annuler : ni canary en cours ni version précédente")
        return {**index, "active": precedente, "precedente": None}

    return _transition(registry, "rollback", calcul, origine=origine, motif=motif)


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
    raise NotImplementedError("deploy.surveiller — détection de dérive + rollback automatique")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Déploiement Mardik")
    sub = parser.add_subparsers(dest="commande", required=True)
    p = sub.add_parser("publier")
    p.add_argument("version", nargs="?", default=None,
                   help="vX.Y.Z (défaut : patch suivant de la base du bundle)")
    p.add_argument("--bundle", default="v2")
    p.add_argument("--commit", default=None)
    p.add_argument("--seuil", type=float, default=None)
    p.add_argument("--rapport", default=None,
                   help="rapport JSON du gate (eval.run_eval --sortie) ; sinon le gate est joué")
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
            registry = Registry()
            rapport = charger_rapport(args.rapport) if args.rapport else None
            version = args.version
            if version is None:
                version = _tag_de_head(Bundle.charger(args.bundle).version)
            manifest = publier(
                version,
                bundle=args.bundle,
                commit=args.commit,
                registry=registry,
                seuil=args.seuil,
                rapport=rapport,
                versions=versions_connues(registry),
            )
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
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
