"""Découverte de flux sur un domaine : chemins .ics classiques + JSON-LD.

Usage :
    python discover.py montreuil.fr nanterre.fr
    python discover.py --yaml montreuil.fr > feeds-candidats.yaml

Règles : robots.txt respecté, User-Agent identifiable, ≤ 1 req/s par domaine
(hérité de PoliteClient), et on s'arrête au premier flux .ics valide par
domaine — inutile de sonder dix chemins si le deuxième répond.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from db import Event
from sources import USER_AGENT, PoliteClient, clean_html, normalize_category, stable_id

log = logging.getLogger("eventmap.discover")

# Ordre = probabilité décroissante. Les deux entrées WordPress/The Events
# Calendar sont les plus rentables sur les sites municipaux français.
ICS_PATHS = [
    "/?post_type=tribe_events&ical=1",
    "/feed/?post_type=tribe_events",
    "/agenda.ics", "/events.ics", "/calendrier.ics", "/evenements.ics",
    "/?ical=1", "/agenda/feed", "/evenements/feed", "/agenda/?ical=1",
    "/agenda/ical", "/calendar.ics", "/ical", "/feed/ical",
]

# Pages où chercher du JSON-LD quand aucun .ics n'existe.
HTML_PATHS = ["/", "/agenda", "/agenda/", "/evenements", "/evenements/", "/sortir", "/programmation"]

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class Finding:
    domain: str
    url: str
    kind: str            # ics | jsonld
    status: str          # ok | empty | error
    events: int = 0
    detail: str = ""
    checked_at: str = ""


# -------------------------------------------------------------- robots.txt

async def robots_allows(client: PoliteClient, base: str, path: str) -> bool:
    rp = RobotFileParser()
    try:
        resp = await client.get(urljoin(base, "/robots.txt"))
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
            return rp.can_fetch(USER_AGENT, urljoin(base, path))
    except httpx.HTTPError:
        pass
    return True  # pas de robots.txt lisible = pas d'interdiction


# ---------------------------------------------------------------- JSON-LD

def extract_jsonld_events(html: str, page_url: str, *, source: str = "jsonld") -> list[Event]:
    """Extrait les `schema.org/Event` d'une page. Tolère les blocs invalides."""
    events: list[Event] = []
    for block in _JSONLD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk_jsonld(data):
            ev = _jsonld_to_event(node, page_url, source)
            if ev:
                events.append(ev)
    return events


def _walk_jsonld(data):
    """Un JSON-LD peut être un objet, une liste, ou un @graph imbriqué."""
    if isinstance(data, list):
        for d in data:
            yield from _walk_jsonld(d)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _walk_jsonld(data["@graph"])
        t = data.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and x.endswith("Event") for x in types):
            yield data


def _jsonld_to_event(node: dict, page_url: str, source: str) -> Event | None:
    name = node.get("name")
    start_raw = node.get("startDate")
    if not name or not start_raw:
        return None
    try:
        start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    end = None
    if node.get("endDate"):
        try:
            end = datetime.fromisoformat(str(node["endDate"]).replace("Z", "+00:00"))
        except ValueError:
            pass

    loc = node.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    geo = loc.get("geo") or {}
    addr = loc.get("address")
    if isinstance(addr, dict):
        city = addr.get("addressLocality")
        address = " ".join(str(addr.get(k, "")) for k in ("streetAddress", "postalCode") if addr.get(k)) or None
    else:
        city, address = None, (str(addr) if addr else None)

    offers = node.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = offers.get("price")
    price_type = "unknown"
    if price is not None:
        try:
            price_type = "free" if float(price) == 0 else "paid"
        except (TypeError, ValueError):
            pass

    url = node.get("url") or page_url
    return Event(
        source=source, source_id=stable_id(url, str(start_raw)), start=start, end=end,
        title=str(name).strip(), description=clean_html(node.get("description")),
        venue=loc.get("name"), address=address, city=city,
        lat=_float(geo.get("latitude")), lon=_float(geo.get("longitude")),
        price_type=price_type, url=url,
        category=normalize_category(str(node.get("@type", "")), str(name)),
    )


def _float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ sonde

async def probe_domain(client: PoliteClient, domain: str) -> list[Finding]:
    from sources import parse_ics

    base = f"https://{domain}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    findings: list[Finding] = []

    # 1. Chemins .ics — on s'arrête au premier qui répond avec des events.
    for path in ICS_PATHS:
        url = urljoin(base, path)
        if not await robots_allows(client, base, path):
            findings.append(Finding(domain, url, "ics", "error", detail="interdit par robots.txt", checked_at=now))
            continue
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            findings.append(Finding(domain, url, "ics", "error", detail=type(exc).__name__, checked_at=now))
            continue
        if resp.status_code != 200 or b"BEGIN:VCALENDAR" not in resp.content[:4000]:
            continue
        try:
            n = len(parse_ics(resp.content, source="probe"))
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding(domain, url, "ics", "error", detail=f"parse: {exc}", checked_at=now))
            continue
        findings.append(Finding(domain, url, "ics", "ok" if n else "empty", events=n, checked_at=now))
        if n:
            return findings

    # 2. JSON-LD dans les pages d'agenda.
    for path in HTML_PATHS:
        url = urljoin(base, path)
        if not await robots_allows(client, base, path):
            continue
        try:
            resp = await client.get(url)
        except httpx.HTTPError:
            continue
        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
            continue
        evs = extract_jsonld_events(resp.text, url)
        if evs:
            findings.append(Finding(domain, url, "jsonld", "ok", events=len(evs), checked_at=now))
            return findings

    if not findings:
        findings.append(Finding(domain, base, "ics", "empty", detail="aucun flux trouvé", checked_at=now))
    return findings


async def discover(domains: list[str]) -> list[Finding]:
    async with PoliteClient(min_interval=1.0, timeout=15.0) as client:
        results = await asyncio.gather(*(probe_domain(client, d) for d in domains), return_exceptions=True)
    out: list[Finding] = []
    for domain, r in zip(domains, results):
        if isinstance(r, Exception):
            out.append(Finding(domain, f"https://{domain}", "ics", "error", detail=f"{type(r).__name__}: {r}"))
        else:
            out.extend(r)
    return out


def to_yaml_entries(findings: list[Finding]) -> str:
    import yaml
    entries = [
        {"url": f.url, "name": f.domain, "city": "", "type": f.kind, "license": "unknown",
         "enabled": True, "last_ok": f.checked_at, "status": f.status, "events_seen": f.events}
        for f in findings if f.status == "ok"
    ]
    return yaml.safe_dump({"feeds": entries}, allow_unicode=True, sort_keys=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("domains", nargs="+")
    ap.add_argument("--yaml", action="store_true", help="sortie prête pour feeds.yaml (seulement les OK)")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.v else logging.WARNING, stream=sys.stderr)

    findings = asyncio.run(discover([urlsplit(d).netloc or d for d in args.domains]))
    if args.yaml:
        print(to_yaml_entries(findings))
        return
    for f in findings:
        mark = {"ok": "OK ", "empty": "—  ", "error": "ERR"}[f.status]
        print(f"{mark} {f.kind:6s} {f.events:4d}  {f.url}  {f.detail}")


if __name__ == "__main__":
    main()
