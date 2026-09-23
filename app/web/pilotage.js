import {
  esc, fmtDuree, fmtEur, fmtMs, fmtPct, fmtScore, icone, initTheme, peindre, rendrePalier,
  rendreTrafic, surveillerResume,
} from "/static/commun.js";

const $ = (s) => document.querySelector(s);
const BADGES = { promotion: "ok", canary: "info", rollback: "danger", alerte: "warn", seuils: "neutre" };
const FILTRES = {
  tous: null,
  deploiement: ["canary", "promotion", "publication", "installation"],
  rollback: ["rollback"],
  seuils: ["seuils"],
};
const VERDICTS = {
  promouvoir: { classe: "ok", icone: "ok", libelle: "Promouvoir" },
  progresser: { classe: "ok", icone: "ok", libelle: "Progresser" },
  attendre: { classe: "warn", icone: "pause", libelle: "Attendre" },
  rollback: { classe: "danger", icone: "danger", libelle: "Revenir en arrière" },
};
const pctErreur = (v) => fmtPct(v == null ? null : v * 100);
const SIGNAUX = {
  score_moyen: { libelle: "Score moyen", sens: "≥", fmt: fmtScore, delta: (d) => fmtScore(d) },
  score_p10: { libelle: "Score p10", sens: "≥", fmt: fmtScore, delta: (d) => fmtScore(d) },
  taux_erreur: { libelle: "Taux d'erreur", sens: "≤", fmt: pctErreur, delta: (d) => `${fmtScore(d * 100)} pt` },
  latence_p95_ms: { libelle: "Latence P95", sens: "≤", fmt: fmtMs, delta: (d) => fmtMs(d) },
};
const ORDRE = ["score_moyen", "score_p10", "taux_erreur", "latence_p95_ms"];

let filtre = "tous";
let dernier = null;

// --- Verdict ----------------------------------------------------------------
function rendreVerdict(r) {
  const d = r.decision;
  if (!d && r.palier) {
    return `<p class="verdict-titre statut warn">${icone("warn")}Verdict indisponible</p>
      <p>Un canary est en cours (${esc(r.palier.version)} à ${esc(r.palier.pourcentage)} %), mais le verdict ne peut pas être calculé : voir les alertes.</p>${rendreTrafic(r.par_version)}`;
  }
  if (!d) {
    return `<p class="verdict-titre statut neutre">${icone("ok")}Aucun canary en cours</p>
      <p>La version active reçoit tout le trafic : rien à décider.</p>${rendreTrafic(r.par_version)}`;
  }
  const v = VERDICTS[d.action];
  const suite = d.action === "progresser" ? ` vers ${esc(d.pourcentage_suivant)} %` : "";
  return `<p class="verdict-titre statut ${v.classe}">${icone(v.icone)}${v.libelle}${suite}</p>
    <p>${esc(d.motif)}</p>
    ${rendreTrafic(r.par_version)}
    ${d.action === "attendre" && !d.constats.length ? rendrePalier(r.palier) : ""}`;
}

