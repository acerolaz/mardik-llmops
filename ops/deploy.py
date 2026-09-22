"""Déploiement : publication, installation, canary, promotion, rollback. [surveillance : SP4]

Le registre (``ops/registry``) enregistre ; ce module décide. Chaque transition
d'index passe par ``_transition`` : verrou exclusif (``index.lock``), état
avant → calcul (refus = ``ErreurDeploiement``) → index écrit atomiquement →
entrée de journal ``{evenement, avant, apres, origine, …}``.

    publier(version=None, *, bundle="v2", commit=None, registry=None, seuil=None,
            rapport=None, versions=None) -> manifest
        Étiquette une version : sans ``version``, le patch suivant de la base
        ``vX.Y`` du bundle (registre ∪ ``versions``) ; avec, elle doit avoir la
        même base et ne pas être connue. Joue le gate (``eval.run_eval.evaluer``)
        sauf si un ``rapport`` est fourni, et REFUSE (``ErreurDeploiement``,
        journal ``publication_refusee``) s'il échoue. Sinon dépose le bundle dans
        le registre avec commit, note, mode et seuils du gate, et journalise
        ``publication``.

    installer(version, depuis, registry=None, *, origine="manuel") -> manifest
        Copie un dossier de version publié par la CI (artefact ``mardik-vX.Y.Z``)
        dans ce registre. Idempotent à empreinte égale ; empreinte différente =
        refus (un tag est immuable). Journalise ``installation``.

    deployer_canary(version, pourcentage=None, registry=None, *, origine="manuel") -> index
        ``pourcentage`` % du trafic (défaut : CANARY_PERCENT, sinon 10 ; 1 à 99)
        vers ``version``. Même version = étape de progression (10 → 50).
        Journalise ``canary``.

    promouvoir(version, registry=None, *, origine="manuel") -> index
        La version devient active à 100 % ; l'ancienne active devient
        ``index["precedente"]`` ; le canary est retiré. Journalise ``promotion``.

    rollback(registry=None, motif="manuel", *, origine="manuel") -> index
        Retour arrière en une opération, sans rebuild : retire le canary s'il y en
        a un, sinon l'active redevient ``precedente`` (un seul niveau).
        Journalise ``rollback`` avec le motif.

    surveiller(...) -> dict                                   [sous-projet 4]
        Dérive de la version surveillée (canary, sinon active) : seuils lus par
        défaut dans ``ops/seuils_pilotage.yaml`` (paramètres explicites
        prioritaires). Dérive critique → ``rollback`` automatique tracé
        (``origine="auto"``) ; dérive de marge → journalise une ``alerte``
        (une seule fois par fenêtre). Un rollback refusé (rien à annuler)
        journalise ``pilotage_refus`` (une seule fois par fenêtre) au lieu de
        lever.

``origine`` : ``manuel``, ``ci:<acteur GitHub>`` ou ``auto`` (surveillance).

Ligne de commande (JSON sur stdout ; refus → ``REFUSÉ : …`` sur stderr, code 1) :
``python -m ops.deploy publier [vX.Y.Z] [--commit SHA] [--rapport r.json]
| installer vX.Y.Z --depuis DOSSIER | canary vX.Y.Z [--pourcentage N]
| promouvoir vX.Y.Z | rollback [--motif M] | surveiller [--boucle]`` ;
``--origine`` sur installer, canary, promouvoir et rollback.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import structlog

from app.llm_client import Bundle
from app.telemetry import MetricsStore
from eval.run_eval import Rapport, evaluer
from ops.pilotage import (
    changements_seuils,
    debut_palier,
    detecter_derive,
    evaluer_palier,
    resume_metier,
    version_surveillee,
)
from ops.registry import MOTIF_VERSION, ErreurRegistre, Registry
from ops.seuils import (
    ErreurSeuilsPilotage,
    SeuilsPilotage,
    charger_seuils_pilotage,
    chemin_seuils_gate,
    chemin_seuils_pilotage,
)
from ops.signaux import filtrer

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
    **details: Any,
) -> dict[str, Any]:
    """Route ``pourcentage`` % du trafic vers ``version`` ; même version = étape suivante.

    ``details`` (pilotage) est recopié tel quel dans l'entrée de journal.
    """
    if not MOTIF_VERSION.match(version):
        raise ErreurDeploiement(f"version invalide : {version!r} (attendu vX.Y.Z)")
    registry = registry or Registry()
    if pourcentage is None:
        pourcentage = _pourcentage_par_defaut()

    def calcul(index: dict[str, Any]) -> dict[str, Any]:
        if index.get("active") is None:
            raise ErreurDeploiement("aucune version active : promouvoir d'abord")
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
        registry, "canary", calcul, origine=origine, version=version, pourcentage=pourcentage,
        **details,
    )


def promouvoir(
    version: str, registry: Registry | None = None, *, origine: str = "manuel", **details: Any
) -> dict[str, Any]:
    """``version`` devient active à 100 % ; l'ancienne active devient ``precedente``.

    ``details`` (pilotage) est recopié tel quel dans l'entrée de journal.
    """
    if not MOTIF_VERSION.match(version):
        raise ErreurDeploiement(f"version invalide : {version!r} (attendu vX.Y.Z)")
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

    return _transition(registry, "promotion", calcul, origine=origine, version=version, **details)


def rollback(
    registry: Registry | None = None,
    motif: str = "manuel",
    *,
    origine: str = "manuel",
    **details: Any,
) -> dict[str, Any]:
    """Retour arrière en une opération, sans rebuild : retire le canary s'il y en a
    un, sinon revient à ``precedente`` (un seul niveau).

    ``details`` (pilotage) est recopié tel quel dans l'entrée de journal.
    """
    registry = registry or Registry()

    def calcul(index: dict[str, Any]) -> dict[str, Any]:
        if index.get("canary") is not None:
            return {**index, "canary": None, "canary_percent": 0}
        precedente = index.get("precedente")
        if precedente is None:
            raise ErreurDeploiement("rien à annuler : ni canary en cours ni version précédente")
        return {**index, "active": precedente, "precedente": None}

    return _transition(registry, "rollback", calcul, origine=origine, motif=motif, **details)


def installer(
    version: str,
    depuis: Path | str,
    registry: Registry | None = None,
    *,
    origine: str = "manuel",
) -> dict[str, Any]:
    """Installe dans ce registre une version publiée ailleurs (artefact CI
    ``mardik-vX.Y.Z``). Idempotent à empreinte égale ; un tag reste immuable."""
    registry = registry or Registry()
    depuis = Path(depuis)
    if not MOTIF_VERSION.match(version):
        raise ErreurDeploiement(f"version invalide : {version!r} (attendu vX.Y.Z)")
    chemin = depuis / "manifest.json"
    try:
        manifeste = json.loads(chemin.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ErreurDeploiement(f"manifeste introuvable : {chemin}") from exc
    except json.JSONDecodeError as exc:
        raise ErreurDeploiement(f"manifeste illisible : {chemin} ({exc})") from exc
    if not isinstance(manifeste, dict) or manifeste.get("version") != version:
        decrit = manifeste.get("version") if isinstance(manifeste, dict) else None
        raise ErreurDeploiement(f"le manifeste de {depuis} décrit {decrit!r}, pas {version}")
    if not (depuis / "config.yaml").exists():
        raise ErreurDeploiement(f"config.yaml absent de {depuis}")

    with _verrou(registry):
        if version in registry.versions():
            installe = registry.manifest(version)
            if installe.get("empreinte") != manifeste.get("empreinte"):
                raise ErreurDeploiement(
                    f"{version} déjà installée avec l'empreinte {installe.get('empreinte')} "
                    f"≠ {manifeste.get('empreinte')} : un tag est immuable"
                )
            return installe
        # Copier dans un dossier temporaire d'abord
        temp_folder = registry.root / f".{version}.installation"
        try:
            # Nettoyer tout dossier temporaire orphelin
            if temp_folder.exists():
                shutil.rmtree(temp_folder, ignore_errors=True)
            shutil.copytree(depuis, temp_folder)
            # Renommer après succès
            os.replace(temp_folder, registry.root / version)
        except OSError as exc:
            # Nettoyer le dossier temporaire en cas d'erreur
            if temp_folder.exists():
                shutil.rmtree(temp_folder, ignore_errors=True)
            raise ErreurDeploiement(f"installation de {version} impossible : {exc}") from exc
        registry.journaliser(
            "installation",
            version=version,
            commit=manifeste.get("commit"),
            note_eval=manifeste.get("note_eval"),
            empreinte=manifeste.get("empreinte"),
            origine=origine,
        )
    return manifeste


# ---------------------------------------------------------------- pilotage
def _journaliser_une_fois(
    registry: Registry, evenement: str, fenetre_s: float, cles: dict[str, Any], **details: Any
) -> dict[str, Any] | None:
    """Journalise sauf si le même événement (mêmes ``cles``) date de moins de
    ``fenetre_s`` : une alerte ou un refus n'est pas répété à chaque tour."""
    limite = time.time() - fenetre_s
    for entree in reversed(registry.journal()):
        if entree.get("ts", 0) < limite:
            break
        if entree.get("evenement") == evenement and all(
            entree.get(k) == v for k, v in cles.items()
        ):
            return None
    return registry.journaliser(evenement, origine="auto", **cles, **details)


