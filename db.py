"""Couche SQLite : schéma, upsert idempotent, recherche géo, suivi des flux.

Décisions structurantes (voir docs/decisions.md) :
- une ligne par créneau, jamais de tableau de dates dans une colonne ;
- `UNIQUE(source, source_id, start)` pour que le refresh soit rejouable ;
- la distance est calculée en SQL (haversine) pour trier côté base et ne
  rapatrier que le rayon demandé.
"""

from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

DB_PATH = os.environ.get("EVENTMAP_DB", "data/eventmap.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    start       TEXT NOT NULL,            -- ISO 8601 UTC
    end         TEXT,
    title       TEXT NOT NULL,
    description TEXT,
    venue       TEXT,
    address     TEXT,
    city        TEXT,
    lat         REAL,
    lon         REAL,
    price_type  TEXT NOT NULL DEFAULT 'unknown',
    category    TEXT NOT NULL DEFAULT 'other',
    url         TEXT,
    updated_at  TEXT,
    ingested_at TEXT NOT NULL,
    doublon_de  INTEGER REFERENCES events(id),
    -- Multi-ville (13.37) : jamais de requête sans city_id.
    city_id      TEXT NOT NULL DEFAULT 'paris',
    country_code TEXT,
    currency     TEXT,
    price_min    REAL,
    price_max    REAL,
    booking_url  TEXT,
    lang         TEXT,                      -- langue du titre/description de base
    i18n         TEXT,                      -- JSON {"ar": {"title":..,"description":..}, ...}
    status       TEXT NOT NULL DEFAULT 'active',  -- active|cancelled|postponed|expired|stale
    geo_source   TEXT,                      -- source|venue_ref|geocoder
    venue_id     TEXT,                      -- lieu canonique (venues/<ville>.yaml)
    first_seen   TEXT,                      -- provenance (13.46)
    last_changed TEXT,
    content_hash TEXT,
    dedup_reason TEXT,
    link_status  TEXT,                      -- ok|dead|unknown (linkcheck.py)
    link_checked TEXT,
    UNIQUE (source, source_id, start)
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events (start);
CREATE INDEX IF NOT EXISTS idx_events_geo   ON events (lat, lon);

-- Entonnoir d'usage SANS donnée personnelle (15.51) : un compteur par jour, ville et étape.
-- Ni identifiant, ni adresse IP, ni cookie, ni chemin : impossible de relier deux actions.
CREATE TABLE IF NOT EXISTS funnel (
    day     TEXT NOT NULL,
    city_id TEXT NOT NULL,
    step    TEXT NOT NULL,
    n       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, city_id, step)
);

CREATE TABLE IF NOT EXISTS source_health (
    source        TEXT PRIMARY KEY,
    last_count    INTEGER NOT NULL DEFAULT 0,
    last_nonempty TEXT,                     -- dernier cycle ayant rapporte >=1
    empty_streak  INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL,
    city_id       TEXT,
    last_ok       TEXT,                     -- dernier cycle SANS erreur (même vide)
    last_error    TEXT,
    error_streak  INTEGER NOT NULL DEFAULT 0,
    last_valid    INTEGER NOT NULL DEFAULT 0,   -- événements valides au dernier cycle
    geo_missing   INTEGER NOT NULL DEFAULT 0,   -- sans coordonnées au dernier cycle
    quarantine_streak INTEGER NOT NULL DEFAULT 0, -- lots anormaux écartés de suite (14.6)
    quarantine_reason TEXT
);

