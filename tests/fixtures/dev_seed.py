"""DONNÉES DE DÉVELOPPEMENT — jamais pour la production.

Remplit une base LOCALE avec des événements factices (source « fixture:dev ») pour
voir l'interface travailler : arabe, anglais, soirée après minuit, gratuit/payant,
annulé, lieu du référentiel. Les titres le disent : rien ici ne prétend décrire un
événement réel de Paris ou de Jeddah.

    EVENTMAP_DB=/tmp/em-dev.db python tests/fixtures/dev_seed.py

Refuse de tourner sur un chemin de production.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import db  # noqa: E402

if db.DB_PATH.startswith(("/opt/", "/var/", "/srv/")) or not os.environ.get("EVENTMAP_DB"):
    sys.exit("refus : définis EVENTMAP_DB vers une base locale jetable (jamais /opt, /var, /srv)")

now = datetime.now(timezone.utc)


def at(hours: float) -> datetime:
    return now + timedelta(hours=hours)


T = "[FIXTURE] "
jeddah = [
    # (titre, +heures, durée h, venue, lat, lon, catégorie, prix, extras)
    (T + "Jazz au bord de la mer / Seaside Jazz", 2, 2, "Jeddah Yacht Club", 21.6529588, 39.1015469, "music",
     dict(price_type="paid", price_min=150.0, price_max=300.0, currency="SAR", booking_url="https://example.org/tickets",
          lang="en", i18n={"ar": {"title": T + "جاز على البحر", "description": "أمسية جاز مباشرة على الواجهة البحرية."}},
          description="An evening of live jazz on the waterfront (test fixture).")),
    (T + "Historic walk", 3, 2, "Al-Balad", 21.4860371, 39.1876960, "talk", dict(price_type="free", lang="en")),
    (T + "جولة في البلد", 3, 2, "البلد", 21.4860371, 39.1876960, "talk", dict(price_type="free", lang="ar",
     source_id="ar-walk")),
    (T + "Midnight film / عرض منتصف الليل", 5, 3, "ROSHN Waterfront", 21.5989237, 39.1063798, "cinema",
     dict(price_type="paid", price_min=60.0, price_max=60.0, currency="SAR", lang="en")),
    (T + "After-midnight set", 9, 2, "Jeddah Super Dome", 21.7492364, 39.1517291, "music", dict(price_type="paid", price_min=90.0, currency="SAR")),
    (T + "Family market", 26, 4, "King Fahd's Fountain", 21.5156595, 39.1450461, "market", dict(price_type="free")),
    (T + "Pottery workshop", 28, 2, None, 21.5700, 39.1500, "workshop", dict(price_type="paid", price_min=120.0, currency="SAR")),
    (T + "Annulé : Stand-up night", 4, 2, None, 21.5400, 39.1700, "theatre", dict(price_type="paid", status="cancelled")),
    (T + "Kids art day", 50, 3, None, 21.6000, 39.1400, "kids", dict(price_type="free")),
]
paris = [
    (T + "Concert de test", 2, 2, "Le Zénith", 48.8584, 2.3470, "music", dict(price_type="free")),
    (T + "Expo de test", 3, 4, None, 48.8606, 2.3376, "expo", dict(price_type="free")),
    (T + "Atelier payant de test", 5, 2, None, 48.8530, 2.3499, "workshop", dict(price_type="paid")),
]

events = []
for city, rows in (("jeddah", jeddah), ("paris", paris)):
    for i, (title, off, dur, venue, lat, lon, cat, extra) in enumerate(rows):
        sid = extra.pop("source_id", f"{city}-{i}")
        events.append(db.Event(source="fixture:dev", source_id=sid, title=title, start=at(off), end=at(off + dur),
                               venue=venue, lat=lat, lon=lon, category=cat, city_id=city,
                               country_code="SA" if city == "jeddah" else "FR", **extra))

con = db.connect()
db.upsert_events(con, events)
con.commit()
print(f"{len(events)} événements de développement écrits dans {db.DB_PATH}")
