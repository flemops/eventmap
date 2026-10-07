"""EventMap — API FastAPI + boucle de rafraîchissement, moteur multi-ville.

Deux responsabilités, volontairement séparées :
- servir `/api/events` et le front (lecture seule sur SQLite) ;
- rafraîchir les sources dans une tâche de fond qui ne peut jamais faire
  tomber l'API, quelle que soit la source qui casse.

Une seule application sert toutes les villes (cities.yaml) : Paris, Jeddah, et
la suivante sans changer une ligne de ce fichier. Toute lecture porte sur UNE
ville (`city_id`), toute source appartient à UNE ville.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import cities
import cultures
import db
import ics
import linkcheck
import pipeline
import registry
import release
import render
import sources
import sources_jsonld
import sources_ods
import sources_paris
import timewin
import venues

log = logging.getLogger("eventmap")
logging.basicConfig(
    level=os.environ.get("EVENTMAP_LOG", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
REFRESH_INTERVAL = int(os.environ.get("EVENTMAP_REFRESH_SECONDS", str(6 * 3600)))
# Seuil d espace libre avant d ecrire (14.18) : sous ce seuil le cycle de refresh est suspendu, a 2x une alerte disk_low est levee.
MIN_FREE_MB = int(os.environ.get("EVENTMAP_MIN_FREE_MB", "200"))
TZ = ZoneInfo("Europe/Paris")          # conservé pour les tests historiques : Paris est la ville par défaut
SITE = "https://eventmap.hamdy-tabsissi.com"

# Compatibilité avec les appels sans ville (avant le multi-ville) : le centre de
# Paris. Les valeurs réelles viennent de cities.yaml.
DEFAULT_LAT, DEFAULT_LON = 48.8584, 2.3470
MAX_RADIUS_KM = 50.0                   # plafond GLOBAL ; chaque ville a le sien (cities.yaml)

# État partagé du refresh, exposé par /health. Pas de verrou : une seule
# tâche écrit, et les lectures concurrentes d'un dict sont sûres en CPython.
_refresh_state: dict = {"running": False, "last_run": None, "last_results": [], "runs": 0,
                        "dedup": {"forte": 0, "faible": 0, "traduction": 0}}
_refresh_lock = asyncio.Lock()


# ------------------------------------------------------------------ refresh

def _build_fetchers(con, now: datetime | None = None
                    ) -> tuple[dict[str, sources.Fetcher], dict[str, registry.SourceSpec]]:
    """Assemble les connecteurs à lancer ce cycle : (fetchers, spec par clé).

    Une source est lancée si elle est autorisée, que sa ville est allumée, qu'elle
    n'est pas coupée, qu'elle n'a pas été désactivée après trop d'échecs, ET
    qu'elle est « due » selon sa cadence propre (`refresh_hours`).
    """
    now = now or datetime.now(timezone.utc)
    specs = registry.load()
    for problem in registry.validate(specs):
        log.error("configuration des sources : %s", problem)

    fetchers: dict[str, sources.Fetcher] = {}
    by_key: dict[str, registry.SourceSpec] = {}
    health = {r["source"]: r for r in db.source_report(con)}

    for spec in specs:
        if spec.kind != "qfap" and spec.enabled and spec.authorization == "ok":
            db.upsert_feed(con, spec.url, spec.kind, name=spec.name, feed_id=spec.id,
                           city_id=spec.city_id)

    db_feeds = {f["url"]: f for f in db.list_feeds(con, enabled_only=True)}
    # Circuit breaker (14.7) : une source coupée temporairement est sondée dès que son délai est écoulé.
    db_feeds.update({f["url"]: f for f in db.feeds_to_probe(con, now)})

    for spec in specs:
        reason = spec.why_not()
        if reason:
            log.debug("source %s non lancée : %s", spec.id, reason)
            continue
        if spec.kind == "qfap":
            fetcher: sources.Fetcher = sources_paris.fetch
        else:
            feed = db_feeds.get(spec.url)
            if feed is None:
                continue                       # désactivée en base après des échecs répétés
            if spec.kind == "ics":
                fetcher = _ics_fetcher(spec.url, feed.get("etag"), feed.get("last_modified"),
                                       geo_bbox=spec.geo_bbox)
            elif spec.kind == "ods_openagenda":
                fetcher = (lambda c, _s=spec: sources_ods.fetch(c, _s.options, source=f"{_s.kind}:{_s.url}"))
            elif spec.kind == "jsonld_sitemap":
                fetcher = (lambda c, _s=spec: sources_jsonld.fetch(
                    c, _s.url, _s.options, source=f"{_s.kind}:{_s.url}"))
            elif spec.kind == "openagenda":
                fetcher = (lambda c, _uid=spec.url.rsplit("/", 1)[-1]: sources.fetch_openagenda(c, _uid))  # noqa: B008 — _uid figé à la création (liaison tardive voulue)
            else:
                continue       # `jsonld` et `llm` ne tournent que sur demande explicite
        # Cadence : ne pas réinterroger une source plus souvent que `refresh_hours`.
        # Tolérance d'un quart d'heure, sinon une source à 6 h est « pas due » à
        # 5 h 59 et ne tourne qu'un cycle sur deux.
        last_ok = (health.get(spec.key) or {}).get("last_ok")
        if last_ok:
            age_h = (now - datetime.fromisoformat(last_ok)).total_seconds() / 3600
            if age_h < spec.refresh_hours - 0.25:
                log.debug("source %s pas encore due (%.1f h < %.1f h)", spec.id, age_h, spec.refresh_hours)
                continue
        fetchers[spec.key] = fetcher
        by_key[spec.key] = spec
    return fetchers, by_key


def _ics_fetcher(url: str, etag: str | None, last_modified: str | None,
                 geo_bbox: dict | None = None) -> sources.Fetcher:
    async def fetch(client: sources.PoliteClient) -> list[db.Event]:
        events, meta = await sources.fetch_ics(client, url, source=f"ics:{url}",
                                               etag=etag, last_modified=last_modified)
        if geo_bbox:
            # Un flux peut déborder de son périmètre déclaré (ex. FICEP :
            # 149 VEVENT dont quelques-uns à Arles, Bruxelles, Lyon) — filtrer
            # sur la boîte englobante plutôt que de faire confiance au flux.
            before = len(events)
            events = [
                e for e in events
                if e.lat is not None and e.lon is not None
                and geo_bbox["lat_min"] <= e.lat <= geo_bbox["lat_max"]
                and geo_bbox["lon_min"] <= e.lon <= geo_bbox["lon_max"]
            ]
            if before != len(events):
                log.info("flux %s : %d évènement(s) hors zone géo écarté(s)", url, before - len(events))
        # On accroche les méta de cache à la liste pour que refresh() les
        # retrouve sans changer la signature commune des connecteurs.
        fetch.meta = meta  # type: ignore[attr-defined]
        return events
    fetch.meta = {}  # type: ignore[attr-defined]
    return fetch


def _publish_priorities(specs: list[registry.SourceSpec]) -> None:
    sources.set_priorities({("qfap" if s.kind == "qfap" else f"{s.kind}:{s.url}"): s.priority
                            for s in specs})


def _persist(results, by_key, fetchers, started):
    """Toute la phase d'écriture d'un cycle (normalisation, upsert, dédoublonnage, purge).

    Synchrone et gourmande en CPU (plusieurs secondes sur la VM, quota de 50 %) : elle
    tourne dans un FIL à part (`asyncio.to_thread`). Exécutée dans la boucle
    d'événements, elle gelait l'API — /health compris — pendant tout le cycle (constaté
    en production le 06/10/2026 : ~45 s sans réponse). La connexion SQLite est créée ET
    utilisée dans ce fil, comme SQLite l'exige."""
    with db.session() as con:
        all_events: list[db.Event] = []
        dropped_total: dict[str, int] = {}
        for r in results:
            spec = by_key.get(r.name)
            city_id = spec.city_id if spec else None
            is_feed = r.name != "qfap"
            if r.ok:
                events, dropped = pipeline.normalize(r.events, city_id or cities.DEFAULT_CITY)
                for k, n in dropped.items():
                    dropped_total[k] = dropped_total.get(k, 0) + n
                # 14.6 : un lot anormal (volume effondré, géocodage soudain cassé) n'est ni écrit ni purgé :
                # le dernier contenu sain reste servi. Après QUARANTINE_MAX_STREAK lots écartés de suite,
                # le suivant est accepté — c'est alors la nouvelle normale, pas un accident.
                prev = db.source_previous(con, r.name)
                why = pipeline.batch_anomaly(
                    (prev or {}).get("last_valid", 0), (prev or {}).get("geo_missing", 0), len(events),
                    sum(1 for e in events if e.lat is None)) if prev else None
                if why and (prev.get("quarantine_streak") or 0) < pipeline.QUARANTINE_MAX_STREAK:
                    streak = db.record_source_quarantine(con, r.name, why, started, city_id=city_id)
                    log.warning("source %s : lot mis en quarantaine (%d/%d) — %s", r.name, streak,
                                pipeline.QUARANTINE_MAX_STREAK, why)
                    continue
                all_events.extend(events)
                meta = getattr(fetchers[r.name], "meta", {}) if is_feed else {}
                if is_feed:
                    db.mark_feed(con, r.name, ok=True,
                                 etag=meta.get("etag"), last_modified=meta.get("last_modified"))
                db.record_source_health(con, r.name, len(r.events), started, city_id=city_id,
                                        valid=len(events),
                                        geo_missing=sum(1 for e in events if e.lat is None))
                # Un 304 ne renvoie rien : on ne purge surtout pas.
                # Une source qui reussit mais renvoie ZERO non plus :
                # `purge_stale` effacerait tout son contenu, et c'est
                # exactement ce que fait un parseur casse en silence.
                # On garde l'ancien contenu, quitte a le voir vieillir.
                if not meta.get("not_modified") and events:
                    for src in {e.source for e in events}:
                        db.purge_stale(con, src, started)
                elif not r.events and not meta.get("not_modified"):
                    log.warning("source %s : 0 evenement alors qu'elle "
                                "repond OK — purge annulee", r.name)
            else:
                db.record_source_failure(con, r.name, r.error or "", started, city_id=city_id)
                if is_feed:
                    disabled = db.mark_feed(con, r.name, ok=False, error=r.error)
                    if disabled:
                        log.warning("flux désactivé après échecs répétés: %s", r.name)

        # Plus de fusion pré-upsert (D9) : toutes les sources sont
        # écrites telles quelles, et dedup_inter_source() marque les
        # doublons APRÈS coup, de façon réversible (doublon_de).
        n = db.upsert_events(con, all_events)
        dedup_counts = sources.dedup_inter_source(con, now=started)
        purged = db.purge_past(con)
    with db.session() as con:          # hors de la transaction d'écriture : un checkpoint y serait refusé
        db.checkpoint(con)
    return len(all_events), n, dedup_counts, purged, dropped_total