CREATE TABLE IF NOT EXISTS feeds (
    url           TEXT PRIMARY KEY,
    name          TEXT,
    city          TEXT,
    kind          TEXT NOT NULL,          -- qfap | ics | jsonld | openagenda | llm
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_ok       TEXT,                   -- dernier refresh réussi
    last_error    TEXT,
    error_count   INTEGER NOT NULL DEFAULT 0,
    etag          TEXT,
    last_modified TEXT,
    feed_id       TEXT,
    city_id       TEXT NOT NULL DEFAULT 'paris'
);
"""

# Au-delà de ce nombre d'échecs consécutifs, un flux est désactivé : on ne
# martèle pas un domaine mort à chaque refresh, et on ne fait pas bannir l'IP.
FEED_MAX_ERRORS = int(os.environ.get("EVENTMAP_FEED_MAX_ERRORS", "5"))


@dataclass
class Event:
    source: str
    source_id: str
    start: datetime
    title: str
    end: datetime | None = None
    description: str | None = None
    venue: str | None = None
    address: str | None = None
    city: str | None = None
    lat: float | None = None
    lon: float | None = None
    price_type: str = "unknown"
    category: str = "other"
    url: str | None = None
    updated_at: datetime | None = None
    # --- schéma normalisé multi-ville (13.4) --------------------------------
    city_id: str = "paris"
    country_code: str | None = None
    currency: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    booking_url: str | None = None
    lang: str | None = None
    i18n: dict | None = None        # {"ar": {"title": .., "description": ..}}
    status: str = "active"
    geo_source: str | None = None
    venue_id: str | None = None

    def __post_init__(self) -> None:
        self.start = _to_utc(self.start)
        self.end = _to_utc(self.end) if self.end else None
        self.updated_at = _to_utc(self.updated_at) if self.updated_at else None


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Les dates naïves sont interprétées comme UTC : un connecteur qui
        # lit de l'heure locale doit attacher le fuseau lui-même.
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.create_function("haversine_km", 4, _haversine_km, deterministic=True)
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Migrations additives sur une base déjà en prod — jamais de DROP/RENAME.

    `CREATE TABLE IF NOT EXISTS` ne touche pas une table existante : une
    colonne ajoutée au schéma doit être posée ici par introspection
    (`PRAGMA table_info`), pas seulement dans `SCHEMA`.
    """
    cols = {r["name"] for r in con.execute("PRAGMA table_info(events)")}
    if "doublon_de" not in cols:
        con.execute("ALTER TABLE events ADD COLUMN doublon_de INTEGER REFERENCES events(id)")

    # --- multi-ville (13.37) ---------------------------------------------
    # DEFAULT 'paris' sur la colonne : toutes les lignes déjà en base (QFAP +
    # FICEP, donc 100 % Paris) sont rattachées à Paris par SQLite lui-même, sans
    # réécriture ni fenêtre où une ligne n'aurait pas de ville.
    added = {
        "city_id": "TEXT NOT NULL DEFAULT 'paris'",
        "country_code": "TEXT", "currency": "TEXT", "price_min": "REAL", "price_max": "REAL",
        "booking_url": "TEXT", "lang": "TEXT", "i18n": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'active'", "geo_source": "TEXT", "venue_id": "TEXT",
        "first_seen": "TEXT", "last_changed": "TEXT", "content_hash": "TEXT", "dedup_reason": "TEXT",
        "link_status": "TEXT", "link_checked": "TEXT",
    }
    first_run = "first_seen" not in cols
    for name, decl in added.items():
        if name not in cols:
            con.execute(f"ALTER TABLE events ADD COLUMN {name} {decl}")
    if first_run:
        # Une seule fois : connect() tourne à chaque requête, on ne rebalaie
        # pas 16 000 lignes à chaque fois.
        con.execute("UPDATE events SET first_seen = ingested_at WHERE first_seen IS NULL")
        con.execute("UPDATE events SET country_code = 'FR' WHERE country_code IS NULL")

    fcols = {r["name"] for r in con.execute("PRAGMA table_info(feeds)")}
    for name, decl in {"feed_id": "TEXT", "city_id": "TEXT NOT NULL DEFAULT 'paris'"}.items():
        if name not in fcols:
            con.execute(f"ALTER TABLE feeds ADD COLUMN {name} {decl}")

    hcols = {r["name"] for r in con.execute("PRAGMA table_info(source_health)")}
    for name, decl in {"city_id": "TEXT", "last_ok": "TEXT", "last_error": "TEXT",
                       "error_streak": "INTEGER NOT NULL DEFAULT 0",
                       "last_valid": "INTEGER NOT NULL DEFAULT 0",
                       "geo_missing": "INTEGER NOT NULL DEFAULT 0",
                       "quarantine_streak": "INTEGER NOT NULL DEFAULT 0",
                       "quarantine_reason": "TEXT"}.items():
        if name not in hcols:
            con.execute(f"ALTER TABLE source_health ADD COLUMN {name} {decl}")

    # Index créés ICI et pas dans SCHEMA : sur une base existante, la colonne
    # n'existe qu'une fois les ALTER passés.
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_city_start ON events (city_id, start)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_city_status ON events (city_id, status)")
    # Sans ce commit, un `connect().close()` (c'est ce que fait le démarrage de
    # l'application) ANNULE le rattrapage `first_seen` ci-dessus — et comme la
    # colonne existe désormais, il ne serait jamais rejoué.
    con.commit()


