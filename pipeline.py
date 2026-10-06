"""Normalisation commune à toutes les sources et à toutes les villes.

Un connecteur produit des `Event` bruts ; `normalize()` en fait des événements
du schéma commun (13.4), sûrs à stocker et à afficher :

  1. texte nettoyé (safety.clean_text), URL validées (safety.safe_url) ;
  2. ville, pays, devise renseignés depuis la configuration de la ville ;
  3. lieu rattaché au référentiel (venues/) ; coordonnées complétées SI la
     source n'en donne pas ;
  4. coordonnées invraisemblables (hors de la ville) écartées et comptées ;
  5. statut déduit (annulé / reporté) ;
  6. versions arabe et anglaise d'un même événement fusionnées en UN événement.

Tout ce qui est écarté est compté, jamais perdu en silence : le compte-rendu
alimente /health.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import cities as cities_mod
import venues
from db import Event
from safety import clean_text, safe_url
from textnorm import has_arabic, norm_key

_CANCELLED = re.compile(r"(^|\W)(cancel+ed|cancellation|annul[ée]e?|ملغ[يى]|ملغاة|إلغاء)(\W|$)", re.I)
_POSTPONED = re.compile(r"(^|\W)(postponed|reschedul\w*|report[ée]e?|مؤجل\w*|تأجيل)(\W|$)", re.I)

STATUSES = ("active", "cancelled", "postponed", "expired", "stale")

# Mise en quarantaine d'un lot anormal (14.6). Règles déterministes, sans LLM, volontairement larges :
# elles ne doivent attraper qu'un parseur cassé ou une source qui dérape, pas une saison creuse.
QUARANTINE_MIN_PREV = 20          # en dessous, la source est trop petite pour juger un « effondrement »
QUARANTINE_DROP_RATIO = 0.3       # un lot à moins de 30 % du précédent
QUARANTINE_GEO_RATIO = 0.6        # plus de 60 % sans coordonnées alors que le lot précédent allait bien (< 30 %)
QUARANTINE_MAX_STREAK = 3         # après 3 lots écartés de suite, le suivant est accepté : c'est la nouvelle normale


def batch_anomaly(prev_valid: int, prev_geo_missing: int, valid: int, geo_missing: int) -> str | None:
    """Raison pour laquelle un lot est suspect, ou None s'il est normal.

    Un lot vide n'est pas jugé ici (déjà traité : purge annulée, dernier contenu conservé). La
    quarantaine ne supprime rien : le lot n'est simplement pas écrit, l'ancien contenu reste servi."""
    if valid <= 0:
        return None
    if prev_valid >= QUARANTINE_MIN_PREV and valid < prev_valid * QUARANTINE_DROP_RATIO:
        return f"volume : {valid} événements valides contre {prev_valid} au cycle précédent"
    if valid >= 10 and geo_missing / valid > QUARANTINE_GEO_RATIO \
            and (prev_valid < 10 or prev_geo_missing / prev_valid < 0.3):
        return f"géocodage : {geo_missing}/{valid} sans coordonnées (cycle précédent : {prev_geo_missing}/{prev_valid})"
    return None
MAX_TITLE = 300
MAX_DESC = 600


def infer_status(title: str | None, current: str = "active") -> str:
    """Le statut que dit le TITRE (« [ANNULÉ] … », « Postponed »). On ne lit pas la
    description : elle parle souvent d'une édition passée."""
    if current != "active":
        return current
    t = title or ""
    if _CANCELLED.search(t):
        return "cancelled"
    if _POSTPONED.search(t):
        return "postponed"
    return "active"


def _lang_of(e: Event) -> str:
    if e.lang:
        return e.lang
    return "ar" if has_arabic(e.title) else "en"


def normalize(events: list[Event], city_id: str) -> tuple[list[Event], Counter]:
    """Renvoie (événements valides, compteurs d'écarts)."""
    city = cities_mod.get(city_id)
    dropped: Counter = Counter()
    out: list[Event] = []
    for e in events:
        title = clean_text(e.title, MAX_TITLE)
        if not title:
            dropped["sans_titre"] += 1
            continue
        e.title = title
        e.description = clean_text(e.description, MAX_DESC)
        e.venue = clean_text(e.venue, 200)
        e.address = clean_text(e.address, 300)
        e.url = safe_url(e.url)
        e.booking_url = safe_url(e.booking_url)
        e.city_id = city_id
        if city:
            e.country_code = e.country_code or city.country
            e.currency = e.currency or (city.currency if (e.price_min or e.price_max) else None)
        e.lang = _lang_of(e) if (city and len(city.languages) > 1) else e.lang
        e.status = infer_status(e.title, e.status or "active")

        v = venues.resolve(city_id, e.venue)
        if v:
            e.venue_id = v.id
            e.venue = v.name(e.lang)
            e.address = e.address or v.address
            if e.lat is None or e.lon is None:
                e.lat, e.lon, e.geo_source = v.lat, v.lon, "venue_ref"
        if e.lat is not None and (e.geo_source is None):
            e.geo_source = "source"

        if e.lat is not None and e.lon is not None:
            ok = -90 <= e.lat <= 90 and -180 <= e.lon <= 180 and not (e.lat == 0 and e.lon == 0)
            if ok and city:
                far = cities_mod._haversine_km(e.lat, e.lon, city.center[0], city.center[1])
                ok = far <= max(40.0, city.max_radius_km * 3)
            if not ok:
                dropped["hors_ville"] += 1
                continue
        else:
            pass   # sans coordonnées : GARDÉ (invisible sur la carte, compté par health)
        out.append(e)
    return merge_translations(out), dropped


def merge_translations(events: list[Event]) -> list[Event]:
    """Fusionne les versions linguistiques d'un même événement d'une même source.

    Une source qui publie `/ar/…` et `/en/…` produit deux `Event` de même
    (source, source_id, start) — ils s'écraseraient l'un l'autre dans la base. On
    garde la version dans la langue par défaut de la ville comme base, et les
    autres dans `i18n`. Les titres et descriptions de la SOURCE sont conservés tels
    quels : rien n'est traduit automatiquement.
    """
    groups: dict[tuple, list[Event]] = defaultdict(list)
    for e in events:
        groups[(e.city_id, e.source, e.source_id, e.start)].append(e)
    out: list[Event] = []
    for (_, _, _, _), g in groups.items():
        if len(g) == 1:
            out.append(g[0])
            continue
        city = cities_mod.get(g[0].city_id)
        default = city.default_language if city else "en"
        g.sort(key=lambda x: (_lang_of(x) != default,))
        base = g[0]
        i18n = dict(base.i18n or {})
        for other in g[1:]:
            lg = _lang_of(other)
            if lg == _lang_of(base):
                continue
            i18n[lg] = {"title": other.title, "description": other.description}
            base.lat = base.lat if base.lat is not None else other.lat
            base.lon = base.lon if base.lon is not None else other.lon
            base.url = base.url or other.url
            base.booking_url = base.booking_url or other.booking_url
        base.i18n = i18n or None
        out.append(base)
    return out


def venue_key(e_venue: str | None, e_venue_id: str | None) -> str:
    return e_venue_id or norm_key(e_venue)
