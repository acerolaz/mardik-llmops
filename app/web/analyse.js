import {
  arrondi2, esc, fmtEur, fmtMs, fmtScore, icone, initTheme, peindre, rendreP95, rendrePalier,
  rendreTrafic, surveillerResume,
} from "/static/commun.js";

const LIMITE_V1 = 16000;      // models/v1/config.yaml : contexte_max_caracteres (au-delà, v1 coupe)
const SEUIL_RELECTURE = 0.6;  // models/v2/config.yaml : seuil_relecture
const MIN_CARACTERES = 20;    // RequeteAnalyseV1/V2 : min_length=20

const $ = (s) => document.querySelector(s);
const texte = $("#texte");
const nombre = (n) => n.toLocaleString("fr-FR");

// --- Indicateurs clés -------------------------------------------------------
surveillerResume({
  boutonPause: $("#pause"),
  horodatage: $("#maj"),
  onData(r) {
    peindre($("#kpi-p95"), rendreP95(r.par_version));
    peindre($("#kpi-canary"), rendreTrafic(r.par_version) + rendrePalier(r.palier));
  },
  onErreur() {
    const m = `<p class="indispo">${icone("warn")}Indicateurs indisponibles — nouvel essai dans 5 s.</p>`;
    peindre($("#kpi-p95"), m);
    peindre($("#kpi-canary"), m);
  },
});

// --- Saisie -----------------------------------------------------------------
function majCompteur() {
  const n = texte.value.length;
  const alerte = n > LIMITE_V1
    ? ` · <span class="warn">${icone("warn")}au-delà de ${nombre(LIMITE_V1)}, v1 tronque le contrat</span>`
    : "";
  $("#compteur").innerHTML = `${nombre(n)} caractère${n > 1 ? "s" : ""}${alerte}`;
}
texte.addEventListener("input", majCompteur);

document.querySelectorAll("[data-exemple]").forEach((bouton) => {
  bouton.addEventListener("click", async () => {
    bouton.disabled = true;
    try {
      const r = await fetch(`/exemples/${bouton.dataset.exemple}.txt`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      texte.value = await r.text();
      $("#erreur-texte").textContent = "";
      majCompteur();
    } catch {
      $("#erreur-texte").textContent = "Exemple introuvable.";
    } finally {
      bouton.disabled = false;
    }
  });
});

// --- Appels API -------------------------------------------------------------
function detailErreur(corps) {
  const d = corps?.detail;
  if (Array.isArray(d)) return d.map((e) => e.msg).join(" ; ");
  return d ?? "réponse illisible";
}

async function appeler(chemin, contenu) {
  const debut = performance.now();
  let r;
  try {
    r = await fetch(chemin, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ texte: contenu }),
    });
  } catch {
    return { ok: false, statut: null, detail: "API injoignable" };
  }
  const corps = await r.json().catch(() => null);
  if (!r.ok) return { ok: false, statut: r.status, detail: detailErreur(corps) };
  return { ok: true, corps, servi: r.headers.get("X-Mardik-Version"), duree: performance.now() - debut };
}

// --- Rendu ------------------------------------------------------------------
const SQUELETTE = '<div class="squelette"><span></span><span></span><span></span></div>';
const colonne = (id, classe, titre) =>
  `<section class="carte colonne ${classe}" aria-labelledby="t-${id}">
     <h3 id="t-${id}">${titre}</h3><div id="c-${id}" aria-live="polite" aria-busy="true">${SQUELETTE}</div>
   </section>`;
const badgeGateway = (servi) => (servi ? `<p class="badge info">servi par ${esc(servi)}</p>` : "");

function rendreV1({ corps, duree, servi }) {
  const etat = corps.tronque
    ? `<p class="badge warn">${icone("warn")}Contrat tronqué</p>`
    : `<p class="badge ok">${icone("ok")}Contrat analysé en entier</p>`;
  const clauses = corps.clauses.length
    ? `<ul class="clauses-v1">${corps.clauses.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`
    : '<p class="vide">Aucune clause détectée.</p>';
  return `${badgeGateway(servi)}${etat}${clauses}
    <p class="meta">${corps.clauses.length} clause(s) · ${fmtMs(duree)} · ${esc(corps.version)} · ${esc(corps.modele)}</p>`;
}

