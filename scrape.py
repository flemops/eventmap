"""Extraction d'événements depuis du HTML non structuré, via un LLM.

Dernier recours, **désactivé par défaut** : coûteux, non déterministe, et la
licence du contenu scrappé est rarement claire. Ne tourne que pour un
domaine explicitement listé dans feeds.yaml avec `type: llm`, jamais depuis
la boucle de refresh automatique.

Nécessite `ANTHROPIC_API_KEY`. Sans clé, `fetch_llm` lève immédiatement :
l'agrégateur marque la source en erreur et continue.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime

from db import Event
from sources import PoliteClient, clean_html, normalize_category, stable_id

log = logging.getLogger("eventmap.scrape")

MODEL = os.environ.get("EVENTMAP_LLM_MODEL", "claude-sonnet-5")
MAX_HTML_CHARS = 60_000  # au-delà on tronque : le contexte coûte, et un agenda tient dans ce volume

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n{3,}")

PROMPT = """Tu extrais des événements d'une page web d'agenda en France.

Renvoie UNIQUEMENT un tableau JSON, sans commentaire, un objet par événement :
{"title": str, "start": "ISO 8601 avec fuseau Europe/Paris", "end": str|null,
 "venue": str|null, "address": str|null, "city": str|null,
 "price": "free"|"paid"|"unknown", "url": str|null, "description": str|null}

Règles :
- Ignore tout ce qui n'est pas un événement daté (articles, menus, pub).
- Si seule la date est connue, mets 19:00 comme heure et note-le dans description.
- Si l'année manque, prends la prochaine occurrence à venir.
- Pas de date = pas d'événement : ne l'inclus pas.

Page ({url}) :
{text}
"""


def html_to_text(html: str) -> str:
    html = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub("\n", html)
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n\n", text)
    return text.strip()[:MAX_HTML_CHARS]


async def extract_with_llm(text: str, url: str) -> list[dict]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY absente : scrape LLM désactivé")
    import anthropic  # import tardif : dépendance optionnelle

    client = anthropic.AsyncAnthropic(api_key=key)
    msg = await client.messages.create(
        model=MODEL, max_tokens=4096,
        messages=[{"role": "user", "content": PROMPT.format(url=url, text=text)}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("la réponse LLM n'est pas un tableau JSON")
    return data


def to_events(items: list[dict], page_url: str, *, source: str) -> list[Event]:
    events: list[Event] = []
    for it in items:
        title, start_raw = it.get("title"), it.get("start")
        if not title or not start_raw:
            continue
        try:
            start = datetime.fromisoformat(str(start_raw))
        except ValueError:
            continue
        end = None
        if it.get("end"):
            try:
                end = datetime.fromisoformat(str(it["end"]))
            except ValueError:
                pass
        url = it.get("url") or page_url
        events.append(Event(
            source=source, source_id=stable_id(url, str(start_raw), str(title)),
            start=start, end=end, title=str(title).strip(),
            description=clean_html(it.get("description")), venue=it.get("venue"),
            address=it.get("address"), city=it.get("city"),
            price_type=it.get("price") if it.get("price") in ("free", "paid") else "unknown",
            url=url, category=normalize_category(str(title), it.get("description")),
        ))
    return events


async def fetch_llm(client: PoliteClient, url: str) -> list[Event]:
    resp = await client.get(url)
    resp.raise_for_status()
    text = html_to_text(resp.text)
    if len(text) < 200:
        raise ValueError(f"{url}: page quasi vide après nettoyage ({len(text)} caractères)")
    items = await extract_with_llm(text, url)
    events = to_events(items, url, source=f"llm:{url}")
    log.info("LLM %s : %d événements extraits", url, len(events))
    return events
