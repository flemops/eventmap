"""Socle commun des connecteurs : client HTTP poli, parser iCalendar,
connecteur OpenAgenda, et agrégation asynchrone tolérante aux pannes.

Le contrat d'un connecteur est volontairement minimal :

    async def fetch(client: PoliteClient) -> list[Event]

Il lève en cas d'échec ; c'est `aggregate()` qui transforme l'exception en
résultat partiel journalisé. Un connecteur ne doit jamais attraper une erreur
pour renvoyer une liste vide : on perdrait l'information « cette source est
tombée », et `mark_feed` ne pourrait plus désactiver les flux morts.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

import safety
from db import Event
from textnorm import norm_key

log = logging.getLogger("eventmap.sources")

CONTACT_URL = os.environ.get("EVENTMAP_CONTACT", "https://github.com/flemops/eventmap")
# ASCII uniquement : un en-tête HTTP n'accepte pas les accents.
USER_AGENT = f"EventMapFR/0.1 (+{CONTACT_URL}; non-commercial event aggregator)"

# Fenêtre d'ingestion : on ne stocke pas des créneaux à deux ans, ils
# gonflent la base pour rien et seront de toute façon réingérés plus tard.
INGEST_HORIZON_DAYS = int(os.environ.get("EVENTMAP_HORIZON_DAYS", "90"))

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_html(text: str | None, max_len: int = 600) -> str | None:
    if not text:
        return None
    out = _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", text))).strip()
    return out[:max_len] or None


def stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def ingest_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=1), now + timedelta(days=INGEST_HORIZON_DAYS)


# ------------------------------------------------------------------ catégories

# Table de normalisation partagée : on aligne les vocabulaires hétérogènes
# des sources sur une petite liste stable, que le front peut afficher telle
# quelle. L'ordre compte : le premier mot-clé trouvé gagne.
CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("music",     ("concert", "musique", "music", "dj", "jazz", "rock", "rap", "électro", "electro")),
    ("theatre",   ("théâtre", "theatre", "spectacle", "danse", "cirque", "humour", "opéra")),
    ("cinema",    ("cinéma", "cinema", "film", "projection", "ciné")),
    ("expo",      ("exposition", "expo", "vernissage", "musée", "galerie")),
    ("kids",      ("enfant", "jeune public", "famille", "kids", "atelier enfant")),
    ("workshop",  ("atelier", "workshop", "cours", "stage", "initiation")),
    ("talk",      ("conférence", "rencontre", "débat", "lecture", "table ronde", "visite")),
    ("sport",     ("sport", "course", "match", "yoga", "randonnée", "balade")),
    ("market",    ("marché", "brocante", "vide-grenier", "salon", "festival")),
]


def normalize_category(*labels: str | None) -> str:
    haystack = " ".join(l.lower() for l in labels if l)
    for cat, keys in CATEGORY_KEYWORDS:
        if any(k in haystack for k in keys):
            return cat
    return "other"


# ------------------------------------------------------------- client HTTP

class PoliteClient:
    """httpx.AsyncClient avec limitation de débit par domaine.

    Un seul client pour tout le refresh : connexions réutilisées, et surtout
    un verrou par domaine qui garantit ≤ 1 requête/s quelle que soit la
    concurrence en amont. C'est la protection de l'IP de la VM.

    Anti-SSRF (13.49) : l'URL de départ ET chaque redirection doivent se
    résoudre vers une adresse publique. Les redirections sont suivies à la main
    (5 sauts maximum) — `follow_redirects=True` d'httpx suivrait un 302 vers
    http://169.254.169.254/ sans que rien ne puisse l'en empêcher.
    """

    MAX_REDIRECTS = 5

    def __init__(self, min_interval: float = 1.0, timeout: float = 20.0,
                 intervals: dict[str, float] | None = None, public_only: bool = True):
        self.min_interval = min_interval
        self.intervals = intervals or {}          # hôte -> délai propre à la source
        self.public_only = public_only
        self._last: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
            timeout=timeout,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "PoliteClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def get(self, url: str, **kw) -> httpx.Response:
        for _ in range(self.MAX_REDIRECTS + 1):
            if self.public_only:
                await safety.ensure_public_url_async(url)
            host = urlsplit(url).netloc
            interval = self.intervals.get(host, self.min_interval)
            async with self._locks[host]:
                wait = interval - (time.monotonic() - self._last[host])
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    resp = await self._client.get(url, **kw)
                finally:
                    self._last[host] = time.monotonic()
            loc = resp.headers.get("location")
            if resp.status_code in (301, 302, 303, 307, 308) and loc:
                url = urljoin(url, loc)
                continue
            return resp
        raise safety.UnsafeUrl("trop de redirections")


# ------------------------------------------------------------- iCalendar

def parse_ics(raw: bytes | str, *, source: str, default_url: str | None = None,
              window: tuple[datetime, datetime] | None = None) -> list[Event]:
    """Développe un calendrier en événements, RRULE incluses, dans la fenêtre."""
    from icalendar import Calendar
    from dateutil.rrule import rrulestr

    start_min, start_max = window or ingest_window()
    cal = Calendar.from_ical(raw)
    events: list[Event] = []

    # Occurrences modifiées ou annulées d'une série (RECURRENCE-ID) : une série
    # n'est PAS « tous les jours de la plage ». On les indexe d'abord pour que
    # l'occurrence générée par la RRULE soit remplacée, pas doublée.
    overrides: dict[tuple[str, datetime], object] = {}
    for comp in cal.walk("VEVENT"):
        rid = comp.get("RECURRENCE-ID")
        if rid is not None:
            overrides[(str(comp.get("UID") or ""), _ical_to_dt(rid.dt))] = comp

    for comp in cal.walk("VEVENT"):
        if comp.get("RECURRENCE-ID") is not None:
            continue          # traitée avec sa série
        summary = str(comp.get("SUMMARY", "")).strip()
        dtstart = comp.get("DTSTART")
        if not summary or dtstart is None:
            continue

        start = _ical_to_dt(dtstart.dt)
        dtend = comp.get("DTEND")
        end = _ical_to_dt(dtend.dt) if dtend is not None else None
        duration = (end - start) if end else None

        # OpenAgenda (FICEP) encode l'occurrence après « // » dans l'UID
        # (ex. « 35008205//20260917T080000Z ») : découper dessus donne
        # l'identifiant stable de la série, commun à toutes ses occurrences.
        # Sans coupure, chaque créneau récurrent porterait un source_id
        # différent — inoffensif pour l'UNIQUE(source, source_id, start) vu
        # que `start` diffère déjà, mais l'identifiant perdrait tout son sens
        # en dehors du stockage (logs, rapprochement manuel). Sans « // »
        # dans l'UID, le split est un no-op.
        raw_uid = str(comp.get("UID") or "")
        uid = (raw_uid or stable_id(summary, start.isoformat())).split("//", 1)[0]
        url = str(comp.get("URL") or default_url or "")
        location = str(comp.get("LOCATION", "")).strip() or None
        if location and " - " in location:
            # Convention OpenAgenda (vérifiée le 03/09 sur le flux FICEP) :
            # LOCATION = "Nom du lieu - adresse", jamais le nom seul. Sans
            # ce découpage, `venue` contenait l'adresse entière et ne
            # correspondait plus jamais à aucun nom de `cultures.yaml`.
            # Coupe sur le PREMIER " - " espacé (pas tout tiret) : des noms
            # comme « Centre Wallonie-Bruxelles I Cours intérieure » ou une
            # adresse « 127-129 rue Saint Martin » ont des tirets non
            # espacés qui ne doivent pas être pris pour le séparateur.
            location = location.split(" - ", 1)[0].strip() or location
        description = clean_html(str(comp.get("DESCRIPTION", "")))
        last_mod = comp.get("LAST-MODIFIED")
        updated = _ical_to_dt(last_mod.dt) if last_mod is not None else None
        lat = lon = None
        if comp.get("GEO"):
            try:
                # icalendar>=6 renvoie un str depuis vGeo.to_ical() (pas des
                # bytes comme les autres propriétés) : bug réel trouvé le
                # 03/09 en testant contre le flux FICEP — sans ce garde-fou,
                # `.decode()` levait AttributeError, avalée silencieusement,
                # et 100 % des événements du flux perdaient leur géoloc.
                raw = comp["GEO"].to_ical()
                raw = raw.decode() if isinstance(raw, bytes) else raw
                lat, lon = (float(x) for x in raw.split(";"))
            except (ValueError, AttributeError):
                pass
        categories = comp.get("CATEGORIES")
        cat_label = " ".join(str(c) for c in (categories.cats if categories else []))
        status = _ical_status(comp)
        excluded = _ical_exdates(comp)

        occurrences: list[datetime] = [start]
        rrule = comp.get("RRULE")
        if rrule:
            rule = rrulestr(rrule.to_ical().decode(), dtstart=start)
            occurrences = list(rule.between(start_min, start_max, inc=True))

        for occ in occurrences:
            if not (start_min <= occ <= start_max):
                continue
            if occ in excluded:
                continue          # EXDATE : « pas ce jour-là » est une information de la source
            o_start, o_end, o_status, o_title = occ, (occ + duration) if duration else None, status, summary
            ov = overrides.get((raw_uid, occ))
            if ov is not None:
                ov_start = ov.get("DTSTART")
                if ov_start is not None:
                    o_start = _ical_to_dt(ov_start.dt)
                    ov_end = ov.get("DTEND")
                    o_end = _ical_to_dt(ov_end.dt) if ov_end is not None else (o_start + duration if duration else None)
                o_status = _ical_status(ov) if ov.get("STATUS") else o_status
                o_title = str(ov.get("SUMMARY") or summary).strip()
            events.append(Event(
                source=source, source_id=uid, start=o_start,
                end=o_end,
                title=o_title, description=description, venue=location,
                lat=lat, lon=lon, url=url or None, updated_at=updated,
                category=normalize_category(cat_label, summary),
                status=o_status,
            ))
    return events


def _ical_status(comp) -> str:
    """STATUS:CANCELLED d'un VEVENT → « cancelled ». TENTATIVE/CONFIRMED restent actifs."""
    st = str(comp.get("STATUS", "")).strip().upper()
    return "cancelled" if st == "CANCELLED" else "active"


