/* EventMap — accueil « mode classique ».
   Range les événements par culture d'origine puis par catégorie, et propose une
   vignette de carte qui renvoie vers /carte. Aucune donnée n'est stockée. */
(() => {
  const CATEGORY_LABELS = {
    music: "Musique", theatre: "Spectacle", cinema: "Cinéma", expo: "Expo", kids: "Enfants",
    workshop: "Atelier", talk: "Rencontre", sport: "Sport", market: "Marché / festival", other: "Autre",
  };
  // Châtelet, rayon large : l'accueil montre tout Paris, pas les environs de l'utilisateur.
  const CENTER = { lat: 48.8584, lon: 2.3470, radius: 8, when: "week" };
  const PRICE_LABEL = { free: "Gratuit", paid: "Payant", free_conditional: "Gratuit*", unknown: "" };

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" });
  const fmtDay = (iso) => new Date(iso).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", timeZone: "Europe/Paris" });

  async function loadEvents(extra = {}) {
    const q = new URLSearchParams({ lat: CENTER.lat, lon: CENTER.lon, radius: CENTER.radius, when: CENTER.when, ...extra });
    const r = await fetch(`/api/events?${q}`);
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
    return `<a class="ev" href="${esc(e.url || "#")}" target="_blank" rel="noopener" role="listitem">
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

  /* ---- section cultures --------------------------------------------------- */
  async function initCultures() {
    const host = $("#cultures"), panel = $("#cult-panel");
    let cultures = [];
    try {
      cultures = (await fetch("/api/cultures").then((r) => r.json())).cultures || [];
    } catch { host.innerHTML = `<div class="empty">Cultures indisponibles pour le moment.</div>`; return; }

    const loaded = await Promise.all(cultures.map(async (c) => {
      try { return { ...c, events: dedupe(await loadEvents({ culture: c.cle })) }; }
      catch { return { ...c, events: [] }; }
    }));

    const total = loaded.reduce((n, c) => n + c.events.length, 0);
    $("#cultures-count").textContent = `${loaded.length} cultures · ${total} événement${total > 1 ? "s" : ""}`;

    // Vérifié en prod le 03/09 : sur une semaine donnée, 3 cultures sur 6 sont à
    // zéro. On garde les 6 visibles (la taxonomie est le différenciant) mais on
    // remonte celles qui ont quelque chose à montrer.
    loaded.sort((a, b) => b.events.length - a.events.length);

    host.innerHTML = loaded.map((c) => {
      const lieu = c.events[0]?.culture?.lieu || "";
      const n = c.events.length;
      // Option C (03/09/2026, décision de Hamdy) : la facette qualifie le
      // LIEU, jamais l'événement — le libellé doit le dire, pas juste
      // afficher le nom du pays comme si l'événement en relevait.
      return `<button class="cult${n ? "" : " vide"}" type="button" data-cle="${esc(c.cle)}" aria-expanded="false"${n ? "" : " disabled"}>
        <b>Lieux dédiés à la culture ${esc(c.nom)}</b>
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
      panel.innerHTML = c.events.length
        ? c.events.map(cardHTML).join("")
        : `<div class="empty">Rien d'annoncé cette semaine pour cette culture.</div>`;
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