// --- Canary vs active -------------------------------------------------------
function bullet(signal, c, active) {
  const echelle = Math.max(c.seuil, c.valeur ?? 0, active ?? 0) * 1.15 || 1;
  const pos = (x) => (x == null ? 0 : Math.min(100, (x / echelle) * 100)).toFixed(2);
  const s = SIGNAUX[signal];
  return `<svg class="bullet-svg" viewBox="0 0 100 12" preserveAspectRatio="none" role="img"
      aria-label="${esc(s.libelle)} du canary : ${esc(s.fmt(c.valeur))}, seuil ${s.sens} ${esc(s.fmt(c.seuil))}">
    <rect class="bullet-fond" x="0" y="2" width="100" height="8" rx="2"/>
    <rect class="bullet-barre ${c.ok ? "v2" : "ko"}" x="0" y="3.5" width="${pos(c.valeur)}" height="5" rx="1.5"/>
    <line class="bullet-cible" x1="${pos(c.seuil)}" x2="${pos(c.seuil)}" y1="0" y2="12" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

function ecart(signal, canary, active) {
  if (canary == null || active == null) return "—";
  const d = canary - active;
  return `${d > 0 ? "+" : ""}${SIGNAUX[signal].delta(d)}`;
}

const coutParAnalyse = (s) => (s && s.requetes ? s.cout_total_eur / s.requetes : null);

function rendreComparaison(r) {
  const d = r.decision;
  if (!d) return '<p class="vide">Aucun canary en cours : rien à comparer.</p>';
  const statsCanary = r.par_version[d.version];
  const statsActive = d.active ? r.par_version[d.active] : null;
  const constats = Object.fromEntries(d.constats.map((c) => [c.signal, c]));
  const lignes = ORDRE.map((signal) => {
    const s = SIGNAUX[signal];
    const c = constats[signal];
    const valeurCanary = c ? c.valeur : statsCanary?.[signal] ?? null;
    const valeurActive = statsActive?.[signal] ?? null;
    const etat = !c
      ? '<span class="statut neutre">en attente</span>'
      : c.ok ? `<span class="statut ok">${icone("ok")}tenu</span>`
        : `<span class="statut danger">${icone("danger")}non tenu</span>`;
    return `<tr>
      <th scope="row">${s.libelle}</th>
      <td class="chiffre">${esc(s.fmt(valeurActive))}</td>
      <td class="chiffre">${esc(s.fmt(valeurCanary))}</td>
      <td>${c ? bullet(signal, c, valeurActive) : ""}</td>
      <td class="chiffre">${c ? `${s.sens} ${esc(s.fmt(c.seuil))}` : "—"}</td>
      <td class="chiffre">${esc(ecart(signal, valeurCanary, valeurActive))}</td>
      <td>${etat}</td>
    </tr>`;
  });
  const cCanary = coutParAnalyse(statsCanary);
  const cActive = coutParAnalyse(statsActive);
  const ratio = cCanary != null && cActive ? `×${fmtScore(cCanary / cActive)}` : "—";
  lignes.push(`<tr>
    <th scope="row">Coût / analyse</th>
    <td class="chiffre">${esc(fmtEur(cActive))}</td><td class="chiffre">${esc(fmtEur(cCanary))}</td>
    <td></td><td>—</td><td class="chiffre">${ratio}</td>
    <td><span class="statut neutre">info, hors verdict</span></td>
  </tr>`);
  return `<div class="table-defilante"><table>
    <thead><tr><th scope="col">Signal</th><th scope="col">Active ${esc(d.active ?? "—")}</th>
      <th scope="col">Canary ${esc(d.version)}</th><th scope="col">Canary vs seuil</th>
      <th scope="col">Seuil</th><th scope="col">Écart</th><th scope="col">État</th></tr></thead>
    <tbody>${lignes.join("")}</tbody></table></div>
    <p class="legende">Trait vertical : le seuil. Coût total canary ${esc(fmtEur(statsCanary?.cout_total_eur))},
      active ${esc(fmtEur(statsActive?.cout_total_eur))} sur la fenêtre.</p>`;
}

// --- Rétroactions et seuils -------------------------------------------------
function derniere(journal, evenements) {
  const e = journal.slice().reverse().find((x) => evenements.includes(x.evenement));
  if (!e?.date) return "aucune dans les 20 derniers événements";
  const d = new Date(e.date);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("fr-FR");
}

function rendreRetroactions(r) {
  const s = r.seuils;
  if (!s) return `<p class="indispo">${icone("warn")}Seuils invalides : voir les alertes.</p>`;
  const lignes = [
    [`${icone("danger")}Rollback automatique`,
      `score &lt; ${fmtScore(s.derive.score_min)} · erreurs &gt; ${pctErreur(s.derive.taux_erreur_max)} · P95 &gt; ${fmtMs(s.derive.latence_p95_max_ms)}`,
      `dernier : ${esc(derniere(r.journal, ["rollback"]))}`],
    [`${icone("ok")}Promotion par paliers`,
      `paliers ${s.promotion.paliers.map(esc).join(" → ")} % · ${esc(s.promotion.requetes_min)} requêtes et ${fmtDuree(s.promotion.duree_min_s)} minimum`,
      `dernière : ${esc(derniere(r.journal, ["canary", "promotion"]))}`],
    [`${icone("warn")}Capture vers le golden dataset`,
      `confiance &lt; ${fmtScore(s.capture.score_max)} → candidat, validé par un juriste`,
      `${esc(r.candidats)} candidat(s) en attente`],
  ];
  return `<div class="table-defilante"><table>
    <thead><tr><th scope="col">Rétroaction</th><th scope="col">Déclencheur</th><th scope="col">État</th></tr></thead>
    <tbody>${lignes.map(([nom, regle, etat]) => `<tr><th scope="row">${nom}</th><td>${regle}</td><td>${etat}</td></tr>`).join("")}</tbody>
  </table></div>`;
}

function rendreSeuils(s) {
  if (!s) return '<p class="vide">Seuils indisponibles.</p>';
  const lignes = [];
  for (const section of ["derive", "promotion", "capture"]) {
    for (const [cle, valeur] of Object.entries(s[section])) {
      lignes.push(`<tr><td>${section}</td><td><code>${esc(cle)}</code></td><td class="chiffre">${esc(Array.isArray(valeur) ? valeur.join(", ") : valeur)}</td></tr>`);
    }
  }
  return `<p>Motif : ${esc(s.motif)}</p><div class="table-defilante"><table>
    <thead><tr><th scope="col">Section</th><th scope="col">Clé</th><th scope="col">Valeur</th></tr></thead>
    <tbody>${lignes.join("")}</tbody></table></div>`;
}

// --- Traçabilité ------------------------------------------------------------
function ligneJournal(e) {
  const date = (() => {
    if (!e.date) return "—";
    const d = new Date(e.date);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("fr-FR");
  })();
  const badge = BADGES[e.evenement] ?? "neutre";
  const origine = e.origine === "auto" ? "automatique" : e.origine ? "humaine" : "—";
  return `<tr>
    <td class="chiffre">${esc(date)}</td>
    <td><span class="badge ${badge}">${esc(e.evenement ?? "—")}</span></td>
    <td><span class="badge neutre">${esc(origine)}</span></td>
    <td>${esc(e.resume ?? e.motif ?? "")}</td>
  </tr>`;
}

function rendreJournal(journal) {
  const types = FILTRES[filtre];
  const lignes = journal.filter((e) => !types || types.includes(e.evenement)).reverse();
  peindre($("#journal"), lignes.length
    ? lignes.map(ligneJournal).join("")
    : '<tr><td colspan="4" class="vide">Aucun événement pour ce filtre.</td></tr>');
}

document.querySelectorAll("[data-filtre]").forEach((bouton) => {
  bouton.addEventListener("click", () => {
    filtre = bouton.dataset.filtre;
    document.querySelectorAll("[data-filtre]").forEach((b) => b.setAttribute("aria-pressed", String(b === bouton)));
    if (dernier) rendreJournal(dernier.journal);
  });
});

// --- Boucle -----------------------------------------------------------------
function rendre(r) {
  dernier = r;
  $("#fenetre").textContent = `fenêtre ${fmtDuree(r.fenetre_s)} · ${r.total} requête(s)`;
  peindre($("#verdict"), rendreVerdict(r));
  peindre($("#alertes"), r.alertes.length
    ? `<ul class="alertes">${r.alertes.map((a) => `<li>${icone("danger")}${esc(a)}</li>`).join("")}</ul>`
    : `<p class="statut ok">${icone("ok")}Aucune alerte</p>`);
  peindre($("#comparaison"), rendreComparaison(r));
  peindre($("#retroactions"), rendreRetroactions(r));
  peindre($("#seuils-contenu"), rendreSeuils(r.seuils));
  rendreJournal(r.journal);
}

function indisponible() {
  peindre($("#verdict"), `<p class="indispo">${icone("warn")}Résumé de pilotage indisponible — nouvel essai dans 5 s.</p>`);
}

$("#logo").innerHTML = icone("scale");
initTheme($("#theme"));
surveillerResume({ onData: rendre, onErreur: indisponible, boutonPause: $("#pause"), horodatage: $("#maj") });
