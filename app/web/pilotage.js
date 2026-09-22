import {
  couleurVersion, esc, fmtDuree, fmtEur, fmtMs, fmtPct, fmtScore, icone, initTheme, peindre,
  rendrePalier, rendreTrafic, surveillerResume,
} from "/static/commun.js";

const $ = (s) => document.querySelector(s);
const BADGES = { promotion: "ok", canary: "info", rollback: "danger", alerte: "warn", seuils: "neutre" };

function histogramme(comptes) {
  const haut = Math.max(0, ...comptes);
  if (!haut) return '<span class="vide">—</span>';
  const pas = 120 / comptes.length;
  const barres = comptes.map((c, i) => {
    const h = (c / haut) * 36;
    const de = (i / comptes.length).toFixed(1);
    const a = ((i + 1) / comptes.length).toFixed(1);
    return `<rect x="${(i * pas).toFixed(1)}" y="${(36 - h).toFixed(1)}" width="${(pas - 1).toFixed(1)}" height="${h.toFixed(1)}"><title>${de}–${a} : ${c}</title></rect>`;
  });
  return `<svg class="mini" viewBox="0 0 120 36" preserveAspectRatio="none" role="img" aria-label="Distribution du score, de 0 à 1">${barres.join("")}</svg>`;
}

function sparkline(valeurs, libelle, fmt) {
  const points = valeurs.filter((v) => v != null);
  if (points.length < 2) return '<span class="vide">—</span>';
  const bas = Math.min(...points);
  const etendue = Math.max(...points) - bas || 1;
  const pas = 120 / (points.length - 1);
  const coords = points.map((v, i) => `${(i * pas).toFixed(1)},${(34 - ((v - bas) / etendue) * 32).toFixed(1)}`);
  return `<svg class="mini" viewBox="0 0 120 36" preserveAspectRatio="none" role="img"
    aria-label="${libelle}, dernière valeur ${fmt(points.at(-1))}"><polyline points="${coords.join(" ")}"/></svg>`;
}

function carteVersion([version, s]) {
  const score = s.score_moyen == null
    ? "— <small>(v1 ne produit pas de score)</small>"
    : `${fmtScore(s.score_moyen)} <small>(P10 ${fmtScore(s.score_p10)})</small>`;
  return `<article class="carte version ${couleurVersion(version)}">
    <h3>${esc(version)}</h3>
    <dl class="stats">
      <div><dt>Trafic</dt><dd>${fmtPct(s.trafic_pct)} <small>(${esc(s.requetes)} req.)</small></dd></div>
      <div><dt>Latence P50 / P95</dt><dd>${fmtMs(s.latence_p50_ms)} / ${fmtMs(s.latence_p95_ms)}</dd></div>
      <div><dt>Taux d'erreur</dt><dd>${fmtPct(s.taux_erreur * 100)}</dd></div>
      <div><dt>Score moyen</dt><dd>${score}</dd></div>
      <div><dt>Coût total</dt><dd>${fmtEur(s.cout_total_eur)}</dd></div>
    </dl>
    <div class="mini-graphes">
      <figure><figcaption>Distribution du score</figcaption>${histogramme(s.histogramme_score)}</figure>
      <figure><figcaption>P95 par minute</figcaption>${sparkline(s.serie_minute.map((p) => p.latence_p95_ms), "P95 par minute", fmtMs)}</figure>
      <figure><figcaption>Erreurs par minute</figcaption>${sparkline(s.serie_minute.map((p) => p.taux_erreur * 100), "Erreurs par minute", fmtPct)}</figure>
    </div>
  </article>`;
}

function ligneJournal(e) {
  const date = (() => {
    if (!e.date) return "—";
    const d = new Date(e.date);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("fr-FR");
  })();
  const badge = BADGES[e.evenement] ?? "neutre";
  return `<tr>
    <td class="chiffre">${esc(date)}</td>
    <td><span class="badge ${badge}">${esc(e.evenement ?? "—")}</span></td>
    <td>${esc(e.origine ?? "—")}</td>
    <td>${esc(e.resume ?? e.motif ?? "")}</td>
  </tr>`;
}

function rendre(r) {
  $("#fenetre").textContent = `fenêtre ${fmtDuree(r.fenetre_s)} · ${r.total} requête(s)`;
  peindre($("#alertes"), r.alertes.length
    ? `<ul class="alertes">${r.alertes.map((a) => `<li>${icone("danger")}${esc(a)}</li>`).join("")}</ul>`
    : `<p class="statut ok">${icone("ok")}Aucune alerte</p>`);
  peindre($("#trafic"), rendreTrafic(r.par_version));
  peindre($("#palier"), rendrePalier(r.palier));
  $("#candidats").textContent = String(r.candidats);
  const versions = Object.entries(r.par_version);
  peindre($("#versions"), versions.length
    ? versions.map(carteVersion).join("")
    : '<p class="carte vide">Aucun trafic dans la fenêtre — lancez <code>make traffic</code> pour alimenter le tableau de bord.</p>');
  peindre($("#journal"), r.journal.length
    ? r.journal.slice().reverse().map(ligneJournal).join("")
    : '<tr><td colspan="4" class="vide">Journal vide.</td></tr>');
}

function indisponible() {
  peindre($("#alertes"), `<p class="indispo">${icone("warn")}Résumé de pilotage indisponible — nouvel essai dans 5 s.</p>`);
}

$("#logo").innerHTML = icone("scale");
initTheme($("#theme"));
surveillerResume({ onData: rendre, onErreur: indisponible, boutonPause: $("#pause"), horodatage: $("#maj") });
