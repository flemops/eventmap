/* EventMap — page de ville : carte + liste + fiche, pour TOUTES les villes.
   Aucune ville n'est codée ici : centre, fuseau, rayons, catégories, langue
   viennent de la configuration posée par le serveur (EM_COMMON.CITY).
   Rien n'est stocké hors de la préférence de ville et des favoris (localStorage, jamais de
   compte) : ni position, ni filtres. L'état utile (quand, gratuit, rayon, catégorie) voyage
   dans l'URL. */
(() => {
  const { CITY, PATHS, LANG, I, esc, safeHref, fmtTime, fmtDay, fmtDayLong, priceText, fmtDist, directionsUrl, api } = window.EM_COMMON;
  const t = I.t;
  const $ = (s) => document.querySelector(s);
  const list = $("#list"), status = $("#status"), notice = $("#notice"), detail = $("#detail"), panel = $("#panel");
  const params = new URLSearchParams(location.search);
  const WHENS = ["today", "tomorrow", "weekend", "week"];
  const DEFAULT_RADIUS = CITY.default_radius_km || CITY.radius_options_km[0];
  const DEFAULT_FREE = CITY.default_price === "free";
  const canHover = matchMedia("(hover: hover)").matches;
  const isWide = () => matchMedia("(min-width: 900px)").matches;
  const cityName = (CITY.names && (CITY.names[LANG] || CITY.names.en)) || CITY.id;

  const state = {
    lat: CITY.center.lat, lon: CITY.center.lon,
    when: WHENS.includes(params.get("when")) ? params.get("when") : "today",
    radius: CITY.radius_options_km.includes(+params.get("r")) ? +params.get("r") : DEFAULT_RADIUS,
    free: params.has("price") ? params.get("price") === "free" : DEFAULT_FREE,
    category: params.get("cat") || "", located: false, me: null, view: "events",
  };
  let lastCount = 0;

  /* ---- carte --------------------------------------------------------------- */
  const map = L.map("map", { zoomControl: false }).setView([state.lat, state.lon], CITY.zoom);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
  const cluster = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 45, spiderfyOnMaxZoom: true, chunkedLoading: true });
  map.addLayer(cluster);
  let meMarker = null, circle = null;
  const byId = new Map();        // id -> { e, marker, cards: [button, …] }
  /* Un déplacement de carte fait par le CODE (sélection d'un événement, cadrage, géoloc)
     ne doit pas être pris pour un geste de l'utilisateur : sinon il proposerait de relancer
     une recherche sur ce qu'il n'a pas touché. */
  let quiet = 0, anchor = null;
  const quietly = (fn) => {
    quiet++;
    try { fn(); } finally { setTimeout(() => { quiet = Math.max(0, quiet - 1); if (!quiet) anchor = map.getCenter(); }, 1200); }
  };
  let selectedId = null;

  const pinIcon = (e, cls = "") => L.divIcon({
    className: "pin-wrap", iconSize: [28, 28], iconAnchor: [14, 14],
    html: `<span class="pin${e.price_type === "free" ? " free" : ""}${cls ? " " + cls : ""}"></span>`,
  });
  const pinOf = (rec) => rec.marker.getElement()?.querySelector(".pin");

  function drawMe() {
    if (meMarker) meMarker.remove();
    if (circle) circle.remove();
    meMarker = circle = null;
    if (state.me) meMarker = L.marker([state.me.lat, state.me.lon], { icon: L.divIcon({ className: "me", iconSize: [14, 14] }), interactive: false, keyboard: false }).addTo(map);
    if (state.located) circle = L.circle([state.lat, state.lon], { radius: state.radius * 1000, color: "#ef3d6e", weight: 1, fillOpacity: 0.05, interactive: false }).addTo(map);
  }

  /* Sur mobile, la feuille du bas masque le bas de la carte : on cadre et on centre sur la
     partie réellement visible. */
  const sheetCover = () => (isWide() || sheet.dataset.state === "peek" ? 0 : sheet.getBoundingClientRect().height);
  function panToVisible(ll) {
    const cover = sheetCover();
    if (!cover) return map.panTo(ll);
    const z = map.getZoom();
    map.panTo(map.unproject(map.project(ll, z).add([0, cover / 2]), z));
  }

  /* ---- toast (retour très court, annoncé aux lecteurs d'écran) ----------------------- */
  const toastEl = $("#toast");
  let toastTimer = null;
  function toast(msg) {
    toastEl.textContent = msg; toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 2200);
  }

  /* ---- notices honnêtes (13.51) : aucune donnée ≠ source en retard ≠ rien d'au programme --- */
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
  const statusNote = (e) => (e.status === "cancelled" ? t("cancelled") : e.status === "postponed" ? t("postponed") : "");

  /* Une carte : HEURE, TITRE, lieu · distance, prix. Catégorie et mentions rares : discrètes. */
  function cardEl(e, { day = state.when !== "today" } = {}) {
    const el = document.createElement("li");
    const price = priceText(e);
    const cat = I.catLabel(e.category);
    const note = statusNote(e);
    const dist = e.distance_km != null ? fmtDist(e.distance_km) : "";
    const where = [e.venue ? `<span dir="auto">${esc(e.venue)}</span>` : "", dist ? `<span>${esc(dist)}</span>` : ""].filter(Boolean).join(" · ");
    el.innerHTML = `<button type="button" class="ev" data-id="${e.id}" aria-pressed="false">
      <span class="ev-time"><b>${esc(fmtTime(e.start))}</b>${day ? `<small>${esc(fmtDay(e.start))}</small>` : ""}</span>
      <span class="ev-body">
        <h3 dir="auto">${esc(e.title)}</h3>
        ${where ? `<span class="ev-where">${where}</span>` : ""}
        <span class="ev-foot">
          ${price ? `<span class="tag ${e.price_type === "free" ? "free" : ""}">${esc(price)}</span>` : ""}
          ${cat ? `<span class="dim">${esc(cat)}</span>` : ""}
          ${note ? `<span class="tag warn">${esc(note)}</span>` : ""}
          ${e.stale ? `<span class="tag warn">${esc(t("stale_item"))}</span>` : ""}
        </span>
      </span></button>`;
    return el;
  }

  /* ---- sélection « top picks » : un classement SIMPLE et déterministe ---------------------
     Uniquement ce que les données disent vraiment : événement actif et à jour, localisé,
     avec lieu, description et lien officiel valides ; commence bientôt ; proche si on est
     géolocalisé ; prix connu. Pas de score affiché, pas de popularité. Si moins de 3
     événements sont assez complets (ou la liste est courte), la section n'apparaît pas :
     un classement sans matière serait un faux classement. */
  const PICK_MIN_LIST = 6, PICK_MIN_SCORE = 4;
  function pickScore(e, now) {
    if ((e.status && e.status !== "active") || e.stale || e.lat == null || e.lon == null) return -1;
    let s = 0;
    if (e.venue) s += 1;
    if (e.description && e.description.length >= 40) s += 1;
    if (!e.link_dead && (safeHref(e.url) || safeHref(e.booking_url))) s += 2;
    if (e.price_type && e.price_type !== "unknown") s += 1;
    const dh = (new Date(e.start) - now) / 36e5;
    if (dh < -1.5) return -1;                      // déjà bien entamé (expo, parcours) : pas un plan de soirée
    s += dh <= 3 ? 2 : 1;
    if (state.located && e.distance_km != null) s += e.distance_km < 1 ? 2 : e.distance_km < 2 ? 1 : 0;
    return s;
  }
  function topPicks(evs) {
    if (state.when !== "today" || evs.length < PICK_MIN_LIST) return [];
    const now = Date.now();
    const ranked = evs.map((e) => ({ e, s: pickScore(e, now) })).filter((x) => x.s >= PICK_MIN_SCORE)
      .sort((a, b) => b.s - a.s || new Date(a.e.start) - new Date(b.e.start) || a.e.id - b.e.id);
    return ranked.length >= 3 ? ranked.slice(0, 3).map((x) => x.e) : [];
  }

  const count = (n) => t("n_events", { n });
  function setCount(n) {
    lastCount = n;
    $("#count").textContent = n ? count(n) : "";
    const done = $("#fp-done");
    if (done) done.textContent = n ? t("show_n", { n }) : t("show_none");
  }
  function setHero() {
    $("#h1").textContent = state.view === "saved" ? t("saved_view") : (I.raw("in_city", state.when) || "{city}").replace("{city}", cityName);
  }

  function register(e, btn) { const r = byId.get(e.id); if (r) r.cards.push(btn); }
  function renderList(evs, { picks = true } = {}) {
    const frag = document.createDocumentFragment();
    const pk = picks ? topPicks(evs) : [];
    if (pk.length) {
      const sec = document.createElement("section");
      sec.className = "picks";
      sec.innerHTML = `<h3 class="sec-title">${esc(t("top_picks"))}</h3>`;
      const ul = document.createElement("ul");
      for (const e of pk) { const c = cardEl(e); register(e, c.firstElementChild); ul.appendChild(c); }
      sec.appendChild(ul);
      frag.appendChild(sec);
    }
    const all = document.createElement("section");
    if (pk.length) all.innerHTML = `<h3 class="sec-title">${esc(t("all_events"))}<span>${esc(count(evs.length))}</span></h3>`;
    const ul = document.createElement("ul");
    for (const e of evs) { const c = cardEl(e); register(e, c.firstElementChild); ul.appendChild(c); }
    all.appendChild(ul);
    frag.appendChild(all);
    list.innerHTML = "";
    list.appendChild(frag);
  }

  function render(data, fit) {
    cluster.clearLayers();
    byId.clear();
    selectedId = null;
    const evs = data.events;
    setCount(evs.length);
    dataNotice(data.data, evs.length);
    const bounds = [];
    for (const e of evs) {
      const m = L.marker([e.lat, e.lon], { icon: pinIcon(e), keyboard: false, riseOnHover: true });
      m.on("click", () => select(e.id, { open: true }));
      if (canHover) {
        m.bindTooltip(`${esc(fmtTime(e.start))} · ${esc(e.title)}`, { direction: "top", offset: [0, -10], opacity: 1 });
        m.on("mouseover", () => { byId.get(e.id)?.cards.forEach((c) => c.classList.add("hover")); });
        m.on("mouseout", () => { byId.get(e.id)?.cards.forEach((c) => c.classList.remove("hover")); });
      }
      cluster.addLayer(m);
      byId.set(e.id, { e, marker: m, cards: [] });
      bounds.push([e.lat, e.lon]);
    }
    if (!evs.length) { list.innerHTML = ""; list.appendChild(emptyState(data)); status.textContent = ""; return; }
    renderList(evs);
    status.innerHTML = state.view === "saved" ? esc(t("n_saved", { n: evs.length })) :
      esc(t("n_events_in", { n: evs.length, r: state.radius })).replace(String(evs.length), `<strong>${evs.length}</strong>`);
    // Ne recadrer que sur une vraie nouvelle recherche : jamais après un pan/zoom manuel.
    if (fit && bounds.length) {
      const pad = { paddingTopLeft: [30, 30], paddingBottomRight: [30, 30 + sheetCover()], maxZoom: 15 };
      quietly(() => map.fitBounds(state.located ? bounds.concat([[state.lat, state.lon]]) : bounds, pad));
    }
  }

  /* ---- états vides : honnêtes, et toujours une action ------------------------------------ */
  function emptyState(data) {
    const el = document.createElement("div");
    el.className = "empty";
    const st = data && data.data && data.data.state;
    const degraded = st && !["ok", "unknown"].includes(st);
    const btns = [];
    let title, hint = "";
    if (state.view === "saved") {
      title = t("saved_empty"); hint = t("saved_hint");
      btns.push([t("back_list"), () => { state.view = "events"; load(); }]);
    } else if (degraded) {
      // Les sources sont en retard : on ne prétend pas qu'il n'y a rien (la bannière le dit aussi).
      title = t("degraded_title");
      btns.push([t("retry"), () => load()]);
    } else {
      const when = I.raw("when_phrase", state.when) || "";
      const opts = CITY.radius_options_km, bigger = opts.find((k) => k > state.radius);
      const next = WHENS[WHENS.indexOf(state.when) + 1];
      if (state.free) { title = t("no_free", { when }); btns.push([t("show_all"), () => setFilters({ free: false })]); }
      else if (bigger) title = t("nothing_near");
      else title = t("none_when", { when });
      if (bigger) btns.push([bigger === CITY.max_radius_km ? t("expand_city") : t("expand_to", { r: bigger }), () => setFilters({ radius: bigger })]);
      if (next) btns.push([I.raw("see_next", next), () => setFilters({ when: next })]);
    }
    el.innerHTML = `<b>${esc(title)}</b>${hint ? `<p>${esc(hint)}</p>` : ""}`;
    btns.slice(0, 2).forEach(([label, fn], i) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn" + (i === 0 ? " primary" : ""); b.textContent = label;
      b.addEventListener("click", fn);
      el.appendChild(b);
    });
    return el;
  }

  /* ---- sélection carte <-> carte événement (13.26) -------------------------- */
  function select(id, { open = false, pan = true } = {}) {
    const prev = byId.get(selectedId);
    if (prev) {
      prev.marker.setIcon(pinIcon(prev.e)); prev.marker.setZIndexOffset(0);
      prev.cards.forEach((c) => { c.classList.remove("active"); c.setAttribute("aria-pressed", "false"); });
    }
    const cur = byId.get(id);
    if (!cur) return;
    selectedId = id;
    cur.marker.setIcon(pinIcon(cur.e, "sel")); cur.marker.setZIndexOffset(1000);
    cur.cards.forEach((c) => { c.classList.add("active"); c.setAttribute("aria-pressed", "true"); });
    if (open) openDetail(cur.e);
    else cur.cards[cur.cards.length - 1]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    if (pan) quietly(() => cluster.zoomToShowLayer(cur.marker, () => panToVisible(cur.marker.getLatLng())));
  }
  list.addEventListener("click", (ev) => {
    const b = ev.target.closest("button.ev"); if (!b) return;
    select(+b.dataset.id, { open: true });
  });
  if (canHover) {          // survol d'une carte → son point s'agrandit sur la carte
    list.addEventListener("mouseover", (ev) => {
      const b = ev.target.closest("button.ev"); const r = b && byId.get(+b.dataset.id);
      if (r && r.e.id !== selectedId) pinOf(r)?.classList.add("hover");
    });
    list.addEventListener("mouseout", (ev) => {
      const b = ev.target.closest("button.ev"); const r = b && byId.get(+b.dataset.id);
      if (r) pinOf(r)?.classList.remove("hover");
    });
  }

  /* ---- favoris (localStorage, jamais de compte) --------------------------------------------- */
  const SAVED_KEY = `em_saved_${CITY.id}`;
  const readSaved = () => { try { const a = JSON.parse(localStorage.getItem(SAVED_KEY) || "[]"); return Array.isArray(a) ? a.filter(Number.isInteger) : []; } catch { return []; } };
  const writeSaved = (a) => { try { localStorage.setItem(SAVED_KEY, JSON.stringify(a.slice(-60))); } catch { /* navigation privée : on ignore */ } };
  const isSaved = (id) => readSaved().includes(id);
  function toggleSaved(id) {
    const a = readSaved(), i = a.indexOf(id);
    if (i >= 0) a.splice(i, 1); else a.push(id);
    writeSaved(a); syncSaved();
    return i < 0;
  }
  function syncSaved() {
    const n = readSaved().length, b = $("#saved-btn");
    b.hidden = n === 0 && state.view !== "saved";
    $("#saved-n").textContent = String(n);
    b.setAttribute("aria-label", `${t("saved_view")} (${n})`);
    b.setAttribute("aria-pressed", String(state.view === "saved"));
  }
  const hearth = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 13.6S2.2 10 2.2 6a3.2 3.2 0 015.8-1.9A3.2 3.2 0 0113.8 6c0 4-5.8 7.6-5.8 7.6z" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>';

  async function showSaved() {
    if (state.view === "saved") { state.view = "events"; syncSaved(); setHero(); return load(); }
    state.view = "saved"; syncSaved(); syncControls(); setHero(); closeDetail(); say("");
    list.classList.add("busy"); status.textContent = t("loading");
    const ids = readSaved();
    const res = await Promise.all(ids.map((id) => fetch(api(`/api/events/${id}`, { lang: LANG })).then((r) => (r.ok ? r.json() : null)).catch(() => undefined)));
    if (state.view !== "saved") return;
    const now = Date.now(), evs = [], keep = [];
    res.forEach((e, i) => {
      if (e === undefined) { keep.push(ids[i]); return; }                    // réseau : on garde l'identifiant
      if (e === null) return;                                                  // disparu : on l'oublie
      const over = new Date(e.end || new Date(e.start).getTime() + 3 * 36e5) < now;
      if (!over) { keep.push(ids[i]); if (e.lat != null) evs.push(e); }
    });
    writeSaved(keep); syncSaved();
    evs.sort((a, b) => new Date(a.start) - new Date(b.start));
    list.classList.remove("busy");
    render({ events: evs, data: { state: "ok" } }, true);
  }
  $("#saved-btn").addEventListener("click", showSaved);

  /* ---- fiche détaillée : quoi, quand, où, combien — puis les actions -------------------------- */
  let lastFocus = null, listScroll = 0;
  function openDetail(e) {
    lastFocus = document.activeElement;
    listScroll = panel.scrollTop;
    const book = e.link_dead ? "" : safeHref(e.booking_url), off = e.link_dead ? "" : safeHref(e.url);
    const price = priceText(e);
    const home = PATHS.home.replace(/\/$/, "");
    const share = `${location.origin}${home}/e/${e.id}`;
    const note = statusNote(e);
    const saved = isSaved(e.id);
    detail.innerHTML = `
      <button type="button" class="back"><span class="i-dir" aria-hidden="true">←</span> ${esc(t("back"))}</button>
      <h2 dir="auto" tabindex="-1">${esc(e.title)}</h2>
      ${note ? `<p><span class="tag warn">${esc(note)}</span></p>` : ""}
      <p class="d-when">${esc(fmtDayLong(e.start))} · <strong>${esc(fmtTime(e.start))}</strong>${e.end ? `<small> – ${esc(fmtTime(e.end))}</small>` : ""}</p>
      ${e.venue || e.address ? `<p class="d-where" dir="auto">${esc(e.venue || e.address)}${e.venue && e.address ? `<small>${esc(e.address)}</small>` : ""}</p>` : ""}
      <p class="d-meta">${e.distance_km != null ? `<span>${esc(fmtDist(e.distance_km))}</span>` : ""}${price ? `<span class="tag ${e.price_type === "free" ? "free" : ""}">${esc(price)}</span>` : ""}<span>${esc(I.catLabel(e.category))}</span></p>
      ${e.description ? `<p class="desc" dir="auto">${esc(e.description)}</p>` : ""}
      <div class="actions">
        <a class="btn primary" target="_blank" rel="noopener" href="${esc(directionsUrl(e))}">${esc(t("directions"))}</a>
        ${book ? `<a class="btn" target="_blank" rel="noopener nofollow" href="${esc(book)}">${esc(t("book"))}</a>` : ""}
        ${off ? `<a class="btn" target="_blank" rel="noopener nofollow" href="${esc(off)}">${esc(t("official"))}</a>` : ""}
      </div>
      <div class="actions second">
        <button class="btn ghost" type="button" id="share">${esc(t("share"))}</button>
        <button class="btn ghost" type="button" id="save" aria-pressed="${saved}">${hearth}<span>${esc(saved ? t("saved") : t("save"))}</span></button>
      </div>
      <p class="prov">${esc(t("source"))}${LANG === "fr" ? " : " : ": "}${esc(sourceLabel(e.source))}${e.last_seen ? ` · ${esc(t("updated"))} ${esc(new Date(e.last_seen).toLocaleDateString(I.locale, { day: "numeric", month: "short", timeZone: CITY.timezone }))}` : ""}</p>`;
    list.hidden = true; status.hidden = true; detail.hidden = false;
    panel.scrollTop = 0;
    if (sheet.dataset.state === "peek") sheetState("half", false);
    detail.querySelector("h2").focus({ preventScroll: true });
    detail.querySelector(".back").addEventListener("click", closeDetail);
    detail.querySelector("#share").addEventListener("click", () => shareEvent(e, share));
    const sv = detail.querySelector("#save");
    sv.addEventListener("click", () => {
      const now = toggleSaved(e.id);
      sv.setAttribute("aria-pressed", String(now)); sv.querySelector("span").textContent = now ? t("saved") : t("save");
      toast(now ? t("saved") : t("unsave"));
    });
    history.replaceState(null, "", `${location.pathname}${location.search}#e${e.id}`);
  }
  async function shareEvent(e, url) {
    try {
      if (navigator.share) { await navigator.share({ title: e.title, url }); return; }
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(url);
      else { const ta = document.createElement("textarea"); ta.value = url; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select(); const ok = document.execCommand("copy"); ta.remove(); if (!ok) throw new Error("copy"); }
      toast(t("copied"));
    } catch (err) { if (err && err.name !== "AbortError") toast(t("copy_failed")); }
  }
  function closeDetail() {
    if (detail.hidden) return;
    detail.hidden = true; list.hidden = false; status.hidden = false;
    history.replaceState(null, "", location.pathname + location.search);
    panel.scrollTop = listScroll;                      // retour à l'endroit exact de la liste
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }

  /* ---- bottom sheet (mobile) ----------------------------------------------------- */
  const sheet = $("#sheet"), grab = $("#grab");
  const ORDER = ["peek", "half", "full"];
  function sheetState(s) {
    sheet.dataset.state = s;
    sheet.style.removeProperty("height");
    grab.setAttribute("aria-expanded", String(s !== "peek"));
    grab.setAttribute("aria-label", t(s === "full" ? "sheet_collapse" : "sheet_expand"));
    setTimeout(() => map.invalidateSize(), 220);
  }
  grab.addEventListener("click", () => sheetState(ORDER[(ORDER.indexOf(sheet.dataset.state) + 1) % ORDER.length]));
  grab.addEventListener("keydown", (ev) => {
    const i = ORDER.indexOf(sheet.dataset.state);
    if (ev.key === "ArrowUp" && i < 2) { ev.preventDefault(); sheetState(ORDER[i + 1]); }
    if (ev.key === "ArrowDown" && i > 0) { ev.preventDefault(); sheetState(ORDER[i - 1]); }
  });
  let drag = null;
  grab.addEventListener("pointerdown", (ev) => { drag = { y: ev.clientY, h: sheet.getBoundingClientRect().height, moved: false }; grab.setPointerCapture(ev.pointerId); sheet.classList.add("dragging"); });
  grab.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const dy = drag.y - ev.clientY;
    if (Math.abs(dy) > 4) drag.moved = true;
    const max = sheet.parentElement.getBoundingClientRect().height - 4;
    sheet.style.height = Math.max(64, Math.min(max, drag.h + dy)) + "px";
  });
  grab.addEventListener("pointerup", () => {
    if (!drag) return;
    sheet.classList.remove("dragging");
    if (drag.moved) {
      const h = sheet.getBoundingClientRect().height, max = sheet.parentElement.getBoundingClientRect().height;
      const targets = { peek: 76, half: max * 0.46, full: max };
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
    if (state.free !== DEFAULT_FREE) q.set("price", state.free ? "free" : "all");
    if (state.radius !== DEFAULT_RADIUS) q.set("r", state.radius);
    if (state.category) q.set("cat", state.category);
    const s = q.toString();
    history.replaceState(null, "", location.pathname + (s ? "?" + s : "") + location.hash);
  }

  async function load(fit = true) {
    if (ctrl) ctrl.abort();
    ctrl = new AbortController();
    const myCtrl = ctrl;
    state.view = "events"; syncSaved(); setHero(); syncControls(); showArea(false);
    closeDetail();
    status.textContent = t("loading");
    // Un rafraîchissement ne vide jamais l'écran : la dernière liste saine reste, estompée,
    // jusqu'à l'arrivée des nouvelles données. Squelette seulement au tout premier chargement.
    if (byId.size) list.classList.add("busy");
    else list.innerHTML = '<div class="skel"></div><div class="skel"></div><div class="skel"></div><div class="skel"></div>';
    syncUrl();
    const q = { lat: state.lat, lon: state.lon, radius: state.radius, when: state.when, lang: LANG };
    if (state.free) q.price = "free";
    if (state.category) q.category = state.category;
    try {
      const r = await fetch(api("/api/events", q), { signal: myCtrl.signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      list.classList.remove("busy");
      render(data, fit);
      drawMe();
    } catch (err) {
      if (err.name === "AbortError") return;
      list.classList.remove("busy");
      if (byId.size) { status.textContent = ""; say(t("error_network"), "warn", true); return; }   // on garde la dernière liste saine
      list.innerHTML = ""; status.textContent = ""; setCount(0);
      const el = document.createElement("div");
      el.className = "empty";
      el.innerHTML = `<b>${esc(t("unavailable_title"))}</b><p>${esc(t("error_network"))}</p>`;
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn primary"; b.textContent = t("retry");
      b.addEventListener("click", () => load());
      el.appendChild(b); list.appendChild(el);
    }
  }
  function setFilters(patch) { Object.assign(state, patch); syncControls(); load(); }

  /* ---- contrôles : barre rapide + panneau Filtres, toujours synchronisés ------------------- */
  const filtersN = () => (["tomorrow", "week"].includes(state.when) ? 1 : 0) + (state.category ? 1 : 0) + (state.radius !== DEFAULT_RADIUS ? 1 : 0);
  const isDefault = () => state.when === "today" && state.free === DEFAULT_FREE && !state.category && state.radius === DEFAULT_RADIUS;
  const seg = (sel, items, pressed, attr) => {
    const root = $(sel);
    root.innerHTML = items.map(([v, label]) => `<button type="button" class="chip" data-${attr}="${esc(v)}" aria-pressed="${pressed(v)}">${esc(label)}</button>`).join("");
  };
  function syncControls() {
    const saved = state.view === "saved";
    document.querySelectorAll("[data-when]").forEach((b) => b.setAttribute("aria-pressed", String(!saved && b.dataset.when === state.when)));
    $("#free").setAttribute("aria-pressed", String(state.free));
    document.querySelectorAll("#fp-price [data-price]").forEach((b) => b.setAttribute("aria-pressed", String((b.dataset.price === "free") === state.free)));
    document.querySelectorAll("#fp-radius [data-radius]").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.radius === state.radius)));
    document.querySelectorAll("#fp-cats [data-cat]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.cat === state.category)));
    const n = filtersN(), fn = $("#filters-n");
    fn.hidden = n === 0; fn.textContent = String(n);
    $("#open-filters").setAttribute("aria-label", n ? `${t("filters")} · ${t("filter_active", { n })}` : t("filters"));
    $("#fp-reset").disabled = isDefault();
  }

  $("#filters-label").textContent = t("filters");
  $("#free").textContent = t("free");
  $("#fp-title").textContent = t("filters");
  $("#fp-reset").textContent = t("reset");
  $("#fp-close").setAttribute("aria-label", t("close"));
  $("#fp-l-when").textContent = t("when_l");
  $("#fp-l-price").textContent = t("price");
  $("#fp-l-cat").textContent = t("categories");
  $("#fp-l-radius").textContent = t("distance");
  document.querySelectorAll("#quick [data-when]").forEach((b) => { b.textContent = b.dataset.when === "weekend" ? t("weekend_s") : t(b.dataset.when); });
  seg("#fp-when", WHENS.map((w) => [w, w === "weekend" ? t("weekend_s") : t(w)]), () => "false", "when");
  seg("#fp-price", [["all", t("all")], ["free", t("free")]], () => "false", "price");
  seg("#fp-radius", CITY.radius_options_km.map((k) => [k, k === CITY.max_radius_km ? t("all_city") : `${k} ${t("km")}`]), () => "false", "radius");
  seg("#fp-cats", [["", t("cat_all")]], () => "false", "cat");
  $("#sheet").setAttribute("aria-label", t("list"));
  $("#list-title").textContent = t("list");
  $("#map").setAttribute("aria-label", t("map"));
  $("#grab").setAttribute("aria-label", t("sheet_expand"));

  document.addEventListener("click", (ev) => {
    const w = ev.target.closest("[data-when]"); if (w) { setFilters({ when: w.dataset.when }); return; }
    const p = ev.target.closest("#fp-price [data-price]"); if (p) { setFilters({ free: p.dataset.price === "free" }); return; }
    const r = ev.target.closest("#fp-radius [data-radius]"); if (r) { setFilters({ radius: +r.dataset.radius }); return; }
    const c = ev.target.closest("#fp-cats [data-cat]"); if (c) { setFilters({ category: c.dataset.cat }); }
  });
  $("#free").addEventListener("click", () => setFilters({ free: !state.free }));
  $("#fp-reset").addEventListener("click", () => setFilters({ when: "today", free: DEFAULT_FREE, category: "", radius: DEFAULT_RADIUS }));

  /* Catégories : seulement celles qui ont de vrais événements (13.23). */
  fetch(api("/api/categories")).then((r) => r.json()).then((c) => {
    const groups = (c.groups && c.groups.length) ? c.groups.map((g) => [g.key, I.grpLabel(g.key)]) : (c.categories || []).map((k) => [k, I.catLabel(k)]);
    seg("#fp-cats", [["", t("cat_all")], ...groups], () => "false", "cat");
    syncControls();
  }).catch(() => {});

  /* ---- panneau Filtres : feuille du bas (mobile) ou popover (ordinateur) ---------------------- */
  const fpanel = $("#filters-panel"), scrim = $("#scrim"), openBtn = $("#open-filters");
  function openFilters() {
    const r = openBtn.getBoundingClientRect();
    fpanel.style.setProperty("--fp-top", `${Math.round(r.bottom + 8)}px`);
    fpanel.style.setProperty("--fp-left", `${Math.max(12, Math.round(r.left))}px`);
    fpanel.style.setProperty("--fp-right", `${Math.max(12, Math.round(innerWidth - r.right))}px`);
    fpanel.hidden = false; scrim.hidden = false;
    openBtn.setAttribute("aria-expanded", "true");
    (fpanel.querySelector('[aria-pressed="true"]') || fpanel.querySelector("button")).focus({ preventScroll: true });
  }
  function closeFilters(refocus = true) {
    if (fpanel.hidden) return;
    fpanel.hidden = true; scrim.hidden = true;
    openBtn.setAttribute("aria-expanded", "false");
    if (refocus) openBtn.focus({ preventScroll: true });
  }
  openBtn.addEventListener("click", () => (fpanel.hidden ? openFilters() : closeFilters()));
  $("#fp-close").addEventListener("click", () => closeFilters());
  $("#fp-done").addEventListener("click", () => closeFilters());
  scrim.addEventListener("click", () => closeFilters());
  fpanel.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { ev.stopPropagation(); closeFilters(); return; }
    if (ev.key !== "Tab" || isWide()) return;          // mobile = modal : le focus reste dans la feuille
    const f = [...fpanel.querySelectorAll("button:not([disabled])")];
    const first = f[0], last = f[f.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && fpanel.hidden && !detail.hidden) closeDetail(); });

  /* ---- géolocalisation : consentement explicite, jamais persistée, jamais bloquante ------ */
  const LocateCtl = L.Control.extend({
    options: { position: LANG === "ar" ? "topleft" : "topright" },
    onAdd() {
      const box = L.DomUtil.create("div", "leaflet-bar");
      box.innerHTML = `<button type="button" class="map-btn" id="geo" aria-pressed="false"><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><circle cx="10" cy="10" r="3.2" fill="currentColor"/><circle cx="10" cy="10" r="6.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 1.5v3M10 15.5v3M1.5 10h3M15.5 10h3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>`;
      L.DomEvent.disableClickPropagation(box);
      return box;
    },
  });
  new LocateCtl().addTo(map);
  L.control.zoom({ position: LANG === "ar" ? "bottomleft" : "bottomright" }).addTo(map);
  const geoBtn = $("#geo");
  geoBtn.setAttribute("aria-label", t("locate")); geoBtn.title = t("locate");
  geoBtn.addEventListener("click", () => {
    if (state.me) { recenterOnMe(); return; }
    if (!navigator.geolocation) { geoNote = t("geo_unavailable"); say(geoNote, "info"); return; }
    status.textContent = t("geo_locating");
    navigator.geolocation.getCurrentPosition(
      (p) => {
        const here = { lat: p.coords.latitude, lon: p.coords.longitude };
        geoNote = "";
        if (distKm(here, CITY.center) > CITY.max_radius_km * 2) {     // clairement hors de cette ville
          status.textContent = ""; suggestCity(here); return;
        }
        state.me = here; recenterOnMe();
      },
      (err) => { geoNote = err && err.code === 1 ? t("geo_denied") : t("geo_unavailable"); status.textContent = ""; say(geoNote, "info"); },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 60000 },
    );
  });
  function recenterOnMe() {
    const moved = !state.located || state.lat !== state.me.lat || state.lon !== state.me.lon;
    state.located = true; state.lat = state.me.lat; state.lon = state.me.lon;
    geoBtn.setAttribute("aria-pressed", "true");
    quietly(() => map.setView([state.lat, state.lon], Math.max(CITY.zoom, 13)));
    if (moved) load(false); else { showArea(false); drawMe(); }
  }
  function distKm(a, b) {
    const R = 6371, rad = (x) => x * Math.PI / 180, dLat = rad(b.lat - a.lat), dLon = rad(b.lon - a.lon);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  /* « Search this area » : déplacer la carte ne relance JAMAIS la recherche tout seul. Après
     un déplacement net (plus d'un tiers du rayon), un bouton propose de chercher ici. */
  const areaBtn = document.createElement("button");
  areaBtn.type = "button"; areaBtn.className = "search-area"; areaBtn.hidden = true; areaBtn.textContent = t("search_area");
  L.DomEvent.disableClickPropagation(areaBtn);
  map.getContainer().appendChild(areaBtn);
  const showArea = (on) => { areaBtn.hidden = !on; };
  areaBtn.addEventListener("click", () => {
    const c = map.getCenter();
    anchor = c;
    state.lat = c.lat; state.lon = c.lng; state.located = false;
    geoBtn.setAttribute("aria-pressed", "false");
    load(false);
  });
  map.on("moveend", () => {
    const c = map.getCenter();
    if (quiet > 0) { anchor = c; return; }          // geste du code : la référence suit la carte
    if (!detail.hidden || state.view === "saved") return;
    if (!anchor) { anchor = c; return; }
    showArea(distKm({ lat: c.lat, lon: c.lng }, { lat: anchor.lat, lon: anchor.lng }) > Math.max(0.35, state.radius * 0.35));
  });

  /* ---- villes : langue, suggestion ---------------------------------------- */
  const pathFor = (cityId, lang, def, suffix) => `${lang && lang !== def ? "/" + lang : ""}/${cityId}${suffix}`;
  let CITIES = [];
  function suggestCity(here) {
    const box = $("#suggest");
    const near = CITIES.filter((c) => c.id !== CITY.id && distKm(here, c.center) <= 60);
    if (near.length !== 1) { box.hidden = true; geoNote = t("geo_unavailable"); say(geoNote, "info"); return; }
    const c = near[0], name = c.names[LANG] || c.names.en || c.id;
    const lang = c.languages.includes(LANG) ? LANG : c.default_language;
    box.hidden = false;
    box.innerHTML = `${esc(t("suggest_city", { city: name }))} <a class="chip" href="${esc(pathFor(c.id, lang, c.default_language, "/carte"))}">${esc(t("yes_go", { city: name }))}</a> <button class="chip" type="button">${esc(t("dismiss"))}</button>`;
    box.querySelector("button").addEventListener("click", () => { box.hidden = true; });
  }
  /* Le sélecteur de ville est rendu par le serveur (render.city_switch_html) ; ici on ne garde
     que la liste des villes allumées, pour la suggestion « vous semblez être à… ». */
  fetch("/api/cities").then((r) => r.json()).then(({ cities }) => { CITIES = cities; }).catch(() => {});

  try { localStorage.setItem("em_city", CITY.id); localStorage.setItem("em_lang", LANG); } catch { /* ignoré */ }
  const langs = $("#lang-links");
  if ((CITY.languages || []).length > 1) {
    const names = { en: "EN", ar: "ع", fr: "FR" };
    langs.innerHTML = CITY.languages.map((l) =>
      `<a href="${esc(pathFor(CITY.id, l, CITY.default_language, "/carte"))}" hreflang="${esc(l)}" lang="${esc(l)}" ${l === LANG ? 'aria-current="true"' : ""}>${esc(names[l] || l)}</a>`).join("");
  } else langs.hidden = true;

  // Quand on arrive sur un lien d'événement partagé (#e123), l'ouvrir une fois la liste chargée.
  const hash = /^#e(\d+)$/.exec(location.hash);
  syncControls(); syncSaved(); setHero();
  load();
  if (hash) {
    const want = +hash[1];
    const iv = setInterval(() => { if (byId.has(want)) { clearInterval(iv); select(want, { open: true }); } }, 300);
    setTimeout(() => clearInterval(iv), 6000);
  }
  sheetState("half");
  addEventListener("resize", () => map.invalidateSize());

  /* Les mentions de sources (obligatoires) : sur mobile elles terminent la liste au lieu de
     voler 60 px de carte en permanence. */
  const foot = $("#foot"), narrow = matchMedia("(max-width: 899px)");
  const placeFoot = () => { if (narrow.matches) panel.appendChild(foot); else document.body.appendChild(foot); };
  narrow.addEventListener("change", placeFoot); placeFoot();
})();
