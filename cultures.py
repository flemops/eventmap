"""Culture d'origine attachée aux LIEUX, pas aux événements.

Aucune source ne fournit ce champ (28 agrégateurs sondés le 02/09/2026, zéro
facette culturelle). On le pose donc à la main, lieu par lieu, dans
`cultures.yaml` — un lieu mono-culturel garantit la culture de sa programmation.

La culture est un enrichissement de la réponse, pas un filtre d'entrée : voir
l'en-tête de `cultures.yaml` pour le raisonnement.

Le fichier est relu à chaud (mtime) : corriger une fiche ne demande pas de
redémarrer le service.
"""

from __future__ import annotations

import logging
import os
import unicodedata
from pathlib import Path

import yaml

log = logging.getLogger("eventmap.cultures")

CULTURES_PATH = Path(os.environ.get("EVENTMAP_CULTURES", "cultures.yaml"))

_cache: dict | None = None
_cache_mtime: float | None = None


def _norm(venue: str) -> str:
    """Clé d'appariement d'un nom de lieu.

    Insensible à la casse, aux accents et aux espaces multiples. Ce n'est pas
    de la coquetterie : la base contient « Institut suédois » (84 créneaux) et
    « Institut Suédois » (53) — le même lieu sous deux orthographes. Sans
    normalisation on en perd la moitié, silencieusement.
    """
    s = unicodedata.normalize("NFKD", venue)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _load() -> dict:
    """Charge le fichier, en le relisant s'il a changé sur le disque."""
    global _cache, _cache_mtime
    try:
        mtime = CULTURES_PATH.stat().st_mtime
    except OSError:
        # Pas de fichier = pas de culture. Le reste d'EventMap fonctionne.
        if _cache is None:
            log.info("%s absent : aucune culture chargée", CULTURES_PATH)
            _cache, _cache_mtime = {"by_venue": {}, "cultures": {}}, None
        return _cache

    if _cache is not None and _cache_mtime == mtime:
        return _cache

    raw = yaml.safe_load(CULTURES_PATH.read_text(encoding="utf-8")) or {}
    cultures = raw.get("cultures") or {}
    by_venue: dict[str, dict] = {}

    for lieu in raw.get("lieux") or []:
        nom, cle = lieu.get("nom"), lieu.get("culture")
        if not nom or not cle:
            log.warning("lieu ignoré (nom ou culture manquant) : %r", lieu)
            continue
        if cle not in cultures:
            # Une faute de frappe dans `culture:` produirait un lieu tagué
            # avec une culture qui n'existe pas — silencieux et faux.
            log.warning("lieu %r : culture inconnue %r, ignoré", nom, cle)
            continue
        by_venue[_norm(nom)] = {
            "cle": cle,
            "nom": cultures[cle].get("nom", cle),
            "lieu": nom,
            "fiche": lieu.get("fiche") or {},
        }

    _cache, _cache_mtime = {"by_venue": by_venue, "cultures": cultures}, mtime
    log.info("cultures : %d lieux, %d cultures", len(by_venue), len(cultures))
    return _cache


def for_venue(venue: str | None) -> dict | None:
    """La culture d'un lieu, ou None s'il n'est pas mono-culturel."""
    if not venue:
        return None
    return _load()["by_venue"].get(_norm(venue))


def venues_for(culture: str) -> list[str]:
    """Les noms de lieux d'une culture, tels qu'écrits dans le YAML.

    Sert à filtrer en SQL. L'appariement en base doit rester insensible à la
    casse (voir `_norm`) — d'où le `lower()` côté requête.
    """
    return [v["lieu"] for v in _load()["by_venue"].values() if v["cle"] == culture]


def all_cultures() -> list[dict]:
    """Inventaire, avec le nombre de lieux — pour /api/cultures."""
    data = _load()
    counts: dict[str, int] = {}
    for v in data["by_venue"].values():
        counts[v["cle"]] = counts.get(v["cle"], 0) + 1
    return [
        {"cle": cle, "nom": meta.get("nom", cle), "lieux": counts.get(cle, 0)}
        for cle, meta in data["cultures"].items()
    ]