@contextmanager
def session(path: str = DB_PATH) -> Iterator[sqlite3.Connection]:
    con = connect(path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --------------------------------------------------------------------- events

def _content_hash(e: Event) -> str:
    """Empreinte du contenu affichable : sert à dater la dernière modification
    RÉELLEMENT détectée (13.46), pas la dernière fois qu'on a revu la ligne."""
    import hashlib
    import json
    parts = [e.title, e.description, _iso(e.start), _iso(e.end), e.venue, e.address,
             e.lat, e.lon, e.price_type, e.price_min, e.price_max, e.currency,
             e.url, e.booking_url, e.status, json.dumps(e.i18n, sort_keys=True, ensure_ascii=False)]
    return hashlib.sha1("".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:16]


def upsert_events(con: sqlite3.Connection, events: list[Event]) -> int:
    """Insère ou met à jour. Retourne le nombre de lignes touchées."""
    import json
    now = _iso(datetime.now(timezone.utc))
    rows = [
        (
            e.source, e.source_id, _iso(e.start), _iso(e.end), e.title,
            e.description, e.venue, e.address, e.city, e.lat, e.lon,
            e.price_type, e.category, e.url, _iso(e.updated_at), now,
            e.city_id, e.country_code, e.currency, e.price_min, e.price_max,
            e.booking_url, e.lang,
            json.dumps(e.i18n, ensure_ascii=False) if e.i18n else None,
            e.status, e.geo_source, e.venue_id, now, now, _content_hash(e),
        )
        for e in events
    ]
    cur = con.executemany(
        """
        INSERT INTO events (source, source_id, start, end, title, description,
                            venue, address, city, lat, lon, price_type,
                            category, url, updated_at, ingested_at,
                            city_id, country_code, currency, price_min, price_max,
                            booking_url, lang, i18n, status, geo_source, venue_id,
                            first_seen, last_changed, content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, source_id, start) DO UPDATE SET
            end = excluded.end, title = excluded.title,
            description = excluded.description, venue = excluded.venue,
            address = excluded.address, city = excluded.city,
            lat = excluded.lat, lon = excluded.lon,
            price_type = excluded.price_type, category = excluded.category,
            url = excluded.url, updated_at = excluded.updated_at,
            ingested_at = excluded.ingested_at,
            city_id = excluded.city_id, country_code = excluded.country_code,
            currency = excluded.currency, price_min = excluded.price_min,
            price_max = excluded.price_max, booking_url = excluded.booking_url,
            lang = excluded.lang, i18n = excluded.i18n, status = excluded.status,
            geo_source = excluded.geo_source, venue_id = excluded.venue_id,
            last_changed = CASE WHEN events.content_hash IS excluded.content_hash
                                THEN events.last_changed ELSE excluded.last_changed END,
            content_hash = excluded.content_hash
        """,
        rows,
    )
    return cur.rowcount


def purge_stale(con: sqlite3.Connection, source: str, before: datetime) -> int:
    """Supprime les lignes d'une source non revues depuis `before`.

    Appelé après un refresh *réussi* de cette source uniquement : si la source
    est tombée, on garde l'ancien contenu plutôt que de vider la base.
    """
    where, params = "source = ? AND ingested_at < ?", (source, _iso(_to_utc(before)))
    _detacher_doublons(con, where, params)
    cur = con.execute(f"DELETE FROM events WHERE {where}", params)
    return cur.rowcount


def purge_past(con: sqlite3.Connection, older_than_days: int = 1) -> int:
    where, params = "start < datetime('now', ?)", (f"-{older_than_days} days",)
    _detacher_doublons(con, where, params)
    cur = con.execute(f"DELETE FROM events WHERE {where}", params)
    return cur.rowcount


def _detacher_doublons(con: sqlite3.Connection, where: str, params: tuple) -> None:
    """Avant une purge : un doublon qui vise une ligne supprimee redevient
    visible (doublon_de = NULL). Sans cela, `foreign_keys = ON` fait echouer
    le DELETE et annule tout le cycle de refresh (incident du 05/10/2026).
    `dedup_inter_source` remarque les doublons au cycle suivant."""
    con.execute(
        f"UPDATE events SET doublon_de = NULL WHERE doublon_de IN (SELECT id FROM events WHERE {where})",
        params,
    )


def search(
    con: sqlite3.Connection,
    *,
    lat: float,
    lon: float,
    radius_km: float,
    start_from: datetime,
    start_to: datetime,
    city_id: str = "paris",
    price_type: str | None = None,
    category: str | list[str] | tuple[str, ...] | None = None,
    venues: list[str] | None = None,
    statuses: tuple[str, ...] = ("active",),
    limit: int = 200,
) -> list[dict]:
    """Événements d'UNE ville, dans le rayon et la fenêtre, triés par date puis distance.

    `city_id` est toujours appliqué (13.38) : une requête Paris ne peut pas
    renvoyer un événement de Jeddah, ni l'inverse, quel que soit le centre ou le
    rayon demandé. Les statuts non actifs (annulé, reporté, périmé) ne sortent
    jamais par défaut.
    """
    # Paramètres nommés : l'ordre des "?" n'a plus d'importance et la requête
    # reste lisible quand on ajoute un filtre.
    # Un événement est pertinent s'il *chevauche* la fenêtre, pas seulement
    # s'il y commence : une expo ouverte de 10h à 19h doit sortir pour
    # « ce soir » à 17h. Sans `end`, on suppose 3 h de durée.
    #
    # Piège SQLite : `datetime()` renvoie "YYYY-MM-DD HH:MM:SS" (espace, sans
    # fuseau) alors que nos colonnes sont en ISO "…T…+00:00". Comparer les
    # deux formats bruts donne un ordre lexicographique faux. On passe tout
    # par julianday() pour comparer des nombres, pas des chaînes.
    clauses = [
        "city_id = :city_id",
        "lat IS NOT NULL",
        # Doublon inter-sources réversible (voir sources.dedup_inter_source) :
        # jamais supprimé, seulement écarté à la lecture.
        "doublon_de IS NULL",
        "julianday(COALESCE(end, start)) + (CASE WHEN end IS NULL THEN 0.125 ELSE 0 END) > julianday(:start_from)",
        "julianday(start) < julianday(:start_to)",
        "haversine_km(lat, lon, :lat, :lon) <= :radius",
    ]
    params: dict = {
        "city_id": city_id,
        "lat": lat, "lon": lon, "radius": radius_km,
        "start_from": _iso(_to_utc(start_from)),
        "start_to": _iso(_to_utc(start_to)),
        "limit": limit,
    }
    if statuses:
        keys = [f"status_{i}" for i in range(len(statuses))]
        clauses.append("status IN (" + ", ".join(f":{k}" for k in keys) + ")")
        params.update(dict(zip(keys, statuses)))
    if price_type:
        clauses.append("price_type = :price_type")
        params["price_type"] = price_type
    if category:
        cats = [category] if isinstance(category, str) else list(category)
        keys = [f"cat_{i}" for i in range(len(cats))]
        clauses.append("category IN (" + ", ".join(f":{k}" for k in keys) + ")")
        params.update(dict(zip(keys, cats)))

    if venues:
        # Filtre par liste de lieux : c'est ainsi qu'on filtre par culture,
        # celle-ci étant un attribut du lieu et non de l'événement.
        # `lower()` obligatoire : la base contient « Institut suédois » et
        # « Institut Suédois » pour le même lieu.
        keys = [f"venue_{i}" for i in range(len(venues))]
        clauses.append("lower(venue) IN (" + ", ".join(f":{k}" for k in keys) + ")")
        params.update({k: v.lower() for k, v in zip(keys, venues)})

    rows = con.execute(
        f"""
        SELECT id, source, source_id, start, end, title, description, venue,
               address, city, lat, lon, price_type, category, url,
               city_id, currency, price_min, price_max, booking_url, lang, i18n,
               status, geo_source, venue_id, link_status, ingested_at AS last_seen, first_seen, last_changed,
               haversine_km(lat, lon, :lat, :lon) AS distance_km
        FROM events
        WHERE {' AND '.join(clauses)}
        ORDER BY MAX(julianday(start), julianday(:start_from)), distance_km
        LIMIT :limit
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


SOURCE_SILENT_DAYS = int(os.environ.get("EVENTMAP_SOURCE_SILENT_DAYS", "3"))


def record_source_health(con: sqlite3.Connection, source: str, count: int,
                         now: datetime, *, city_id: str | None = None,
                         valid: int | None = None, geo_missing: int = 0) -> None:
    """Trace ce qu'une source a rapporte, meme quand elle rapporte zero.

    C'est la seule trace du mode de panne le plus dangereux : un parseur HTML
    qui casse apres une refonte de site ne leve aucune erreur, il renvoie une
    liste vide. `last_ok` reste vert, le journal reste muet.

    Par ville et par source (13.53) : `last_ok` (dernier cycle sans erreur),
    `last_nonempty` (dernière donnée reçue), `last_valid` (événements valides),
    `geo_missing` (géocodage échoué), `error_streak` remis à zéro.
    """
    iso = _iso(_to_utc(now))
    con.execute(
        """
        INSERT INTO source_health (source, last_count, last_nonempty, empty_streak, updated_at,
                                   city_id, last_ok, last_error, error_streak, last_valid, geo_missing)
        VALUES (:s, :c, CASE WHEN :c > 0 THEN :t END, CASE WHEN :c > 0 THEN 0 ELSE 1 END, :t,
                :city, :t, NULL, 0, :valid, :geo)
        ON CONFLICT(source) DO UPDATE SET
            last_count    = :c,
            last_nonempty = CASE WHEN :c > 0 THEN :t ELSE last_nonempty END,
            empty_streak  = CASE WHEN :c > 0 THEN 0 ELSE empty_streak + 1 END,
            updated_at    = :t,
            city_id       = COALESCE(:city, city_id),
            last_ok       = :t,
            last_error    = NULL,
            error_streak  = 0,
            last_valid    = :valid,
            geo_missing   = :geo,
            quarantine_streak = 0,
            quarantine_reason = NULL
        """,
        {"s": source, "c": count, "t": iso, "city": city_id,
         "valid": count if valid is None else valid, "geo": geo_missing},
    )


def source_previous(con: sqlite3.Connection, source: str) -> dict | None:
    """Ce que la source avait rapporté au cycle précédent (pour juger le lot courant)."""
    r = con.execute("SELECT last_valid, geo_missing, quarantine_streak FROM source_health WHERE source = ?",
                    (source,)).fetchone()
    return dict(r) if r else None


def record_source_quarantine(con: sqlite3.Connection, source: str, reason: str, now: datetime,
                             *, city_id: str | None = None) -> int:
    """Un lot anormal a été ÉCARTÉ (ni écrit, ni purge) : on garde le dernier contenu sain et on le trace.
    `last_ok` avance (la source répond) ; `last_valid`/`geo_missing` ne bougent PAS : la référence reste
    le dernier lot sain. Renvoie la série en cours."""
    iso = _iso(_to_utc(now))
    con.execute(
        """
        INSERT INTO source_health (source, last_count, updated_at, city_id, last_ok, quarantine_streak, quarantine_reason)
        VALUES (:s, 0, :t, :city, :t, 1, :r)
        ON CONFLICT(source) DO UPDATE SET
            updated_at = :t, last_ok = :t, city_id = COALESCE(:city, city_id),
            quarantine_streak = quarantine_streak + 1, quarantine_reason = :r
        """, {"s": source, "t": iso, "city": city_id, "r": reason[:300]})
    return con.execute("SELECT quarantine_streak FROM source_health WHERE source = ?", (source,)).fetchone()[0]


def integrity(con: sqlite3.Connection) -> str:
    """« ok » ou le premier message de `PRAGMA quick_check` (14.8). Lecture seule, quelques ms."""
    try:
        return con.execute("PRAGMA quick_check(1)").fetchone()[0]
    except sqlite3.DatabaseError as exc:      # base illisible : c'est exactement ce qu'on veut signaler
        return f"illisible : {type(exc).__name__}"


def backup_to(con: sqlite3.Connection, dest: str) -> None:
    """Copie cohérente à chaud (API de sauvegarde SQLite, sûre sous WAL) — avant une migration destructive."""
    out = sqlite3.connect(dest)
    try:
        con.backup(out)
    finally:
        out.close()


def record_source_failure(con: sqlite3.Connection, source: str, error: str, now: datetime,
                          *, city_id: str | None = None) -> None:
    """Un cycle en erreur ne touche NI `last_ok` NI `last_nonempty` : l'âge de
    la dernière donnée reçue continue de grandir, c'est ce qu'on veut voir."""
    iso = _iso(_to_utc(now))
    con.execute(
        """
        INSERT INTO source_health (source, last_count, empty_streak, updated_at, city_id,
                                   last_error, error_streak)
        VALUES (:s, 0, 0, :t, :city, :e, 1)
        ON CONFLICT(source) DO UPDATE SET
            last_error   = :e,
            error_streak = error_streak + 1,
            city_id      = COALESCE(:city, city_id)
        """,
        {"s": source, "t": iso, "city": city_id, "e": (error or "unknown")[:300]},
    )


def source_report(con: sqlite3.Connection, city_id: str | None = None) -> list[dict]:
    """État de chaque source, avec l'âge (en heures) de sa dernière donnée."""
    q = """
        SELECT source, city_id, last_count, last_valid, geo_missing, empty_streak,
               quarantine_streak, quarantine_reason,
               error_streak, last_error, last_ok, last_nonempty,
               ROUND((julianday('now') - julianday(last_nonempty)) * 24, 1) AS data_age_h,
               ROUND((julianday('now') - julianday(last_ok)) * 24, 1)       AS ok_age_h
        FROM source_health
    """
    params: tuple = ()
    if city_id:
        q += " WHERE city_id = ?"
        params = (city_id,)
    return [dict(r) for r in con.execute(q + " ORDER BY source", params)]


def silent_sources(con: sqlite3.Connection, days: int = SOURCE_SILENT_DAYS) -> list[dict]:
    """Sources muettes depuis trop longtemps, avec leur anciennete en jours.

    `last_nonempty IS NULL` est inclus : une source qui n'a JAMAIS rien
    rapporte est le cas le plus suspect, pas le moins.
    """
    rows = con.execute(
        """
        SELECT source, last_count, last_nonempty, empty_streak,
               CAST(julianday('now') - julianday(COALESCE(last_nonempty, updated_at)) AS INT) AS silent_days
        FROM source_health
        WHERE empty_streak > 0
          AND (last_nonempty IS NULL
               OR julianday('now') - julianday(last_nonempty) >= :d)
        ORDER BY silent_days DESC
        """,
        {"d": days},
    ).fetchall()
    return [dict(r) for r in rows]


def stats(con: sqlite3.Connection, city_id: str | None = None) -> dict:
    where, params = ("WHERE city_id = ?", (city_id,)) if city_id else ("", ())
    row = con.execute(
        f"""
        SELECT COUNT(*)                         AS total,
               COUNT(DISTINCT source)           AS sources,
               SUM(start >= datetime('now'))    AS upcoming,
               SUM(lat IS NULL)                 AS without_geo,
               MAX(ingested_at)                 AS last_ingest
        FROM events {where}
        """,
        params,
    ).fetchone()
    return dict(row)


def stats_by_city(con: sqlite3.Connection) -> dict[str, dict]:
    rows = con.execute(
        """
        SELECT city_id,
               COUNT(*) AS total,
               SUM(start >= datetime('now') AND status = 'active' AND doublon_de IS NULL) AS upcoming,
               SUM(lat IS NULL) AS without_geo,
               SUM(doublon_de IS NOT NULL) AS duplicates,
               MAX(ingested_at) AS last_ingest
        FROM events GROUP BY city_id
        """
    ).fetchall()
    return {r["city_id"]: dict(r) for r in rows}


# ---------------------------------------------------------------------- feeds

def upsert_feed(con: sqlite3.Connection, url: str, kind: str,
                name: str | None = None, city: str | None = None,
                feed_id: str | None = None, city_id: str = "paris") -> None:
    con.execute(
        """
        INSERT INTO feeds (url, name, city, kind, feed_id, city_id) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (url) DO UPDATE SET name = excluded.name, city = excluded.city,
                                       kind = excluded.kind, feed_id = excluded.feed_id,
                                       city_id = excluded.city_id
        """,
        (url, name, city, kind, feed_id, city_id),
    )


def list_feeds(con: sqlite3.Connection, enabled_only: bool = True) -> list[dict]:
    q = "SELECT * FROM feeds" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY url"
    return [dict(r) for r in con.execute(q)]


def mark_feed(con: sqlite3.Connection, url: str, *, ok: bool, error: str | None = None,
              etag: str | None = None, last_modified: str | None = None) -> bool:
    """Met à jour l'état d'un flux. Retourne True s'il vient d'être désactivé."""
    now = _iso(datetime.now(timezone.utc))
    if ok:
        con.execute(
            """UPDATE feeds SET last_ok = ?, last_error = NULL, error_count = 0,
                                etag = COALESCE(?, etag),
                                last_modified = COALESCE(?, last_modified)
               WHERE url = ?""",
            (now, etag, last_modified, url),
        )
        return False

    con.execute(
        "UPDATE feeds SET last_error = ?, error_count = error_count + 1 WHERE url = ?",
        ((error or "unknown")[:500], url),
    )
    count = con.execute("SELECT error_count FROM feeds WHERE url = ?", (url,)).fetchone()
    if count and count[0] >= FEED_MAX_ERRORS:
        con.execute("UPDATE feeds SET enabled = 0 WHERE url = ?", (url,))
        return True
    return False


FUNNEL_STEPS = ("city_open", "pick_open", "filter_used", "event_open", "official_click", "directions_click",
                "save", "share", "evening_add", "calendar_add")


def funnel_hit(con: sqlite3.Connection, day: str, city_id: str, step: str) -> None:
    con.execute("INSERT INTO funnel (day, city_id, step, n) VALUES (?, ?, ?, 1) "
                "ON CONFLICT (day, city_id, step) DO UPDATE SET n = n + 1", (day, city_id, step))
    con.commit()


def funnel_stats(con: sqlite3.Connection, since_day: str, city_id: str | None = None) -> dict[str, int]:
    q, args = "SELECT step, SUM(n) AS n FROM funnel WHERE day >= ?", [since_day]
    if city_id:
        q += " AND city_id = ?"
        args.append(city_id)
    out = dict.fromkeys(FUNNEL_STEPS, 0)
    out.update({r["step"]: r["n"] for r in con.execute(q + " GROUP BY step", args)})
    return out
