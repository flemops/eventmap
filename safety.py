"""Toute donnée externe est NON FIABLE tant qu'elle n'est pas passée ici.

Trois défenses, toutes déterministes et testées sans réseau :

* `clean_text`  — texte brut : balises, entités, caractères de contrôle et
  marques d'ordre bidirectionnel « override » retirés, longueur bornée. Le front
  échappe de toute façon à l'affichage ; ceci évite de STOCKER du contenu piégé
  (et d'envoyer du HTML brut à un consommateur de l'API qui ne l'échapperait pas).
* `safe_url`    — liens affichés : http(s) uniquement, jamais d'identifiants
  dans l'URL, pas de `javascript:`/`data:`/`file:`, pas d'espaces ni de contrôle.
* `ensure_public_url` — protection SSRF côté ingestion : un flux ne doit jamais
  pouvoir nous faire interroger le réseau interne (127.0.0.1, 10/8, 169.254/16
  — dont le service de métadonnées du cloud — etc.). Appliqué à l'URL de départ
  ET à chaque redirection.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html import unescape
from urllib.parse import urlsplit

_TAG_RE = re.compile(r"<[^>]*>")
_SCRIPT_BLOCK_RE = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_BIDI_OVERRIDE_RE = re.compile("[‪-‮]")
_WS_RE = re.compile(r"\s+")
MAX_URL_LEN = 2000


def clean_text(text: str | None, max_len: int = 600) -> str | None:
    if not text:
        return None
    t = _SCRIPT_BLOCK_RE.sub(" ", str(text))
    t = _TAG_RE.sub(" ", t)
    t = unescape(t)
    t = _TAG_RE.sub(" ", t)          # une entité peut décoder vers une balise (&lt;b&gt;)
    t = _CTRL_RE.sub(" ", _BIDI_OVERRIDE_RE.sub("", t))
    t = _WS_RE.sub(" ", t).strip()
    return t[:max_len] or None


def safe_url(url: str | None, *, schemes: tuple[str, ...] = ("https", "http")) -> str | None:
    """L'URL normalisée si elle est sûre à afficher, sinon None."""
    if not url:
        return None
    u = str(url).strip()
    if not u or len(u) > MAX_URL_LEN or _CTRL_RE.search(u) or re.search(r"\s", u):
        return None
    try:
        p = urlsplit(u)
        host = p.hostname
    except ValueError:
        return None
    if p.scheme.lower() not in schemes or not host:
        return None
    if p.username or p.password:
        return None
    return u


class UnsafeUrl(ValueError):
    pass


def _is_public_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
                or a.is_reserved or a.is_unspecified)


def ensure_public_url(url: str, *, resolver=socket.getaddrinfo) -> None:
    """Lève UnsafeUrl si l'URL pointe (ou se résout) vers une adresse non publique."""
    if safe_url(url) is None:
        raise UnsafeUrl("URL refusée (schéma, identifiants ou caractères)")
    host = urlsplit(url).hostname or ""
    try:
        ips = {ai[4][0] for ai in resolver(host, None)}
    except socket.gaierror as exc:
        raise UnsafeUrl(f"nom de domaine non résolu: {host}") from exc
    if not ips:
        raise UnsafeUrl(f"aucune adresse pour {host}")
    bad = [ip for ip in ips if not _is_public_ip(ip.split("%")[0])]
    if bad:
        raise UnsafeUrl(f"{host} se résout vers une adresse non publique")


async def ensure_public_url_async(url: str) -> None:
    """Variante non bloquante : la résolution DNS ne gèle pas la boucle d'événements."""
    import asyncio
    if safe_url(url) is None:
        raise UnsafeUrl("URL refusée (schéma, identifiants ou caractères)")
    host = urlsplit(url).hostname or ""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrl(f"nom de domaine non résolu: {host}") from exc
    ips = {ai[4][0] for ai in infos}
    if not ips or any(not _is_public_ip(ip.split("%")[0]) for ip in ips):
        raise UnsafeUrl(f"{host} se résout vers une adresse non publique")