async def refresh() -> list[sources.SourceResult]:
    """Un cycle complet : agrégation, normalisation, dédoublonnage, upsert, purge, marquage."""
    if _refresh_lock.locked():
        log.info("refresh déjà en cours, cycle ignoré")
        return []

    async with _refresh_lock:
        _refresh_state["running"] = True
        started = datetime.now(timezone.utc)
        t0 = time.monotonic()
        try:
            free = db.free_space_mb()
            if free is not None and free < MIN_FREE_MB:
                # Garde avant écriture (14.18) : un disque plein corrompt une écriture en cours ; on suspend le cycle,
                # on garde ce qui est servi, et /health le dit (refresh_failed + disk_low).
                log.error("refresh suspendu : %.0f Mo libres (< %d Mo)", free, MIN_FREE_MB)
                _refresh_state["last_results"] = [{"source": "refresh", "ok": False, "events": 0, "duration_s": 0.0,
                                                   "error": "cycle suspendu : disque presque plein"}]
                return []
            with db.session() as con:
                fetchers, by_key = _build_fetchers(con, started)
            _publish_priorities(registry.load())

            policies = {k: {"timeout_s": s.timeout_s, "retries": s.retries} for k, s in by_key.items()}
            intervals = {}
            for s in by_key.values():
                if s.url:
                    from urllib.parse import urlsplit
                    intervals[urlsplit(s.url).netloc] = s.min_interval_s
            results = await sources.aggregate(fetchers, policies=policies, intervals=intervals)

            n_events, n, dedup_counts, purged, dropped_total = await asyncio.to_thread(
                _persist, results, by_key, fetchers, started)

            links = {}
            for c in cities.all_active():
                if c.features.get("linkcheck"):
                    async with sources.PoliteClient(timeout=10.0) as lc:
                        with db.session() as con:
                            links[c.id] = await linkcheck.run(con, lc, c.id, started)
            if links:
                _refresh_state["links"] = links

            ok = sum(1 for r in results if r.ok)
            log.info("refresh terminé en %.1fs : %d/%d sources OK, %d créneaux, %d upserts, "
                     "%d doublons forts + %d faibles + %d traductions marqués, %d passés purgés, écartés=%s",
                     time.monotonic() - t0, ok, len(results), n_events, n,
                     dedup_counts["forte"], dedup_counts["faible"], dedup_counts["traduction"],
                     purged, dropped_total or "{}")
            _refresh_state["last_results"] = [
                {"source": r.name, "ok": r.ok, "events": len(r.events),
                 "duration_s": round(r.duration_s, 1), "error": r.error,
                 "city": (by_key[r.name].city_id if r.name in by_key else None)}
                for r in results
            ]
            _refresh_state["dedup"] = dedup_counts
            _refresh_state["dropped"] = dropped_total
            return results
        except Exception as exc:
            # Dernier filet : un bug dans refresh() lui-même ne doit pas tuer
            # la boucle. On logue la trace et on attend le prochain cycle.
            log.exception("refresh en échec")
            # Et /health doit le dire : sans cela il restait « ok » sur le
            # cycle précédent — vide après un redémarrage — alors que rien
            # n'avait été écrit (incident du 05/10/2026). Le type seul, jamais
            # le message (il peut contenir une adresse ou un chemin).
            _refresh_state["last_results"] = [
                {"source": "refresh", "ok": False, "events": 0,
                 "duration_s": round(time.monotonic() - t0, 1),
                 "error": f"cycle interrompu : {type(exc).__name__}"}
            ]
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
    _publish_priorities(registry.load())
    task = asyncio.create_task(refresh_loop(), name="refresh_loop")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="EventMap", version="0.2", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