function rendreClause(c) {
  const relire = arrondi2(c.confiance) < SEUIL_RELECTURE
    ? `<span class="statut warn">${icone("warn")}à relire</span>` : "";
  return `<details class="clause">
    <summary><span class="clause-type">${esc(c.type)}</span>
      <span class="chiffre">${fmtScore(c.confiance)}</span>${relire}
      <span class="clause-sections">§ ${c.sections.map(esc).join(", ")}</span></summary>
    <blockquote>${esc(c.extrait)}</blockquote>
  </details>`;
}

function rendreV2({ corps, servi }) {
  const fiable = arrondi2(corps.confiance_globale) >= SEUIL_RELECTURE;
  const verdict = fiable
    ? `<span class="statut ok">${icone("ok")}fiable</span>`
    : `<span class="statut danger">${icone("danger")}Analyse non fiable</span>`;
  const clauses = corps.clauses.map(rendreClause).join("") || '<p class="vide">Aucune clause détectée.</p>';
  const warnings = corps.warnings.length
    ? `<ul class="warnings">${corps.warnings.map((w) => `<li>${icone("warn")}<span>${esc(w)}</span></li>`).join("")}</ul>`
    : "";
  return `${badgeGateway(servi)}
    <div class="fiabilite"><span>Indice de fiabilité</span>
      <strong class="chiffre-grand">${fmtScore(corps.confiance_globale)}</strong>${verdict}</div>
    ${clauses}${warnings}
    <p class="meta">${esc(corps.sections)} section(s) · ${fmtMs(corps.latence_ms)} · ${fmtEur(corps.cout_eur)} · ${esc(corps.model_version)}</p>`;
}

function remplir(id, r, rendre) {
  const cible = document.getElementById(`c-${id}`);
  cible.innerHTML = r.ok
    ? rendre(r)
    : `<div class="erreur" role="alert">${icone("danger")}<div>
         <strong>${r.statut ? `Erreur ${r.statut}` : "Erreur réseau"}</strong><p>${esc(r.detail)}</p></div></div>`;
  cible.setAttribute("aria-busy", "false");
  cible.classList.add("apparait");
}

// --- Soumission -------------------------------------------------------------
$("#form-analyse").addEventListener("submit", async (evenement) => {
  evenement.preventDefault();
  const contenu = texte.value;
  if (contenu.length < MIN_CARACTERES) {
    $("#erreur-texte").textContent = `Le contrat doit contenir au moins ${MIN_CARACTERES} caractères.`;
    texte.focus();
    return;
  }
  $("#erreur-texte").textContent = "";
  const bouton = $("#analyser");
  bouton.disabled = true;
  bouton.textContent = "Analyse…";
  const resultats = $("#resultats");
  try {
    if (new FormData(evenement.target).get("mode") === "gateway") {
      resultats.className = "resultats une-colonne";
      resultats.innerHTML = colonne("gateway", "", "Via la gateway canary");
      const r = await appeler("/analyse", contenu);
      remplir("gateway", r, r.ok && "confiance_globale" in r.corps ? rendreV2 : rendreV1);
    } else {
      resultats.className = "resultats";
      resultats.innerHTML = colonne("v1", "v1", "v1 · historique") + colonne("v2", "", "v2 · nouvelle version");
      await Promise.allSettled([
        appeler("/v1/analyse", contenu).then((r) => remplir("v1", r, rendreV1)),
        appeler("/v2/analyse", contenu).then((r) => remplir("v2", r, rendreV2)),
      ]);
    }
  } finally {
    bouton.disabled = false;
    bouton.textContent = "Analyser";
  }
});

$("#logo").innerHTML = icone("scale");
initTheme($("#theme"));
majCompteur();
