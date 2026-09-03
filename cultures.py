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


def _norm_text(s: str) -> str:
    """Même normalisation que `_norm`, sans compresser les espaces internes —
    sert à chercher un mot-clé dans un titre/description, pas à apparier un
    nom de lieu exact."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def _is_excluded(cle: str, title: str, description: str | None, data: dict) -> bool:
    """Option C (03/09/2026, décision de Hamdy — voir l'en-tête de
    cultures.yaml) : un lieu est admis s'il est *majoritairement* dédié à sa
    culture, et on retire l'attribut événement par événement quand le titre
    ou la description trahit un événement multi-pays — soit un mot-clé de
    `exclusions`, soit le nom d'une AUTRE culture déclarée dans ce fichier.
    """
    text = _norm_text(f"{title} {description or ''}")
    if any(_norm_text(kw) in text for kw in data.get("exclusions") or []):
        return True
    for autre_cle, meta in data["cultures"].items():
        if autre_cle == cle:
            continue
        nom = _norm_text(meta.get("nom", ""))
        if nom and nom in text:
            return True
    return False


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
    exclusions = raw.get("exclusions") or []
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

    _cache = {"by_venue": by_venue, "cultures": cultures, "exclusions": exclusions}
    _cache_mtime = mtime
    log.info("cultures : %d lieux, %d cultures, %d mots-clés d'exclusion",
             len(by_venue), len(cultures), len(exclusions))
    return _cache


def for_venue(venue: str | None, title: str | None = None,
             description: str | None = None) -> dict | None:
    """La culture d'un lieu, ou None s'il n'est pas mono-culturel.

    `title`/`description` permettent d'appliquer l'option C : un événement
    manifestement multi-pays (mot-clé d'exclusion, ou nom d'une autre
    culture) n'hérite pas de l'attribut du lieu, même si le lieu reste admis.
    """
    if not venue:
        return None
    data = _load()
    v = data["by_venue"].get(_norm(venue))
    if not v:
        return None
    if title is not None and _is_excluded(v["cle"], title, description, data):
        return None
    return v


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


def excluded_count(con) -> int:
    """Nombre d'événements de lieux mono-culturels écartés par l'option C
    (mot-clé d'exclusion ou nom d'une autre culture) — pour /health. Calculé
    à la demande plutôt qu'accumulé en mémoire : reste exact même si le
    fichier est corrigé à chaud entre deux appels.
    """
    data = _load()
    venues = [v["lieu"] for v in data["by_venue"].values()]
    if not venues:
        return 0
    placeholders = ", ".join("?" for _ in venues)
    rows = con.execute(
        f"SELECT venue, title, description FROM events WHERE lower(venue) IN ({placeholders})",
        [v.lower() for v in venues],
    ).fetchall()
    n = 0
    for r in rows:
        v = data["by_venue"].get(_norm(r["venue"]))
        if v and _is_excluded(v["cle"], r["title"], r["description"], data):
            n += 1
    return n
