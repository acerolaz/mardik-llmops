"""Le gate d'évaluation.

Contrat attendu :

    evaluer(version, *, n_essais=None, seuil=None, latence_max_ms=None,
            cout_max_eur=None, contrats=..., attendus=..., registry=None,
            telemetry=None, sous_ensemble=None, historique=..., seuils=None) -> Rapport

    Rapport (dataclass, sérialisable en JSON) :
        version, date, essais,
        note                 moyenne sur les contrats du rappel des clauses attendues
                             (clauses attendues trouvées / clauses attendues), elle-même
                             moyennée sur ``n_essais`` passes — deux passes du vrai
                             modèle ne donnent pas la même note : c'est voulu.
        par_contrat          {contrat_id: {"note", "seuil_note", "passe", "trouvees",
                              "manquantes", "latence_ms", "cout_eur"}}
        latence_p95_ms       P95 des latences par analyse    (contrainte client : < 8 s)
        cout_moyen_eur       coût moyen par analyse          (contrainte client : < 0,15 €)
        passe                note >= seuil ET aucun contrat sous son ``seuil_note``
                             ET latence_p95_ms < latence_max_ms ET cout_moyen_eur < cout_max_eur
        motifs               liste des raisons d'échec (vide si passe)

* ``version`` désigne soit un bundle en chantier (``v1``, ``v2`` → ``models/``),
  soit une version livrée (``v2.0.3`` → registre) ;
* chaque contrat de ``eval/contrats/`` est analysé avec le moteur que
  dicte la stratégie du bundle (``analyser_v1`` / ``analyser_v2``) ;
* ``n_essais`` vaut par défaut ``parametres.essais_eval`` du bundle (1 sinon) ;
* chaque exécution ajoute une ligne à ``eval/history.jsonl`` (le rapport) ;
* les seuils viennent de ``eval/seuils.yaml`` (``SEUILS_PATH``) ; les
  arguments ``seuil`` / ``latence_max_ms`` / ``cout_max_eur`` les surchargent ;
  le rapport recopie les seuils appliqués et ``mode_eval`` (``mock`` | ``reel``) ;
* en ligne de commande : ``python -m eval.run_eval --version v2 --seuil 0.75``
  → affiche le rapport, code de sortie 0 si le gate passe, 1 sinon.
  ``--essais N`` force le nombre de passes, ``--contrats c01,c07`` restreint,
  ``--sortie rapport.json`` écrit le rapport (relu par ``ops.deploy publier
  --rapport``), ``--historique`` change le fichier d'historique ; code 2 si le
  gate est mal configuré (seuils, golden dataset, stratégie).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.api_v1 import analyser_v1
from app.api_v2 import analyser_v2
from app.llm_client import Bundle, ErreurLLM, LLMClient, mode_mock
from app.pipeline import DocumentTropLong
from app.telemetry import NoopSpanExporter, Telemetry, build_telemetry
from ops.registry import MOTIF_VERSION, Registry

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_CONTRATS = RACINE / "eval" / "contrats"
CHEMIN_ATTENDUS = RACINE / "eval" / "attendus.jsonl"
CHEMIN_HISTORIQUE = RACINE / "eval" / "history.jsonl"
CHEMIN_SEUILS_DEFAUT = RACINE / "eval" / "seuils.yaml"
CHEMIN_METRIQUES_EVAL_DEFAUT = RACINE / "eval" / ".metrics_eval.jsonl"
CLES_SEUILS = ("note_min", "latence_p95_max_ms", "cout_moyen_max_eur")


# ------------------------------------------------------------------- seuils
class ErreurSeuils(ValueError):
    """Le fichier de seuils du gate est absent ou invalide."""


@dataclass(frozen=True)
class Seuils:
    note_min: float
    latence_p95_max_ms: float
    cout_moyen_max_eur: float
    motif: str = ""

    def surcharger(
        self,
        *,
        note_min: float | None = None,
        latence_p95_max_ms: float | None = None,
        cout_moyen_max_eur: float | None = None,
    ) -> Seuils:
        """Remplace les seuils fournis (usage local et tests) ; ``None`` = inchangé."""
        valeurs = {
            "note_min": note_min,
            "latence_p95_max_ms": latence_p95_max_ms,
            "cout_moyen_max_eur": cout_moyen_max_eur,
        }
        return replace(self, **{k: float(v) for k, v in valeurs.items() if v is not None})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def chemin_seuils() -> Path:
    """``SEUILS_PATH`` si défini, sinon ``eval/seuils.yaml`` (lu à chaque appel)."""
    return Path(os.environ.get("SEUILS_PATH") or CHEMIN_SEUILS_DEFAUT)


def charger_seuils(chemin: Path | str | None = None) -> Seuils:
    chemin = Path(chemin) if chemin else chemin_seuils()
    try:
        contenu = chemin.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ErreurSeuils(f"fichier de seuils introuvable : {chemin}") from exc
    except OSError as exc:
        raise ErreurSeuils(f"fichier de seuils illisible : {chemin} ({exc})") from exc
    try:
        data = yaml.safe_load(contenu)
    except yaml.YAMLError as exc:
        raise ErreurSeuils(f"{chemin} : YAML invalide ({exc})") from exc
    if not isinstance(data, dict):
        raise ErreurSeuils(f"{chemin} : attendu un dictionnaire de seuils")
    valeurs: dict[str, float] = {}
    for cle in CLES_SEUILS:
        if cle not in data:
            raise ErreurSeuils(f"{chemin} : clé « {cle} » manquante")
        valeur = data[cle]
        if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
            raise ErreurSeuils(f"{chemin} : « {cle} » doit être un nombre, reçu {valeur!r}")
        valeurs[cle] = float(valeur)
    motif = str(data.get("motif") or "").strip()
    if not motif:
        raise ErreurSeuils(f"{chemin} : clé « motif » manquante ou vide")
    return Seuils(**valeurs, motif=motif)


@dataclass
class Rapport:
    version: str
    date: str
    essais: int
    note: float
    par_contrat: dict[str, dict[str, Any]]
    latence_p95_ms: float
    cout_moyen_eur: float
    passe: bool
    motifs: list[str] = field(default_factory=list)
    seuil: float = 0.75
    mode_eval: str = "mock"
    seuils: dict[str, Any] = field(default_factory=dict)
    bundle_empreinte: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def depuis_dict(cls, data: dict[str, Any]) -> Rapport:
        """Relit un rapport sérialisé (``--sortie``) ; les clés inconnues sont ignorées."""
        noms = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in noms})


def charger_attendus(chemin: Path = CHEMIN_ATTENDUS) -> dict[str, dict[str, Any]]:
    attendus: dict[str, dict[str, Any]] = {}
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        if ligne.strip():
            item = json.loads(ligne)
            attendus[item["contrat_id"]] = item
    return attendus


def charger_bundle(version: str, registry: Registry | None = None) -> Bundle:
    if MOTIF_VERSION.match(version):
        return (registry or Registry()).bundle(version)
    return Bundle.charger(version)


def _p95(valeurs: list[float]) -> float:
    if not valeurs:
        return 0.0
    tri = sorted(valeurs)
    return tri[min(len(tri) - 1, int(round(0.95 * len(tri) + 0.5)) - 1)]


# ----------------------------------------------------------------- notation
def noter_contrat(trouves: Iterable[str], attendu: dict[str, Any]) -> dict[str, Any]:
    """Rappel des clauses attendues d'un contrat (fonction pure)."""
    presentes = set(trouves)
    attendues: list[str] = list(attendu["clauses_attendues"])
    trouvees = [c for c in attendues if c in presentes]
    manquantes = [c for c in attendues if c not in presentes]
    note = round(len(trouvees) / len(attendues), 4) if attendues else 1.0
    seuil_note = float(attendu["seuil_note"])
    return {
        "note": note,
        "seuil_note": seuil_note,
        "passe": note >= seuil_note,
        "trouvees": trouvees,
        "manquantes": manquantes,
    }


