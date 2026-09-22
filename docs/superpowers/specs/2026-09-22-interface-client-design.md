# Interface client — pages « Analyse » et « Pilotage »

*Spec de conception — 2026-09-22. Livrable « Un client via un frontend accessible via un lien » (`brief_v2.md`).*

## 1. Contexte et décisions de cadrage

Le brief demande un client web accessible via un lien ; `spec.md` le borne à un
« frontend minimal de démonstration » (UI avancée hors périmètre). Aujourd'hui,
le seul client est `scripts/client_v1.py` (CLI) et le seul écran est le tableau
de bord brut de `ops/dashboard.py` (`:8501`).

Décisions validées en brainstorming :

| Question | Décision |
|---|---|
| Public | **Démo de soutenance** : lisibilité, comparaison v1/v2, pilotage en direct |
| Accès v1/v2 | **Côte à côte** (même contrat envoyé à `/v1` et `/v2`) **+ mode « via la gateway »** (`POST /analyse`, en-tête `X-Mardik-Version`) |
| KPI sur la page client | **Latence P95 vs SLO 8 s** et **Canary en cours** — rien d'autre |
| Tous les KPI | Page dédiée **« Pilotage »** : tous les champs de `ops.dashboard.resume()` |
| Hébergement | **Tout sur l'app `:8000`** : pages statiques + route JSON mince. Un seul lien, même origine, pas de CORS |
| Stack front | HTML/CSS/JS vanilla, aucune chaîne Node, aucune lib de graphique |
| Design | Système issu de **UI UX PRO MAX** (§5) |

## 2. Périmètre

**Dans le périmètre**

- `app/web/index.html`, `app/web/pilotage.html` — les deux pages
- `app/web/styles.css` — tokens et composants partagés, clair/sombre
- `app/web/commun.js` — thème, `fetch` du résumé, rafraîchissement, rendus SVG partagés (bullet P95, barre de trafic, stepper palier)
- `app/web/analyse.js`, `app/web/pilotage.js` — logique propre à chaque page
- `app/pilotage.py` — `GET /pilotage/resume` (schéma `ResumePilotage`)
- `app/main.py` — inclusion du routeur, montages statiques, `GET /` et `GET /pilotage`
- `tests/integration/test_interface.py`

**Hors périmètre**

- `app/api_v1.py`, `app/api_v2.py`, `app/gateway.py`, `ops/dashboard.py` : **non modifiés** (le service `:8501` reste en place, ses tests aussi)
- `docker-compose.yml` : inchangé (le volume `./eval` est déjà monté dans `app`)
- Authentification, historique des analyses, export, upload PDF
- **Toute action de pilotage depuis l'UI** (promotion, rollback, seuils) : l'UI observe, le pilote et la CI agissent

## 3. Architecture

```
navigateur ──GET /, /pilotage──────────► app/main.py ─► FileResponse(app/web/*.html)
           ──GET /static/*─────────────► StaticFiles(app/web)
           ──GET /exemples/c02.txt─────► StaticFiles(eval/contrats)   (lecture seule)
           ──POST /v1/analyse ┐
           ──POST /v2/analyse ┘ en parallèle (mode comparaison)
           ──POST /analyse ────────────► gateway (mode gateway)
           ──GET /pilotage/resume (5 s)► app/pilotage.py ─► ops.dashboard.resume()
```

### 3.1 `GET /pilotage/resume`

Route **mince**, déclarée en `def` (exécutée dans le threadpool : `resume()` lit
des fichiers de façon synchrone). Elle appelle `ops.dashboard.resume()` sans
argument — même fenêtre que le pilote — et renvoie un modèle Pydantic ; aucune
logique d'agrégation n'est dupliquée.

