"""Tableau de bord : latence, erreurs, score de confiance, trafic par version.

Contrat attendu :

    resume(metriques=None, *, fenetre_s=300, registry=None, seuils=None,
           candidats=None, maintenant=None) -> dict
        Agrège les mesures des ``fenetre_s`` dernières secondes de
        ``MetricsStore`` (``ops/metrics.jsonl`` par défaut) :
        {
          "fenetre_s": 300, "total": 128,
          "par_version": {
             "v1.0.0": {"requetes": 115, "trafic_pct": 89.8, "latence_p50_ms": …,
                        "latence_p95_ms": …, "taux_erreur": 0.02,
                        "score_moyen": null, "score_p10": null,
                        "cout_total_eur": …, "histogramme_score": […],
                        "serie_minute": […]},
             "v2.0.0": {… "score_moyen": 0.84 …}
          },
          "palier": {"version": …, "pourcentage": …, "depuis_s": …,
                     "requetes": …, "duree_min_s": …, "requetes_min": …} | None,
          "alertes": […],
          "candidats": 3,
          "journal": [ …5 derniers événements de déploiement… ]
        }
        Les versions sans trafic dans la fenêtre n'apparaissent pas.
        ``score_moyen`` vaut ``None`` pour une version qui ne produit pas de
        score (v1). Les erreurs sont exclues des latences et des scores.

    python -m ops.dashboard              → affiche le résumé en texte
    python -m ops.dashboard --serve      → page HTML auto-rafraîchie sur :8501
                                           (service ``dashboard`` du docker-compose)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from app.capture import candidats_en_attente
from app.telemetry import Mesure, MetricsStore
from ops.pilotage import debut_palier, detecter_derive, version_surveillee
from ops.registry import Registry
from ops.seuils import ErreurSeuilsPilotage, SeuilsPilotage, charger_seuils_pilotage
from ops.signaux import agreger, filtrer, histogramme, scores, serie_par_minute


def _par_version(mesures: list[Mesure]) -> dict[str, dict[str, Any]]:
    total = len(mesures)
    par_version: dict[str, dict[str, Any]] = {}
    for version in sorted({m.version for m in mesures}):
        du = filtrer(mesures, version)
        a = agreger(du)
        par_version[version] = {
            "requetes": a.requetes,
            "trafic_pct": round(100 * a.requetes / total, 1),
            "latence_p50_ms": a.latence_p50_ms,
            "latence_p95_ms": a.latence_p95_ms,
            "taux_erreur": a.taux_erreur,
            "score_moyen": a.score_moyen,
            "score_p10": a.score_p10,
            "cout_total_eur": a.cout_total_eur,
            "histogramme_score": histogramme(scores(du)),
            "serie_minute": serie_par_minute(du),
        }
    return par_version


def _palier(
    index: dict[str, Any],
    journal: list[dict[str, Any]],
    mesures: list[Mesure],
    seuils: SeuilsPilotage | None,
    maintenant: float,
) -> dict[str, Any] | None:
    canary = index.get("canary")
    if canary is None:
        return None
    debut = debut_palier(journal, canary)
    return {
        "version": canary,
        "pourcentage": index.get("canary_percent"),
        "depuis_s": round(maintenant - debut, 1) if debut is not None else None,
        "requetes": len(filtrer(mesures, canary, depuis_ts=debut)),
        "duree_min_s": seuils.promotion.duree_min_s if seuils else None,
        "requetes_min": seuils.promotion.requetes_min if seuils else None,
    }


def resume(
    metriques: MetricsStore | None = None,
    *,
    fenetre_s: float = 300,
    registry: Registry | None = None,
    seuils: SeuilsPilotage | None = None,
    candidats: Path | None = None,
    maintenant: float | None = None,
) -> dict[str, Any]:
    metriques = metriques or MetricsStore()
    registry = registry or Registry()
    maintenant = maintenant if maintenant is not None else time.time()
    mesures = metriques.lire(depuis_s=fenetre_s)
    index, journal = registry.index(), registry.journal()

    alertes: list[str] = []
    if seuils is None:
        try:
            seuils = charger_seuils_pilotage()
        except ErreurSeuilsPilotage as exc:
            alertes.append(f"seuils invalides : {exc}")
    surveillee = version_surveillee(index)
    if seuils is not None and surveillee is not None:
        debut = debut_palier(journal, surveillee) if index.get("canary") == surveillee else None
        derive = detecter_derive(
            surveillee, filtrer(mesures, surveillee, depuis_ts=debut), seuils.derive,
            minimum=seuils.minimum,
        )
        if derive.niveau in ("critique", "marge"):
            alertes.append(f"{surveillee} : {derive.motif}")

    return {
        "fenetre_s": fenetre_s,
        "total": len(mesures),
        "par_version": _par_version(mesures),
        "palier": _palier(index, journal, mesures, seuils, maintenant),
        "alertes": alertes,
        "candidats": len(candidats_en_attente(candidats)),
        "journal": journal[-5:],
    }


def _ms(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f} ms"


def _score(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def rendre_texte(r: dict[str, Any]) -> str:
    lignes = [f"== Mardik — fenêtre {r['fenetre_s']:.0f} s — {r['total']} requêtes =="]
    if not r["par_version"]:
        lignes.append("aucun trafic dans la fenêtre")
    for version, s in r["par_version"].items():
        lignes.append(
            f"{version:<10} trafic {s['trafic_pct']:5.1f} %  P50 {_ms(s['latence_p50_ms'])}  "
            f"P95 {_ms(s['latence_p95_ms'])}  erreurs {s['taux_erreur'] * 100:.1f} %  "
            f"score {_score(s['score_moyen'])} (P10 {_score(s['score_p10'])})  "
            f"coût {s['cout_total_eur']:.4f} €"
        )
    p = r.get("palier")
    if p:
        depuis = "?" if p["depuis_s"] is None else f"{p['depuis_s']:.0f}"
        lignes.append(
            f"palier : {p['version']} à {p['pourcentage']} % depuis {depuis} s "
            f"({p['requetes']}/{p['requetes_min']} requêtes)"
        )
    if r.get("alertes"):
        lignes.append("alertes :")
        lignes.extend(f"  ! {a}" for a in r["alertes"])
    lignes.append(f"candidats à verser : {r.get('candidats', 0)}")
    lignes.append("journal :")
    for e in r.get("journal", []):
        texte = e.get("resume") or e.get("motif") or ""
        lignes.append(
            f"  {e.get('date', '')}  {e.get('evenement')}  [{e.get('origine', '-')}]  {texte}"
        )
    return "\n".join(lignes)


def rendre_html(r: dict[str, Any]) -> str:
    raise NotImplementedError("dashboard.rendre_html — page HTML auto-rafraîchie")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tableau de bord Mardik")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--fenetre", type=float, default=300)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.serve:
        import uvicorn
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse

        app = FastAPI(title="Mardik dashboard")

        @app.get("/", response_class=HTMLResponse)
        def page() -> str:
            return rendre_html(resume(fenetre_s=args.fenetre))

        @app.get("/api")
        def api() -> dict[str, Any]:
            return resume(fenetre_s=args.fenetre)

        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
        return 0
    r = resume(fenetre_s=args.fenetre)
    print(json.dumps(r, ensure_ascii=False, indent=2) if args.json else rendre_texte(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
