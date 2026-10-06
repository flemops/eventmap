"""Pages HTML : choix de ville, accueil de ville, carte, fiche d'événement.

Les pages sont des gabarits statiques (static/*.html) dans lesquels des jetons
`%%NOM%%` sont remplacés ici. Pourquoi côté serveur :

* SEO / GEO (13.40) — titre, description, canonical, hreflang, JSON-LD et une
  liste d'événements en HTML pur sont dans la RÉPONSE, pas construits par un
  JavaScript que les moteurs peuvent ne pas exécuter ;
* partage (13.39) — les aperçus de lien lisent les balises `og:` et jamais le
  corps de la page ;
* aucun script en ligne : la CSP est `script-src 'self'`. La configuration de la
  ville passe dans un bloc `<script type="application/json">`, qui n'est pas
  exécutable donc non soumis à la CSP.

Toute valeur issue des sources est échappée ici (html.escape) ET a déjà été
nettoyée à l'ingestion (safety.clean_text / safe_url).
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from fastapi.responses import HTMLResponse, RedirectResponse

import cities
import db
import i18n_server as i18n
import registry
import timewin
from safety import safe_url

STATIC = Path(__file__).resolve().parent / "static"
PAGE_ID = re.compile(r"^[0-9]{1,12}$")


@lru_cache(maxsize=8)
def _template(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _json_block(obj) -> str:
    """JSON sûr à poser dans un <script> : `</script>` et `<!--` neutralisés."""
    # <, > et & en \uXXXX : le JSON reste identique pour qui le parse, et aucune
    # séquence (</script>, <!--, <script>) ne peut plus exister dans le bloc.
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


@lru_cache(maxsize=1)
def asset_version() -> str:
    """Empreinte des fichiers statiques : change à chaque déploiement qui les modifie.
    Cloudflare met le JS/CSS en cache plusieurs heures — sans `?v=` un correctif
    n'atteint personne (même piège que le portfolio)."""
    import hashlib
    h = hashlib.sha1()
    for f in sorted(STATIC.glob("*")):
        if f.suffix in (".js", ".css"):
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


def footer_html(city: cities.City, lang: str = "fr") -> str:
    s = i18n.strings(lang)
    srcs = [s for s in registry.by_city(registry.load(), city.id) if s.runnable() and s.attribution]
    links = " · ".join(f'<a href="{_esc(s.home)}" rel="noopener">{_esc(s.attribution)}</a>' if s.home
                       else _esc(s.attribution) for s in srcs)
    osm = '<a href="https://www.openstreetmap.org/copyright" rel="noopener">OpenStreetMap</a>'
    colon = " : " if lang == "fr" else ": "
    return (f"{s['sources']}{colon}{links}. " if links else "") + f"{s['maps']} © {osm}."


def _fill(tpl: str, tokens: dict[str, str]) -> str:
    out = tpl
    for k, v in tokens.items():
        out = out.replace(f"%%{k}%%", v)
    return out


# ------------------------------------------------------------------ URLs

def city_path(city: cities.City, lang: str | None = None, suffix: str = "") -> str:
    """URL stable d'une page de ville. La langue par défaut n'a pas de préfixe."""
    pre = f"/{lang}" if lang and lang != city.default_language else ""
    return f"{pre}/{city.id}{suffix}"


def _alternates(site: str, city: cities.City, suffix: str) -> str:
    links = [f'<link rel="alternate" hreflang="{_esc(lg)}" href="{site}{city_path(city, lg, suffix)}">'
             for lg in city.languages]
    links.append(f'<link rel="alternate" hreflang="x-default" href="{site}{city_path(city, None, suffix)}">')
    return "\n".join(links) if len(city.languages) > 1 else ""


def _locale(lang: str, city: cities.City) -> str:
    return {"fr": "fr_FR", "en": "en_GB", "ar": "ar_SA"}.get(lang, lang)


# ------------------------------------------------------------- événements