def _window(when: str, now: datetime, city: cities.City | None = None) -> tuple[datetime, datetime]:
    """Fenêtre temporelle dans le fuseau de la ville, renvoyée en UTC pour la base.

    Sans ville : Paris, comme avant le multi-ville (les tests historiques et les
    appels directs ne changent pas).
    """
    try:
        if city is None:
            return timewin.window(when, now, TZ)
        return timewin.window(when, now, city.tz, weekend_days=city.weekend_days,
                              cutoff=city.night_cutoff_hour)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _city_or_404(city_id: str | None) -> cities.City:
    c = cities.active(city_id)
    if c is None:
        raise HTTPException(404, f"ville inconnue ou indisponible : {city_id!r}")
    return c


def _venues_pour_cultures(culture: str | None) -> tuple[list[str], list[str] | None]:
    """Résout le paramètre `culture` en (clés demandées, lieux à filtrer).

    Plusieurs clés séparées par des virgules sont acceptées : l'accueil affiche
    les 14 cultures et faisait donc 14 requêtes — ~1 s côté client (mesuré le
    05/09/2026) pour ~25 créneaux. Une clé unique reste le cas nominal et se
    comporte à l'identique.

    Fonction séparée de la route pour être testable : les paramètres de
    `api_events` ont des objets `Query()` pour valeurs par défaut, elle n'est
    donc pas appelable directement.

    Deux pièges, tous deux couverts par des tests :
    - paramètre absent (`None` ou vide) : renvoyer `None` et non `[]`, car une
      liste vide passée à `db.search` ne filtre rien du tout — on croirait
      filtrer et on rendrait la base entière ;
    - paramètre fourni mais sans aucune clé exploitable (`,`, `  ,  `) : c'est
      une saisie malformée, elle rendait 404 avant l'ajout du multi-clés et doit
      continuer, plutôt que de dégénérer silencieusement en absence de filtre.
    """
    if not culture:
        return [], None
    cles = [c.strip() for c in culture.split(",") if c.strip()]
    if not cles:
        raise HTTPException(404, f"culture inconnue ou sans lieu : {culture!r}")
    lieux: list[str] = []
    for cle in cles:
        v = cultures.venues_for(cle)
        if not v:
            raise HTTPException(404, f"culture inconnue ou sans lieu : {cle!r}")
        lieux.extend(v)
    return cles, list(dict.fromkeys(lieux))  # un lieu ne compte qu'une fois


