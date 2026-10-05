/* EventMap — choix de ville.
   - Première visite : les cartes de ville s'affichent (aucun login, aucune étape inutile).
   - Visites suivantes : on ouvre directement la dernière ville utilisée, sauf si on arrive
     ici volontairement (?choose=1, ou un clic sur « changer de ville »).
   - La préférence est un CONFORT (localStorage) : les villes ont chacune une URL stable
     (/paris, /jeddah), c'est elle qui fait foi, pas ce stockage.
   - La position n'est demandée que sur clic, jamais à l'ouverture, jamais bloquante. */
(() => {
  let cfg = {};
  try { cfg = JSON.parse(document.getElementById("em-city").textContent); } catch { /* ignoré */ }
  const cities = cfg.cities || [];
  const cards = [...document.querySelectorAll(".city-card")];

  let last = null;
  try { last = localStorage.getItem("em_city"); } catch { /* navigation privée */ }
  const wantsChoice = new URLSearchParams(location.search).has("choose");
  const known = cards.find((c) => c.dataset.city === last);
  if (known && !wantsChoice) {
    // Même langue que la dernière visite, si la ville la propose.
    let lang = null;
    try { lang = localStorage.getItem("em_lang"); } catch { /* ignoré */ }
    const c = cities.find((x) => x.id === last);
    const href = known.getAttribute("href");
    location.replace(c && lang && lang !== c.default_language && c.languages.includes(lang) ? `/${lang}${href}` : href);
    return;
  }

  cards.forEach((c) => c.addEventListener("click", () => {
    try { localStorage.setItem("em_city", c.dataset.city); } catch { /* ignoré */ }
  }));

  const km = (a, b) => {
    const R = 6371, r = (x) => x * Math.PI / 180, dLat = r(b.lat - a.lat), dLon = r(b.lon - a.lon);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(r(a.lat)) * Math.cos(r(b.lat)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  };
  const msg = document.getElementById("find-msg");
  document.getElementById("find").addEventListener("click", () => {
    msg.hidden = false;
    if (!navigator.geolocation) { msg.textContent = "Location isn't available — pick a city above."; return; }
    msg.textContent = "…";
    navigator.geolocation.getCurrentPosition((p) => {
      const here = { lat: p.coords.latitude, lon: p.coords.longitude };
      const near = cities.filter((c) => km(here, c.center) <= 60);
      cards.forEach((c) => c.classList.remove("match"));
      if (near.length !== 1) { msg.textContent = "You're not clearly in one of these cities — pick one above."; return; }
      const card = cards.find((c) => c.dataset.city === near[0].id);
      if (card) { card.classList.add("match"); card.focus(); msg.textContent = `You seem to be in ${near[0].names.en}.`; }
    }, () => { msg.textContent = "Location denied — pick a city above."; },
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 60000 });
  });
})();
