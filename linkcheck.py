"""Vérification périodique des liens officiels / de réservation (13.47).

Seulement pour les villes qui l'activent (`features.linkcheck` dans cities.yaml) :
quelques dizaines d'événements par cycle, un par seconde et par domaine, jamais
plus de `BUDGET` requêtes. Un lien MORT (404/410, ou domaine disparu) n'est pas
supprimé de la base — la trace reste — mais il n'est plus proposé à l'utilisateur :
l'API renvoie `link_dead: true` et le front masque les boutons « Réserver » et
« Page officielle ». Une panne réseau ou un 5xx n'est PAS un lien mort : on ne
conclut rien, on réessaiera au cycle suivant.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import safety

log = logging.getLogger("eventmap.linkcheck")
BUDGET = 40
RECHECK_AFTER = timedelta(days=2)
DEAD_CODES = {404, 410}


async def check_url(client, url: str) -> str:
    """'ok' | 'dead' | 'unknown'."""
    try:
        resp = await client.get(url)
    except safety.UnsafeUrl:
        return "dead"                       # un lien vers le réseau interne n'est jamais proposé
    except Exception as exc:  # noqa: BLE001 — réseau, DNS, délai : on ne conclut rien
        name = type(exc).__name__
        return "dead" if name in ("ConnectError",) and "Name or service not known" in str(exc) else "unknown"
    if resp.status_code in DEAD_CODES:
        return "dead"
    if 200 <= resp.status_code < 400 or resp.status_code in (401, 403, 405, 429):
        return "ok"                         # 403/405 : le site refuse les robots, pas un lien mort
    return "unknown"


async def run(con, client, city_id: str, now: datetime | None = None, budget: int = BUDGET) -> dict:
    now = now or datetime.now(timezone.utc)
    stale = (now - RECHECK_AFTER).isoformat(timespec="seconds")
    rows = con.execute(
        """SELECT id, url, booking_url FROM events
           WHERE city_id = ? AND status = 'active' AND doublon_de IS NULL
             AND start >= datetime('now', '-1 day')
             AND (url IS NOT NULL OR booking_url IS NOT NULL)
             AND (link_checked IS NULL OR link_checked < ?)
           ORDER BY start LIMIT ?""", (city_id, stale, budget)).fetchall()
    out = {"checked": 0, "dead": 0}
    cache: dict[str, str] = {}
    for r in rows:
        verdicts = []
        for u in (r["url"], r["booking_url"]):
            if not u:
                continue
            if u not in cache:
                cache[u] = await check_url(client, u)
            verdicts.append(cache[u])
        status = "dead" if verdicts and all(v == "dead" for v in verdicts) else (
            "ok" if "ok" in verdicts else "unknown")
        con.execute("UPDATE events SET link_status = ?, link_checked = ? WHERE id = ?",
                    (status, now.isoformat(timespec="seconds"), r["id"]))
        out["checked"] += 1
        out["dead"] += status == "dead"
    return out