def combiner_essais(essais: list[dict[str, Any]]) -> dict[str, Any]:
    """Moyenne des passes d'un contrat ; trouvées / manquantes du dernier essai."""
    dernier = essais[-1]
    n = len(essais)
    note = round(sum(e["note"] for e in essais) / n, 4)
    return {
        **dernier,
        "note": note,
        "passe": note >= dernier["seuil_note"],
        "latence_ms": round(sum(e["latence_ms"] for e in essais) / n, 1),
        "cout_eur": round(sum(e["cout_eur"] for e in essais) / n, 6),
    }


def agreger(
    par_contrat: dict[str, dict[str, Any]],
    latences: list[float],
    couts: list[float],
    seuils: Seuils,
    erreurs: Iterable[str] = (),
) -> tuple[float, float, float, list[str]]:
    """Note globale, P95, coût moyen et motifs d'échec (liste vide = gate passé)."""
    notes = [c["note"] for c in par_contrat.values()]
    note = round(sum(notes) / len(notes), 4) if notes else 0.0
    p95 = _p95(latences)
    cout = round(sum(couts) / len(couts), 6) if couts else 0.0
    motifs = list(erreurs)
    if note < seuils.note_min:
        motifs.append(f"note {note:.3f} < seuil {seuils.note_min}")
    for cid, c in par_contrat.items():
        if not c["passe"]:
            motifs.append(f"{cid} : note {c['note']:.2f} < seuil_note {c['seuil_note']:.2f}")
    if p95 >= seuils.latence_p95_max_ms:
        motifs.append(f"latence P95 {p95:.0f} ms ≥ {seuils.latence_p95_max_ms:.0f} ms")
    if cout >= seuils.cout_moyen_max_eur:
        motifs.append(f"coût moyen {cout:.4f} € ≥ {seuils.cout_moyen_max_eur} €")
    return note, p95, cout, motifs


