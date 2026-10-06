/* EventMap — sélecteur de ville : le bouton « Coming soon » (aria-disabled, focusable) affiche /
   masque sa courte explication. Aucune navigation, aucune requête : la ville n'a ni page ni donnée. */
(() => {
  document.querySelectorAll(".cs-soon").forEach((btn) => {
    const msg = document.getElementById(btn.getAttribute("aria-controls"));
    if (!msg) return;
    const set = (open) => { msg.hidden = !open; btn.setAttribute("aria-expanded", String(open)); };
    btn.addEventListener("click", () => set(msg.hidden));
    btn.addEventListener("keydown", (e) => { if (e.key === "Escape") set(false); });
    document.addEventListener("click", (e) => { if (!msg.hidden && !btn.contains(e.target) && !msg.contains(e.target)) set(false); });
  });
})();
