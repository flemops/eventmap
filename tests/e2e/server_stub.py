"""Serveur de test E2E : base jetable + AUCUNE ingestion réseau. Lancé par conftest.py (sous-processus).

    EVENTMAP_DB=/tmp/e2e.db python tests/e2e/server_stub.py 8799
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import uvicorn  # noqa: E402

import db  # noqa: E402
import main  # noqa: E402

if not os.environ.get("EVENTMAP_DB") or db.DB_PATH.startswith(("/opt/", "/var/", "/srv/")):
    sys.exit("refus : EVENTMAP_DB doit pointer vers une base jetable")


def seed() -> None:
    """Événements FACTICES (« [E2E] ») : jamais présentés comme réels."""
    now = datetime.now(timezone.utc)

    def ev(i, title, start_h, dur_h, lat, lon, **kw):
        return db.Event(source="fixture:e2e", source_id=f"e2e-{i}", title=f"[E2E] {title}",
                        start=now + timedelta(hours=start_h), end=(now + timedelta(hours=start_h + dur_h)) if dur_h else None,
                        lat=lat, lon=lon, **kw)

    rows = [
        ev(1, "Concert gratuit", 1, 2, 48.8584, 2.3470, price_type="free", category="music", venue="Salle A",
           description="Un concert de test assez long pour compter comme description complète."),
        ev(2, "Expo payante", 2, 3, 48.8606, 2.3376, price_type="paid", price_min=12.0, currency="EUR", category="expo",
           venue="Musée B", description="Une exposition de test assez longue pour compter comme description complète."),
        ev(3, "Atelier sans fin connue", 2, 0, 48.8530, 2.3499, price_type="free", category="workshop", venue="Atelier C"),
        ev(4, "Cinéma", 5, 2, 48.8700, 2.3300, price_type="paid", category="cinema", venue="Ciné D"),
        ev(5, "Théâtre", 6, 2, 48.8650, 2.3600, price_type="paid", category="theatre", venue="Théâtre E"),
        ev(6, "Marché", 7, 2, 48.8500, 2.3400, price_type="free", category="market", venue="Place F"),
    ]
    con = db.connect()
    db.upsert_events(con, rows)
    con.commit()
    con.close()


async def _idle():
    await asyncio.sleep(10**9)


main.refresh_loop = _idle
seed()
uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