def _ajoute_culture_cle(rows: list[dict], cles: list[str]) -> None:
    """Marque chaque événement de la culture du LIEU par lequel il est entré.

    Uniquement quand plusieurs cultures sont demandées : sans ce champ, le
    client ne peut pas regrouper les résultats d'un appel groupé. Le champ
    `culture` ne suffit pas — il vaut `None` dès qu'un mot-clé d'exclusion
    s'applique (événement multi-pays), alors que le filtre SQL, lui, porte sur
    le lieu et a bien ramené l'événement : regrouper sur `culture` perdrait
    ces événements en silence.
    """
    if len(cles) <= 1:
        return
    for r in rows:
        v = cultures.for_venue(r.get("venue"))
        r["culture_cle"] = v["cle"] if v else None


def _localize(row: dict, lang: str | None, city: cities.City) -> None:
    """Titre/description dans la langue demandée SI la source en fournit une
    version (jamais de traduction automatique). Sinon la version de base."""
    import json
    raw = row.pop("i18n", None)
    alts = json.loads(raw) if raw else {}
    row["languages"] = sorted({row.get("lang") or city.default_language, *alts})
    v = venues.by_id(city.id, row.get("venue_id"))
    if v and lang:
        row["venue"] = v.name(lang)          # le lieu s'affiche dans la langue de l'interface
    if lang and lang in alts and lang != row.get("lang"):
        alt = alts[lang]
        row["title_original"] = row["title"]
        row["title"] = alt.get("title") or row["title"]
        row["description"] = alt.get("description") or row.get("description")
        row["lang"] = lang


def _data_state(con, city: cities.City, now: datetime) -> dict:
    """L'état honnête des DONNÉES d'une ville — pour que « aucun événement » ne
    soit jamais affiché quand la vraie réponse est « la source est en panne » (13.51).

    unknown       : aucune source n'a encore été interrogée (démarrage) ;
    ok            : toutes les sources répondent et leurs données sont fraîches ;
    partial       : une partie des sources est en erreur ou en retard ;
    stale         : plus aucune donnée fraîche ;
    no_sources    : la ville n'a aucune source active.
    """
    specs = [s for s in registry.by_city(registry.load(), city.id) if s.runnable()]
    if not specs:
        return {"state": "no_sources", "sources": []}
    report = {r["source"]: r for r in db.source_report(con, city.id)}
    items = []
    for s in specs:
        r = report.get(s.key)
        if r is None:
            # Jamais interrogée (démarrage, source récemment allumée) : inconnu, pas « en retard ».
            items.append({"id": s.id, "ok": None, "data_age_h": None, "error": False})
            continue
        age = r.get("data_age_h")
        late = (age is not None and age > (s.stale_after_hours or city.stale_after_hours)) or r.get("error_streak", 0) >= 2
        items.append({"id": s.id, "ok": not late, "data_age_h": age,
                      "error": bool(r.get("last_error") and r.get("error_streak", 0) > 0)})
    known = [i for i in items if i["ok"] is not None]
    if not known:
        return {"state": "unknown", "sources": items}
    good = sum(1 for i in known if i["ok"])
    state = "ok" if good == len(known) else ("partial" if good else "stale")
    return {"state": state, "sources": items}


