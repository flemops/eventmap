/* EventMap — page de ville : carte + liste + fiche, pour TOUTES les villes.
   Aucune ville n'est codée ici : centre, fuseau, rayons, catégories, langue
   viennent de la configuration posée par le serveur (EM_COMMON.CITY).
   Rien n'est stocké hors de la préférence de ville (localStorage) : ni position,
   ni filtres. L'état utile (quand, gratuit) voyage dans l'URL. */
(() => {
  const { CITY, PATHS, LANG, I, esc, safeHref, fmtTime, fmtDay, fmtDayLong, priceText, directionsUrl, api } = window.EM_COMMON;
  const t = I.t;
  const $ = (s) => document.querySelector(s);
  const list = $("#list"), status = $("#status"), notice = $("#notice"), detail = $("#detail");
  const params = new URLSearchParams(location.search);

  const state = {
    lat: CITY.center.lat, lon: CITY.center.lon,
    when: ["today", "tomorrow", "weekend", "week"].includes(params.get("when")) ? params.get("when") : "today",
    radius: CITY.radius_options_km.includes(+params.get("r")) ? +params.get("r") : (CITY.default_radius_km || CITY.radius_options_km[0]),
    free: params.has("price") ? params.get("price") === "free" : CITY.default_price === "free",
    category: params.get("cat") || "", located: false,
  };

  /* ---- libellés ----------------------------------------------------------- */
  document.querySelectorAll("#when [data-when]").forEach((b) => { b.textContent = t(b.dataset.when); });
  $("#geo").textContent = t("near_me");
  $("#free").textContent = t("free");
  $("#lbl-city").textContent = t("city");
  $("#sheet").setAttribute("aria-label", t("list"));
  $("#map").setAttribute("aria-label", t("map"));
  $("#radius").innerHTML = CITY.radius_options_km.map((k, i) =>
    `<option value="${k}">${k === CITY.max_radius_km ? t("all_city") : `${k} ${t("km")}`}</option>`).join("");
  $("#radius").value = String(state.radius);
  $("#free").setAttribute("aria-pressed", String(state.free));
  document.querySelectorAll("#when [data-when]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.when === state.when)));
  $("#grab").setAttribute("aria-label", t("sheet_expand"));

  /* ---- carte --------------------------------------------------------------- */
  const map = L.map("map", { zoomControl: false }).setView([state.lat, state.lon], CITY.zoom);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
  L.control.zoom({ position: LANG === "ar" ? "bottomleft" : "bottomright" }).addTo(map);
  const cluster = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 45, spiderfyOnMaxZoom: true, chunkedLoading: true });
  map.addLayer(cluster);
  let me = null, circle = null;
  const byId = new Map();        // id -> { e, marker, card }
  let selectedId = null;

  const colorOf = (e) => (e.price_type === "free" ? "#4fd694" : "#f0a93c");
  const styleOf = (e, sel) => ({ radius: sel ? 11 : 7, color: sel ? "#ffffff" : colorOf(e), weight: sel ? 3 : 2, fillColor: colorOf(e), fillOpacity: sel ? 1 : 0.85 });

  function drawCenter() {
    if (me) me.remove();
    if (circle) circle.remove();
    if (!state.located) return;
    me = L.marker([state.lat, state.lon], { icon: L.divIcon({ className: "me", iconSize: [14, 14] }), interactive: false, keyboard: false }).addTo(map);
    circle = L.circle([state.lat, state.lon], { radius: state.radius * 1000, color: "#ef3d6e", weight: 1, fillOpacity: 0.05, interactive: false }).addTo(map);
  }

  /* ---- notices honnêtes (13.51) ------------------------------------------- */
  function say(msg, kind = "warn", retry = false) {
    if (!msg) { notice.hidden = true; notice.textContent = ""; return; }
    notice.hidden = false;
    notice.className = "notice" + (kind === "info" ? " info" : "");
    notice.textContent = msg;
    if (retry) {
      const b = document.createElement("button");
      b.className = "chip"; b.type = "button"; b.textContent = t("retry");
      b.addEventListener("click", () => load());
      notice.appendChild(b);
    }
  }
  let geoNote = "";
  function dataNotice(data, n) {
    const st = data && data.state;
    if (st === "no_sources") return say(t("no_sources"));
    if (st === "stale") return say(n ? t("stale") : t("empty_but_degraded"));
    if (st === "partial") return say(n ? t("partial") : t("empty_but_degraded"));
    say(geoNote, "info");
  }

  /* ---- rendu ---------------------------------------------------------------- */
  const sourceLabel = (src) => {
    if (!src) return "";
    if (src === "qfap") return "Que faire à Paris (Ville de Paris)";
    const m = /^[a-z]+:(https?:\/\/[^/]+)/i.exec(src);
    return m ? m[1].replace(/^https?:\/\//, "") : src;
  };

  function cardEl(e) {
    const el = document.createElement("li");
    const price = priceText(e);
    const cat = I.catLabel(e.category);
    const stNote = e.status === "cancelled" ? t("cancelled") : e.status === "postponed" ? t("postponed") : "";
    el.innerHTML = `<button type="button" class="ev" data-id="${e.id}" aria-pressed="false">
      <span class="when"><b>${esc(fmtTime(e.start))}</b><small>${state.when === "today" ? "" : esc(fmtDay(e.start))}</small></span>
      <span>
        <h3 dir="auto">${esc(e.title)}</h3>
        <span class="meta">
          ${e.venue ? `<span dir="auto">${esc(e.venue)}</span>` : ""}
          <span>${esc(String(e.distance_km))} ${esc(t("km"))}</span>
          ${price ? `<span class="price ${esc(e.price_type)}">${esc(price)}</span>` : ""}
          ${cat ? `<span>${esc(cat)}</span>` : ""}
          ${stNote ? `<span class="badge">${esc(stNote)}</span>` : ""}
          ${e.stale ? `<span class="stale">${esc(t("stale_item"))}</span>` : ""}
        </span>
      </span></button>`;
    return el;
  }

  function render(data, fit) {
    cluster.clearLayers();
    byId.clear();
    selectedId = null;
    list.innerHTML = "";
    const evs = data.events;
    $("#count").textContent = evs.length ? `· ${t("n_events", { n: evs.length })}` : "";
    dataNotice(data.data, evs.length);
    if (!evs.length) {
      const degraded = data.data && data.data.state && !["ok", "unknown"].includes(data.data.state);
      list.innerHTML = degraded ? "" :
        `<div class="empty"><b>${esc(t("none_title"))}</b>${esc(t("none_hint"))}</div>`;
      status.textContent = degraded ? t("none_city") : "";
      return;
    }
    status.innerHTML = esc(t("n_events_in", { n: evs.length, r: state.radius })).replace(String(evs.length), `<strong>${evs.length}</strong>`);
    const ul = document.createElement("ul");
    const bounds = [];
    for (const e of evs) {
      const card = cardEl(e);
      const m = L.circleMarker([e.lat, e.lon], styleOf(e, false));
      m.on("click", () => select(e.id, { open: true }));
      cluster.addLayer(m);
      byId.set(e.id, { e, marker: m, card: card.firstElementChild });
      ul.appendChild(card);
      bounds.push([e.lat, e.lon]);
    }
    list.appendChild(ul);
    // Ne recadrer que sur une vraie nouvelle recherche : jamais après un pan/zoom manuel.
    if (fit && bounds.length) {
      map._justFit = true;
      map.fitBounds(state.located ? bounds.concat([[state.lat, state.lon]]) : bounds, { padding: [30, 30], maxZoom: 15 });
    }
  }

  /* ---- sélection carte <-> carte événement (13.26) -------------------------- */
  function select(id, { open = false, pan = true } = {}) {
    const prev = byId.get(selectedId);
    if (prev) { prev.marker.setStyle(styleOf(prev.e, false)); prev.card.classList.remove("active"); prev.card.setAttribute("aria-pressed", "false"); }
    const cur = byId.get(id);
    if (!cur) return;
    selectedId = id;
    cur.marker.setStyle(styleOf(cur.e, true)); cur.marker.bringToFront?.();
    cur.card.classList.add("active"); cur.card.setAttribute("aria-pressed", "true");
    if (pan) cluster.zoomToShowLayer(cur.marker, () => map.panTo(cur.marker.getLatLng()));
    if (open) openDetail(cur.e); else cur.card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  list.addEventListener("click", (ev) => {
    const b = ev.target.closest("button.ev"); if (!b) return;
    select(+b.dataset.id, { open: true });
  });

  /* ---- fiche détaillée -------------------------------------------------------- */
  let lastFocus = null;
  function openDetail(e) {
    lastFocus = document.activeElement;
    const book = safeHref(e.booking_url), off = safeHref(e.url);
    const price = priceText(e);
    const home = PATHS.home.replace(/\/$/, "");
    const share = `${location.origin}${home}/e/${e.id}`;
    const stNote = e.status === "cancelled" ? t("cancelled") : e.status === "postponed" ? t("postponed") : "";
    detail.innerHTML = `
      <button type="button" class="back"><span class="i-dir" aria-hidden="true">←</span> ${esc(t("close"))}</button>
      <h2 dir="auto" tabindex="-1">${esc(e.title)}</h2>
      ${stNote ? `<p><span class="badge">${esc(stNote)}</span></p>` : ""}
      <dl>
        <div><dt>🕒</dt><dd>${esc(fmtDayLong(e.start))} · ${esc(fmtTime(e.start))}${e.end ? ` – ${esc(fmtTime(e.end))}` : ""}</dd></div>
        ${e.venue ? `<div><dt>📍</dt><dd dir="auto">${esc(e.venue)}${e.address ? `<br><small>${esc(e.address)}</small>` : ""}</dd></div>` : ""}
        <div><dt>${esc(t("distance"))}</dt><dd>${esc(String(e.distance_km))} ${esc(t("km"))}</dd></div>
        ${price ? `<div><dt>${esc(t("price"))}</dt><dd>${esc(price)}</dd></div>` : ""}
        <div><dt>${esc(t("categories"))}</dt><dd>${esc(I.catLabel(e.category))}</dd></div>
      </dl>
      ${e.description ? `<p class="desc" dir="auto">${esc(e.description)}</p>` : ""}
      <div class="actions">
        <a class="btn primary" target="_blank" rel="noopener" href="${esc(directionsUrl(e))}">${esc(t("directions"))}</a>
        ${book ? `<a class="btn" target="_blank" rel="noopener nofollow" href="${esc(book)}">${esc(t("book"))}</a>` : ""}
        ${off ? `<a class="btn" target="_blank" rel="noopener nofollow" href="${esc(off)}">${esc(t("official"))}</a>` : ""}
        <button class="btn" type="button" id="share">${esc(t("share"))}</button>
      </div>
      <p class="prov">${esc(t("source"))} : ${esc(sourceLabel(e.source))}${e.last_seen ? ` · ${esc(t("updated"))} ${esc(new Date(e.last_seen).toLocaleDateString(I.locale, { day: "numeric", month: "short", timeZone: CITY.timezone }))}` : ""}</p>`;
    list.hidden = true; status.hidden = true; detail.hidden = false;
    sheetState("half", false);
    detail.querySelector("h2").focus();
    detail.querySelector(".back").addEventListener("click", closeDetail);
    detail.querySelector("#share").addEventListener("click", async (ev) => {
      try {
        if (navigator.share) await navigator.share({ title: e.title, url: share });
        else { await navigator.clipboard.writeText(share); ev.target.textContent = t("copied"); }
      } catch { /* annulé par l'utilisateur */ }
    });
    history.replaceState(null, "", `${location.pathname}${location.search}#e${e.id}`);
  }
  function closeDetail() {
    detail.hidden = true; list.hidden = false; status.hidden = false;
    history.replaceState(null, "", location.pathname + location.search);
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !detail.hidden) closeDetail(); });

  /* ---- bottom sheet (mobile) ----------------------------------------------------- */
  const sheet = $("#sheet"), grab = $("#grab");
  const ORDER = ["peek", "half", "full"];
  function sheetState(s, fromUser = true) {
    sheet.dataset.state = s;
    sheet.style.removeProperty("height");
    grab.setAttribute("aria-expanded", String(s !== "peek"));
    grab.setAttribute("aria-label", t(s === "full" ? "sheet_collapse" : "sheet_expand"));
    setTimeout(() => map.invalidateSize(), 250);
  }
  grab.addEventListener("click", () => sheetState(ORDER[(ORDER.indexOf(sheet.dataset.state) + 1) % ORDER.length]));
  let drag = null;
  grab.addEventListener("pointerdown", (ev) => { drag = { y: ev.clientY, h: sheet.getBoundingClientRect().height, moved: false }; grab.setPointerCapture(ev.pointerId); sheet.classList.add("dragging"); });
  grab.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const dy = drag.y - ev.clientY;
    if (Math.abs(dy) > 4) drag.moved = true;
    const max = sheet.parentElement.getBoundingClientRect().height - 4;
    sheet.style.height = Math.max(72, Math.min(max, drag.h + dy)) + "px";
  });
  grab.addEventListener("pointerup", () => {
    if (!drag) return;
    sheet.classList.remove("dragging");
    if (drag.moved) {
      const h = sheet.getBoundingClientRect().height, max = sheet.parentElement.getBoundingClientRect().height;
      const targets = { peek: 92, half: max * 0.46, full: max };
      sheetState(Object.entries(targets).sort((a, b) => Math.abs(a[1] - h) - Math.abs(b[1] - h))[0][0]);
      grab.dataset.dragged = "1";
    }
    drag = null;
  });
  grab.addEventListener("click", (ev) => { if (grab.dataset.dragged) { delete grab.dataset.dragged; ev.stopImmediatePropagation(); } }, true);

  /* ---- chargement ---------------------------------------------------------------- */
  let ctrl = null;
  function syncUrl() {
    const q = new URLSearchParams();
    if (state.when !== "today") q.set("when", state.when);
    if (state.free !== (CITY.default_price === "free")) q.set("price", state.free ? "free" : "all");
    if (state.radius !== (CITY.default_radius_km || CITY.radius_options_km[0])) q.set("r", state.radius);
    if (state.category) q.set("cat", state.category);
    const s = q.toString();
    history.replaceState(null, "", location.pathname + (s ? "?" + s : "") + location.hash);
  }

  async function load(fit = true) {
    if (ctrl) ctrl.abort();
    ctrl = new AbortController();
    const myCtrl = ctrl;
    status.textContent = t("loading");
    list.innerHTML = '<div class="skel"></div><div class="skel"></div><div class="skel"></div>';
    closeDetail();
    syncUrl();
    const q = { lat: state.lat, lon: state.lon, radius: state.radius, when: state.when, lang: LANG };
    if (state.free) q.price = "free";
    if (state.category) q.category = state.category;
    try {
      const r = await fetch(api("/api/events", q), { signal: myCtrl.signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      render(data, fit);
      drawCenter();
    } catch (err) {
      if (err.name === "AbortError") return;
      list.innerHTML = ""; status.textContent = ""; $("#count").textContent = "";
      say(t("error_network"), "warn", true);
    }
  }

  document.querySelector("#when").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-when]"); if (!b) return;
    document.querySelectorAll("#when [data-when]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    state.when = b.dataset.when; load();
  });
  $("#radius").addEventListener("change", (e) => { state.radius = +e.target.value; load(); });
  $("#free").addEventListener("click", (e) => { state.free = !state.free; e.currentTarget.setAttribute("aria-pressed", String(state.free)); load(); });

  /* Catégories : seulement celles qui ont de vrais événements (13.23). */
  const catSel = $("#category");
  catSel.innerHTML = `<option value="">${esc(t("cat_all"))}</option>`;
  fetch(api("/api/categories")).then((r) => r.json()).then((c) => {
    const groups = (c.groups && c.groups.length) ? c.groups.map((g) => [g.key, I.grpLabel(g.key)]) : (c.categories || []).map((k) => [k, I.catLabel(k)]);
    catSel.innerHTML = `<option value="">${esc(t("cat_all"))}</option>` + groups.map(([k, l]) => `<option value="${esc(k)}">${esc(l)}</option>`).join("");
    catSel.value = state.category;
  }).catch(() => {});
  catSel.addEventListener("change", (e) => { state.category = e.target.value; load(); });

  /* ---- géolocalisation : consentement explicite, jamais persistée, jamais bloquante ------ */
  $("#geo").addEventListener("click", () => {
    if (!navigator.geolocation) { geoNote = t("geo_unavailable"); say(geoNote, "info"); return; }
    status.textContent = t("geo_locating");
    navigator.geolocation.getCurrentPosition(
      (p) => {
        const here = { lat: p.coords.latitude, lon: p.coords.longitude };
        geoNote = ""; state.located = true; $("#geo").setAttribute("aria-pressed", "true");
        const d = distKm(here, CITY.center);
        if (d > CITY.max_radius_km * 2) {               // clairement hors de cette ville
          state.located = false; state.lat = CITY.center.lat; state.lon = CITY.center.lon;
          suggestCity(here); load(); return;
        }
        state.lat = here.lat; state.lon = here.lon; map.setView([state.lat, state.lon], Math.max(CITY.zoom, 13)); load();
      },
      (err) => { geoNote = err && err.code === 1 ? t("geo_denied") : t("geo_unavailable"); status.textContent = ""; say(geoNote, "info"); },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 60000 },
    );
  });
  function distKm(a, b) {
    const R = 6371, rad = (x) => x * Math.PI / 180, dLat = rad(b.lat - a.lat), dLon = rad(b.lon - a.lon);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  /* Déplacer la carte redéfinit le centre de recherche (avec un délai pour ne pas mitrailler l'API). */
  let moveTimer = null;
  map.on("moveend", () => {
    if (map._justFit) { map._justFit = false; return; }
    clearTimeout(moveTimer);
    moveTimer = setTimeout(() => { const c = map.getCenter(); state.lat = c.lat; state.lon = c.lng; load(false); }, 500);
  });

  /* ---- villes : sélecteur, langue, suggestion ---------------------------------------- */
  const pathFor = (cityId, lang, def, suffix) => `${lang && lang !== def ? "/" + lang : ""}/${cityId}${suffix}`;
  function carry() {
    const q = new URLSearchParams();
    if (state.when !== "today") q.set("when", state.when);
    q.set("price", state.free ? "free" : "all");
    return "?" + q.toString();
  }
  let CITIES = [];
  function suggestCity(here) {
    const near = CITIES.filter((c) => c.id !== CITY.id && distKm(here, c.center) <= 60);
    if (near.length !== 1) { geoNote = t("geo_unavailable"); say(geoNote, "info"); return; }
    const c = near[0], name = c.names[LANG] || c.names.en || c.id;
    notice.hidden = false; notice.className = "notice info";
    notice.innerHTML = `${esc(t("suggest_city", { city: name }))} <a class="chip" href="${esc(pathFor(c.id, LANG === "ar" || LANG === "fr" ? (c.languages.includes(LANG) ? LANG : c.default_language) : c.default_language, c.default_language, "/carte"))}">${esc(t("yes_go", { city: name }))}</a>`;
  }
  fetch("/api/cities").then((r) => r.json()).then(({ cities }) => {
    CITIES = cities;
    const sel = $("#city-select");
    sel.innerHTML = cities.map((c) => `<option value="${esc(c.id)}"${c.id === CITY.id ? " selected" : ""}>${esc(c.names[LANG] || c.names.en || c.id)}</option>`).join("");
    sel.addEventListener("change", () => {
      const c = cities.find((x) => x.id === sel.value); if (!c) return;
      try { localStorage.setItem("em_city", c.id); } catch { /* navigation privée */ }
      const lang = c.languages.includes(LANG) ? LANG : c.default_language;
      location.href = pathFor(c.id, lang, c.default_language, "/carte") + carry();
    });
    if (cities.length < 2) sel.closest(".sw").hidden = true;
  }).catch(() => { $("#city-select").closest(".sw").hidden = true; });

  try { localStorage.setItem("em_city", CITY.id); } catch { /* ignoré */ }
  const langs = $("#lang-links");
  if ((CITY.languages || []).length > 1) {
    const names = { en: "EN", ar: "ع", fr: "FR" };
    langs.innerHTML = CITY.languages.map((l) =>
      `<a href="${esc(pathFor(CITY.id, l, CITY.default_language, "/carte"))}" hreflang="${esc(l)}" lang="${esc(l)}" ${l === LANG ? 'aria-current="true"' : ""}>${esc(names[l] || l)}</a>`).join("");
  } else langs.hidden = true;

  // Quand on arrive sur un lien d'événement partagé (#e123), l'ouvrir une fois la liste chargée.
  const hash = /^#e(\d+)$/.exec(location.hash);
  load();
  if (hash) {
    const want = +hash[1];
    const iv = setInterval(() => { if (byId.has(want)) { clearInterval(iv); select(want, { open: true }); } }, 300);
    setTimeout(() => clearInterval(iv), 6000);
  }
  sheetState(matchMedia("(min-width: 900px)").matches ? "half" : "half", false);
})();