def surveiller(
    registry: Registry | None = None,
    metriques: MetricsStore | None = None,
    *,
    fenetre_s: float | None = None,
    score_min: float | None = None,
    taux_erreur_max: float | None = None,
    latence_p95_max_ms: float | None = None,
    minimum: int | None = None,
    seuils: SeuilsPilotage | None = None,
) -> dict[str, Any]:
    """Dérive critique de la version surveillée → rollback automatique, tracé.

    Les paramètres à ``None`` viennent de ``seuils`` (sinon de
    ``ops/seuils_pilotage.yaml``) ; les valeurs explicites l'emportent.
    """
    registry = registry or Registry()
    metriques = metriques or MetricsStore()
    s = seuils or charger_seuils_pilotage()
    fenetre = fenetre_s if fenetre_s is not None else s.fenetre_s
    mini = minimum if minimum is not None else s.minimum
    surcharges = {"score_min": score_min, "taux_erreur_max": taux_erreur_max,
                  "latence_p95_max_ms": latence_p95_max_ms}
    seuils_derive = replace(s.derive, **{k: v for k, v in surcharges.items() if v is not None})

    index = registry.index()
    version = version_surveillee(index)
    if version is None:
        return {"version": None, "mesures": 0, "derive": False, "niveau": "aucune",
                "motif": "aucune version active", "rollback": False}
    debut = debut_palier(registry.journal(), version) if index.get("canary") == version else None
    mesures = filtrer(metriques.lire(depuis_s=fenetre), version, depuis_ts=debut)
    derive = detecter_derive(version, mesures, seuils_derive, minimum=mini)
    resultat = {"version": version, "mesures": derive.mesures, "derive": derive.critique,
                "niveau": derive.niveau, "motif": derive.motif, "rollback": False}

    if derive.niveau == "marge":
        score = next(c for c in derive.constats if c.signal == "score_moyen")
        _journaliser_une_fois(
            registry, "alerte", fenetre, {"version": version, "signal": "score_moyen"},
            valeur=score.valeur, seuil=score.seuil, motif=derive.motif,
            resume=resume_metier("alerte", version=version, valeur=score.valeur,
                                 seuil=score.seuil),
        )
    elif derive.critique:
        c = derive.principal
        details = {
            "signal": c.signal, "valeur": c.valeur, "seuil": c.seuil,
            "mesures": derive.mesures, "constats": [x.to_dict() for x in derive.constats],
            "resume": resume_metier("rollback", version=version, signal=c.signal,
                                    valeur=c.valeur, seuil=c.seuil, mesures=derive.mesures,
                                    sous_seuil=derive.sous_seuil),
        }
        try:
            rollback(registry, motif=derive.motif, origine="auto", **details)
            resultat["rollback"] = True
        except ErreurDeploiement as exc:
            _journaliser_une_fois(
                registry, "pilotage_refus", fenetre,
                {"version": version, "action": "rollback"},
                raison=str(exc), motif=derive.motif,
                resume=resume_metier("pilotage_refus", action="rollback", raison=str(exc)),
            )
    return resultat


