/* EventMap — état des sources de données, visible par le visiteur.
 *
 * Quand une source externe ne répond plus (ex. le flux « Que faire à Paris »
 * injoignable depuis le serveur), /health passe en « degraded » mais le site
 * continue de servir les derniers événements valides. Sans ce bandeau, le
 * visiteur ne voit qu'une carte moins fournie et peut croire le site en panne.
 * On dit donc ce qui se passe, en distinguant la source en panne du site
 * lui-même — sans jamais afficher l'erreur brute (elle peut contenir une
 * adresse).
 *
 * Autonome (aucune dépendance à common.js, mis en cache sans version) : si
 * /health ne répond pas ou que tout va bien, rien ne s'affiche.
 */
(() => {
  const NOMS = [
    [/^qfap$/, "Que faire à Paris (Ville de Paris)"],
    [/openagenda/i, "FICEP (OpenAgenda)"],
  ];
  const nomDe = (source) => (NOMS.find(([re]) => re.test(source)) || [null, "une source de données"])[1];

  async function verifier() {
    let sante;
    try {
      const r = await fetch("/health", { cache: "no-store" });
      if (!r.ok) return;
      sante = await r.json();
    } catch {
      return;
    }
    // Multi-ville : une source en panne à Jeddah ne concerne pas la page de Paris.
    let ville = "paris";
    try { ville = JSON.parse(document.getElementById("em-city").textContent).city.id; } catch { /* page sans ville */ }
    const enPanne = ((sante.refresh && sante.refresh.last_results) || [])
      .filter((s) => s && s.ok === false && (!s.city || s.city === ville));
    if (!enPanne.length) return;

    // « refresh » : c'est le cycle de mise a jour lui-meme qui s'est
    // interrompu, pas une source (main.py, refresh()).
    const cycle = enPanne.some((s) => s.source === "refresh");
    const noms = [...new Set(enPanne.filter((s) => s.source !== "refresh").map((s) => nomDe(String(s.source || ""))))];
    const p = document.createElement("p");
    p.className = "etat-sources";
    p.setAttribute("role", "status");
    p.textContent = cycle || !noms.length
      ? "La mise à jour automatique des événements a échoué au dernier passage : les événements déjà collectés restent affichés. Le site, lui, fonctionne normalement."
      : (noms.length > 1 ? `Les sources ${noms.join(" et ")} ne répondent plus` : `La source ${noms[0]} ne répond plus`) +
        " pour l'instant : les événements déjà collectés restent affichés, les nouveaux arriveront à son retour. Le site, lui, fonctionne normalement.";
    // Styles en ligne (autorisés par la CSP) : common.css est servi sans
    // version, une ancienne copie en cache ne doit pas laisser le bandeau nu.
    Object.assign(p.style, {
      margin: "8px 0 0",
      padding: "8px 12px",
      borderRadius: "8px",
      border: "1px solid rgba(214, 160, 40, .55)",
      background: "rgba(214, 160, 40, .12)",
      fontSize: "13px",
      lineHeight: "1.45",
    });
    const header = document.querySelector("header");
    if (header) header.appendChild(p);
    else document.body.prepend(p);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", verifier);
  else verifier();
})();