@app.get("/api/events")
def api_events(
    city: str = Query(cities.DEFAULT_CITY, pattern="^[a-z][a-z0-9-]{1,30}$",
                      description="identifiant de ville (voir /api/cities) ; Paris par défaut"),
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    radius: float = Query(2.0, gt=0, le=MAX_RADIUS_KM, description="km"),
    when: str = Query("today", pattern="^(now|today|tomorrow|weekend|week)$"),
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$",
                             description="jour précis AAAA-MM-JJ (calendrier de la ville) ; prime sur `when`"),
    price: str | None = Query(None, pattern="^(free|paid|free_conditional)$"),
    category: str | None = Query(None, max_length=30),
    culture: str | None = Query(None, max_length=200,
        description="cle(s) de culture separees par des virgules (voir /api/cultures) : "
                    "filtre sur les lieux mono-culturels"),
    lang: str | None = Query(None, pattern="^[a-z]{2}$"),
    limit: int = Query(100, ge=1, le=300),
):
    c = _city_or_404(city)
    if radius > c.max_radius_km:
        raise HTTPException(422, f"radius > {c.max_radius_km} km pour {c.id}")
    lat = c.center[0] if lat is None else lat
    lon = c.center[1] if lon is None else lon
    now = datetime.now(timezone.utc)
    if date:
        try:
            start, end = timewin.day_window(date, now, c.tz, cutoff=c.night_cutoff_hour)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        start, end = _window(when, now, c)

    cles, venues = _venues_pour_cultures(culture)
    cats = None
    if category:
        cats = list(c.category_groups.get(category, [category]))

    with db.session() as con:
        rows = db.search(con, city_id=c.id, lat=lat, lon=lon, radius_km=radius, start_from=start,
                         start_to=end, price_type=price, category=cats,
                         venues=venues, limit=limit)
        state = _data_state(con, c, now)
    stale_before = now - timedelta(hours=c.stale_after_hours)
    for r in rows:
        r["distance_km"] = round(r["distance_km"], 2)
        # La culture enrichit la reponse ; elle n'est jamais la porte d'entree.
        # La question posee reste « ce soir, a 2 km, oui ou non ». Titre et
        # description passés pour l'option C : un évènement multi-pays perd
        # l'attribut même si son lieu reste admis (cf. cultures.yaml).
        if c.features.get("cultures"):
            r["culture"] = cultures.for_venue(r.get("venue"), r.get("title"), r.get("description"))
        r["is_free"] = r["price_type"] == "free"
        if r.pop("link_status", None) == "dead":
            r["link_dead"] = True              # lien mort : le front n'affiche ni « Réserver » ni « Page officielle »
            r["url"] = r["booking_url"] = None
        r["stale"] = (r.get("last_seen") or "") < stale_before.isoformat(timespec="seconds")
        _localize(r, lang, c)

    if c.features.get("cultures"):
        _ajoute_culture_cle(rows, cles)

    return {
        "count": len(rows),
        "city": c.id,
        "window": {"from": start.isoformat(timespec="minutes"), "to": end.isoformat(timespec="minutes")},
        "center": {"lat": lat, "lon": lon, "radius_km": radius},
        "data": state,
        "events": rows,
    }


@app.get("/api/events/{event_id}")
def api_event(event_id: int, city: str = Query(cities.DEFAULT_CITY, pattern="^[a-z][a-z0-9-]{1,30}$"),
              lang: str | None = Query(None, pattern="^[a-z]{2}$")):
    """Un événement et sa provenance auditable (13.46). Un id d'une AUTRE ville
    rend 404 : on ne devine pas une ville à partir d'un identifiant."""
    c = _city_or_404(city)
    with db.session() as con:
        row = con.execute("SELECT * FROM events WHERE id = ? AND city_id = ?", (event_id, c.id)).fetchone()
        if row is None:
            raise HTTPException(404, "événement introuvable")
        r = dict(row)
        canon = None
        if r["doublon_de"]:
            canon = con.execute("SELECT id FROM events WHERE id = ?", (r["doublon_de"],)).fetchone()
    r["last_seen"] = r.pop("ingested_at")
    if r.pop("link_status", None) == "dead":
        r["link_dead"] = True
        r["url"] = r["booking_url"] = None
    r["provenance"] = {
        "source": r["source"], "source_id": r["source_id"], "url": r["url"],
        "first_seen": r["first_seen"], "last_seen": r["last_seen"],
        "last_changed": r["last_changed"], "source_updated_at": r["updated_at"],
        "duplicate_of": canon["id"] if canon else None, "reason": r["dedup_reason"],
    }
    _localize(r, lang, c)
    for k in ("content_hash", "doublon_de", "dedup_reason"):
        r.pop(k, None)
    return r


