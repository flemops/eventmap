/* EventMap — helpers partagés entre app.js (/carte) et accueil.js (/). */
window.EM_COMMON = (() => {
  const CATEGORY_LABELS = {
    music: "Musique", theatre: "Spectacle", cinema: "Cinéma", expo: "Expo", kids: "Enfants",
    workshop: "Atelier", talk: "Rencontre", sport: "Sport", market: "Marché / festival", other: "Autre",
  };
  const PRICE_LABEL = { free: "Gratuit", paid: "Payant", free_conditional: "Gratuit*", unknown: "" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" });
  const fmtDay = (iso) => new Date(iso).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", timeZone: "Europe/Paris" });
  return { CATEGORY_LABELS, PRICE_LABEL, esc, fmtTime, fmtDay };
})();
