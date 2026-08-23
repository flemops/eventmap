"""Connecteur « Que faire à Paris » — Ville de Paris, Opendatasoft Explore v2.1.

Source unique du projet depuis le recentrage sur Paris intra-muros (23/08) :
2534 events à venir, tous géolocalisés dans Paris, mis à jour quotidiennement.

Tout ce qui est codé ici a été vérifié sur la réponse réelle le 22/08/2026
(voir docs/sources.md) :
- `limit` plafonné à 100 → pagination par `offset` ;
- `occurrences` est une chaîne `début_fin;début_fin…`, pas un tableau ;
- `lat_lon` est un dict `{lon, lat}` ;
- le champ de tags s'appelle `qfap_tags`, pas `tags`.
"""

from __future__ import annotations

import logging
from datetime import datetime

from db import Event
from sources import PoliteClient, clean_html, ingest_window, normalize_category

log = logging.getLogger("eventmap.qfap")

SOURCE = "qfap"
ENDPOINT = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/que-faire-a-paris-/records"
PAGE_SIZE = 100          # plafond vérifié : 101 → HTTP 400
MAX_PAGES = 60           # garde-fou : 6000 events, au-delà on s'arrête et on logue

# Le jeu n'expose que ces champs ; on ne rapatrie que ceux-là pour alléger.
FIELDS = ",".join([
    "id", "title", "lead_text", "description", "occurrences",
    "address_name", "address_street", "address_zipcode", "address_city",
    "lat_lon", "price_type", "qfap_tags", "universe_tags", "url", "updated_at",
])
REQUIRED = ("id", "title", "occurrences")

PRICE_MAP = {
    "gratuit": "free",
    "payant": "paid",
    "gratuit sous condition": "free_conditional",
}


def parse_occurrences(raw: str | None) -> list[tuple[datetime, datetime | None]]:
    """`2026-09-18T14:00:00+02:00_2026-09-18T15:00:00+02:00;…` → [(start, end)]."""
    out: list[tuple[datetime, datetime | None]] = []
    for slot in (raw or "").split(";"):
        slot = slot.strip()
        if not slot:
            continue
        begin, _, end = slot.partition("_")
        try:
            start = datetime.fromisoformat(begin)
        except ValueError:
            log.debug("occurrence illisible ignorée: %r", slot)
            continue
        end_dt = None
        if end:
            try:
                end_dt = datetime.fromisoformat(end)
            except ValueError:
                pass
        out.append((start, end_dt))
    return out


def to_events(record: dict, window: tuple[datetime, datetime]) -> list[Event]:
    # Distinguer « clé absente » (renommage côté Opendatasoft, on doit le voir
    # tout de suite) de « valeur vide » (event sans créneau : donnée normale,
    # on l'ignore simplement).
    missing = [f for f in REQUIRED if f not in record]
    if missing:
        raise KeyError(f"champs absents de la réponse QFAP: {missing} — "
                       "l'API a probablement changé, voir docs/sources.md")
    if not record.get("occurrences") or not record.get("title"):
        return []

    geo = record.get("lat_lon") or {}
    lat, lon = geo.get("lat"), geo.get("lon")
    address = " ".join(
        p for p in (record.get("address_street"), record.get("address_zipcode")) if p
    ) or None
    category = normalize_category(record.get("qfap_tags"), record.get("universe_tags"), record["title"])
    description = clean_html(record.get("lead_text")) or clean_html(record.get("description"))
    updated = record.get("updated_at")
    start_min, start_max = window

    events: list[Event] = []
    for start, end in parse_occurrences(record["occurrences"]):
        if not (start_min <= start <= start_max):
            continue
        events.append(Event(
            source=SOURCE, source_id=str(record["id"]), start=start, end=end,
            title=record["title"].strip(), description=description,
            venue=record.get("address_name"), address=address,
            city=record.get("address_city"), lat=lat, lon=lon,
            price_type=PRICE_MAP.get((record.get("price_type") or "").strip().lower(), "unknown"),
            category=category, url=record.get("url"),
            updated_at=datetime.fromisoformat(updated) if updated else None,
        ))
    return events


async def fetch(client: PoliteClient) -> list[Event]:
    """Rapatrie tout le jeu page par page et l'éclate en créneaux."""
    window = ingest_window()
    events: list[Event] = []
    offset = 0
    total: int | None = None

    for page in range(MAX_PAGES):
        resp = await client.get(ENDPOINT, params={
            "limit": PAGE_SIZE, "offset": offset, "select": FIELDS,
            # Filtre côté serveur : périmètre Paris intra-muros uniquement, et
            # pas d'events déjà passés. Mesuré le 23/08 : 2534 events Paris à
            # venir sur 2892 toutes villes — on évite ~12 % de données inutiles.
            "where": f"address_city=\"Paris\" AND date_end >= '{window[0].date().isoformat()}'",
            "order_by": "id",
        })
        resp.raise_for_status()
        data = resp.json()
        if total is None:
            total = int(data.get("total_count", 0))
            log.info("QFAP: %d events annoncés", total)

        results = data.get("results", [])
        if not results:
            break
        for rec in results:
            events.extend(to_events(rec, window))

        offset += len(results)
        if offset >= total:
            break
    else:
        log.warning("QFAP: MAX_PAGES atteint (%d), ingestion tronquée à %d records", MAX_PAGES, offset)

    log.info("QFAP: %d records → %d créneaux dans la fenêtre", offset, len(events))
    return events
