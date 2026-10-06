"""Export iCalendar (.ics) d'un ou plusieurs événements — 15.43.

Uniquement ce que la source donne : pas de durée inventée (sans fin connue, un VEVENT
n'a que DTSTART), pas de trajet, pas de rappel. Heures en UTC (suffixe Z) : tout client
agenda les convertit dans le fuseau de l'utilisateur. Texte échappé (RFC 5545 §3.3.11)
et lignes pliées à 75 octets (§3.1).
"""

from __future__ import annotations

from datetime import datetime, timezone

CRLF = "\r\n"
BS = "\\"


def _esc(text: str | None) -> str:
    out = str(text or "").replace(BS, BS + BS).replace(";", BS + ";").replace(",", BS + ",")
    return out.replace("\r\n", BS + "n").replace("\n", BS + "n").replace("\r", BS + "n")


def _fold(line: str) -> str:
    if len(line.encode("utf-8")) <= 75:
        return line
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return (CRLF + " ").join(out)


def _utc(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def calendar(events: list[dict], site: str, city_name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//EventMap//Tonight//EN", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{_esc('EventMap — ' + city_name)}"]
    for e in events:
        loc = ", ".join(x for x in (e.get("venue"), e.get("address")) if x)
        lines += ["BEGIN:VEVENT", f"UID:eventmap-{e['city_id']}-{e['id']}@{site.split('//')[-1]}",
                  f"DTSTAMP:{stamp}", f"DTSTART:{_utc(e['start'])}"]
        if e.get("end") and _utc(e["end"]) > _utc(e["start"]):      # une fin antérieure au début est une erreur de source : ignorée
            lines.append(f"DTEND:{_utc(e['end'])}")
        lines.append(f"SUMMARY:{_esc(e['title'])}")
        if loc:
            lines.append(f"LOCATION:{_esc(loc)}")
        if e.get("lat") is not None and e.get("lon") is not None:
            lines.append(f"GEO:{e['lat']:.6f};{e['lon']:.6f}")
        if e.get("description"):
            lines.append(f"DESCRIPTION:{_esc(e['description'][:500])}")
        lines.append(f"URL:{site}/{e['city_id']}/e/{e['id']}")
        if e.get("status") == "cancelled":
            lines.append("STATUS:CANCELLED")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return CRLF.join(_fold(x) for x in lines) + CRLF
