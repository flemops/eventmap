"""Configuration par ville (cities.yaml) et interrupteurs d'exploitation.

Le code ne connaît aucune ville par son nom : tout ce qui varie d'une ville à
l'autre (fuseau, devise, langues, jours de week-end, centre de carte, sources)
vit dans cities.yaml. Ajouter une ville ne demande aucune modification ici.

Interrupteurs (variables d'environnement, lues à CHAQUE appel — un
`systemctl restart` suffit, jamais un redéploiement) :

    EVENTMAP_CITIES_ENABLED=jeddah      allume une ville éteinte dans le YAML
    EVENTMAP_CITIES_DISABLED=jeddah     éteint une ville ; GAGNE toujours
    EVENTMAP_SOURCES_DISABLED=ficep     éteint une source (id de feeds.yaml)
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

CITIES_FILE = Path(os.environ.get("EVENTMAP_CITIES", Path(__file__).resolve().parent / "cities.yaml"))
DEFAULT_CITY = "paris"

_cache: dict[str, "City"] | None = None


@dataclass(frozen=True)
class City:
    id: str
    country: str
    names: dict
    timezone: str
    currency: str
    languages: tuple
    default_language: str
    center: tuple          # (lat, lon)
    zoom: int
    radius_options_km: tuple
    default_radius_km: float
    max_radius_km: float
    weekend_days: tuple
    night_cutoff_hour: int
    home: str
    default_price: str
    features: dict
    stale_after_hours: int
    sources: tuple
    category_groups: dict
    enabled_in_file: bool
    teaser: bool = False   # ville éteinte annoncée « Coming soon » dans le sélecteur (aucune donnée, aucune API)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def enabled(self) -> bool:
        return is_enabled(self)

    def name(self, lang: str | None = None) -> str:
        return self.names.get(lang or self.default_language) or self.names.get("en") or self.id

    def public(self) -> dict:
        """Ce que le front a le droit de savoir. Jamais de chemin ni de secret."""
        return {
            "id": self.id,
            "country": self.country,
            "names": self.names,
            "timezone": self.timezone,
            "currency": self.currency,
            "languages": list(self.languages),
            "default_language": self.default_language,
            "center": {"lat": self.center[0], "lon": self.center[1]},
            "zoom": self.zoom,
            "radius_options_km": list(self.radius_options_km),
            "default_radius_km": self.default_radius_km,
            "max_radius_km": self.max_radius_km,
            "weekend_days": list(self.weekend_days),
            "home": self.home,
            "default_price": self.default_price,
            "features": self.features,
            "category_groups": {k: list(v) for k, v in self.category_groups.items()},
        }


def _csv_env(name: str) -> set[str]:
    return {x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()}


def is_enabled(city: City) -> bool:
    if city.id in _csv_env("EVENTMAP_CITIES_DISABLED"):
        return False
    return city.enabled_in_file or city.id in _csv_env("EVENTMAP_CITIES_ENABLED")


def source_enabled(source_id: str) -> bool:
    return source_id.lower() not in _csv_env("EVENTMAP_SOURCES_DISABLED")


def _parse(raw: dict) -> dict[str, City]:
    out: dict[str, City] = {}
    for cid, c in (raw.get("cities") or {}).items():
        wd = tuple(int(d) for d in c.get("weekend_days", (5, 6)))
        if len(wd) != 2 or (wd[1] - wd[0]) != 1:
            raise ValueError(f"ville {cid}: weekend_days doit être deux jours consécutifs, reçu {wd}")
        langs = tuple(c.get("languages") or ["en"])
        default_lang = c.get("default_language") or langs[0]
        if default_lang not in langs:
            raise ValueError(f"ville {cid}: default_language {default_lang!r} absent de languages")
        cut = int(c.get("night_cutoff_hour", 0))
        if not 0 <= cut <= 8:
            raise ValueError(f"ville {cid}: night_cutoff_hour hors de 0..8")
        mc = c["map_center"]
        out[cid] = City(
            id=cid, country=c["country"], names=dict(c.get("names") or {}),
            timezone=c["timezone"], currency=c["currency"], languages=langs,
            default_language=default_lang, center=(float(mc["lat"]), float(mc["lon"])),
            zoom=int(c.get("default_zoom", 12)),
            radius_options_km=tuple(c.get("radius_options_km") or (2, 5, 10)),
            default_radius_km=float(c.get("default_radius_km", 2)),
            max_radius_km=float(c.get("max_radius_km", 10)),
            weekend_days=wd, night_cutoff_hour=cut,
            home=c.get("home", "carte"), default_price=c.get("default_price") or "",
            features=dict(c.get("features") or {}),
            stale_after_hours=int(c.get("stale_after_hours", 48)),
            sources=tuple(c.get("sources") or ()),
            category_groups={k: tuple(v) for k, v in (c.get("category_groups") or {}).items()},
            enabled_in_file=bool(c.get("enabled", False)), teaser=bool(c.get("teaser", False)),
        )
        ZoneInfo(c["timezone"])   # échoue tôt si le fuseau est inconnu
    if DEFAULT_CITY not in out:
        raise ValueError(f"{CITIES_FILE}: la ville par défaut {DEFAULT_CITY!r} est absente")
    return out


def load(force: bool = False) -> dict[str, City]:
    global _cache
    if _cache is None or force:
        _cache = _parse(yaml.safe_load(CITIES_FILE.read_text(encoding="utf-8")) or {})
    return _cache


def get(city_id: str | None) -> City | None:
    """La ville déclarée (allumée ou non). None si inconnue."""
    return load().get((city_id or DEFAULT_CITY).lower())


def active(city_id: str | None) -> City | None:
    """La ville si elle est déclarée ET allumée — sinon None. C'est ce que les
    routes publiques doivent utiliser : jamais `get`."""
    c = get(city_id)
    return c if c and c.enabled else None


def all_active() -> list[City]:
    return [c for c in load().values() if c.enabled]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def nearest_active(lat: float, lon: float, within_km: float = 60.0) -> City | None:
    """La ville allumée dont le centre est à moins de `within_km`, si UNE SEULE
    l'est. Deux candidates ou aucune : on ne devine pas (13.16)."""
    close = [c for c in all_active()
             if _haversine_km(lat, lon, c.center[0], c.center[1]) <= within_km]
    return close[0] if len(close) == 1 else None
