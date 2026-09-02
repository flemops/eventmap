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
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

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
    UNIQUE (source, source_id, start)
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events (start);
CREATE INDEX IF NOT EXISTS idx_events_geo   ON events (lat, lon);

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
    last_modified TEXT
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
    return con


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

def upsert_events(con: sqlite3.Connection, events: list[Event]) -> int:
    """Insère ou met à jour. Retourne le nombre de lignes touchées."""
    now = _iso(datetime.now(timezone.utc))
    rows = [
        (
            e.source, e.source_id, _iso(e.start), _iso(e.end), e.title,
            e.description, e.venue, e.address, e.city, e.lat, e.lon,
            e.price_type, e.category, e.url, _iso(e.updated_at), now,
        )
        for e in events
    ]
    cur = con.executemany(
        """
        INSERT INTO events (source, source_id, start, end, title, description,
                            venue, address, city, lat, lon, price_type,
                            category, url, updated_at, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, source_id, start) DO UPDATE SET
            end = excluded.end, title = excluded.title,
            description = excluded.description, venue = excluded.venue,
            address = excluded.address, city = excluded.city,
            lat = excluded.lat, lon = excluded.lon,
            price_type = excluded.price_type, category = excluded.category,
            url = excluded.url, updated_at = excluded.updated_at,
            ingested_at = excluded.ingested_at
        """,
        rows,
    )
    return cur.rowcount


def purge_stale(con: sqlite3.Connection, source: str, before: datetime) -> int:
    """Supprime les lignes d'une source non revues depuis `before`.

    Appelé après un refresh *réussi* de cette source uniquement : si la source
    est tombée, on garde l'ancien contenu plutôt que de vider la base.
    """
    cur = con.execute(
        "DELETE FROM events WHERE source = ? AND ingested_at < ?",
        (source, _iso(_to_utc(before))),
    )
    return cur.rowcount


def purge_past(con: sqlite3.Connection, older_than_days: int = 1) -> int:
    cur = con.execute(
        "DELETE FROM events WHERE start < datetime('now', ?)",
        (f"-{older_than_days} days",),
    )
    return cur.rowcount


def search(
    con: sqlite3.Connection,
    *,
    lat: float,
    lon: float,
    radius_km: float,
    start_from: datetime,
    start_to: datetime,
    price_type: str | None = None,
    category: str | None = None,
    venues: list[str] | None = None,
    limit: int = 200,
) -> list[dict]:
    """Événements dans le rayon et la fenêtre, triés par distance puis date."""
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
        "lat IS NOT NULL",
        "julianday(COALESCE(end, start)) + (CASE WHEN end IS NULL THEN 0.125 ELSE 0 END) > julianday(:start_from)",
        "julianday(start) < julianday(:start_to)",
        "haversine_km(lat, lon, :lat, :lon) <= :radius",
    ]
    params: dict = {
        "lat": lat, "lon": lon, "radius": radius_km,
        "start_from": _iso(_to_utc(start_from)),
        "start_to": _iso(_to_utc(start_to)),
        "limit": limit,
    }
    if price_type:
        clauses.append("price_type = :price_type")
        params["price_type"] = price_type
    if category:
        clauses.append("category = :category")
        params["category"] = category

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
               haversine_km(lat, lon, :lat, :lon) AS distance_km
        FROM events
        WHERE {' AND '.join(clauses)}
        ORDER BY MAX(julianday(start), julianday(:start_from)), distance_km
        LIMIT :limit
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def stats(con: sqlite3.Connection) -> dict:
    row = con.execute(
        """
        SELECT COUNT(*)                         AS total,
               COUNT(DISTINCT source)           AS sources,
               SUM(start >= datetime('now'))    AS upcoming,
               SUM(lat IS NULL)                 AS without_geo,
               MAX(ingested_at)                 AS last_ingest
        FROM events
        """
    ).fetchone()
    return dict(row)


# ---------------------------------------------------------------------- feeds

def upsert_feed(con: sqlite3.Connection, url: str, kind: str,
                name: str | None = None, city: str | None = None) -> None:
    con.execute(
        """
        INSERT INTO feeds (url, name, city, kind) VALUES (?, ?, ?, ?)
        ON CONFLICT (url) DO UPDATE SET name = excluded.name, city = excluded.city,
                                       kind = excluded.kind
        """,
        (url, name, city, kind),
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
