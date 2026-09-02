"""EventMap FR — API FastAPI + boucle de rafraîchissement.

Deux responsabilités, volontairement séparées :
- servir `/api/events` et le front statique (lecture seule sur SQLite) ;
- rafraîchir les sources toutes les 6 h, dans une tâche de fond qui ne peut
  jamais faire tomber l'API, quelle que soit la source qui casse.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import cultures
import db
import sources
import sources_paris

log = logging.getLogger("eventmap")
logging.basicConfig(
    level=os.environ.get("EVENTMAP_LOG", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
FEEDS_FILE = Path(os.environ.get("EVENTMAP_FEEDS", BASE_DIR / "feeds.yaml"))
REFRESH_INTERVAL = int(os.environ.get("EVENTMAP_REFRESH_SECONDS", str(6 * 3600)))
TZ = ZoneInfo("Europe/Paris")

# Périmètre : Paris intra-muros. Centre par défaut = Châtelet, et le rayon
# maximal couvre tout Paris depuis le centre (~6 km jusqu'au périphérique)
# sans déborder inutilement sur la petite couronne.
DEFAULT_LAT, DEFAULT_LON = 48.8584, 2.3470
MAX_RADIUS_KM = 8.0

# État partagé du refresh, exposé par /health. Pas de verrou : une seule
# tâche écrit, et les lectures concurrentes d'un dict sont sûres en CPython.
_refresh_state: dict = {"running": False, "last_run": None, "last_results": [], "runs": 0}
_refresh_lock = asyncio.Lock()


# ------------------------------------------------------------------ refresh

def _build_fetchers(con) -> dict[str, sources.Fetcher]:
    """Assemble les connecteurs actifs : QFAP toujours, puis feeds.yaml."""
    fetchers: dict[str, sources.Fetcher] = {"qfap": sources_paris.fetch}

    if FEEDS_FILE.exists():
        spec = yaml.safe_load(FEEDS_FILE.read_text(encoding="utf-8")) or {}
        for feed in spec.get("feeds", []):
            if not feed.get("enabled", True):
                continue
            db.upsert_feed(con, feed["url"], feed.get("type", "ics"),
                           name=feed.get("name"), city=feed.get("city"))

    for feed in db.list_feeds(con, enabled_only=True):
        url, kind = feed["url"], feed["kind"]
        if kind == "ics":
            fetchers[url] = _ics_fetcher(url, feed.get("etag"), feed.get("last_modified"))
        elif kind == "openagenda":
            uid = url.rsplit("/", 1)[-1]
            fetchers[url] = lambda c, _uid=uid: sources.fetch_openagenda(c, _uid)
        # `jsonld` et `llm` sont gérés par discover/scrape, pas par la boucle
        # de refresh : ils ne tournent que sur demande explicite.
    return fetchers


def _ics_fetcher(url: str, etag: str | None, last_modified: str | None) -> sources.Fetcher:
    async def fetch(client: sources.PoliteClient) -> list[db.Event]:
        events, meta = await sources.fetch_ics(client, url, source=f"ics:{url}",
                                               etag=etag, last_modified=last_modified)
        # On accroche les méta de cache à la liste pour que refresh() les
        # retrouve sans changer la signature commune des connecteurs.
        fetch.meta = meta  # type: ignore[attr-defined]
        return events
    fetch.meta = {}  # type: ignore[attr-defined]
    return fetch


async def refresh() -> list[sources.SourceResult]:
    """Un cycle complet : agrégation, dédoublonnage, upsert, purge, marquage."""
    if _refresh_lock.locked():
        log.info("refresh déjà en cours, cycle ignoré")
        return []

    async with _refresh_lock:
        _refresh_state["running"] = True
        started = datetime.now(timezone.utc)
        t0 = time.monotonic()
        try:
            with db.session() as con:
                fetchers = _build_fetchers(con)

            results = await sources.aggregate(fetchers)

            with db.session() as con:
                all_events: list[db.Event] = []
                for r in results:
                    is_feed = r.name != "qfap"
                    if r.ok:
                        all_events.extend(r.events)
                        meta = getattr(fetchers[r.name], "meta", {}) if is_feed else {}
                        if is_feed:
                            db.mark_feed(con, r.name, ok=True,
                                         etag=meta.get("etag"), last_modified=meta.get("last_modified"))
                        # Un 304 ne renvoie rien : on ne purge surtout pas.
                        if not meta.get("not_modified"):
                            src = r.name if r.name == "qfap" else f"ics:{r.name}"
                            db.purge_stale(con, src, started)
                    elif is_feed:
                        disabled = db.mark_feed(con, r.name, ok=False, error=r.error)
                        if disabled:
                            log.warning("flux désactivé après échecs répétés: %s", r.name)

                deduped = sources.dedupe(all_events)
                n = db.upsert_events(con, deduped)
                purged = db.purge_past(con)

            ok = sum(1 for r in results if r.ok)
            log.info("refresh terminé en %.1fs : %d/%d sources OK, %d créneaux, %d upserts, %d passés purgés",
                     time.monotonic() - t0, ok, len(results), len(all_events), n, purged)
            _refresh_state["last_results"] = [
                {"source": r.name, "ok": r.ok, "events": len(r.events),
                 "duration_s": round(r.duration_s, 1), "error": r.error}
                for r in results
            ]
            return results
        except Exception:
            # Dernier filet : un bug dans refresh() lui-même ne doit pas tuer
            # la boucle. On logue la trace et on attend le prochain cycle.
            log.exception("refresh en échec")
            return []
        finally:
            _refresh_state["running"] = False
            _refresh_state["last_run"] = started.isoformat(timespec="seconds")
            _refresh_state["runs"] += 1


async def refresh_loop() -> None:
    # Premier refresh décalé : l'API répond tout de suite avec l'ancienne base
    # au lieu de bloquer le démarrage pendant 45 s de rapatriement.
    await asyncio.sleep(5)
    while True:
        await refresh()
        await asyncio.sleep(REFRESH_INTERVAL)


# ---------------------------------------------------------------------- app

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect().close()  # crée le schéma au démarrage, pas à la première requête
    task = asyncio.create_task(refresh_loop(), name="refresh_loop")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="EventMap FR", version="0.1", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


def _window(when: str, now: datetime) -> tuple[datetime, datetime]:
    """Fenêtre temporelle en heure de Paris, renvoyée en UTC pour la base."""
    local = now.astimezone(TZ)
    day0 = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if when == "today":
        # « ce soir » commence maintenant, pas à minuit : inutile de proposer
        # un événement déjà commencé il y a trois heures.
        start, end = local, day0 + timedelta(days=1)
    elif when == "tomorrow":
        start, end = day0 + timedelta(days=1), day0 + timedelta(days=2)
    elif when == "weekend":
        # Le week-end se termine lundi 00:00. S'il a déjà commencé, on part
        # de maintenant ; sinon du samedi à venir.
        monday = day0 + timedelta(days=(7 - local.weekday()))
        saturday = monday - timedelta(days=2)
        start, end = (local if local.weekday() >= 5 else saturday), monday
    elif when == "week":
        start, end = local, day0 + timedelta(days=7)
    else:
        raise HTTPException(400, f"when invalide: {when!r} (today|tomorrow|weekend|week)")
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


@app.get("/api/events")
def api_events(
    lat: float = Query(DEFAULT_LAT, ge=-90, le=90),
    lon: float = Query(DEFAULT_LON, ge=-180, le=180),
    radius: float = Query(2.0, gt=0, le=MAX_RADIUS_KM, description="km"),
    when: str = Query("today", pattern="^(today|tomorrow|weekend|week)$"),
    price: str | None = Query(None, pattern="^(free|paid|free_conditional)$"),
    category: str | None = Query(None, max_length=20),
    culture: str | None = Query(None, max_length=40,
        description="cle de culture (voir /api/cultures) : filtre sur les lieux mono-culturels"),
    limit: int = Query(100, ge=1, le=300),
):
    start, end = _window(when, datetime.now(timezone.utc))

    venues = None
    if culture:
        venues = cultures.venues_for(culture)
        if not venues:
            raise HTTPException(404, f"culture inconnue ou sans lieu : {culture!r}")

    with db.session() as con:
        rows = db.search(con, lat=lat, lon=lon, radius_km=radius, start_from=start,
                         start_to=end, price_type=price, category=category,
                         venues=venues, limit=limit)
    for r in rows:
        r["distance_km"] = round(r["distance_km"], 2)
        # La culture enrichit la reponse ; elle n'est jamais la porte d'entree.
        # La question posee reste « ce soir, a 2 km, oui ou non ».
        r["culture"] = cultures.for_venue(r.get("venue"))
    return {
        "count": len(rows),
        "window": {"from": start.isoformat(timespec="minutes"), "to": end.isoformat(timespec="minutes")},
        "center": {"lat": lat, "lon": lon, "radius_km": radius},
        "events": rows,
    }


@app.get("/api/cultures")
def api_cultures():
    """Cultures declarees, avec le nombre de lieux mono-culturels de chacune.

    Volontairement pauvre : ce n'est pas un menu de navigation. La culture est
    un attribut du lieu, affiche en reponse — pas un filtre d'entree.
    """
    return {"cultures": cultures.all_cultures()}


@app.get("/api/categories")
def api_categories():
    return {"categories": [c for c, _ in sources.CATEGORY_KEYWORDS] + ["other"]}


@app.get("/health")
def health():
    with db.session() as con:
        s = db.stats(con)
        feeds = db.list_feeds(con, enabled_only=False)
    degraded = any(not r["ok"] for r in _refresh_state["last_results"])
    body = {
        "status": "degraded" if degraded else "ok",
        "db": s,
        "feeds": {"total": len(feeds), "enabled": sum(f["enabled"] for f in feeds)},
        "refresh": _refresh_state,
    }
    return JSONResponse(body, status_code=200)


@app.post("/api/refresh")
async def api_refresh():
    """Déclenchement manuel. Non exposé par nginx : réservé au localhost."""
    results = await refresh()
    return {"sources": [{"name": r.name, "ok": r.ok, "events": len(r.events), "error": r.error} for r in results]}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
