// Partagé par les pages Analyse, Pilotage et Observabilité : échappement, formatage, icônes,
// thème, rafraîchissement d'un résumé, timer et petits graphiques SVG.

export const PALIERS = [10, 50, 100];  // ops/seuils_pilotage.yaml : paliers du canary
const PERIODE_MS = 5000;

const ENTITES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ENTITES[c]);

const nombre = (d) => new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
export const fmtMs = (v) => (v == null ? "—" : `${nombre(0).format(v)} ms`);
// Arrondi comme Python round(v, 2) : toFixed part de la valeur binaire exacte (0.595 → 0.59).
export const arrondi2 = (v) => Number(v.toFixed(2));
export const fmtScore = (v) => (v == null ? "—" : nombre(2).format(arrondi2(v)));
export const fmtPct = (v) => (v == null ? "—" : `${nombre(1).format(v)} %`);
export const fmtEur = (v) => (v == null ? "—" : `${nombre(3).format(v)} €`);
export const fmtDuree = (s) => {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)} s`;
  return `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, "0")} s`;
};

const fmtSecondes = (ms) => `${nombre(1).format(ms / 1000)} s`;

// Chronomètre d'un appel : affiche le temps écoulé tous les 100 ms dans `el`
// (qui doit être aria-live="off" : on n'annonce pas chaque tick) ; la fonction
// renvoyée l'arrête, fige la valeur finale et renvoie la durée en ms.
export function demarrerTimer(el) {
  const debut = performance.now();
  const afficher = () => { el.textContent = fmtSecondes(performance.now() - debut); };
  afficher();
  const minuterie = setInterval(afficher, 100);
  return () => {
    clearInterval(minuterie);
    const duree = performance.now() - debut;
    el.textContent = fmtSecondes(duree);
    return duree;
  };
}

// Tracés Lucide (ISC), inline : ni CDN ni emoji.
const TRACES = {
  ok: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  warn: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  danger: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
  pause: '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  scale: '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
};
export const icone = (nom) =>
  `<svg class="icone" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${TRACES[nom]}</svg>`;

// Réécrit une zone rafraîchie seulement si son HTML change : une zone aria-live
// réécrite à l'identique est réannoncée par les lecteurs d'écran à chaque tour.
const DERNIER = new WeakMap();
export function peindre(el, html) {
  if (DERNIER.get(el) === html) return;
  DERNIER.set(el, html);
  el.innerHTML = html;
}

export const couleurVersion = (v) => (String(v).startsWith("v1") ? "v1" : "v2");

export function initTheme(bouton) {
  const racine = document.documentElement;
  const systemeSombre = matchMedia("(prefers-color-scheme: dark)");
  try {
    const memo = localStorage.getItem("mardik-theme");
    if (memo === "dark" || memo === "light") racine.dataset.theme = memo;
  } catch { /* stockage indisponible : on suit le système */ }
  const courant = () => racine.dataset.theme || (systemeSombre.matches ? "dark" : "light");
  const peindre = () => {
    const sombre = courant() === "dark";
    bouton.innerHTML = icone(sombre ? "sun" : "moon");
    bouton.setAttribute("aria-label", sombre ? "Passer en thème clair" : "Passer en thème sombre");
  };
  bouton.addEventListener("click", () => {
    racine.dataset.theme = courant() === "dark" ? "light" : "dark";
    try { localStorage.setItem("mardik-theme", racine.dataset.theme); } catch { /* idem */ }
    peindre();
  });
  systemeSombre.addEventListener("change", peindre);
  peindre();
}

export function surveillerResume({ url = "/pilotage/resume", onData, onErreur, boutonPause, horodatage }) {
  let minuterie = null;
  let enPause = false;
  let derniere = null;
  async function tour() {
    try {
      const r = await fetch(url, { headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      onData(await r.json());
      derniere = Date.now();
    } catch (e) {
      onErreur(e);
    }
  }
  function planifier() {
    clearInterval(minuterie);
    if (enPause || document.hidden) return;
    tour();
    minuterie = setInterval(tour, PERIODE_MS);
  }
  setInterval(() => {
    const age = derniere == null ? null : Math.round((Date.now() - derniere) / 1000);
    horodatage.textContent = enPause ? "en pause" : age == null ? "en attente…" : `màj il y a ${age} s`;
  }, 1000);
  boutonPause.addEventListener("click", () => {
    enPause = !enPause;
    boutonPause.setAttribute("aria-pressed", String(enPause));
    boutonPause.innerHTML = `${icone(enPause ? "play" : "pause")}<span>${enPause ? "Reprendre" : "Pause"}</span>`;
    planifier();
  });
  document.addEventListener("visibilitychange", planifier);
  planifier();
}

// Page Analyse : message du tableau des clauses quand aucune clause n'est à afficher.
// `reponses` : une entrée par version demandée — null tant que l'appel est en cours.
export function messageSansClause(reponses) {
  if (reponses.some((r) => r == null)) return "Chargement…";
  if (!reponses.some((r) => r.ok)) return "Aucune clause : analyse en échec";
  return "Aucune clause détectée.";
}

const AUCUN_TRAFIC = '<p class="vide">Aucun trafic dans la fenêtre.</p>';

export function rendreTrafic(parVersion) {
  const versions = Object.entries(parVersion);
  if (!versions.length) return AUCUN_TRAFIC;
  const libelle = versions.map(([v, s]) => `${v} ${fmtPct(s.trafic_pct)}`).join(", ");
  const parts = versions.map(([v, s]) =>
    `<span class="trafic-part ${couleurVersion(v)}" style="flex-grow:${Number(s.trafic_pct) || 0}">${esc(v)} · ${fmtPct(s.trafic_pct)}</span>`);
  return `<div class="trafic" role="img" aria-label="Part de trafic : ${esc(libelle)}">${parts.join("")}</div>`;
}

function jauge(libelle, valeur, cible, fmt) {
  const pct = valeur == null || !cible ? 0 : Math.min(100, (valeur / cible) * 100);
  return `<div class="jauge">
    <div class="jauge-tete"><span>${libelle}</span>
      <span class="chiffre">${valeur == null ? "—" : fmt(valeur)} / ${cible == null ? "—" : fmt(cible)}</span></div>
    <div class="jauge-piste" role="progressbar" aria-label="${libelle}" aria-valuemin="0"
         aria-valuemax="100" aria-valuenow="${Math.round(pct)}"><div class="jauge-remplie" style="width:${pct}%"></div></div>
  </div>`;
}

export function rendrePalier(palier) {
  if (!palier) return '<p class="vide">Aucun canary en cours : la version active reçoit tout le trafic.</p>';
  if (palier.pourcentage == null) {
    return `<p class="palier-titre">${esc(palier.version)} reçoit une part inconnue du trafic</p>
      ${jauge("Requêtes du palier", palier.requetes, palier.requetes_min, (n) => String(n))}
      ${jauge("Durée du palier", palier.depuis_s, palier.duree_min_s, fmtDuree)}`;
  }
  const etapes = PALIERS.map((p) => {
    const etat = p < palier.pourcentage ? "fait" : p === palier.pourcentage ? "actif" : "a-venir";
    return `<li class="etape ${etat}"${etat === "actif" ? ' aria-current="step"' : ""}><span class="pastille"></span>${p} %</li>`;
  });
  return `<p class="palier-titre">${esc(palier.version)} reçoit <strong>${esc(palier.pourcentage)} %</strong> du trafic</p>
    <ol class="stepper" aria-label="Paliers du canary">${etapes.join("")}</ol>
    ${jauge("Requêtes du palier", palier.requetes, palier.requetes_min, (n) => String(n))}
    ${jauge("Durée du palier", palier.depuis_s, palier.duree_min_s, fmtDuree)}`;
}