def _upcoming(city: cities.City, limit: int = 40, now: datetime | None = None) -> list[dict]:
    """Les événements de « ce soir » pour le HTML indexable. Aucune requête n'est
    faite pour une ville sans donnée : la page se rend quand même."""
    now = now or datetime.now(timezone.utc)
    start, end = timewin.window("today", now, city.tz, weekend_days=city.weekend_days,
                                cutoff=city.night_cutoff_hour)
    lat, lon = city.center
    try:
        with db.session() as con:
            return db.search(con, city_id=city.id, lat=lat, lon=lon, radius_km=city.max_radius_km,
                             start_from=start, start_to=end, limit=limit)
    except Exception:  # noqa: BLE001 — une page ne doit jamais tomber faute de base
        return []


def _fmt_dt(iso: str, city: cities.City, lang: str) -> str:
    dt = datetime.fromisoformat(iso).astimezone(city.tz)
    return dt.strftime("%Y-%m-%d %H:%M")


def _event_jsonld(e: dict, city: cities.City, site: str, lang: str) -> dict:
    ld = {
        "@type": "Event",
        "name": e["title"],
        "startDate": datetime.fromisoformat(e["start"]).astimezone(city.tz).isoformat(timespec="minutes"),
        "eventStatus": {"cancelled": "https://schema.org/EventCancelled",
                        "postponed": "https://schema.org/EventPostponed"}.get(
                            e.get("status"), "https://schema.org/EventScheduled"),
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": {"@type": "Place", "name": e.get("venue") or city.name(lang),
                     "address": {"@type": "PostalAddress", "addressLocality": city.name(lang),
                                 "addressCountry": city.country}},
        "url": f"{site}{city_path(city, lang, '/e/' + str(e['id']))}",
    }
    if e.get("end"):
        ld["endDate"] = datetime.fromisoformat(e["end"]).astimezone(city.tz).isoformat(timespec="minutes")
    if e.get("lat") is not None:
        ld["location"]["geo"] = {"@type": "GeoCoordinates", "latitude": e["lat"], "longitude": e["lon"]}
    if e.get("description"):
        ld["description"] = e["description"]
    if e.get("price_type") == "free":
        ld["isAccessibleForFree"] = True
    elif e.get("price_min") is not None and e.get("currency"):
        ld["offers"] = {"@type": "Offer", "price": e["price_min"], "priceCurrency": e["currency"],
                        **({"url": e["booking_url"]} if e.get("booking_url") else {})}
    return ld


def _events_html(events: list[dict], city: cities.City, lang: str) -> str:
    if not events:
        return ""
    items = []
    for e in events:
        when = _fmt_dt(e["start"], city, lang)
        items.append(f'<li><a href="{_esc(city_path(city, lang, "/e/" + str(e["id"])))}">'
                     f'<time datetime="{_esc(e["start"])}">{_esc(when)}</time> '
                     f'{_esc(e["title"])}</a>'
                     f'{" — " + _esc(e["venue"]) if e.get("venue") else ""}</li>')
    return "<ul>" + "".join(items) + "</ul>"


# ------------------------------------------------------------------ pages

def _page(name: str, city: cities.City, lang: str, site: str, *, suffix: str, title: str, desc: str,
          h1: str, extra: dict[str, str] | None = None, events: list[dict] | None = None,
          ld_extra: list[dict] | None = None, canonical_suffix: str | None = None) -> HTMLResponse:
    s = i18n.strings(lang)
    if canonical_suffix == "":
        canon = f"{site}/"               # accueil unique : « / » est l'adresse canonique
    else:
        canon = f"{site}{city_path(city, lang, suffix if canonical_suffix is None else canonical_suffix)}"
    page_ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "@id": f"{site}/#website", "name": "EventMap", "url": f"{site}/",
         "inLanguage": lang},
        {"@type": "WebPage", "@id": f"{canon}#webpage", "name": title, "url": canon, "inLanguage": lang,
         "description": desc, "isPartOf": {"@id": f"{site}/#website"}},
        *(ld_extra or []),
    ]}
    tokens = {
        "LANG": _esc(lang), "DIR": "rtl" if lang == "ar" else "ltr",
        "TITLE": _esc(title), "DESC": _esc(desc), "CANON": _esc(canon), "OGLOCALE": _esc(_locale(lang, city)),
        "H1": _esc(h1), "ALTERNATES": _alternates(site, city, suffix),
        "JSONLD": _json_block(page_ld),
        "CITYJSON": _json_block({"city": city.public(), "lang": lang,
                                 "paths": {"home": city_path(city, lang), "map": city_path(city, lang, "/carte")},
                                 "languages": list(city.languages)}),
        "MAP_URL": _esc(city_path(city, lang, "/carte")), "HOME_URL": _esc(city_path(city, lang)),
        "CITY_NAME": _esc(city.name(lang)),
        "SEO_EVENTS": _events_html(events or [], city, lang),
        "SEO_NOSCRIPT": _esc(s["noscript"]), "V": asset_version(), "FOOTER": footer_html(city, lang),
        **{k: v for k, v in (extra or {}).items()},
    }
    resp = HTMLResponse(_fill(_template(name), tokens))
    return resp


