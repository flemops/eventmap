"""Référentiel de lieux par ville (venues/<ville>.yaml).

Un lieu a UNE identité (`id`) et plusieurs écritures (nom arabe, nom anglais,
alias). `resolve()` rattache le texte brut d'une source à cette identité : deux
écritures du même lieu deviennent le même objet, ce qui permet de dédupliquer un
événement publié en arabe par une source et en anglais par une autre.

Les coordonnées viennent du référentiel (OpenStreetMap, ODbL) et ne servent qu'à
COMPLÉTER un événement dont la source n'en donne pas (`geo_source='venue_ref'`),
jamais à écraser celles d'une source.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from textnorm import norm_key

VENUES_DIR = Path(os.environ.get("EVENTMAP_VENUES", Path(__file__).resolve().parent / "venues"))


@dataclass(frozen=True)
class Venue:
    id: str
    city_id: str
    name_en: str
    name_ar: str | None
    lat: float
    lon: float
    district: str | None = None
    address: str | None = None
    osm: str | None = None

    def name(self, lang: str | None) -> str:
        if lang == "ar" and self.name_ar:
            return self.name_ar
        return self.name_en


_index: dict[str, dict[str, Venue]] | None = None


def _build(directory: Path) -> dict[str, dict[str, Venue]]:
    out: dict[str, dict[str, Venue]] = {}
    if not directory.is_dir():
        return out
    for f in sorted(directory.glob("*.yaml")):
        spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        city = spec["city"]
        idx = out.setdefault(city, {})
        for v in spec.get("venues", []):
            venue = Venue(
                id=v["id"], city_id=city, name_en=v["name_en"], name_ar=v.get("name_ar"),
                lat=float(v["lat"]), lon=float(v["lon"]), district=v.get("district"),
                address=v.get("address"), osm=v.get("osm"),
            )
            for raw in [v["name_en"], v.get("name_ar"), *(v.get("aliases") or [])]:
                k = norm_key(raw)
                if not k:
                    continue
                if k in idx and idx[k].id != venue.id:
                    raise ValueError(f"{f.name}: l'alias {raw!r} désigne deux lieux "
                                     f"({idx[k].id} et {venue.id})")
                idx[k] = venue
    return out


def load(force: bool = False) -> dict[str, dict[str, Venue]]:
    global _index
    if _index is None or force:
        _index = _build(VENUES_DIR)
    return _index


def resolve(city_id: str, raw_name: str | None) -> Venue | None:
    """Le lieu canonique pour cette écriture, ou None. Comparaison exacte sur la
    clé normalisée : pas de correspondance « floue », un faux rapprochement de lieu
    est pire qu'un lieu non reconnu."""
    k = norm_key(raw_name)
    return load().get(city_id, {}).get(k) if k else None


def by_id(city_id: str, venue_id: str | None) -> Venue | None:
    if not venue_id:
        return None
    return next((v for v in all_for(city_id) if v.id == venue_id), None)


def all_for(city_id: str) -> list[Venue]:
    return list({v.id: v for v in load().get(city_id, {}).values()}.values())
