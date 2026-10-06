"""Connecteur Opendatasoft « Public events — OpenAgenda » (Licence Ouverte v1.0, Etalab).

Jeu : https://public.opendatasoft.com/explore/dataset/evenements-publics-openagenda/ — les événements
publics publiés sur OpenAgenda, rediffusés en open data (API Explore v2.1, sans clé). Licence ouverte :
réutilisation libre, y compris commerciale, avec mention de la source (« Source : OpenAgenda — Licence
Ouverte v1.0 », affichée dans le pied de page via `attribution` dans feeds.yaml).

C'est la voie PROPRE vers OpenAgenda : l'API d'OpenAgenda elle-même exige une clé (vérifié : 403 sans),
et ce jeu contient déjà ses agendas publics, FICEP compris — le dédoublonnage inter-sources (sources.py)
ramène à un seul événement ceux que QFAP ou FICEP publient aussi.

`timings` (JSON) donne chaque créneau réel d'un événement : une série n'est JAMAIS étalée sur toute la
plage de dates (13.43) ; chaque créneau devient une ligne, bornée à la fenêtre d'ingestion.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from db import Event
from sources import PoliteClient, clean_html, ingest_window, normalize_category

log = logging.getLogger("eventmap.ods")

PAGE_SIZE = 100            # plafond de l'API Explore v2.1
MAX_PAGES = 40             # 4 000 événements : au-delà on s'arrête et on le dit
MAX_SLOTS_PER_EVENT = 60   # une exposition ouverte tous les jours pendant un an ne doit pas noyer la base
FIELDS = ("uid,title_fr,description_fr,conditions_fr,keywords_fr,timings,location_name,location_address,"
          "location_coordinates,location_city,canonicalurl,updatedat")
_FREE = re.compile(r"(?i)\b(gratuit|entr[ée]e libre|acc[èe]s libre|gratuite)\b")
_PAID = re.compile(r"(?i)(\d\s?€|€\s?\d|\beuros?\b|\bpayant\b|\btarif\b|\bbillet(?:terie)?\b|\br[ée]servation obligatoire\b)")


def _price_type(conditions: str | None) -> str:
    c = conditions or ""
    if _FREE.search(c) and not _PAID.search(c):
        return "free"
    if _PAID.search(c) and not _FREE.search(c):
        return "paid"
    return "unknown"          # « gratuit pour les -12 ans, 10 € sinon » : on ne tranche pas


def record_to_events(rec: dict, source: str, window: tuple[datetime, datetime]) -> list[Event]:
    uid, title = rec.get("uid"), (rec.get("title_fr") or "").strip()
    coords = rec.get("location_coordinates") or {}
    if not uid or not title or coords.get("lat") is None:
        return []
    try:
        slots = json.loads(rec.get("timings") or "[]")
    except (TypeError, ValueError):
        return []
    lo, hi = window
    out: list[Event] = []
    updated = None
    try:
        updated = datetime.fromisoformat(rec["updatedat"]) if rec.get("updatedat") else None
    except ValueError:
        pass
    for slot in slots:
        try:
            start = datetime.fromisoformat(slot["begin"])
            end = datetime.fromisoformat(slot["end"]) if slot.get("end") else None
        except (KeyError, ValueError, TypeError):
            continue
        if not (lo <= start <= hi):
            continue
        out.append(Event(
            source=source, source_id=str(uid), start=start, end=end, title=title,
            description=clean_html(rec.get("description_fr")),
            venue=rec.get("location_name"), address=rec.get("location_address"), city=rec.get("location_city"),
            lat=float(coords["lat"]), lon=float(coords["lon"]), url=rec.get("canonicalurl"),
            price_type=_price_type(rec.get("conditions_fr")), updated_at=updated,
            category=normalize_category(str(rec.get("keywords_fr") or ""), title),
        ))
        if len(out) >= MAX_SLOTS_PER_EVENT:
            break
    return out


async def fetch(client: PoliteClient, opts: dict, source: str) -> list[Event]:
    host = opts.get("host", "public.opendatasoft.com")
    dataset = opts.get("dataset", "evenements-publics-openagenda")
    where = opts.get("where", 'location_city="Paris" AND lastdate_end >= now()')
    url = f"https://{host}/api/explore/v2.1/catalog/datasets/{dataset}/records"
    window = ingest_window()
    events: list[Event] = []
    total = None
    for page in range(MAX_PAGES):
        resp = await client.get(url, params={"select": FIELDS, "where": where, "order_by": "uid",
                                             "limit": PAGE_SIZE, "offset": page * PAGE_SIZE})
        resp.raise_for_status()
        body = resp.json()
        total = body.get("total_count", total)
        results = body.get("results", [])
        for rec in results:
            events.extend(record_to_events(rec, source, window))
        if len(results) < PAGE_SIZE:
            break
    else:
        log.warning("%s : limite de %d pages atteinte (total annoncé %s)", dataset, MAX_PAGES, total)
    if not total:
        raise ValueError(f"{dataset}: 0 enregistrement pour « {where} » (champ renommé ? filtre cassé ?)")
    if not events:
        raise ValueError(f"{dataset}: {total} enregistrements mais aucun créneau exploitable (champ « timings » ?)")
    return events
