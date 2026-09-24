import { arrondi2, demarrerTimer, esc, fmtNombre, fmtScore, icone, initTheme, messageSansClause } from "/static/commun.js";

const LIMITE_V1 = 16000;      // models/v1/config.yaml : contexte_max_caracteres (au-delà, v1 coupe)
const SEUIL_RELECTURE = 0.6;  // models/v2/config.yaml : seuil_relecture
const MIN_CARACTERES = 20;    // RequeteAnalyseV1/V2 : min_length=20
const ENDPOINTS = {
  v1: { chemin: "/v1/analyse", titre: "v1 · historique" },
  v2: { chemin: "/v2/analyse", titre: "v2 · nouvelle version" },
};
const MODES = { v1: ["v1"], v2: ["v2"], comparer: ["v1", "v2"] };

const $ = (s) => document.querySelector(s);
const texte = $("#texte");

// --- Saisie -----------------------------------------------------------------
function majCompteur() {
  const n = texte.value.length;
  const alerte = n > LIMITE_V1
    ? ` · <span class="warn">${icone("warn")}au-delà de ${fmtNombre(LIMITE_V1)}, v1 tronque le contrat</span>`
    : "";
  $("#compteur").innerHTML = `${fmtNombre(n)} caractère${n > 1 ? "s" : ""}${alerte}`;
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
  return { ok: true, corps };
}

// --- Suivi (timers) ---------------------------------------------------------
function preparerSuivi(versions) {
  const suivi = $("#suivi");
  suivi.hidden = false;
  suivi.innerHTML = versions.map((v) => `
    <p class="timer" id="suivi-${v}">
      <span class="timer-nom">${esc(ENDPOINTS[v].chemin)}</span>
      <span class="timer-duree" id="duree-${v}" aria-live="off">0,0 s</span>
      <span class="timer-etat" id="etat-${v}" aria-live="polite">en cours…</span>
    </p>`).join("");
}

function terminerSuivi(v, r, arreter) {
  arreter();
  $(`#etat-${v}`).innerHTML = r.ok
    ? `<span class="statut ok">${icone("ok")}terminé</span>`
    : `<span class="statut danger">${icone("danger")}${r.statut ? `Erreur ${r.statut}` : "Erreur réseau"} : ${esc(r.detail)}</span>`;
}

// --- Rendu des résultats ----------------------------------------------------
const EN_COURS = '<span class="vide">…</span>';
const INDISPONIBLE = '<span class="vide">—</span>';

function cellule(v, r, rendre) {
  if (!r) return EN_COURS;
  if (!r.ok) return INDISPONIBLE;
  return rendre(v, r.corps);
}

function fiabiliteDocument(v, corps) {
  if (v === "v1") return '<span class="vide">non mesuré</span>';
  const fiable = arrondi2(corps.confiance_globale) >= SEUIL_RELECTURE;
  return `<span class="chiffre">${fmtScore(corps.confiance_globale)}</span> ${fiable
    ? `<span class="statut ok">${icone("ok")}fiable</span>`
    : `<span class="statut danger">${icone("danger")}non fiable</span>`}`;
}

function lectureComplete(v, corps) {
  if (v === "v2") return `<span class="statut ok">${icone("ok")}oui (${esc(corps.sections)} section(s))</span>`;
  return corps.tronque
    ? `<span class="statut warn">${icone("warn")}non, tronqué à ${fmtNombre(LIMITE_V1)} caractères</span>`
    : `<span class="statut ok">${icone("ok")}oui</span>`;
}

function auteur(v, corps) {
  return `${esc(corps.modele)} · ${esc(v === "v1" ? corps.version : corps.model_version)}`;
}