def fichiers_seuils() -> dict[str, Path]:
    """Les deux fichiers de seuils suivis au journal (pilotage et gate)."""
    return {
        "ops/seuils_pilotage.yaml": chemin_seuils_pilotage(),
        "eval/seuils.yaml": chemin_seuils_gate(),
    }


def _journaliser_seuils(registry: Registry, seuils: SeuilsPilotage) -> None:
    try:
        evenements = changements_seuils(registry.journal(), fichiers_seuils())
    except ErreurSeuilsPilotage as exc:
        _journaliser_une_fois(
            registry, "seuils_invalides", seuils.fenetre_s, {"raison": str(exc)},
            fichier="seuils",
            resume=resume_metier("seuils_invalides", fichier="seuils", raison=str(exc)),
        )
        return
    for ev in evenements:
        registry.journaliser("seuils", origine="auto", **ev,
                             resume=resume_metier("seuils", **ev))


def _journaliser_incident_seuils(
    registry: Registry, seuils: SeuilsPilotage, exc: ErreurSeuilsPilotage
) -> None:
    """Trace des seuils illisibles. Un incident d'écriture du journal ne doit
    pas faire sortir le pilote : seul un seuil invalide au démarrage l'arrête."""
    try:
        _journaliser_une_fois(
            registry, "seuils_invalides", seuils.fenetre_s, {"raison": str(exc)},
            fichier="ops/seuils_pilotage.yaml",
            resume=resume_metier("seuils_invalides",
                                 fichier="ops/seuils_pilotage.yaml", raison=str(exc)),
        )
    except (ErreurRegistre, OSError, json.JSONDecodeError) as incident:
        structlog.get_logger("mardik").warning("pilotage.incident", cause=str(incident))


