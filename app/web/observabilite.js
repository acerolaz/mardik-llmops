import { esc, fmtDate, fmtDuree, fmtEur, fmtMs, fmtNombre, fmtPct, icone, initTheme, peindre, surveillerResume } from "/static/commun.js";

const $ = (s) => document.querySelector(s);
const LIENS = [["traces", "Traces"], ["issues", "Erreurs (issues)"], ["performance", "Performance"]];

function etatSentry(s) {
  if (s.actif) return `<p class="statut ok">${icone("ok")}Sentry actif (environnement ${esc(s.environnement)}) : traces et erreurs y sont envoyées, sans le texte des contrats.</p>`;
  return `<p class="statut neutre">${icone("warn")}<span>Sentry non configuré : traces et logs restent sur la console du serveur.
    Définir <code>SENTRY_DSN</code> pour activer.</span></p>`;
}

function ligneRoute(s) {
  return `<tr>
    <th scope="row"><code>${esc(s.route)}</code></th><td>${esc(s.version)}</td>
    <td class="chiffre">${esc(s.requetes)}</td><td class="chiffre">${fmtPct(s.taux_erreur * 100)}</td>
    <td class="chiffre">${fmtMs(s.latence_p50_ms)}</td><td class="chiffre">${fmtMs(s.latence_p95_ms)}</td>
    <td class="chiffre">${fmtNombre(s.appels_llm_moyen, 1)}</td><td class="chiffre">${fmtNombre(s.tokens_moyen)}</td>
    <td class="chiffre">${fmtPct(s.taux_tronque * 100)}</td><td class="chiffre">${fmtEur(s.cout_total_eur)}</td>
  </tr>`;
}

function ligneErreur(e) {
  return `<tr><td class="chiffre">${esc(fmtDate(e.date))}</td><td><code>${esc(e.route)}</code></td>
    <td>${esc(e.version)}</td><td class="chiffre">${fmtMs(e.latence_ms)}</td></tr>`;
}

function liens(s) {
  if (!s.actif) return '<p class="vide">Liens disponibles une fois Sentry configuré.</p>';
  if (!s.liens) return '<p class="vide">Définir <code>SENTRY_UI_URL</code> pour afficher les liens vers Sentry.</p>';
  return LIENS.map(([cle, libelle]) =>
    `<a class="bouton" href="${esc(s.liens[cle])}" target="_blank" rel="noopener noreferrer">${libelle}<span class="visuellement-cache"> (nouvel onglet)</span></a>`).join("");
}

function rendre(r) {
  $("#fenetre").textContent = `fenêtre ${fmtDuree(r.fenetre_s)}`;
  peindre($("#etat-sentry"), etatSentry(r.sentry));
  peindre($("#routes"), r.par_route.length
    ? r.par_route.map(ligneRoute).join("")
    : '<tr><td colspan="10" class="vide">Aucun trafic dans la fenêtre.</td></tr>');
  peindre($("#erreurs"), r.erreurs_recentes.length
    ? r.erreurs_recentes.map(ligneErreur).join("")
    : '<tr><td colspan="4" class="vide">Aucune erreur dans la fenêtre.</td></tr>');
  peindre($("#liens"), liens(r.sentry));
}

function indisponible() {
  peindre($("#etat-sentry"), `<p class="indispo">${icone("warn")}Résumé d'observabilité indisponible — nouvel essai dans 5 s.</p>`);
}

$("#logo").innerHTML = icone("scale");
initTheme($("#theme"));
surveillerResume({
  url: "/observabilite/resume", onData: rendre, onErreur: indisponible,
  boutonPause: $("#pause"), horodatage: $("#maj"),
});