# ------------------------------------------------------------------ moteurs
Moteur = Callable[[str, LLMClient, Telemetry], list[str]]


def _types_v1(texte: str, client: LLMClient, telemetry: Telemetry) -> list[str]:
    return list(analyser_v1(texte, client, telemetry).clauses)


def _types_v2(texte: str, client: LLMClient, telemetry: Telemetry) -> list[str]:
    return [c.type for c in analyser_v2(texte, client, telemetry).clauses]


# Une nouvelle stratégie (v3…) = une nouvelle entrée ; evaluer ne change pas.
MOTEURS: dict[str, Moteur] = {"monolithique": _types_v1, "map_reduce_clauses": _types_v2}


def moteur_pour(strategie: str) -> Moteur:
    try:
        return MOTEURS[strategie]
    except KeyError:
        raise ValueError(f"stratégie {strategie!r} sans moteur d'évaluation") from None


# --------------------------------------------------------------- évaluation
def mode_eval() -> str:
    """``reel`` seulement quand le vrai modèle répond sans enregistrement (``MOCK=off``)."""
    return "reel" if mode_mock() == "off" else "mock"


def _telemetry_eval() -> Telemetry:
    """Télémétrie du gate : pas de spans, mesures à part de ``ops/metrics.jsonl``."""
    chemin = os.environ.get("METRICS_EVAL_PATH") or CHEMIN_METRIQUES_EVAL_DEFAUT
    return build_telemetry(
        span_exporter=NoopSpanExporter(),
        metrics_path=chemin,
        level=os.environ.get("LOG_LEVEL", "INFO"),
    )


def _charger_contrats(
    dossier: Path, annotes: dict[str, dict[str, Any]], sous_ensemble: list[str] | None
) -> dict[str, str]:
    """Textes des contrats retenus ; un golden dataset incohérent ne passe jamais."""
    ids = sous_ensemble or sorted(annotes)
    inconnus = [cid for cid in ids if cid not in annotes]
    if inconnus:
        raise ValueError(f"contrat(s) sans annotation dans attendus.jsonl : {', '.join(inconnus)}")
    textes: dict[str, str] = {}
    for cid in ids:
        chemin = Path(dossier) / f"{cid}.txt"
        if not chemin.exists():
            raise FileNotFoundError(f"contrat annoté introuvable : {chemin}")
        textes[cid] = chemin.read_text(encoding="utf-8")
    return textes


def _essai_en_erreur(attendu: dict[str, Any]) -> dict[str, Any]:
    return {**noter_contrat([], attendu), "latence_ms": 0.0, "cout_eur": 0.0}