def _ical_exdates(comp) -> set[datetime]:
    """Toutes les dates exclues d'une série, quel que soit le nombre de lignes EXDATE."""
    raw = comp.get("EXDATE")
    if raw is None:
        return set()
    out: set[datetime] = set()
    for line in (raw if isinstance(raw, list) else [raw]):
        for d in getattr(line, "dts", []):
            out.add(_ical_to_dt(d.dt))
    return out


def _ical_to_dt(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    # Événement « journée entière » : on le place à 09:00 UTC pour qu'il
    # tombe dans une fenêtre « aujourd'hui » sans prétendre connaître l'heure.
    return datetime(value.year, value.month, value.day, 9, tzinfo=timezone.utc)


async def fetch_ics(client: PoliteClient, url: str, *, source: str = "ics",
                    etag: str | None = None, last_modified: str | None = None) -> tuple[list[Event], dict]:
    """Retourne (events, meta). meta contient etag/last_modified pour le cache.

    Un 304 renvoie une liste vide **et** meta["not_modified"]=True : l'appelant
    ne doit alors pas purger les anciennes lignes de ce flux.
    """
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    resp = await client.get(url, headers=headers)
    meta = {"etag": resp.headers.get("ETag"), "last_modified": resp.headers.get("Last-Modified")}
    if resp.status_code == 304:
        meta["not_modified"] = True
        return [], meta
    resp.raise_for_status()
    if b"BEGIN:VCALENDAR" not in resp.content[:2000]:
        raise ValueError(f"{url}: la réponse n'est pas un calendrier iCalendar")
    return parse_ics(resp.content, source=source, default_url=url), meta


# ------------------------------------------------------------- OpenAgenda

OPENAGENDA_KEY = os.environ.get("OPENAGENDA_KEY")
OPENAGENDA_API = "https://api.openagenda.com/v2"


async def fetch_openagenda(client: PoliteClient, agenda_uid: str, *, key: str | None = None) -> list[Event]:
    """Connecteur OpenAgenda v2. Inactif sans clé (vérifié : 403 sans `key`)."""
    key = key or OPENAGENDA_KEY
    if not key:
        raise RuntimeError("OPENAGENDA_KEY absente : connecteur désactivé")

    start_min, start_max = ingest_window()
    events: list[Event] = []
    after: list | None = None
    while True:
        params = {
            "key": key, "size": 100, "detailed": 1,
            "timings[gte]": start_min.date().isoformat(),
            "timings[lte]": start_max.date().isoformat(),
        }
        if after:
            params["after"] = after
        resp = await client.get(f"{OPENAGENDA_API}/agendas/{agenda_uid}/events", params=params)
        resp.raise_for_status()
        data = resp.json()
        for ev in data.get("events", []):
            loc = ev.get("location") or {}
            title = (ev.get("title") or {}).get("fr") or next(iter((ev.get("title") or {}).values()), "")
            desc = (ev.get("description") or {}).get("fr")
            for t in ev.get("timings", []):
                events.append(Event(
                    source=f"openagenda:{agenda_uid}", source_id=str(ev["uid"]),
                    start=datetime.fromisoformat(t["begin"]),
                    end=datetime.fromisoformat(t["end"]) if t.get("end") else None,
                    title=title, description=clean_html(desc),
                    venue=loc.get("name"), address=loc.get("address"), city=loc.get("city"),
                    lat=loc.get("latitude"), lon=loc.get("longitude"),
                    url=ev.get("canonicalUrl"),
                    category=normalize_category(" ".join(k.get("label", {}).get("fr", "") for k in ev.get("keywords", []) or []), title),
                    updated_at=datetime.fromisoformat(ev["updatedAt"]) if ev.get("updatedAt") else None,
                ))
        after = data.get("after")
        if not after or not data.get("events"):
            break
    return events


# ------------------------------------------------------------- agrégation

def _is_client_error(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return isinstance(code, int) and 400 <= code < 500 and code != 429


Fetcher = Callable[[PoliteClient], Awaitable[list[Event]]]


@dataclass
class SourceResult:
    name: str
    events: list[Event]
    error: str | None = None
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


async def aggregate(fetchers: dict[str, Fetcher], *, concurrency: int = 4,
                    per_source_timeout: float = 120.0,
                    policies: dict[str, dict] | None = None,
                    intervals: dict[str, float] | None = None) -> list[SourceResult]:
    """Lance tous les connecteurs et renvoie un résultat **par source**.

    Une source qui lève ou dépasse son délai produit un SourceResult en
    erreur ; les autres continuent. C'est `return_exceptions=True` qui
    garantit qu'une panne isolée ne remonte jamais jusqu'à l'API.
    """
    sem = asyncio.Semaphore(concurrency)
    policies = policies or {}

    async def run(name: str, fetcher: Fetcher, client: PoliteClient) -> SourceResult:
        t0 = time.monotonic()
        pol = policies.get(name, {})
        timeout = pol.get("timeout_s", per_source_timeout)
        attempts = 1 + int(pol.get("retries", 0))
        async with sem:
            err = ""
            for attempt in range(attempts):
                try:
                    # Un délai PAR SOURCE : une source lente ne bloque pas les autres
                    # (le sémaphore est tenu, mais wait_for borne chaque tentative).
                    events = await asyncio.wait_for(fetcher(client), timeout)
                    return SourceResult(name, events, duration_s=time.monotonic() - t0)
                except Exception as exc:  # noqa: BLE001 — on veut tout capturer ici
                    err = f"{type(exc).__name__}: {exc}"[:500]
                    log.warning("source %s en erreur (tentative %d/%d): %s", name, attempt + 1, attempts, err)
                    # Pas de nouvelle tentative sur une erreur qui ne passera pas
                    # (URL refusée, 4xx) ; backoff exponentiel plafonné sinon.
                    if isinstance(exc, safety.UnsafeUrl) or _is_client_error(exc):
                        break
                    if attempt + 1 < attempts:
                        await asyncio.sleep(min(30.0, 2.0 ** (attempt + 1)))
            return SourceResult(name, [], error=err, duration_s=time.monotonic() - t0)

    async with PoliteClient(intervals=intervals) as client:
        results = await asyncio.gather(
            *(run(n, f, client) for n, f in fetchers.items()),
            return_exceptions=True,
        )

    # gather ne devrait plus rien laisser passer, mais un CancelledError ou
    # un bug dans run() lui-même ne doit pas non plus faire tomber l'appelant.
    out: list[SourceResult] = []
    for name, r in zip(fetchers, results):
        if isinstance(r, SourceResult):
            out.append(r)
        else:
            out.append(SourceResult(name, [], error=f"{type(r).__name__}: {r}"))
    return out


# ---------------------------------------------------------- dédoublonnage
#
# Design du 03/09/2026 (remplace la fusion pré-upsert de D9/docs/decisions.md,
# qui écartait silencieusement le perdant avant même l'écriture en base).
# Garde-fou du paquet de déploiement : « la fusion ne supprime jamais un
# enregistrement ». Toutes les sources sont donc upsertées telles quelles ;
# cette passe tourne APRÈS l'upsert (voir main.refresh) et se contente de
# MARQUER les perdants (colonne `doublon_de`, jamais de DELETE) — `db.search`
# les filtre à la lecture. Réversible par construction : corriger un faux
# positif est un simple UPDATE, pas une réingestion.

def _norm_title(t: str) -> str:
    # textnorm.norm_key garde les lettres arabes. L'ancienne version passait par
    # ASCII et vidait tout titre arabe : deux titres arabes DIFFERENTS devenaient
    # deux chaines vides, donc "identiques" (similarite 1.0) - chaque evenement
    # arabe d'un meme creneau aurait ete marque doublon de l'autre.
    return norm_key(t)


def _norm_venue(v: str) -> str:
    return norm_key(v)


def _title_similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return 0.0                 # un titre vide ne ressemble a rien, pas meme a un autre vide
    return SequenceMatcher(None, na, nb).ratio()


def _sim(na: str, nb: str) -> float:
    """Ratio de similarité entre deux titres DÉJÀ normalisés, avec arrêt anticipé :
    `quick_ratio` est une borne supérieure peu coûteuse de `ratio`."""
    from difflib import SequenceMatcher
    if not na or not nb:
        return 0.0
    sm = SequenceMatcher(None, na, nb)
    if sm.real_quick_ratio() < 0.85 or sm.quick_ratio() < 0.85:
        return 0.0
    return sm.ratio()


# Priorite de chaque source (registry.SourceSpec.priority), posee par main au
# demarrage : un enregistrement plus ancien d'une source moins fiable ne peut pas
# l'emporter sur l'information plus recente d'une source officielle (13.6).
_PRIORITY: dict[str, int] = {}


def set_priorities(mapping: dict[str, int]) -> None:
    _PRIORITY.clear()
    _PRIORITY.update(mapping)


def _priority(source: str) -> int:
    return _PRIORITY.get(source, 0)


# Priorité par champ en cas de fusion (passe forte uniquement) — la source
# la plus riche gagne le champ. Un seul nom par source réelle aujourd'hui ;
# étendre le tuple au fil de l'ajout de nouvelles sources (régional, Agenda
# Culturel) ne touche que cette table.
_FIELD_PRIORITY = {
    "price_type": ("qfap",),
    "geo": ("ficep", "qfap"),
    "description": ("qfap",),
}


def _source_family(source: str) -> str:
    if source == "qfap":
        return "qfap"
    if "agendas/61665301" in source:   # FICEP via OpenAgenda, cf. mapping-ficep.md
        return "ficep"
    return source


def _merge_fields(con, cluster: list[dict]) -> int:
    """Complète le survivant selon la priorité par champ. Retourne son id.

    Ne touche jamais `url` : ni QFAP ni FICEP ne sont « le site du lieu »
    (l'un scrape la Ville de Paris, l'autre agrège via OpenAgenda) — la règle
    « l'url d'origine du lieu, jamais l'agrégateur » n'a pas encore de
    candidat à qui s'appliquer.
    """
    by_family: dict[str, dict] = {}
    for r in cluster:
        by_family.setdefault(_source_family(r["source"]), r)
    anchor = by_family.get("qfap") or min(cluster, key=lambda r: r["id"])

    price = next((by_family[f]["price_type"] for f in _FIELD_PRIORITY["price_type"]
                 if f in by_family and by_family[f]["price_type"] != "unknown"),
                anchor["price_type"])
    geo_row = next((by_family[f] for f in _FIELD_PRIORITY["geo"]
                    if f in by_family and by_family[f]["lat"] is not None), None)
    lat, lon = (geo_row["lat"], geo_row["lon"]) if geo_row else (anchor["lat"], anchor["lon"])
    desc = next((by_family[f]["description"] for f in _FIELD_PRIORITY["description"]
                if f in by_family and by_family[f]["description"]), anchor["description"])

    con.execute(
        "UPDATE events SET price_type = ?, lat = ?, lon = ?, description = ? WHERE id = ?",
        (price, lat, lon, desc, anchor["id"]),
    )
    return anchor["id"]


def _lang_script(title: str) -> str:
    from textnorm import has_arabic
    return "ar" if has_arabic(title) else "latin"


def _same_place(x: dict, y: dict, max_m: float = 150.0) -> bool:
    if x.get("venue_id") and x.get("venue_id") == y.get("venue_id"):
        return True
    from db import _haversine_km
    if None in (x["lat"], y["lat"]):
        return False
    d = _haversine_km(x["lat"], x["lon"], y["lat"], y["lon"])
    return d is not None and d * 1000 <= max_m


def dedup_inter_source(con, *, now: datetime | None = None) -> dict[str, int]:
    """Rapproche les enregistrements de sources differentes qui decrivent le
    meme evenement, VILLE PAR VILLE. Quatre passes :

      1. exacte - meme (source, source_id) : deja garanti par la contrainte
         UNIQUE de la table, rien a faire ici.
      2. forte  - titres similaires a >= 0.85, meme creneau a +/- 30 min, et
         < 150 m (ou geoloc manquante d'un cote, auquel cas on ne peut pas
         infirmer le rapprochement). Fusion de champs par priorite en plus
         du lien : c'est la seule passe qui "fusionne", au sens du paquet.
      3. faible - titres similaires a >= 0.92, meme jour, meme lieu normalise.
         Lien seulement, aucune fusion de champs - "a marquer plutot qu'a
         fusionner" : la confiance est plus faible, on ne reecrit rien.
      4. traduction - meme creneau (+/- 30 min), meme lieu (referentiel ou
         < 150 m), meme categorie, mais titres d'ECRITURES differentes (arabe /
         latin) : c'est le meme evenement vu en deux langues (13.42). Le titre
         de l'autre langue est range dans `i18n` du survivant.

    Ne supprime jamais. Reinitialise `doublon_de` sur les evenements a venir
    avant de le recalculer entierement a chaque cycle, pour qu'un changement
    de priorite ou une source qui disparait ne laisse pas de lien orphelin.
    """
    import json
    from cities import get as _city
    from db import _haversine_km, _iso

    now = now or datetime.now(timezone.utc)
    horizon = _iso(now - timedelta(days=1))
    rows = [dict(r) for r in con.execute(
        "SELECT id, source, source_id, title, start, lat, lon, venue, venue_id, "
        "description, price_type, url, city_id, category, lang, i18n, status "
        "FROM events WHERE start >= ?",
        (horizon,),
    )]
    con.execute("UPDATE events SET doublon_de = NULL, dedup_reason = NULL WHERE start >= ?", (horizon,))

    by_day: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        # Normalisé UNE fois par ligne (pas une fois par comparaison) : c'est ce qui
        # coûtait l'essentiel du temps du cycle (7 s pour 16 000 lignes, bien plus sur la VM).
        r["_nt"] = _norm_title(r["title"])
        r["_nv"] = _norm_venue(r["venue"]) if r["venue"] else ""
        r["_ts"] = datetime.fromisoformat(r["start"]).timestamp()
        by_day[(r["city_id"], r["start"][:10])].append(r)

    counts = {"forte": 0, "faible": 0, "traduction": 0}
    for day_rows in by_day.values():
        clusters: list[list[dict]] = []
        kinds: list[str] = []
        for r in day_rows:
            placed = False
            for idx, cluster in enumerate(clusters):
                anchor = cluster[0]
                if anchor["source"] == r["source"]:
                    continue                       # meme source : gere par UNIQUE
                # Filtres bon marché d'abord : sans créneau proche NI même lieu, aucune des
                # deux passes ne peut aboutir — inutile de comparer les titres.
                dt = abs(anchor["_ts"] - r["_ts"])
                same_venue = bool(anchor["_nv"] and r["_nv"] and anchor["_nv"] == r["_nv"])
                if dt > 1800 and not same_venue:
                    continue
                sim = _sim(anchor["_nt"], r["_nt"])
                if sim < 0.85:
                    continue
                forte = False
                if sim >= 0.85 and dt <= 1800:
                    if anchor["lat"] is None or r["lat"] is None:
                        forte = True
                    else:
                        d = _haversine_km(anchor["lat"], anchor["lon"], r["lat"], r["lon"])
                        forte = d is not None and d * 1000 <= 150
                faible = sim >= 0.92 and same_venue
                if not (forte or faible):
                    continue
                cluster.append(r)
                if forte:
                    kinds[idx] = "forte"
                placed = True
                break
            if not placed:
                clusters.append([r])
                kinds.append("faible")   # cluster a un seul element : sans effet

        # --- passe 4 : traductions entre clusters restes seuls ----------------
        singles = [c[0] for c in clusters if len(c) == 1]
        linked: set[int] = set()
        for i, x in enumerate(singles):
            if x["id"] in linked:
                continue
            for y in singles[i + 1:]:
                if y["id"] in linked or x["source"] == y["source"]:
                    continue
                if _lang_script(x["title"]) == _lang_script(y["title"]):
                    continue
                if x["category"] != y["category"]:
                    continue
                dt = abs((datetime.fromisoformat(x["start"]) - datetime.fromisoformat(y["start"])).total_seconds())
                if dt > 1800 or not _same_place(x, y):
                    continue
                c = _city(x["city_id"])
                default = c.default_language if c else "en"
                keep, other = (x, y) if (_lang_script(x["title"]) == "ar") == (default == "ar") else (y, x)
                lg = "ar" if _lang_script(other["title"]) == "ar" else "en"
                i18n = json.loads(keep["i18n"]) if keep.get("i18n") else {}
                i18n.setdefault(lg, {"title": other["title"], "description": other["description"]})
                con.execute("UPDATE events SET i18n = ? WHERE id = ?",
                            (json.dumps(i18n, ensure_ascii=False), keep["id"]))
                con.execute("UPDATE events SET doublon_de = ?, dedup_reason = 'traduction' WHERE id = ?",
                            (keep["id"], other["id"]))
                counts["traduction"] += 1
                linked.update((x["id"], y["id"]))
                break

        for cluster, kind in zip(clusters, kinds):
            if len(cluster) < 2:
                continue
            counts[kind] += len(cluster) - 1
            # Le survivant : la source de plus haute priorite, puis le plus ancien enregistrement.
            top = max(cluster, key=lambda r: (_priority(r["source"]), -r["id"]))
            if kind == "forte":
                merged_id = _merge_fields(con, cluster)
                merged = next(r for r in cluster if r["id"] == merged_id)
                winner_id = top["id"] if _priority(top["source"]) > _priority(merged["source"]) else merged_id
            else:
                winner_id = top["id"]
            # Un "annule" d'une source au moins aussi prioritaire que le survivant gagne.
            w = next(r for r in cluster if r["id"] == winner_id)
            neg = [r for r in cluster if r["status"] in ("cancelled", "postponed")
                   and _priority(r["source"]) >= _priority(w["source"])]
            if neg:
                con.execute("UPDATE events SET status = ? WHERE id = ?", (neg[0]["status"], winner_id))
            for r in cluster:
                if r["id"] != winner_id:
                    con.execute("UPDATE events SET doublon_de = ?, dedup_reason = ? WHERE id = ?",
                                (winner_id, kind, r["id"]))

    return counts