@app.post("/api/funnel", status_code=204)
def api_funnel_hit(city: str = Query(cities.DEFAULT_CITY, pattern="^[a-z][a-z0-9-]{1,30}$"),
                   step: str = Query(..., pattern="^[a-z_]{3,20}$")):
    """Compte une étape d'usage (15.51). Aucune donnée personnelle n'est lue ni stockée :
    ni IP, ni identifiant, ni en-tête. Étape ou ville inconnue : ignorée sans erreur."""
    c = cities.active(city)
    if c is not None and step in db.FUNNEL_STEPS:
        with db.session() as con:
            db.funnel_hit(con, datetime.now(c.tz).date().isoformat(), c.id, step)
    return Response(status_code=204)


@app.get("/api/funnel")
def api_funnel(days: int = Query(7, ge=1, le=90), city: str | None = Query(None, pattern="^[a-z][a-z0-9-]{1,30}$")):
    """Compteurs agrégés (jamais d'événement individuel) : répondent à « choisit-on une ville,
    ouvre-t-on une fiche, enregistre-t-on, partage-t-on ? »."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    with db.session() as con:
        return {"since": since, "city": city, "steps": db.funnel_stats(con, since, city)}


MAX_ICS_EVENTS = 20


@app.get("/api/calendar.ics")
def api_calendar(city: str = Query(cities.DEFAULT_CITY, pattern="^[a-z][a-z0-9-]{1,30}$"),
                 ids: str = Query(..., pattern=r"^\d{1,12}(,\d{1,12}){0,19}$",
                                  description="identifiants séparés par des virgules (20 au plus)"),
                 lang: str | None = Query(None, pattern="^[a-z]{2}$")):
    """Un ou plusieurs événements au format iCalendar (« Ajouter à mon agenda », 15.43).
    Aucune donnée personnelle : seuls les événements demandés, de la ville demandée."""
    c = _city_or_404(city)
    wanted = list(dict.fromkeys(int(x) for x in ids.split(",")))[:MAX_ICS_EVENTS]
    with db.session() as con:
        marks = ",".join("?" * len(wanted))
        rows = [dict(r) for r in con.execute(
            f"SELECT * FROM events WHERE city_id = ? AND doublon_de IS NULL AND id IN ({marks}) ORDER BY start",
            [c.id, *wanted])]
    if not rows:
        raise HTTPException(404, "aucun événement")
    for r in rows:
        _localize(r, lang, c)
    body = ics.calendar(rows, SITE, c.name(lang or c.default_language))
    name = f"eventmap-{c.id}-{wanted[0]}.ics" if len(rows) == 1 else f"eventmap-{c.id}-soiree.ics"
    return Response(content=body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/cities")
def api_cities():
    """Villes ALLUMÉES uniquement. Une ville éteinte n'existe pas pour le public."""
    return {"cities": [c.public() for c in cities.all_active()], "default": cities.DEFAULT_CITY}


@app.get("/api/cultures")
def api_cultures():
    """Cultures declarees, avec le nombre de lieux mono-culturels de chacune.

    Volontairement pauvre : ce n'est pas un menu de navigation. La culture est
    un attribut du lieu, affiche en reponse — pas un filtre d'entree.
    """
    return {"cultures": cultures.all_cultures()}


@app.get("/api/categories")
def api_categories(city: str | None = Query(None, pattern="^[a-z][a-z0-9-]{1,30}$")):
    if city is None:
        return {"categories": [c for c, _ in sources.CATEGORY_KEYWORDS] + ["other"]}
    # Avec une ville : seulement les catégories qui ont de VRAIS événements à venir
    # (13.23 — aucune catégorie vide ni décorative).
    c = _city_or_404(city)
    with db.session() as con:
        rows = con.execute(
            "SELECT category, COUNT(*) AS n FROM events WHERE city_id = ? AND status = 'active' "
            "AND doublon_de IS NULL AND lat IS NOT NULL AND start >= datetime('now', '-1 day') "
            "GROUP BY category", (c.id,)).fetchall()
    counts = {r["category"]: r["n"] for r in rows}
    groups = [{"key": k, "categories": cs, "count": sum(counts.get(x, 0) for x in cs)}
              for k, cs in c.category_groups.items()]
    return {"categories": [k for k, n in counts.items() if n], "counts": counts,
            "groups": [g for g in groups if g["count"]]}


_STARTED = datetime.now(timezone.utc)
_COMMIT = release.read_commit(BASE_DIR)      # commit déployé, exposé par /health (release.py)
STARTUP_GRACE = timedelta(minutes=20)       # le premier cycle part 5 s apres le demarrage et dure ~1-2 min