```python
class PointMinute(BaseModel):          # ops.signaux.serie_par_minute
    model_config = ConfigDict(extra="allow")
    minute: int
    requetes: int
    latence_p95_ms: float | None
    taux_erreur: float

class StatsVersion(BaseModel):
    requetes: int
    trafic_pct: float
    latence_p50_ms: float | None
    latence_p95_ms: float | None
    taux_erreur: float
    score_moyen: float | None
    score_p10: float | None
    cout_total_eur: float
    histogramme_score: list[int]
    serie_minute: list[PointMinute]

class Palier(BaseModel):
    version: str
    pourcentage: int | None
    depuis_s: float | None
    requetes: int
    duree_min_s: float | None
    requetes_min: int | None

class EntreeJournal(BaseModel):        # événements hétérogènes du registre
    model_config = ConfigDict(extra="allow")
    date: str | None = None
    evenement: str | None = None
    origine: str | None = None
    resume: str | None = None
    motif: str | None = None

class ResumePilotage(BaseModel):
    fenetre_s: float
    total: int
    par_version: dict[str, StatsVersion]
    palier: Palier | None
    alertes: list[str]
    candidats: int
    journal: list[EntreeJournal]
```

`resume()` dégrade déjà ses sources illisibles en `alertes` : la route n'ajoute
aucun `try/except`.

### 3.2 Montages dans `app/main.py`

- `app.include_router(pilotage.router)`
- `app.mount("/static", StaticFiles(directory=RACINE / "app" / "web"))`
- `app.mount("/exemples", StaticFiles(directory=RACINE / "eval" / "contrats"))` — `RACINE = Path(__file__).resolve().parent.parent`, convention de `app/telemetry.py`, `app/capture.py`, `app/llm_client.py`. Pas de variable d'environnement : ces dossiers sont versionnés et ne changent pas entre environnements.
- `GET /` → `index.html`, `GET /pilotage` → `pilotage.html` (`FileResponse`, `include_in_schema=False`)

## 4. Pages

### 4.1 « Analyse » (`GET /`)

```
┌──────────────────────────────────────────────────────────────────┐
│ ⚖ Mardik        [Analyse]  Pilotage                    ☾ thème   │
├──────────────────────────────────────────────────────────────────┤
│ ┌─ Latence P95 vs SLO 8 s ────────┐ ┌─ Canary en cours ────────┐ │
│ │ v1 ▓▓▓▓▓░░░░░░│ 3 120 ms  ✓      │ │ v1 ██████████ 90 %  v2 ▓ │ │
│ │ v2 ▓▓▓▓▓▓▓▓░░│ 6 840 ms  ✓      │ │ ●10 ──○50 ──○100 %        │ │
│ │               └ 8 s              │ │ 34/50 req · 4 min / 5 min│ │
│ └─────────────────────────────────┘ └──────────────────────────┘ │
│                     ⏸ pause · màj il y a 3 s · Tout le pilotage →│
├──────────────────────────────────────────────────────────────────┤
│ Texte du contrat                   [Exemple court] [Exemple long]│
│ ┌──────────────────────────────────────────────────────────────┐ │
│ └──────────────────────────────────────────────────────────────┘ │
│ 64 272 caractères · ⚠ au-delà de 16 000, v1 tronque              │
│ ( ● Comparer v1 / v2   ○ Via la gateway )          [ Analyser ]  │
├───────────────────────────────┬──────────────────────────────────┤
│ v1 · historique               │ v2 · nouvelle version            │
│ ⚠ Contrat tronqué             │ Indice de fiabilité  0,87  ✓     │
│ • résiliation                 │ ┌ résiliation   0,91  §3  ▸extrait│
│ • confidentialité             │ ├ garantie      0,42  ⚠ à relire │
│ 3 clauses · 2,1 s             │ 14 sections · 5,2 s · 0,031 €    │
│                               │ ⚠ warnings                       │
└───────────────────────────────┴──────────────────────────────────┘
```

**Bandeau KPI** (données de `/pilotage/resume`)

- *Latence P95 vs SLO* : bullet chart par version présente dans `par_version`, trait cible à 8 s, valeur en texte, statut ✓ « dans le SLO » / ⚠ « hors SLO » (icône + texte). `latence_p95_ms` à `null` → « — ».
- *Canary en cours* : barre empilée de `trafic_pct` ; stepper 10 → 50 → 100 % (palier courant = `palier.pourcentage`) ; deux jauges `requetes / requetes_min` et `depuis_s / duree_min_s`. `palier` à `null` → « Aucun canary en cours ».
- Rafraîchi toutes les **5 s** ; bouton **pause/reprise** ; suspendu quand l'onglet est masqué (`visibilitychange`) ; horodatage « màj il y a N s » ; `aria-live="polite"`.
- Échec du `fetch` → cartes en état « indisponible », la page d'analyse reste utilisable.

