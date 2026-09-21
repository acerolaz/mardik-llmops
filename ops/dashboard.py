"""Tableau de bord : latence, erreurs, score de confiance, trafic par version. [STUB]

Contrat attendu :

    resume(metriques=None, *, fenetre_s=300) -> dict
        Agrège les mesures des ``fenetre_s`` dernières secondes de
        ``MetricsStore`` (``ops/metrics.jsonl`` par défaut) :
        {
          "fenetre_s": 300, "total": 128,
          "par_version": {
             "v1.0.0": {"requetes": 115, "trafic_pct": 89.8, "latence_p50_ms": …,
                        "latence_p95_ms": …, "taux_erreur": 0.02,
                        "score_moyen": null, "cout_total_eur": …},
             "v2.0.0": {… "score_moyen": 0.84 …}
          },
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
from statistics import median
from typing import Any

from app.telemetry import MetricsStore
from ops.registry import Registry


def resume(
    metriques: MetricsStore | None = None,
    *,
    fenetre_s: float = 300,
    registry: Registry | None = None,
) -> dict[str, Any]:
    store = metriques or MetricsStore()
    reg = registry or Registry()
    mesures = store.lire(depuis_s=fenetre_s)
    total = len(mesures)

    par_version: dict[str, dict[str, Any]] = {}
    for m in mesures:
        bloc = par_version.setdefault(
            m.version,
            {"requetes": 0, "latences": [], "scores": [], "erreurs": 0, "cout_total_eur": 0.0},
        )
        bloc["requetes"] += 1
        bloc["cout_total_eur"] += float(m.cout_eur)
        if m.erreur:
            bloc["erreurs"] += 1
            continue
        bloc["latences"].append(float(m.latence_ms))
        if m.score is not None:
            bloc["scores"].append(float(m.score))

    resultat: dict[str, dict[str, Any]] = {}
    for version, bloc in par_version.items():
        req = bloc["requetes"]
        latences = bloc["latences"]
        scores = bloc["scores"]
        resultat[version] = {
            "requetes": req,
            "trafic_pct": round((req / total * 100), 1) if total else 0.0,
            "latence_p50_ms": round(float(median(latences)), 1) if latences else 0.0,
            "latence_p95_ms": round(_p95(latences), 1),
            "taux_erreur": round(bloc["erreurs"] / req, 3) if req else 0.0,
            "score_moyen": round(sum(scores) / len(scores), 3) if scores else None,
            "cout_total_eur": round(bloc["cout_total_eur"], 6),
        }

    return {
        "fenetre_s": fenetre_s,
        "total": total,
        "par_version": resultat,
        "journal": reg.journal()[-5:],
    }


def rendre_texte(r: dict[str, Any]) -> str:
    lignes = [f"Fenêtre: {r['fenetre_s']} s — total: {r['total']} requêtes"]
    for version, bloc in sorted(r.get("par_version", {}).items()):
        score = "n/a" if bloc["score_moyen"] is None else f"{bloc['score_moyen']:.3f}"
        lignes.append(
            f"- {version} | trafic={bloc['trafic_pct']:.1f}% ({bloc['requetes']})"
            f" | latence p50/p95={bloc['latence_p50_ms']:.1f}/{bloc['latence_p95_ms']:.1f} ms"
            f" | erreurs={bloc['taux_erreur']:.3f} | score={score}"
        )
    if r.get("journal"):
        lignes.append("Derniers événements:")
        for entree in r["journal"]:
            lignes.append(f"  - {entree.get('date', '')} {entree.get('evenement', '')}")
    return "\n".join(lignes)


def rendre_html(r: dict[str, Any]) -> str:
    lignes = ""
    for version, bloc in sorted(r.get("par_version", {}).items()):
        score = "n/a" if bloc["score_moyen"] is None else f"{bloc['score_moyen']:.3f}"
        lignes += (
            "<tr>"
            f"<td>{version}</td>"
            f"<td>{bloc['requetes']}</td>"
            f"<td>{bloc['trafic_pct']:.1f}%</td>"
            f"<td>{bloc['latence_p50_ms']:.1f}</td>"
            f"<td>{bloc['latence_p95_ms']:.1f}</td>"
            f"<td>{bloc['taux_erreur']:.3f}</td>"
            f"<td>{score}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <meta http-equiv="refresh" content="5" />
  <title>Mardik dashboard</title>
</head>
<body>
  <h1>Mardik dashboard</h1>
  <p>Fenêtre: {r["fenetre_s"]} s — total: {r["total"]} requêtes</p>
  <table border="1" cellspacing="0" cellpadding="6">
    <thead>
      <tr>
        <th>Version</th><th>Requêtes</th><th>Trafic</th>
        <th>P50 ms</th><th>P95 ms</th><th>Taux erreur</th><th>Score moyen</th>
      </tr>
    </thead>
    <tbody>{lignes}</tbody>
  </table>
</body>
</html>"""


def _p95(valeurs: list[float]) -> float:
    if not valeurs:
        return 0.0
    tri = sorted(valeurs)
    return tri[min(len(tri) - 1, int(round(0.95 * len(tri) + 0.5)) - 1)]


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