def compute_alerts(con, now: datetime) -> list[dict]:
    """Ce qui justifie d'etre reveille - par ville et par source (13.53).

    Sert `/health?strict=1` (HTTP 503 s'il y a quoi que ce soit) et le champ `alerts` de
    `/health`. Chaque alerte a un `kind` stable, utilisable comme mot-cle par un outil de
    surveillance :

      refresh_stalled   la boucle de rafraichissement ne tourne plus (plus de cycle depuis
                        plus de deux intervalles) ;
      refresh_failed    le dernier cycle s'est interrompu ;
      never_ingested    une source qui devrait tourner n'a jamais rien rapporte ;
      ingestion_errors  3 cycles d'erreurs de suite ;
      silent_source     elle repond mais ne rapporte plus rien (2 cycles vides de suite) ;
      stale_data        la derniere donnee recue est plus vieille que `stale_after_hours` ;
      geocoding         plus de 30 % des evenements valides sans coordonnees ;
      quarantine        des lots anormaux sont ecartes depuis 2 cycles (le dernier contenu sain reste servi) ;
      db_integrity      `PRAGMA quick_check` ne repond pas « ok ».
      disk_low          moins de 2 x EVENTMAP_MIN_FREE_MB libres sur le volume de la base (le cycle est suspendu sous 1 x).
      source_breaker    une source est coupee par le circuit breaker depuis plus d une coupure (indisponible durablement ; une premiere coupure se repare seule).

    Seules les villes ALLUMEES comptent : une ville eteinte, ou une source non autorisee,
    n'est pas une panne.
    """
    out: list[dict] = []
    uptime = now - _STARTED
    last = _refresh_state.get("last_run")
    if last:
        age = now - datetime.fromisoformat(last)
        if age > timedelta(seconds=2 * REFRESH_INTERVAL + 1800):
            out.append({"kind": "refresh_stalled", "city": None, "source": None,
                        "detail": f"dernier cycle il y a {age.total_seconds() / 3600:.1f} h"})
    elif uptime > STARTUP_GRACE:
        out.append({"kind": "refresh_stalled", "city": None, "source": None,
                    "detail": "aucun cycle depuis le demarrage"})
    if any(r.get("source") == "refresh" and not r.get("ok") for r in _refresh_state.get("last_results", [])):
        out.append({"kind": "refresh_failed", "city": None, "source": None, "detail": "cycle interrompu"})

    free = db.free_space_mb()
    if free is not None and free < MIN_FREE_MB * 2:
        out.append({"kind": "disk_low", "city": None, "source": None, "detail": f"{free:.0f} Mo libres"})

    verdict = db.integrity(con)
    if verdict != "ok":
        out.append({"kind": "db_integrity", "city": None, "source": None, "detail": verdict[:200]})

    specs = registry.load()
    for c in cities.all_active():
        report = {r["source"]: r for r in db.source_report(con, c.id)}
        for s in registry.by_city(specs, c.id):
            if not s.runnable():
                continue
            r = report.get(s.key)
            base = {"city": c.id, "source": s.id}
            if r is None:
                if uptime > STARTUP_GRACE:
                    out.append({**base, "kind": "never_ingested", "detail": "aucune donnee recue"})
                continue
            if (r.get("error_streak") or 0) >= 3:
                out.append({**base, "kind": "ingestion_errors", "detail": f'{r["error_streak"]} cycles en erreur'})
            if (r.get("empty_streak") or 0) >= 2:
                out.append({**base, "kind": "silent_source", "detail": f'{r["empty_streak"]} cycles vides'})
            age_h = r.get("data_age_h")
            seuil = s.stale_after_hours or c.stale_after_hours
            if age_h is not None and age_h > seuil:
                out.append({**base, "kind": "stale_data",
                            "detail": f"donnee vieille de {age_h} h (seuil {seuil} h)"})
            if (r.get("quarantine_streak") or 0) >= 2:
                out.append({**base, "kind": "quarantine",
                            "detail": f'{r["quarantine_streak"]} lots ecartes : {r.get("quarantine_reason")}'})
            valid = r.get("last_valid") or 0
            if valid >= 10 and (r.get("geo_missing") or 0) / valid > 0.3:
                out.append({**base, "kind": "geocoding", "detail": f'{r["geo_missing"]}/{valid} sans coordonnees'})
    # Source coupée par le circuit breaker plus d'une fois de suite = indisponible durablement (14.20) :
    # une première coupure (6 h) se répare seule et ne dérange personne.
    for f in db.breaker_open(con):
        if f["breaker_level"] >= 2:
            out.append({"kind": "source_breaker", "city": f["city_id"], "source": f["feed_id"],
                        "detail": f'coupée par le breaker (niveau {f["breaker_level"]}, reprise à partir de {f["breaker_until"]})'})
    return out


