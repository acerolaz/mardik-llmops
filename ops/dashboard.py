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
          "canary": "v2.0.0" | None   (de l'index, même si les métriques sont illisibles),
          "decision": {"action": "rollback|promouvoir|progresser|attendre",
                       "version": …, "active": …, "motif": …,
                       "pourcentage_suivant": …, "constats": […]} | None,
          "seuils": {… ops/seuils_pilotage.yaml …} | None,
          "journal": [ …20 derniers événements de déploiement… ]
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
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Any

from app.capture import candidats_en_attente
from app.telemetry import Mesure, MetricsStore
from ops.pilotage import (
    Derive,
    debut_palier,
    detecter_derive,
    evaluer_palier,
    resume_metier,
    version_surveillee,
)
from ops.registry import ErreurRegistre, Registry
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


_ILLISIBLE = (OSError, json.JSONDecodeError)


def _lu(lire: Callable[[], Any], defaut: Any, alertes: list[str], alerte: str,
        erreurs: tuple[type[Exception], ...] = _ILLISIBLE) -> Any:
    """Source illisible → alerte et ``defaut`` : la page se dégrade, elle ne tombe pas en 500."""
    try:
        return lire()
    except erreurs as exc:
        alertes.append(f"{alerte} : {exc}")
        return defaut


def _palier(
    index: dict[str, Any],
    journal: list[dict[str, Any]],
    lues: list[Mesure],
    seuils: SeuilsPilotage | None,
    maintenant: float,
) -> dict[str, Any] | None:
    """Le palier canary en cours, compté sur **tout le palier** et non sur la
    fenêtre de surveillance : ``ops.deploy.tour`` décide sur ce même compte."""
    canary = index.get("canary")
    if canary is None:
        return None
    debut = debut_palier(journal, canary)
    requetes = 0 if debut is None else len(filtrer(lues, canary, depuis_ts=debut))
    return {
        "version": canary,
        "pourcentage": index.get("canary_percent"),
        "depuis_s": round(maintenant - debut, 1) if debut is not None else None,
        "requetes": requetes,
        "duree_min_s": seuils.promotion.duree_min_s if seuils else None,
        "requetes_min": seuils.promotion.requetes_min if seuils else None,
    }


def _decision(
    index: dict[str, Any],
    journal: list[dict[str, Any]],
    lues: list[Mesure],
    seuils: SeuilsPilotage | None,
    derive: Derive | None,
    maintenant: float,
) -> dict[str, Any] | None:
    """Le verdict du pilote, avec ses propres fonctions et sur le même compte que
    ``ops.deploy.tour`` : l'écran montre ce que le pilote décidera. Une dérive
    critique du canary l'emporte sur le palier."""
    canary = index.get("canary")
    if canary is None or seuils is None:
        return None
    active = index.get("active")
    if derive is not None and derive.version == canary and derive.critique:
        return {"action": "rollback", "version": canary, "active": active, "motif": derive.motif,
                "pourcentage_suivant": None, "constats": [c.to_dict() for c in derive.constats]}
    debut = debut_palier(journal, canary)
    if debut is None:
        return None
    depuis_s = maintenant - debut
    d = evaluer_palier(
        canary,
        filtrer(lues, canary, depuis_ts=debut),
        filtrer(lues, active or "", depuis_ts=debut),
        seuils,
        depuis_s=depuis_s,
        pourcentage=int(index.get("canary_percent") or 0),
    )
    return {"action": d.action, "version": canary, "active": active, "motif": d.motif,
            "pourcentage_suivant": d.pourcentage_suivant,
            "constats": [c.to_dict() for c in d.constats]}


def resume(
    metriques: MetricsStore | None = None,
    *,
    fenetre_s: float | None = None,
    registry: Registry | None = None,
    seuils: SeuilsPilotage | None = None,
    candidats: Path | None = None,
    maintenant: float | None = None,
) -> dict[str, Any]:
    """``fenetre_s`` à ``None`` (défaut) : celle des seuils chargés (``ops/seuils_pilotage.yaml``),
    sinon 300 s si les seuils sont invalides — même convention que ``ops.deploy.surveiller``,
    pour que le tableau de bord affiche la même fenêtre que le pilote."""
    metriques = metriques or MetricsStore()
    registry = registry or Registry()
    maintenant = maintenant if maintenant is not None else time.time()

    alertes: list[str] = []
    if seuils is None:
        try:
            seuils = charger_seuils_pilotage()
        except ErreurSeuilsPilotage as exc:
            alertes.append(f"seuils invalides : {exc}")
    fenetre = fenetre_s if fenetre_s is not None else (seuils.fenetre_s if seuils else 300)
    index, journal = _lu(lambda: (registry.index(), registry.journal()), ({}, []), alertes,
                         "registre illisible", (ErreurRegistre, *_ILLISIBLE))
    # Une seule lecture de metrics.jsonl : la fenêtre, et tout le palier du canary
    # s'il a commencé avant (palier et verdict se comptent sur le palier entier).
    canary = index.get("canary")
    debut_canary = debut_palier(journal, canary) if canary else None
    portee = fenetre if debut_canary is None else max(fenetre, max(maintenant - debut_canary, 0) + 1)
    lues = _lu(lambda: metriques.lire(depuis_s=portee), None, alertes, "métriques illisibles")
    lisibles = lues is not None                # illisibles : ni palier ni verdict, plutôt qu'un faux 0
    lues = lues or []
    seuil_fenetre = time.time() - fenetre      # même borne que MetricsStore.lire(depuis_s=fenetre)
    mesures = [m for m in lues if m.ts >= seuil_fenetre] if fenetre else lues

    derive: Derive | None = None
    surveillee = version_surveillee(index)
    if seuils is not None and surveillee is not None:
        debut = debut_palier(journal, surveillee) if index.get("canary") == surveillee else None
        derive = detecter_derive(
            surveillee, filtrer(mesures, surveillee, depuis_ts=debut), seuils.derive,
            minimum=seuils.minimum,
        )
        if derive.niveau == "marge":
            score = next(c for c in derive.constats if c.signal == "score_moyen")
            alertes.append(resume_metier(
                "alerte", version=surveillee, valeur=score.valeur, seuil=score.seuil,
            ))
        elif derive.niveau == "critique":
            c = derive.principal
            alertes.append(resume_metier(
                "derive_critique", version=surveillee, signal=c.signal,
                valeur=c.valeur, seuil=c.seuil,
            ))

    palier = _palier(index, journal, lues, seuils, maintenant) if lisibles else None
    candidats_n = _lu(lambda: len(candidats_en_attente(candidats)), 0, alertes,
                      "candidats illisibles")
    decision = _decision(index, journal, lues, seuils, derive, maintenant) if lisibles else None
    return {
        "fenetre_s": fenetre,
        "total": len(mesures),
        "par_version": _par_version(mesures),
        "palier": palier,
        "canary": canary,
        "decision": decision,
        "seuils": seuils.to_dict() if seuils else None,
        "alertes": alertes,
        "candidats": candidats_n,
        "journal": journal[-20:],
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


_COULEURS = ("#4c78a8", "#f58518", "#54a24b", "#b279a2")

_STYLE = """
:root { --fond: #fafafa; --texte: #1d1d1f; --carte: #fff; --trait: #d0d0d5; --alerte: #b42318; }
@media (prefers-color-scheme: dark) {
  :root { --fond: #16161a; --texte: #ececf1; --carte: #202027; --trait: #3a3a44; }
}
body { font: 14px/1.4 system-ui, sans-serif; margin: 0; padding: 16px;
       background: var(--fond); color: var(--texte); }
h1 { font-size: 18px; } h2 { font-size: 15px; margin-top: 24px; }
table { border-collapse: collapse; width: 100%; background: var(--carte); }
th, td { border-bottom: 1px solid var(--trait); padding: 6px 8px; text-align: left; }
svg { display: block; } svg.jauge { width: 100%; max-width: 600px; height: 20px; }
svg.histo, svg.spark { width: 120px; height: 36px; }
svg.histo rect { fill: #4c78a8; } svg.spark polyline { fill: none; stroke: #f58518; }
.alerte { color: var(--alerte); font-weight: 600; } .vide { opacity: .6; }
"""


def _svg_jauge(par_version: dict[str, dict[str, Any]]) -> str:
    x, parts = 0.0, []
    for i, (version, s) in enumerate(par_version.items()):
        largeur = s["trafic_pct"] * 3
        parts.append(
            f'<rect x="{x:.1f}" y="0" width="{largeur:.1f}" height="20" '
            f'fill="{_COULEURS[i % len(_COULEURS)]}"><title>{escape(version)} '
            f'{s["trafic_pct"]} %</title></rect>'
        )
        x += largeur
    return (f'<svg class="jauge" viewBox="0 0 300 20" role="img" '
            f'aria-label="part de trafic par version">{"".join(parts)}</svg>')


def _svg_histogramme(comptes: list[int], largeur: int = 120, hauteur: int = 36) -> str:
    haut = max(comptes) or 1
    pas = largeur / len(comptes)
    rects = "".join(
        f'<rect x="{i * pas:.1f}" y="{hauteur - c / haut * hauteur:.1f}" '
        f'width="{pas - 1:.1f}" height="{c / haut * hauteur:.1f}"/>'
        for i, c in enumerate(comptes)
    )
    return (f'<svg class="histo" viewBox="0 0 {largeur} {hauteur}" role="img" '
            f'aria-label="distribution du score, de 0 à 1">{rects}</svg>')


def _svg_sparkline(valeurs: list[float | None], largeur: int = 120, hauteur: int = 36) -> str:
    points = [v for v in valeurs if v is not None]
    if len(points) < 2:
        return '<span class="vide">—</span>'
    bas, haut = min(points), max(points)
    etendue = (haut - bas) or 1
    pas = largeur / (len(points) - 1)
    coords = " ".join(
        f"{i * pas:.1f},{hauteur - (v - bas) / etendue * hauteur:.1f}"
        for i, v in enumerate(points)
    )
    return (f'<svg class="spark" viewBox="0 0 {largeur} {hauteur}">'
            f'<polyline points="{coords}"/></svg>')


def rendre_html(r: dict[str, Any]) -> str:
    lignes_versions = []
    for version, s in r["par_version"].items():
        serie = s["serie_minute"]
        lignes_versions.append(
            "<tr>"
            f"<td>{escape(version)}</td><td>{s['trafic_pct']} %</td>"
            f"<td>{_ms(s['latence_p50_ms'])} / {_ms(s['latence_p95_ms'])}</td>"
            f"<td>{s['taux_erreur'] * 100:.1f} %</td>"
            f"<td>{_score(s['score_moyen'])} (P10 {_score(s['score_p10'])})</td>"
            f"<td>{_svg_histogramme(s['histogramme_score'])}</td>"
            f"<td>{_svg_sparkline([p['latence_p95_ms'] for p in serie])}</td>"
            f"<td>{_svg_sparkline([p['taux_erreur'] for p in serie])}</td>"
            "</tr>"
        )
    if lignes_versions:
        tableau = (
            "<table><tr><th>Version</th><th>Trafic</th><th>P50 / P95</th><th>Erreurs</th>"
            "<th>Score moyen</th><th>Distribution du score</th><th>P95 / min</th>"
            "<th>Erreurs / min</th></tr>" + "".join(lignes_versions) + "</table>"
        )
    else:
        tableau = '<p class="vide">aucun trafic dans la fenêtre</p>'
    p = r.get("palier")
    palier = (
        f"<p>palier : {escape(p['version'])} à {p['pourcentage']} % depuis "
        f"{p['depuis_s'] if p['depuis_s'] is not None else '?'} s "
        f"({p['requetes']}/{p['requetes_min']} requêtes)</p>"
        if p else "<p>palier : aucun canary en cours</p>"
    )
    alertes = "".join(f'<li class="alerte">{escape(a)}</li>' for a in r.get("alertes", []))
    alertes = alertes or '<li class="vide">aucune</li>'
    journal = "".join(
        f"<tr><td>{escape(str(e.get('date', '')))}</td><td>{escape(str(e.get('evenement')))}"
        f"</td><td>{escape(str(e.get('origine', '-')))}</td>"
        f"<td>{escape(str(e.get('resume') or e.get('motif') or ''))}</td></tr>"
        for e in reversed(r.get("journal", []))
    )
    return (
        "<!doctype html>\n<html lang=\"fr\"><head><meta charset=\"utf-8\">"
        '<meta http-equiv="refresh" content="5">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Pilotage Mardik</title><style>{_STYLE}</style></head><body>"
        f"<h1>Pilotage Mardik — fenêtre {r['fenetre_s']:.0f} s, {r['total']} requêtes</h1>"
        f"<h2>Part de trafic</h2>{_svg_jauge(r['par_version'])}"
        f"<h2>Par version</h2>{tableau}"
        f"<h2>Palier canary</h2>{palier}"
        f"<h2>Alertes</h2><ul>{alertes}</ul>"
        f"<h2>Candidats à verser</h2><p>{r.get('candidats', 0)}</p>"
        "<h2>Journal de pilotage</h2><table><tr><th>Date</th><th>Événement</th>"
        f"<th>Origine</th><th>Résumé</th></tr>{journal}</table>"
        "</body></html>"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tableau de bord Mardik")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--fenetre", type=float, default=None,
                        help="défaut : celle des seuils de pilotage (sinon 300 s)")
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
