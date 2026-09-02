(() => {
  const CATEGORY_LABELS = {
    music: "Musique", theatre: "Spectacle", cinema: "Cinéma", expo: "Expo", kids: "Enfants",
    workshop: "Atelier", talk: "Rencontre", sport: "Sport", market: "Marché / festival", other: "Autre",
  };
  // Châtelet par défaut : le centre est remplacé par la géoloc si l'utilisateur
  // l'accepte. Rien n'est jamais stocké : ni position, ni préférence.
  const state = { lat: 48.8584, lon: 2.3470, when: "today", radius: 2, price: "free", category: "", located: false };

  const $ = (s) => document.querySelector(s);
  const list = $("#list"), status = $("#status");

  const map = L.map("map", { zoomControl: false }).setView([state.lat, state.lon], 13);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap",
  }).addTo(map);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  const markers = L.layerGroup().addTo(map);
  let me = null, circle = null;

  function drawCenter() {
    if (me) me.remove();
    if (circle) circle.remove();
    me = L.marker([state.lat, state.lon], { icon: L.divIcon({ className: "me", iconSize: [14, 14] }), interactive: false }).addTo(map);
    circle = L.circle([state.lat, state.lon], { radius: state.radius * 1000, color: "#ef3d6e", weight: 1, fillOpacity: 0.05, interactive: false }).addTo(map);
  }

  const fmtTime = (iso) => new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" });
  const fmtDay = (iso) => new Date(iso).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", timeZone: "Europe/Paris" });
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const priceLabel = { free: "Gratuit", paid: "Payant", free_conditional: "Gratuit*", unknown: "" };

  function render(data, fit) {
    markers.clearLayers();
    list.innerHTML = "";
    const evs = data.events;
    if (!evs.length) {
      list.innerHTML = `<div class="empty"><b>Rien dans ce rayon</b>Élargis la distance ou regarde demain.</div>`;
      status.innerHTML = `<strong>0</strong> événement`;
      return;
    }
    status.innerHTML = `<strong>${evs.length}</strong> événement${evs.length > 1 ? "s" : ""} à moins de ${state.radius} km`;
    const bounds = [];
    evs.forEach((e, i) => {
      const card = document.createElement("a");
      card.className = "ev"; card.role = "listitem"; card.href = e.url || "#"; card.target = "_blank"; card.rel = "noopener";
      card.innerHTML = `
        <div class="when"><b>${fmtTime(e.start)}</b><small>${state.when === "today" ? "" : fmtDay(e.start)}</small></div>
        <div>
          <h3>${esc(e.title)}</h3>
          <div class="meta">
            <span>${esc(e.venue || e.city || "")}</span>
            <span>${e.distance_km} km</span>
            <span class="price ${e.price_type}">${priceLabel[e.price_type] || ""}</span>
            <span>${CATEGORY_LABELS[e.category] || ""}</span>
          </div>
        </div>`;
      const m = L.circleMarker([e.lat, e.lon], { radius: 7, color: e.price_type === "free" ? "#4fd694" : "#f0a93c", weight: 2, fillOpacity: .85 })
        .bindPopup(`<b>${esc(e.title)}</b><br>${fmtTime(e.start)} · ${esc(e.venue || "")}<br>${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">Voir la page</a>` : ""}`);
      m.on("click", () => { document.querySelectorAll(".ev.active").forEach((x) => x.classList.remove("active")); card.classList.add("active"); card.scrollIntoView({ block: "nearest", behavior: "smooth" }); });
      card.addEventListener("mouseenter", () => m.openPopup());
      markers.addLayer(m); list.appendChild(card); bounds.push([e.lat, e.lon]);
    });
    // Ne recadrer que sur une vraie nouvelle recherche (filtre change, geoloc,
    // premier chargement) -- jamais apres un pan/zoom manuel de l'utilisateur,
    // sinon son geste est efface des que les evenements se rechargent.
    if (fit && bounds.length) map.fitBounds(bounds.concat([[state.lat, state.lon]]), { padding: [30, 30], maxZoom: 15 });
  }

  let ctrl = null;
  async function load(fit = true) {
    if (ctrl) ctrl.abort();
    ctrl = new AbortController();
    status.textContent = "Chargement…";
    const q = new URLSearchParams({ lat: state.lat, lon: state.lon, radius: state.radius, when: state.when });
    if (state.price) q.set("price", state.price);
    if (state.category) q.set("category", state.category);
    try {
      const r = await fetch(`/api/events?${q}`, { signal: ctrl.signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      render(await r.json(), fit);
      drawCenter();
    } catch (err) {
      if (err.name !== "AbortError") status.textContent = "Impossible de charger les événements.";
    }
  }

  $("#when").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-when]"); if (!b) return;
    document.querySelectorAll("[data-when]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    state.when = b.dataset.when; load();
  });
  $("#radius").addEventListener("change", (e) => { state.radius = +e.target.value; load(); });
  $("#price").addEventListener("change", (e) => { state.price = e.target.value; load(); });
  $("#category").addEventListener("change", (e) => { state.category = e.target.value; load(); });

  // Géoloc : API navigateur, consentement explicite au clic, jamais persistée.
  $("#geo").addEventListener("click", () => {
    if (!navigator.geolocation) return;
    status.textContent = "Localisation…";
    navigator.geolocation.getCurrentPosition(
      (p) => { state.lat = p.coords.latitude; state.lon = p.coords.longitude; state.located = true; $("#geo").setAttribute("aria-pressed", "true"); map.setView([state.lat, state.lon], 14); load(); },
      () => { status.textContent = "Localisation refusée — position par défaut conservée."; },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 60000 },
    );
  });

  // Déplacer la carte redéfinit le centre de recherche (avec un délai pour
  // ne pas mitrailler l'API pendant un glissement).
  let moveTimer = null;
  map.on("moveend", () => {
    if (map._justFit) { map._justFit = false; return; }
    clearTimeout(moveTimer);
    // fit=false : l'utilisateur vient de choisir sa vue a la main, ne pas la lui reprendre.
    moveTimer = setTimeout(() => { const c = map.getCenter(); state.lat = c.lat; state.lon = c.lng; load(false); }, 500);
  });
  const origFit = map.fitBounds.bind(map);
  map.fitBounds = (...a) => { map._justFit = true; return origFit(...a); };

  fetch("/api/categories").then((r) => r.json()).then(({ categories }) => {
    const sel = $("#category");
    categories.forEach((c) => { const o = document.createElement("option"); o.value = c; o.textContent = CATEGORY_LABELS[c] || c; sel.appendChild(o); });
  }).catch(() => {});

  load();
})();