**Formulaire**

- `<label>` visible, `<textarea>` ; compteur de caractères, avertissement si > **16 000** (limite v1, constante JS commentée → `models/v1/config.yaml`).
- Boutons *Exemple court* (`c02`, 6 ko) et *Exemple long* (`c07`, 64 ko : v1 tronque, v2 découpe) → `GET /exemples/{id}.txt`.
- Choix de mode en boutons radio : *Comparer v1 / v2* (défaut) | *Via la gateway*.
- Validation côté client : texte < 20 caractères → message sous le champ (même règle que `min_length=20` ; l'API reste juge : un 422 est affiché tel quel).
- Pendant l'appel : bouton désactivé « Analyse… », squelettes dans les colonnes.

**Résultats**

- *Comparaison* : `Promise.allSettled` sur `/v1/analyse` et `/v2/analyse` ; chaque colonne se remplit **indépendamment** (un 413 v2 n'empêche pas l'affichage v1).
- *Colonne v1* : liste des clauses ; badge **« Contrat tronqué »** si `tronque` ; `version`, `modele`, durée mesurée côté navigateur.
- *Colonne v2* : **« Indice de fiabilité »** = `confiance_globale` ; badge **« Analyse non fiable »** si < 0,60 ; clauses en `<details>` (type, confiance, sections, extrait) ; confiance < 0,60 → « à relire » ; `sections`, `latence_ms`, `cout_eur`, `model_version` ; liste des `warnings`.
- *Gateway* : une colonne, badge « servi par {X-Mardik-Version} », rendu v1 ou v2 selon la forme de la réponse (présence de `confiance_globale`).
- *Erreurs* : 413 / 422 / 503 / 501 → carte d'erreur dans la colonne concernée, code + `detail` de l'API, `role="alert"`. Erreur réseau → « API injoignable ».
- Seuil 0,60 : constante JS commentée → `seuil_relecture` de `models/v2/config.yaml`.

### 4.2 « Pilotage » (`GET /pilotage`) — tout `resume()`

```
┌ Header ──────────────────────────────── fenêtre 300 s · 128 requêtes ┐
│ Alertes : bandeau danger si non vide, sinon « Aucune alerte » ✓       │
├──────────────── Part de trafic (barre empilée) ───────────────────────┤
├─ Palier canary (stepper + 2 jauges) ─┬─ Candidats à verser : 3 ───────┤
├──────────── Par version : une carte par version ──────────────────────┤
│ v2.0.0  trafic · P50 / P95 · erreurs · score (P10) · coût total       │
│         [histogramme score] [sparkline P95/min] [sparkline erreurs/min]│
├──────────── Journal de pilotage (5 derniers, <table>) ────────────────┤
│ date · événement (badge) · origine · résumé (ou motif)                │
└──────────────────────────────────────────────────────────────────────┘
```

- Tous les champs de `ResumePilotage` sont affichés.
- `score_moyen` à `null` → « — (v1 ne produit pas de score) ».
- Événements du journal en badges colorés **doublés de texte**.
- Aucun trafic → état vide explicatif (« lancer `make traffic` »).
- Même rafraîchissement 5 s / pause / masquage que la page Analyse.

## 5. Système visuel (UI UX PRO MAX)

Issu de `search.py --design-system` (« legaltech AI contract analysis… », densité 7, mouvement 3) puis de recherches `color` et `typography` ciblées sur le juridique.

- **Style** : *Trust & Authority* (recommandé services juridiques, WCAG AAA) — cartes blanches, bordure fine, ombre minimale, chiffres en avant.
- **Écarté** : pattern « AI Personalization Landing » (landing marketing) ; palette violet/rose proposée par défaut (l'outil la liste lui-même en anti-pattern) ; scroll-reveal GSAP (dépendance inutile).

**Palette « Legal Services »** — tokens sur `:root`, redéfinis en sombre sous `@media (prefers-color-scheme: dark)` et `[data-theme="dark"]` :

| Token | Clair | Sombre | Usage |
|---|---|---|---|
| `--primary` | `#1E3A8A` | `#93B4F5` | titres, v2, liens, focus |
| `--accent` | `#B45309` | `#F59E0B` | CTA « Analyser », palier actif |
| `--bg` | `#F8FAFC` | `#0B1220` | fond |
| `--card` | `#FFFFFF` | `#111A2E` | cartes |
| `--fg` | `#0F172A` | `#E2E8F0` | texte |
| `--muted-fg` | `#64748B` | `#94A3B8` | texte secondaire |
| `--border` | `#CBD5E1` | `#23304A` | traits |
| `--v1` | `#64748B` | `#94A3B8` | version historique |
| `--ok` / `--warn` / `--danger` | `#15803D` / `#B45309` / `#DC2626` | `#4ADE80` / `#FBBF24` / `#F87171` | états (toujours icône + texte) |

**Typographie** : paire *Legal Professional* — **EB Garamond** (titres, grands chiffres) + **Lato** (corps 16 px, interligne 1,5) via Google Fonts, repli `Georgia` / `system-ui` ; `font-variant-numeric: tabular-nums` sur toutes les métriques.

**Graphiques** : SVG inline écrits à la main — bullet chart (valeur vs cible, recommandé pour « performance vs target »), barre empilée, stepper, histogramme, sparklines. Valeur toujours affichée en texte à côté.

**Icônes** : SVG Lucide inline (pas de CDN, pas d'emoji).

**Mouvement** : transitions CSS 150–250 ms, fondu d'entrée des résultats, squelettes ; tout coupé sous `prefers-reduced-motion: reduce`.

**Thème** : suit `prefers-color-scheme`, bouton de bascule, choix mémorisé en `localStorage` (lecture/écriture sous `try/catch`).

**Accessibilité** : contraste ≥ 4,5:1, focus visible, cibles ≥ 44 px, labels visibles, `aria-live` sur les KPI, `role="alert"` sur les erreurs, navigation active soulignée.

**Responsive** : 375 / 768 / 1024 / 1440 px ; colonnes v1 | v2 empilées < 768 px ; tableaux défilants dans leur carte, jamais la page.

## 6. Tests

`tests/integration/test_interface.py`, `MOCK=on`, avec les fixtures existantes de `tests/conftest.py` (`client`, `registry`, `metriques`, `environnement` autouse qui isole métriques et registre dans `tmp_path`) :

1. `GET /pilotage/resume` → 200, corps valide pour `ResumePilotage`, avec des métriques et un registre de test (canary en cours → `palier` non nul).
2. `GET /pilotage/resume` sans trafic → `par_version == {}`, `total == 0`.
3. `GET /` et `GET /pilotage` → 200, `text/html`, contiennent les points d'accroche attendus (`id` du formulaire, script référencé).
4. `GET /static/styles.css` et `GET /exemples/c02.txt` → 200.
5. Garde-fou : `test_client_v1_fonctionne` (acceptance) reste vert — `/v1` inchangé.

Le JS n'a pas de test automatisé ; il est **vérifié en navigateur réel** (les deux modes, les deux exemples, un 413, clair/sombre, 375 px) avec captures d'écran avant livraison.

## 7. Critères de réussite

- Un seul lien (`http://localhost:8000/`) donne accès à l'analyse v1/v2/gateway et, en un clic, au pilotage complet.
- Avec l'exemple long, la démo montre d'un coup d'œil « Contrat tronqué » côté v1 et l'indice de fiabilité côté v2.
- La page Analyse n'affiche que P95 vs SLO et le canary ; la page Pilotage affiche tous les champs de `resume()`.
- `make test` vert ; aucun fichier de `/v1`, `/v2`, gateway ou `ops/dashboard.py` modifié.