@app.get("/health")
def health(strict: bool = Query(False, description="503 s'il y a une alerte (surveillance externe)")):
    now = datetime.now(timezone.utc)
    with db.session() as con:
        s = db.stats(con)
        feeds = db.list_feeds(con, enabled_only=False)
        excluded = cultures.excluded_count(con)
        by_city = db.stats_by_city(con)
        reports = {c.id: db.source_report(con, c.id) for c in cities.load().values()}
        states = {c.id: _data_state(con, c, now) for c in cities.all_active()}
    with db.session() as con:
        silent = db.silent_sources(con)
        alerts = compute_alerts(con, now)
    degraded = any(not r["ok"] for r in _refresh_state["last_results"]) or bool(silent)

    city_body = {}
    for c in cities.load().values():
        specs = registry.by_city(registry.load(), c.id)
        city_body[c.id] = {
            "enabled": c.enabled,
            "events": by_city.get(c.id, {}),
            "data": states.get(c.id),
            "sources": reports.get(c.id, []),
            # Sources déclarées mais NON lancées, et pourquoi — pour qu'on ne les croie pas muettes.
            "not_running": [{"id": sp.id, "reason": sp.why_not()} for sp in specs if sp.why_not()],
            "anomalies": [
                {"source": r["source"], "reason": "vide" if r["empty_streak"] >= 2 else "erreurs"}
                for r in reports.get(c.id, []) if r["empty_streak"] >= 2 or r["error_streak"] >= 3
            ],
        }
        if c.enabled and states.get(c.id, {}).get("state") in ("stale",):
            degraded = True
    body = {
        "status": "degraded" if degraded else "ok",
        "release": {"commit": _COMMIT[:12] if _COMMIT else None, "python": platform.python_version()},
        "silent_sources": silent,
        "db": s,
        "feeds": {"total": len(feeds), "enabled": sum(f["enabled"] for f in feeds)},
        # Mesurable, sinon on ne sait jamais si l'option C (cultures.yaml)
        # ou le dédoublonnage inter-sources (sources.dedup_inter_source)
        # mordent trop — ou pas assez.
        "cultures": {"excluded_events": excluded},
        "dedup": _refresh_state["dedup"],
        "cities": city_body,
        "alerts": alerts,
        "refresh": _refresh_state,
    }
    # Par defaut TOUJOURS 200, meme degrade : la sonde de disponibilite (Uptime Kuma) ne doit
    # pas sonner pour un probleme de donnees alors que le service repond. La surveillance DES
    # DONNEES interroge `/health?strict=1`, qui repond 503 des qu'il y a une alerte.
    return JSONResponse(body, status_code=503 if (strict and alerts) else 200)


@app.post("/api/refresh")
async def api_refresh():
    """Déclenchement manuel. Non exposé par nginx : réservé au localhost."""
    results = await refresh()
    return {"sources": [{"name": r.name, "ok": r.ok, "events": len(r.events), "error": r.error} for r in results]}


# ------------------------------------------------------------------- pages

# GET *et* HEAD : plusieurs vérificateurs de liens et déplieurs d'URL envoient un
# HEAD avant le GET, et FastAPI répondait 405 (`allow: GET`) — une page joignable
# qui se déclare inaccessible à toute machine qui demande poliment d'abord.
PAGE_METHODS = ["GET", "HEAD"]


@app.api_route("/carte", methods=PAGE_METHODS)
def carte_historique():
    """URL d'avant le multi-ville : redirigée définitivement vers la carte de Paris."""
    return RedirectResponse(f"/{cities.DEFAULT_CITY}/carte", status_code=301)


@app.api_route("/", methods=PAGE_METHODS)
def index():
    return render.root_page(SITE)


@app.get("/sitemap.xml")
def sitemap():
    """Pages publiques des villes ALLUMÉES (une ville éteinte n'y figure pas).

    Le sitemap ne peut pas être annoncé par un `robots.txt` : Cloudflare sert son
    propre fichier « content signals » à l'edge et l'origine n'est jamais consultée
    (vérifié le 05/09/2026 : `curl 127.0.0.1:8000/robots.txt` → 404, l'edge → 200).
    Il faut donc le déclarer dans la Search Console, ou éditer le robots.txt géré
    depuis le tableau de bord Cloudflare.
    """
    return Response(content=render.sitemap_xml(SITE), media_type="application/xml")


# `/static` est monté AVANT la route attrape-tout des pages de ville : sinon
# `/{a}/{b}` avalerait `/static/...`.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.api_route("/{a}", methods=PAGE_METHODS)
@app.api_route("/{a}/{b}", methods=PAGE_METHODS)
@app.api_route("/{a}/{b}/{c}", methods=PAGE_METHODS)
@app.api_route("/{a}/{b}/{c}/{d}", methods=PAGE_METHODS)
def city_pages(a: str, b: str | None = None, c: str | None = None, d: str | None = None):
    """/paris, /paris/carte, /paris/e/12, /ar/jeddah, /ar/jeddah/carte, /ar/jeddah/e/12."""
    page = render.city_page([p for p in (a, b, c, d) if p is not None], SITE)
    if page is None:
        return render.not_found_page()
    return page