def evaluer(
    version: str,
    *,
    n_essais: int | None = None,
    seuil: float | None = None,
    latence_max_ms: float | None = None,
    cout_max_eur: float | None = None,
    contrats: Path = DOSSIER_CONTRATS,
    attendus: Path = CHEMIN_ATTENDUS,
    registry: Registry | None = None,
    telemetry: Telemetry | None = None,
    sous_ensemble: list[str] | None = None,
    historique: Path | None = CHEMIN_HISTORIQUE,
    seuils: Path | None = None,
) -> Rapport:
    bundle = charger_bundle(version, registry)
    moteur = moteur_pour(bundle.strategie)
    appliques = charger_seuils(seuils).surcharger(
        note_min=seuil, latence_p95_max_ms=latence_max_ms, cout_moyen_max_eur=cout_max_eur
    )
    annotes = charger_attendus(attendus)
    textes = _charger_contrats(contrats, annotes, sous_ensemble)
    telemetry = telemetry or _telemetry_eval()
    n = n_essais if n_essais is not None else int(bundle.parametres.get("essais_eval") or 1)
    if n < 1:
        raise ValueError(f"nombre d'essais invalide : {n} (≥ 1)")

    essais: dict[str, list[dict[str, Any]]] = {cid: [] for cid in textes}
    latences: list[float] = []
    couts: list[float] = []
    erreurs: list[str] = []
    for _ in range(n):
        for cid, texte in textes.items():
            try:
                trouves = moteur(texte, LLMClient(bundle), telemetry)
            except ErreurLLM as exc:
                erreurs.append(f"{cid} : erreur LLM — {exc}")
                essais[cid].append(_essai_en_erreur(annotes[cid]))
                continue
            except DocumentTropLong as exc:
                erreurs.append(f"{cid} : document trop long — {exc}")
                essais[cid].append(_essai_en_erreur(annotes[cid]))
                continue
            mesure = telemetry.metriques.lire()[-1]  # le gate est séquentiel
            latences.append(mesure.latence_ms)
            couts.append(mesure.cout_eur)
            essais[cid].append(
                {
                    **noter_contrat(trouves, annotes[cid]),
                    "latence_ms": mesure.latence_ms,
                    "cout_eur": mesure.cout_eur,
                }
            )

    par_contrat = {cid: combiner_essais(e) for cid, e in essais.items()}
    note, p95, cout, motifs = agreger(par_contrat, latences, couts, appliques, erreurs)
    rapport = Rapport(
        version=bundle.version,
        date=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        essais=n,
        note=note,
        par_contrat=par_contrat,
        latence_p95_ms=p95,
        cout_moyen_eur=cout,
        passe=not motifs,
        motifs=motifs,
        seuil=appliques.note_min,
        mode_eval=mode_eval(),
        seuils=appliques.to_dict(),
        bundle_empreinte=bundle.empreinte(),
    )
    if historique is not None:
        historique = Path(historique)
        historique.parent.mkdir(parents=True, exist_ok=True)
        with historique.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rapport.to_dict(), ensure_ascii=False) + "\n")
    return rapport


def afficher(rapport: Rapport) -> None:
    print(f"== Gate d'évaluation — {rapport.version} ({rapport.essais} essai(s)) ==")
    for cid, c in rapport.par_contrat.items():
        etat = "OK " if c["passe"] else "KO "
        manque = f"  manquantes: {', '.join(c['manquantes'])}" if c["manquantes"] else ""
        print(f"  {etat} {cid}  note={c['note']:.2f}  (seuil {c['seuil_note']}){manque}")
    print(
        f"note globale = {rapport.note:.3f} | P95 = {rapport.latence_p95_ms:.0f} ms"
        f" | coût moyen = {rapport.cout_moyen_eur:.4f} €"
    )
    print("GATE : " + ("PASSE" if rapport.passe else "ÉCHEC — " + " ; ".join(rapport.motifs)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate d'évaluation Mardik")
    parser.add_argument("--version", default="v2")
    parser.add_argument("--essais", type=int, default=None)
    parser.add_argument("--contrats", default=None, help="liste c01,c02,… (défaut : tous)")
    parser.add_argument("--seuil", type=float, default=None,
                        help="surcharge note_min (défaut : eval/seuils.yaml)")
    parser.add_argument("--latence-max-ms", type=float, default=None,
                        help="surcharge latence_p95_max_ms (défaut : eval/seuils.yaml)")
    parser.add_argument("--cout-max-eur", type=float, default=None,
                        help="surcharge cout_moyen_max_eur (défaut : eval/seuils.yaml)")
    parser.add_argument("--sortie", default=None, help="écrit le rapport en JSON")
    parser.add_argument("--historique", default=str(CHEMIN_HISTORIQUE))
    args = parser.parse_args(argv)
    sous_ensemble = re.split(r"[,\s]+", args.contrats.strip()) if args.contrats else None
    try:
        rapport = evaluer(
            args.version,
            n_essais=args.essais,
            seuil=args.seuil,
            latence_max_ms=args.latence_max_ms,
            cout_max_eur=args.cout_max_eur,
            sous_ensemble=sous_ensemble,
            historique=Path(args.historique),
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"GATE MAL CONFIGURÉ : {exc}", file=sys.stderr)
        return 2
    afficher(rapport)
    if args.sortie:
        sortie = Path(args.sortie)
        sortie.parent.mkdir(parents=True, exist_ok=True)
        sortie.write_text(
            json.dumps(rapport.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if rapport.passe else 1


if __name__ == "__main__":
    sys.exit(main())