def tour(
    registry: Registry, metriques: MetricsStore, seuils: SeuilsPilotage
) -> dict[str, Any]:
    """Une tour du pilote : seuils → surveillance → palier. Relit tout, ne garde rien."""
    _journaliser_seuils(registry, seuils)
    surveillance = surveiller(registry, metriques, seuils=seuils)
    if surveillance["rollback"]:
        return {"surveillance": surveillance, "palier": None}
    index = registry.index()
    canary = index.get("canary")
    debut = debut_palier(registry.journal(), canary) if canary else None

    if canary is not None and debut is None:
        # Canary à l'index, mais aucun début de palier au journal (journal perdu
        # ou tronqué) : sans trace, la version resterait figée sans explication.
        raison = "aucun début de palier au journal (journal perdu ou tronqué)"
        _journaliser_une_fois(
            registry, "pilotage_refus", seuils.fenetre_s,
            {"version": canary, "action": "palier"},
            raison=raison,
            resume=resume_metier("pilotage_refus", action="palier", raison=raison),
        )
    if canary is None or debut is None:
        return {"surveillance": surveillance, "palier": None}

    maintenant = time.time()
    depuis_s = maintenant - debut
    mesures = metriques.lire(depuis_s=depuis_s + 1)
    decision = evaluer_palier(
        canary,
        filtrer(mesures, canary, depuis_ts=debut),
        filtrer(mesures, index.get("active") or "", depuis_ts=debut),
        seuils,
        depuis_s=depuis_s,
        pourcentage=int(index.get("canary_percent") or 0),
    )
    details = {
        "motif": decision.motif,
        "constats": [c.to_dict() for c in decision.constats],
        "requetes": decision.requetes,
        "depuis_s": round(decision.depuis_s, 1),
    }
    try:
        if decision.action == "progresser":
            deployer_canary(
                canary, decision.pourcentage_suivant, registry, origine="auto",
                resume=resume_metier("canary", version=canary,
                                     pourcentage=decision.pourcentage_suivant,
                                     requetes=decision.requetes, depuis_s=decision.depuis_s),
                **details,
            )
        elif decision.action == "promouvoir":
            promouvoir(canary, registry, origine="auto",
                       resume=resume_metier("promotion", version=canary), **details)
    except ErreurDeploiement as exc:
        _journaliser_une_fois(
            registry, "pilotage_refus", seuils.fenetre_s,
            {"version": canary, "action": decision.action},
            raison=str(exc),
            resume=resume_metier("pilotage_refus", action=decision.action, raison=str(exc)),
        )
    return {"surveillance": surveillance,
            "palier": {"action": decision.action, "motif": decision.motif}}


