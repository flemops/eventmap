/* EventMap — accueil « mode classique ».
   Range les événements par culture d'origine puis par catégorie, et propose une
   vignette de carte qui renvoie vers /carte. Aucune donnée n'est stockée. */
(() => {
  const { CATEGORY_LABELS, esc, fmtTime, fmtDay, PRICE_LABEL, CITY, api } = window.EM_COMMON;
  // Centre de la ville, rayon large : l'accueil montre toute la ville, pas les environs de l'utilisateur.
  const CENTER = { lat: CITY.center.lat, lon: CITY.center.lon, radius: CITY.max_radius_km, when: "week" };

  const $ = (s) => document.querySelector(s);

  async function loadEvents(extra = {}) {
    const r = await fetch(api("/api/events", { lat: CENTER.lat, lon: CENTER.lon, radius: CENTER.radius, when: CENTER.when, ...extra }));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return (await r.json()).events || [];
  }

  /* L'API renvoie des créneaux : la même expo revient une fois par jour d'ouverture.
     On regroupe par source_id pour n'afficher qu'une fiche, avec son nombre de dates. */
  function dedupe(events) {
    const byKey = new Map();
    for (const e of events) {
      const key = `${e.source || ""}:${e.source_id || `${e.title}@${e.venue}`}`;
      const prev = byKey.get(key);
      if (!prev) byKey.set(key, { ...e, dates: 1 });
      else {
        prev.dates += 1;
        if (new Date(e.start) < new Date(prev.start)) { prev.start = e.start; prev.end = e.end; }
      }
    }
    return [...byKey.values()].sort((a, b) => new Date(a.start) - new Date(b.start));
  }

  function cardHTML(e) {
    const price = PRICE_LABEL[e.price_type] || "";
    const cat = CATEGORY_LABELS[e.category] || "";
    const dates = e.dates > 1 ? `<span class="dates">+${e.dates - 1} date${e.dates > 2 ? "s" : ""}</span>` : "";
    return `<a class="ev" href="${esc(e.url || "#")}" target="_blank" rel="noopener">
      <div class="when"><b>${fmtTime(e.start)}</b><small>${fmtDay(e.start)}</small></div>
      <div>
        <h3>${esc(e.title)}</h3>
        <div class="meta">
          <span>${esc(e.venue || e.city || "")}</span>
          ${price ? `<span class="price ${esc(e.price_type)}">${price}</span>` : ""}
          ${cat ? `<span>${cat}</span>` : ""}
          ${dates}
        </div>
      </div>
    </a>`;
  }

  /* ---- fiche de lieu ------------------------------------------------------
     L'API renvoie la fiche du lieu dans chaque événement (culture.fiche).
     On n'affiche que ce qui est réellement renseigné : tout champ encore marqué
     « À VÉRIFIER », « À ÉCRIRE » ou « BROUILLON » est masqué plutôt que montré
     au public. Une fiche à moitié écrite ne doit jamais fuir en production. */
  const FICHE_LABELS = [
    ["entree", "Entrée"],
    ["horaires", "Horaires"],
    ["reservation", "Réservation"],
    ["acces", "Accès"],
    ["ponctualite", "Ponctualité"],
    ["langue", "Langue"],
    ["photos", "Photos"],
    ["tenue", "Tenue"],
    ["venir_seul", "Venir seul"],
    ["non_inities", "Sans rien y connaître"],
    ["cadre", "Le lieu"],
  ];

  const NON_PUBLIABLE = /(À VÉRIFIER|A VERIFIER|À ÉCRIRE|A ECRIRE|BROUILLON)/i;
  const utilisable = (v) => typeof v === "string" && v.trim() && !NON_PUBLIABLE.test(v);

  // « Source : … » en fin de champ : conservé, mais en gris, c'est une preuve
  // pas une phrase.
  function valeurHTML(v) {
    const i = v.indexOf("Source :");
    if (i === -1) return esc(v);
    return `${esc(v.slice(0, i).trim())} <span class="src">${esc(v.slice(i))}</span>`;
  }

  function ficheHTML(fiche) {
    if (!fiche) return "";
    const lignes = FICHE_LABELS
      .filter(([k]) => utilisable(fiche[k]))
      .map(([k, label]) => `<div><dt>${label}</dt><dd>${valeurHTML(fiche[k])}</dd></div>`)
      .join("");
    const intro = utilisable(fiche.pourquoi) ? `<p class="fiche-intro">${esc(fiche.pourquoi)}</p>` : "";
    if (!intro && !lignes) return "";
    return `<div class="fiche">${intro}${lignes ? `<dl>${lignes}</dl>` : ""}</div>`;
  }

  /* ---- section cultures --------------------------------------------------- */
  async function initCultures() {
    const host = $("#cultures"), panel = $("#cult-panel");
    let cultures = [];
    try {
      cultures = (await fetch("/api/cultures").then((r) => r.json())).cultures || [];
    } catch { host.innerHTML = `<div class="empty">Cultures indisponibles pour le moment.</div>`; return; }

    // Recale le squelette sur le nombre réel de cultures. Le plancher CSS de
    // #cultures réserve déjà la hauteur (c'est lui qui tient le CLS) ; ceci
    // évite seulement d'afficher un grand vide pendant la requête qui suit.
    host.innerHTML = `<div class="skel"></div>`.repeat(cultures.length);

    /* Un seul appel pour toutes les cultures. Il y en avait un par culture, soit
       14 requêtes et ~880 ms mesurés le 05/09/2026 — pour 25 créneaux au total.
       `limit: 300` est le maximum accepté par l'API et laisse une marge de x12 ;
       au-delà, db.search coupe par distance et une culture éloignée
       disparaîtrait sans bruit, d'où le garde-fou.
       Le regroupement se fait sur `culture_cle` (le lieu par lequel l'événement
       est entré) et non sur `culture`, qui vaut null dès qu'un mot-clé
       d'exclusion s'applique — s'en servir perdrait ces événements. */
    let bruts = [];
    try {
      bruts = await loadEvents({ culture: cultures.map((c) => c.cle).join(","), limit: 300 });
    } catch { /* la grille reste affichée, toutes les cultures à zéro */ }
    if (bruts.length >= 300) console.warn("EventMap : limite d'événements atteinte, des cultures peuvent manquer");

    const parCle = new Map(cultures.map((c) => [c.cle, []]));
    for (const e of bruts) parCle.get(e.culture_cle)?.push(e);
    const loaded = cultures.map((c) => ({ ...c, events: dedupe(parCle.get(c.cle)) }));

    const total = loaded.reduce((n, c) => n + c.events.length, 0);
    $("#cultures-count").textContent = `${loaded.length} cultures · ${total} événement${total > 1 ? "s" : ""}`;

    // Vérifié en prod le 03/09 : sur une semaine donnée, 3 cultures sur 6 sont à
    // zéro. On garde les 6 visibles (la taxonomie est le différenciant) mais on
    // remonte celles qui ont quelque chose à montrer.
    loaded.sort((a, b) => b.events.length - a.events.length);

    host.innerHTML = loaded.map((c) => {
      const lieu = (c.events.find((e) => e.culture?.lieu) || c.events[0])?.culture?.lieu || "";
      const n = c.events.length;
      // Option C revue (03/09/2026, décision de Hamdy) : la facette qualifie
      // le LIEU, jamais l'événement, mais le libellé du bouton n'affiche que
      // le nom (ex. « Suède ») — c'est le sous-titre .lieu juste en dessous
      // qui porte la nuance « lieu dédié à », pas le titre.
      return `<button class="cult${n ? "" : " vide"}" type="button" data-cle="${esc(c.cle)}" aria-expanded="false"${n ? "" : " disabled"}>
        <b>${esc(c.nom)}</b>
        <span class="lieu">${esc(lieu)}</span>
        <span class="n">${n} à venir</span>
      </button>`;
    }).join("");

    host.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".cult"); if (!btn) return;
      const open = btn.getAttribute("aria-expanded") === "true";
      host.querySelectorAll(".cult").forEach((b) => b.setAttribute("aria-expanded", "false"));
      if (open) { panel.innerHTML = ""; return; }
      btn.setAttribute("aria-expanded", "true");
      const c = loaded.find((x) => x.cle === btn.dataset.cle);
      // La fiche est celle du LIEU, mais un même lieu a plusieurs écritures (QFAP « Maison de la culture du
      // Japon à Paris », FICEP « Maison de la Culture du Japon ») dont certaines n'ont pas de fiche : on prend
      // la première événement qui en porte une — sinon le premier événement, quel qu'il soit, décidait de tout.
      const avecFiche = c.events.map((e) => e.culture?.fiche).find((f) => f && Object.keys(f).length);
      const fiche = ficheHTML(avecFiche);
      panel.innerHTML = c.events.length
        ? fiche + c.events.map(cardHTML).join("")
        : fiche + `<div class="empty">Rien d'annoncé cette semaine pour cette culture.</div>`;
      /* Le panneau s'ouvre SOUS la grille des cultures : avec 15 cultures sur 4 colonnes il est
         hors de l'écran, et le clic semblait ne rien faire (signalé le 06/10/2026). On l'amène
         à l'écran, instantanément : un défilement animé n'est pas fiable partout (pas d'image
         produite dans un onglet en arrière-plan) et l'utilisateur vient de cliquer. */
      panel.scrollIntoView({ behavior: "auto", block: "start" });
    });
  }

  /* ---- vignette carte + section catégories -------------------------------- */
  async function initRest() {
    let events = [];
    try { events = await loadEvents({ price: "free" }); }
    catch { $("#list").innerHTML = `<div class="empty">Impossible de charger les événements.</div>`; return; }

    // Carte miniature : non interactive, le clic est géré par le lien qui l'enveloppe.
    if (window.L && events.length) {
      const map = L.map("minimap", {
        zoomControl: false, attributionControl: false, dragging: false, scrollWheelZoom: false,
        doubleClickZoom: false, boxZoom: false, keyboard: false, touchZoom: false,
      });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
      const pts = events.map((e) => [e.lat, e.lon]);
      events.forEach((e) => L.circleMarker([e.lat, e.lon], {
        radius: 5, color: e.price_type === "free" ? "#4fd694" : "#f0a93c", weight: 2, fillOpacity: .85, interactive: false,
      }).addTo(map));
      map.fitBounds(pts, { padding: [24, 24], maxZoom: 13 });
    }
    $("#map-count").textContent = `· ${events.length} créneaux`;

    const uniq = dedupe(events);
    const cats = [...new Set(uniq.map((e) => e.category).filter(Boolean))]
      .sort((a, b) => (CATEGORY_LABELS[a] || a).localeCompare(CATEGORY_LABELS[b] || b));
    $("#cat-count").textContent = `${uniq.length} événement${uniq.length > 1 ? "s" : ""} gratuits · 7 jours`;

    const chips = $("#cats"), list = $("#list");
    chips.innerHTML = [`<button class="chip" type="button" data-cat="" aria-pressed="true">Tout</button>`]
      .concat(cats.map((c) => `<button class="chip" type="button" data-cat="${esc(c)}" aria-pressed="false">${CATEGORY_LABELS[c] || esc(c)}</button>`))
      .join("");

    const draw = (cat) => {
      const sel = cat ? uniq.filter((e) => e.category === cat) : uniq;
      list.innerHTML = sel.length ? sel.map(cardHTML).join("") : `<div class="empty">Rien dans cette catégorie cette semaine.</div>`;
    };
    draw("");

    chips.addEventListener("click", (ev) => {
      const b = ev.target.closest(".chip"); if (!b) return;
      chips.querySelectorAll(".chip").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      draw(b.dataset.cat);
    });
  }

  initCultures();
  initRest();
})();