def _home_canonical(city: cities.City, lang: str) -> str | None:
    """Tant qu'UNE SEULE ville est allumée, « / » reste l'adresse canonique de
    son accueil (c'était celle de Paris avant le multi-ville : on ne déplace pas un
    référencement pour rien). Dès qu'il y en a deux, « / » devient le choix de
    ville et chaque accueil prend son adresse propre."""
    if len(cities.all_active()) == 1 and lang == city.default_language:
        return ""
    return None


def _city_home(city: cities.City, lang: str, site: str) -> HTMLResponse:
    s = i18n.strings(lang)
    n = city.name(lang)
    if city.home == "accueil":
        return _page("accueil.html", city, lang, site, suffix="", canonical_suffix=_home_canonical(city, lang),
                     title=s["home_title"].format(city=n), desc=s["home_desc"].format(city=n),
                     h1=s["home_h1"].format(city=n))
    events = _upcoming(city)
    return _page("index.html", city, lang, site, suffix="", canonical_suffix=_home_canonical(city, lang),
                 title=s["map_title"].format(city=n), desc=s["map_desc"].format(city=n),
                 h1=s["tonight_in"].format(city=n), events=events,
                 ld_extra=[_event_jsonld(e, city, site, lang) for e in events[:20]])


def _city_map(city: cities.City, lang: str, site: str) -> HTMLResponse:
    s = i18n.strings(lang)
    n = city.name(lang)
    events = _upcoming(city)
    return _page("index.html", city, lang, site, suffix="/carte",
                 title=s["map_title"].format(city=n), desc=s["map_desc"].format(city=n),
                 h1=s["tonight_in"].format(city=n), events=events,
                 ld_extra=[_event_jsonld(e, city, site, lang) for e in events[:20]])


def _event_page(city: cities.City, lang: str, site: str, event_id: int) -> HTMLResponse | None:
    with db.session() as con:
        row = con.execute("SELECT * FROM events WHERE id = ? AND city_id = ?", (event_id, city.id)).fetchone()
    if row is None:
        return None
    e = dict(row)
    raw = e.get("i18n")
    alts = json.loads(raw) if raw else {}
    if lang in alts and lang != e.get("lang"):
        e["title"] = alts[lang].get("title") or e["title"]
        e["description"] = alts[lang].get("description") or e["description"]
    s = i18n.strings(lang)
    when = _fmt_dt(e["start"], city, lang)
    desc = " · ".join(x for x in (when, e.get("venue"), e.get("description")) if x)[:300]
    canon_suffix = f"/e/{e['id']}"
    # Un doublon écarté renvoie vers son survivant : une seule page canonique par événement.
    if e.get("doublon_de"):
        canon_suffix = f"/e/{e['doublon_de']}"
    parts = [f'<p><time datetime="{_esc(e["start"])}">{_esc(when)}</time>'
             + (" · " + _esc(e["venue"]) if e.get("venue") else "") + "</p>"]
    if e.get("description"):
        parts.append("<p>" + _esc(e["description"]) + "</p>")
    official = safe_url(e.get("url"))
    if official:
        parts.append('<p><a rel="nofollow noopener" href="' + _esc(official) + '">' + _esc(s["official_page"]) + "</a></p>")
    body = "".join(parts)
    return _page("event.html", city, lang, site, suffix=f"/e/{e['id']}", canonical_suffix=canon_suffix,
                 title=f"{e['title']} — {city.name(lang)} — EventMap", desc=desc, h1=e["title"],
                 extra={"EVENT_BODY": body, "EVENT_ID": str(e["id"])},
                 ld_extra=[_event_jsonld({**e, "id": e["id"]}, city, site, lang)])