def piloter(
    registry: Registry | None = None,
    metriques: MetricsStore | None = None,
    *,
    tours: int | None = None,
    attendre: Callable[[float], None] = time.sleep,
    charger: Callable[[], SeuilsPilotage] = charger_seuils_pilotage,
) -> int:
    """Boucle du pilote (service ``pilote``). Seuils invalides au démarrage →
    ``ErreurSeuilsPilotage`` ; en cours de route → derniers seuils valides."""
    registry = registry or Registry()
    metriques = metriques or MetricsStore()
    seuils = charger()
    joues = 0
    while tours is None or joues < tours:
        if joues:
            try:
                seuils = charger()
            except ErreurSeuilsPilotage as exc:
                _journaliser_incident_seuils(registry, seuils, exc)
        try:
            tour(registry, metriques, seuils)
        except (ErreurRegistre, OSError, json.JSONDecodeError) as exc:
            # Incident transitoire (registre/métriques) : on journalise et on
            # continue la boucle plutôt que de faire sortir le pilote (spec :
            # seul un seuil invalide AU DÉMARRAGE doit l'arrêter).
            structlog.get_logger("mardik").warning("pilotage.incident", cause=str(exc))
        joues += 1
        if tours is None or joues < tours:
            attendre(seuils.intervalle_s)
    return joues


def _afficher(donnees: dict[str, Any]) -> None:
    print(json.dumps(donnees, ensure_ascii=False, indent=2))


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
    i = sub.add_parser("installer")
    i.add_argument("version")
    i.add_argument("--depuis", required=True, help="dossier de version (artefact CI)")
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
    s.add_argument("--fenetre", type=float, default=None)
    pl = sub.add_parser("piloter", help="boucle du pilote : surveillance + promotion")
    pl.add_argument("--tours", type=int, default=None, help="nombre de tours (défaut : infini)")
    for commande in (i, c, pr, r):
        commande.add_argument("--origine", default="manuel",
                              help="manuel | ci:<acteur> | auto")
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
        elif args.commande == "installer":
            _afficher(installer(args.version, args.depuis, origine=args.origine))
        elif args.commande == "canary":
            _afficher(deployer_canary(args.version, args.pourcentage, origine=args.origine))
        elif args.commande == "promouvoir":
            _afficher(promouvoir(args.version, origine=args.origine))
        elif args.commande == "rollback":
            _afficher(rollback(motif=args.motif, origine=args.origine))
        elif args.commande == "surveiller":
            while True:
                res = surveiller(fenetre_s=args.fenetre)
                print(res)
                if not args.boucle or res["rollback"]:
                    break
                time.sleep(args.intervalle)
        elif args.commande == "piloter":
            piloter(tours=args.tours)
    except ErreurDeploiement as exc:
        print(f"REFUSÉ : {exc}", file=sys.stderr)
        return 1
    except ErreurSeuilsPilotage as exc:
        print(f"SEUILS INVALIDES : {exc}", file=sys.stderr)
        return 1
    except (ErreurRegistre, OSError, json.JSONDecodeError) as exc:
        print(f"ÉCHEC : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
