/* EventMap — helpers partagés par toutes les pages (accueil, carte, ville, fiche).
   Lit la configuration de la ville dans <script type="application/json" id="em-city">
   posé par le serveur : fuseau, centre de carte, devise, langue. */
window.EM_COMMON = (() => {
  let CFG = {};
  try { CFG = JSON.parse(document.getElementById("em-city")?.textContent || "{}"); } catch { /* page sans ville */ }
  const CITY = CFG.city || {
    id: "paris", timezone: "Europe/Paris", currency: "EUR", zoom: 13, weekend_days: [5, 6],
    center: { lat: 48.8584, lon: 2.3470 }, radius_options_km: [1, 2, 4, 8], default_radius_km: 2, max_radius_km: 8,
    default_price: "free", features: { cultures: true }, names: { fr: "Paris" }, category_groups: {},
  };
  const I = window.EM_I18N || { lang: "fr", locale: "fr-FR", t: (k) => k, catLabel: (k) => k, grpLabel: (k) => k };
  const PATHS = CFG.paths || { home: "/", map: "/carte" };

  const CATEGORY_LABELS = new Proxy({}, { get: (_, k) => (typeof k === "string" ? I.catLabel(k) : undefined) });
  const PRICE_LABEL = { free: I.t("free"), paid: I.t("paid"), free_conditional: I.t("free_cond"), unknown: "" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  /* Un lien venu d'une source n'est affiché que s'il est en http(s) : le serveur le
     garantit déjà (safety.safe_url), ceci est la deuxième ligne de défense. */
  const safeHref = (u) => { try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : ""; } catch { return ""; } };

  const tz = CITY.timezone;
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString(I.locale, { hour: "2-digit", minute: "2-digit", timeZone: tz });
  const fmtDay = (iso) => new Date(iso).toLocaleDateString(I.locale, { weekday: "short", day: "numeric", timeZone: tz });
  const fmtDayLong = (iso) => new Date(iso).toLocaleDateString(I.locale, { weekday: "long", day: "numeric", month: "long", timeZone: tz });
  const money = (v, cur) => { try { return new Intl.NumberFormat(I.locale, { style: "currency", currency: cur || CITY.currency, maximumFractionDigits: 0 }).format(v); } catch { return `${v} ${cur || ""}`; } };

  /* Prix affiché : uniquement ce que la source donne. */
  function priceText(e) {
    if (e.price_type === "free") return PRICE_LABEL.free;
    if (e.price_min != null && e.price_max != null && e.price_max !== e.price_min) return `${money(e.price_min, e.currency)} – ${money(e.price_max, e.currency)}`;
    if (e.price_min != null) return I.t("from_price", { p: money(e.price_min, e.currency) });
    return PRICE_LABEL[e.price_type] || "";
  }
  const directionsUrl = (e) => `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(e.lat + "," + e.lon)}`;
  const api = (path, params = {}) => {
    const q = new URLSearchParams({ city: CITY.id, ...params });
    return `${path}?${q}`;
  };

  return { CITY, PATHS, LANG: I.lang, I, CATEGORY_LABELS, PRICE_LABEL, esc, safeHref, fmtTime, fmtDay, fmtDayLong,
           priceText, directionsUrl, api };
})();
