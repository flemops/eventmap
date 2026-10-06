/* EventMap — sélecteur de ville : le <details> s'ouvre tout seul ; ici, seulement ce qui demande
   du JavaScript — fermer au clic extérieur / Échap (le focus revient au bouton) et le bouton
   « Coming soon » (aria-disabled, focusable) qui affiche / masque sa courte explication.
   Aucune navigation, aucune requête : la ville n'a ni page ni donnée. */
(() => {
  const root = document.querySelector(".cityswitch .cs");
  if (!root) return;
  const summary = root.querySelector("summary");
  const close = () => { root.open = false; };
  document.addEventListener("click", (e) => { if (root.open && !root.contains(e.target)) close(); });
  root.addEventListener("keydown", (e) => { if (e.key === "Escape" && root.open) { close(); summary.focus(); } });
  root.addEventListener("toggle", () => {
    if (root.open) return;
    root.querySelectorAll(".cs-msg").forEach((m) => { m.hidden = true; });
    root.querySelectorAll(".cs-soon").forEach((b) => b.setAttribute("aria-expanded", "false"));
  });
  root.querySelectorAll(".cs-soon").forEach((btn) => {
    const msg = document.getElementById(btn.getAttribute("aria-controls"));
    if (!msg) return;
    btn.addEventListener("click", () => {
      const open = msg.hidden;
      msg.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
    });
  });
})();
