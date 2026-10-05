"""Registre des sources : qui alimente quelle ville, avec quelle autorisation et
à quelle cadence.

Une source n'est interrogée que si TOUT ceci est vrai :
  1. `enabled` dans feeds.yaml ;
  2. `authorization: ok` — la réutilisation a été vérifiée (licence, CGU,
     robots) ou accordée par écrit. `pending` / `refused` ne tournent JAMAIS,
     même si `enabled: true` (13.7) ;
  3. la ville de la source est allumée (cities.yaml / interrupteurs) ;
  4. elle n'est pas dans EVENTMAP_SOURCES_DISABLED (13.54).

Politique d'ingestion par source (13.45) : `refresh_hours` (cadence : on ne la
réinterroge pas plus souvent), `timeout_s`, `retries` (avec attente
exponentielle), `min_interval_s` (délai entre deux requêtes sur le même
domaine).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import cities

FEEDS_FILE = Path(os.environ.get("EVENTMAP_FEEDS", Path(__file__).resolve().parent / "feeds.yaml"))

AUTHORIZATIONS = ("ok", "pending", "refused")


@dataclass(frozen=True)
class SourceSpec:
    id: str
    name: str
    city_id: str
    kind: str                      # qfap | ics | jsonld | openagenda | llm
    url: str | None
    enabled: bool = True
    authorization: str = "ok"
    priority: int = 0              # plus haut = plus fiable ; départage un conflit entre sources
    refresh_hours: float = 6.0
    timeout_s: float = 120.0
    retries: int = 1
    min_interval_s: float = 1.0
    geo_bbox: dict | None = None
    note: str = ""
    attribution: str = ""
    home: str = ""

    @property
    def key(self) -> str:
        """Clé du connecteur dans refresh() : « qfap » ou l'URL du flux, comme avant."""
        return self.url if self.kind != "qfap" else "qfap"

    def runnable(self) -> bool:
        c = cities.active(self.city_id)
        return bool(self.enabled and self.authorization == "ok" and c is not None
                    and cities.source_enabled(self.id))

    def why_not(self) -> str | None:
        if not self.enabled:
            return "désactivée dans feeds.yaml"
        if self.authorization != "ok":
            return f"autorisation « {self.authorization} »"
        if cities.active(self.city_id) is None:
            return f"ville {self.city_id} éteinte"
        if not cities.source_enabled(self.id):
            return "coupée par EVENTMAP_SOURCES_DISABLED"
        return None


QFAP = SourceSpec(
    id="qfap", name="Que faire à Paris — Ville de Paris (ODbL)", city_id="paris",
    kind="qfap", url=None, priority=10, refresh_hours=6, timeout_s=180, retries=1,
    note="Open data de la Ville de Paris, licence ODbL.",
    attribution="Que faire à Paris — Ville de Paris (ODbL)",
    home="https://opendata.paris.fr/explore/dataset/que-faire-a-paris-/",
)


def load(path: Path | None = None) -> list[SourceSpec]:
    specs = [QFAP]
    f = path or FEEDS_FILE
    if f.exists():
        raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for i, feed in enumerate(raw.get("feeds", [])):
            auth = feed.get("authorization", "ok")
            if auth not in AUTHORIZATIONS:
                raise ValueError(f"{f}: feed #{i}: authorization {auth!r} invalide")
            fid = feed.get("id") or feed["url"]
            specs.append(SourceSpec(
                id=fid, name=feed.get("name") or fid, city_id=feed.get("city", "paris"),
                kind=feed.get("type", "ics"), url=feed["url"],
                enabled=bool(feed.get("enabled", True)), authorization=auth,
                priority=int(feed.get("priority", 0)),
                refresh_hours=float(feed.get("refresh_hours", 6)),
                timeout_s=float(feed.get("timeout_s", 120)),
                retries=int(feed.get("retries", 1)),
                min_interval_s=float(feed.get("min_interval_s", 1.0)),
                geo_bbox=feed.get("geo_bbox"), note=feed.get("note", "") or "",
                attribution=feed.get("attribution", "") or "", home=feed.get("home", "") or "",
            ))
    return specs


def validate(specs: list[SourceSpec]) -> list[str]:
    """Incohérences entre feeds.yaml et cities.yaml (renvoie des messages, [] si OK)."""
    problems: list[str] = []
    known = cities.load()
    ids = [s.id for s in specs]
    for dup in {i for i in ids if ids.count(i) > 1}:
        problems.append(f"identifiant de source en double: {dup}")
    for s in specs:
        if s.city_id not in known:
            problems.append(f"source {s.id}: ville inconnue {s.city_id!r}")
    for c in known.values():
        for sid in c.sources:
            if sid not in ids:
                problems.append(f"ville {c.id}: source {sid!r} absente de feeds.yaml")
    return problems


def by_city(specs: list[SourceSpec], city_id: str) -> list[SourceSpec]:
    return [s for s in specs if s.city_id == city_id]