function tableauDocument(versions, etat) {
  const lignes = [
    ["Indice de fiabilité", fiabiliteDocument],
    ["Lecture complète", lectureComplete],
    ["Auteur de l'analyse", auteur],
  ];
  return `<section class="carte" aria-labelledby="t-document">
    <h2 id="t-document">Document</h2>
    <div class="table-defilante"><table>
      <thead><tr><th scope="col">Critère</th>${versions.map((v) => `<th scope="col">${esc(ENDPOINTS[v].titre)}</th>`).join("")}</tr></thead>
      <tbody>${lignes.map(([libelle, rendre]) => `<tr><th scope="row">${libelle}</th>${
        versions.map((v) => `<td>${cellule(v, etat[v], rendre)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
  </section>`;
}

const cleType = (t) => String(t).trim().toLowerCase();

function tableauClauses(versions, etat) {
  const types = new Map();   // clé normalisée → libellé affiché
  for (const v of versions) {
    const r = etat[v];
    if (!r?.ok) continue;
    for (const c of r.corps.clauses) {
      const libelle = v === "v1" ? c : c.type;
      if (!types.has(cleType(libelle))) types.set(cleType(libelle), libelle);
    }
  }
  if (!types.size) {
    return `<section class="carte"><h2>Clauses clés</h2><p class="vide">${messageSansClause(versions.map((v) => etat[v]))}</p></section>`;
  }
  const tries = [...types.entries()].sort((a, b) => a[1].localeCompare(b[1], "fr"));
  const celluleClause = (v, cle) => cellule(v, etat[v], (version, corps) => {
    if (version === "v1") {
      return corps.clauses.some((c) => cleType(c) === cle)
        ? `<span class="statut ok">${icone("ok")}détectée</span>` : INDISPONIBLE;
    }
    const c = corps.clauses.find((x) => cleType(x.type) === cle);
    if (!c) return INDISPONIBLE;
    const relire = arrondi2(c.confiance) < SEUIL_RELECTURE
      ? ` <span class="statut warn">${icone("warn")}à relire</span>` : "";
    return `<span class="chiffre">${fmtScore(c.confiance)}</span>${relire}
      <span class="extrait">« ${esc(c.extrait)} » · § ${c.sections.map(esc).join(", ")}</span>`;
  });
  return `<section class="carte" aria-labelledby="t-clauses">
    <h2 id="t-clauses">Clauses clés et fiabilité par clause</h2>
    <div class="table-defilante"><table>
      <thead><tr><th scope="col">Type</th>${versions.map((v) => `<th scope="col">${esc(ENDPOINTS[v].titre)}</th>`).join("")}</tr></thead>
      <tbody>${tries.map(([cle, libelle]) => `<tr><th scope="row">${esc(libelle)}</th>${
        versions.map((v) => `<td>${celluleClause(v, cle)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
  </section>`;
}

function alertes(versions, etat, longueur) {
  const items = [];
  for (const v of versions) {
    const r = etat[v];
    if (!r) continue;
    if (!r.ok) {
      items.push(`${v} · analyse échouée (${r.detail}) : aucun résultat à valider pour cette version`);
      continue;
    }
    if (v === "v1" && r.corps.tronque) {
      items.push(`v1 · contrat tronqué : ${fmtNombre(longueur - LIMITE_V1)} caractères non lus — préférez la v2`);
    }
    if (v === "v2") items.push(...r.corps.warnings.map((w) => `v2 · ${w}`));
  }
  if (!versions.every((v) => etat[v])) return "";
  const contenu = items.length
    ? `<ul class="warnings">${items.map((w) => `<li>${icone("warn")}<span>${esc(w)}</span></li>`).join("")}</ul>`
    : `<p class="statut ok">${icone("ok")}Aucune alerte : l'analyse peut être validée.</p>`;
  return `<section class="carte" aria-labelledby="t-alertes"><h2 id="t-alertes">Alertes et préconisations</h2>${contenu}</section>`;
}

function jsonBrut(versions, etat) {
  return versions.filter((v) => etat[v]?.ok).map((v) => `
    <details class="json-brut"><summary>JSON brut ${esc(v)}</summary>
      <pre>${esc(JSON.stringify(etat[v].corps, null, 2))}</pre></details>`).join("");
}

function rendre(versions, etat, longueur) {
  $("#resultats").innerHTML = tableauDocument(versions, etat) + tableauClauses(versions, etat)
    + alertes(versions, etat, longueur) + jsonBrut(versions, etat);
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
  const versions = MODES[new FormData(evenement.target).get("mode")] ?? MODES.v2;
  const etat = Object.fromEntries(versions.map((v) => [v, null]));
  const bouton = $("#analyser");
  bouton.disabled = true;
  bouton.textContent = "Analyse…";
  preparerSuivi(versions);
  rendre(versions, etat, contenu.length);
  try {
    await Promise.allSettled(versions.map(async (v) => {
      const arreter = demarrerTimer($(`#duree-${v}`));
      const r = await appeler(ENDPOINTS[v].chemin, contenu);
      terminerSuivi(v, r, arreter);
      etat[v] = r;
      rendre(versions, etat, contenu.length);
    }));
  } finally {
    bouton.disabled = false;
    bouton.textContent = "Analyser";
  }
});

$("#logo").innerHTML = icone("scale");
initTheme($("#theme"));
majCompteur();
