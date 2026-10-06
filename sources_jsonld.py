"""Connecteur « sitemap + JSON-LD Event » : une salle qui publie une page par événement, balisée
schema.org/Event, listée dans son sitemap.

Pourquoi ce format : c'est le balisage que la salle publie POUR les machines (moteurs de recherche,
assistants) ; on lit les pages publiques que son robots.txt autorise, une par une, poliment
(PoliteClient : 1 requête / `min_interval_s`, User-Agent identifiable, SSRF refusé).

Options (feeds.yaml → `options:`) :
  include / exclude   regex sur l'URL : quelles pages sont des événements, lesquelles écarter
                      (ex. la copie `_en` de chaque page) ;
  date_in_url         regex à 1 groupe capturant AAAA-MM-JJ dans l'URL : on ne télécharge pas les
                      pages passées ;
  max_pages           garde-fou par cycle ;
  tz                  fuseau de la salle (Europe/Paris) ;
  naive_utc_label     true si un site écrit l'heure LOCALE avec un « Z » : le « Z » est alors ignoré et
                      l'heure lue dans `tz`. FAUX par défaut, et faux pour le Bataclan : on l'a cru à
                      tort le 06/10/2026 parce que l'en-tête de sa page affiche l'heure UTC (« 17h00 ») ;
                      son déroulé dit « ouverture des portes 19h00 » = 17:00Z en heure d'été. Toujours
                      confronter le JSON-LD au déroulé horaire de la page avant d'activer cette option ;
  venue               {name, address, lat, lon} : le JSON-LD de la salle ne donne pas de coordonnées ;
  default_category    catégorie quand le titre ne permet pas d'en déduire une.

Un connecteur ne renvoie JAMAIS une liste vide pour masquer une panne : aucun sitemap lisible, aucune
page événement, ou plus de la moitié des pages en échec = exception (la source est alors marquée en
erreur et /health?strict=1 le dit).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from db import Event
from discover import _JSONLD_RE, _walk_jsonld
from sources import PoliteClient, normalize_category, stable_id

log = logging.getLogger("eventmap.jsonld")

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_STATUS = {"eventcancelled": "cancelled", "eventpostponed": "postponed", "eventrescheduled": "postponed"}


def _slug(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


def _parse_start(raw, tz: ZoneInfo, naive_utc_label: bool) -> datetime | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if naive_utc_label and (s.endswith("Z") or s.endswith("z")):
            s = re.sub(r"\.\d+Z?$", "", s.rstrip("Zz"))        # « …18:00:00.000Z » -> « …18:00:00 »
            return datetime.fromisoformat(s).replace(tzinfo=tz)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)
    except ValueError:
        return None


def events_from_page(html: str, page_url: str, opts: dict, source: str) -> list[Event]:
    tz = ZoneInfo(opts.get("tz", "Europe/Paris"))
    naive = bool(opts.get("naive_utc_label"))
    venue = opts.get("venue") or {}
    out: list[Event] = []
    for block in _JSONLD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk_jsonld(data):
            name, start = node.get("name"), _parse_start(node.get("startDate"), tz, naive)
            if not name or start is None:
                continue
            end = _parse_start(node.get("endDate"), tz, naive) if node.get("endDate") else None
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = offers.get("price")
            try:
                pmin = float(price) if price not in (None, "") else None
            except (TypeError, ValueError):
                pmin = None
            status = _STATUS.get(str(node.get("eventStatus", "")).rsplit("/", 1)[-1].lower(), "active")
            desc = node.get("description")
            cat = normalize_category(str(name), str(desc or "")[:200])
            if cat == "other" and opts.get("default_category"):
                cat = opts["default_category"]
            out.append(Event(
                source=source, source_id=_slug(page_url) or stable_id(page_url, str(name)),
                start=start, end=end, title=str(name), description=desc,
                venue=venue.get("name"), address=venue.get("address"), city=opts.get("city", "Paris"),
                lat=venue.get("lat"), lon=venue.get("lon"),
                price_type="paid" if (pmin or 0) > 0 else "unknown", price_min=pmin,
                currency=offers.get("priceCurrency"), category=cat, url=page_url,
                booking_url=offers.get("url"), status=status,
            ))
    return out


async def fetch(client: PoliteClient, sitemap_url: str, opts: dict, source: str,
                today: date | None = None) -> list[Event]:
    today = today or datetime.now(timezone.utc).date()
    resp = await client.get(sitemap_url)
    resp.raise_for_status()
    locs = _LOC.findall(resp.text)
    if not locs:
        raise ValueError(f"{sitemap_url}: aucun <loc> dans le sitemap")
    inc = re.compile(opts.get("include", "."))
    exc = re.compile(opts["exclude"]) if opts.get("exclude") else None
    dre = re.compile(opts["date_in_url"]) if opts.get("date_in_url") else None
    pages = []
    for u in locs:
        if not inc.search(u) or (exc and exc.search(u)):
            continue
        if dre:
            m = dre.search(u)
            try:
                if m and date.fromisoformat(m.group(1)) < today - timedelta(days=1):
                    continue                       # événement passé : inutile de le télécharger
            except ValueError:
                pass
        pages.append(u)
    if not pages:
        raise ValueError(f"{sitemap_url}: aucune page événement à venir (filtre include/exclude/date ?)")
    pages = pages[: int(opts.get("max_pages", 150))]

    events: list[Event] = []
    failed = 0
    for u in pages:
        try:
            r = await client.get(u)
            r.raise_for_status()
            events.extend(events_from_page(r.text, u, opts, source))
        except Exception as exc_:  # noqa: BLE001 — une page ne fait pas tomber la source
            failed += 1
            log.warning("page %s : %s: %s", u, type(exc_).__name__, exc_)
    if failed > len(pages) / 2:
        raise RuntimeError(f"{sitemap_url}: {failed}/{len(pages)} pages en échec")
    if not events:
        raise ValueError(f"{sitemap_url}: {len(pages)} pages lues, aucun JSON-LD Event exploitable")
    return events