def city_page(parts: list[str], site: str):
    """Résout un chemin public. Renvoie None (→ 404) si rien ne correspond."""
    parts = [p.lower() for p in parts]
    lang: str | None = None
    # /{lang}/{ville}[/...] : le premier segment est une langue, pas une ville.
    if len(parts) >= 2 and cities.get(parts[0]) is None and cities.get(parts[1]) is not None:
        lang, parts = parts[0], parts[1:]
    city = cities.active(parts[0])
    if city is None or (cities.get(parts[0]) is None):
        return None
    if lang is None:
        lang = city.default_language
    elif lang not in city.languages or lang == city.default_language:
        return None
    rest = parts[1:]
    if not rest:
        return _city_home(city, lang, site)
    if rest == ["carte"]:
        return _city_map(city, lang, site)
    if len(rest) == 2 and rest[0] == "e" and PAGE_ID.match(rest[1]):
        return _event_page(city, lang, site, int(rest[1]))
    return None


def root_page(site: str):
    """`/` : le choix de ville quand il y en a plusieurs ; sinon la ville unique,
    servie directement (rien à choisir) avec sa propre adresse comme canonique."""
    active = cities.all_active()
    if len(active) == 1:
        c = active[0]
        resp = city_page([c.id], site)
        return resp
    s = i18n.strings("en")
    cards = []
    for c in active:
        cards.append(
            f'<a class="city-card" href="{_esc(city_path(c))}" data-city="{_esc(c.id)}">'
            f'<b>{_esc(c.name("en"))}</b>'
            f'<small lang="ar" dir="rtl">{_esc(c.names.get("ar", ""))}</small>'
            f'<span class="go">{_esc(s["explore"])}</span></a>')
    tokens = {
        "CITY_CARDS": "".join(cards),
        "CITYJSON": _json_block({"cities": [c.public() for c in active]}),
        "V": asset_version(), "SITE": site, "TITLE": _esc(s["gate_title"]), "DESC": _esc(s["gate_desc"]),
        "H1": _esc(s["gate_h1"]), "SOON": _esc(s["soon"]), "CANON": f"{site}/",
        "JSONLD": _json_block({"@context": "https://schema.org", "@type": "WebSite", "name": "EventMap",
                               "url": f"{site}/", "inLanguage": "en"}),
    }
    return HTMLResponse(_fill(_template("gate.html"), tokens))


def not_found_page() -> HTMLResponse:
    """404 lisible : on dit ce qui est disponible au lieu d'un JSON brut. Une ville éteinte
    (ou inconnue) arrive ici — elle n'est jamais présentée comme « vide »."""
    s = i18n.strings("en")
    cards = "".join(
        f'<a class="city-card" href="{_esc(city_path(c))}"><b>{_esc(c.name("en"))}</b>'
        f'<span class="go">{_esc(s["explore"])}</span></a>' for c in cities.all_active())
    return HTMLResponse(_fill(_template("notfound.html"), {
        "TITLE": _esc(s["nf_title"]), "MESSAGE": _esc(s["nf_msg"]), "CITY_CARDS": cards, "V": asset_version()}),
        status_code=404)


def sitemap_xml(site: str) -> str:
    """Pages publiques des villes allumées. « / » n'y figure que s'il EST une page
    distincte (le choix de ville) : avec une seule ville il sert son accueil,
    dont l'adresse canonique est « / » elle-même (voir _home_canonical)."""
    active = cities.all_active()
    urls: list[str] = [f"{site}/"]
    for c in active:
        for lg in c.languages:
            urls.append(f"{site}{city_path(c, lg)}")
            urls.append(f"{site}{city_path(c, lg, '/carte')}")
    if len(active) == 1:
        # « /paris » est servi mais son canonique est « / » : on ne liste que le canonique.
        urls = [u for u in urls if u != f"{site}{city_path(active[0], active[0].default_language)}"]
    seen: set[str] = set()
    items = []
    for loc in urls:
        if loc in seen:
            continue
        seen.add(loc)
        items.append(f"<url><loc>{_esc(loc)}</loc><changefreq>daily</changefreq></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(items) + "</urlset>")
