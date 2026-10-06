"""Phase 15 — « Tonight-first » : date précise (15.18) et export agenda (15.43).

Sans réseau. Les événements ci-dessous sont des FIXTURES de test, pas des données réelles.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import db
import ics
import main
import timewin

UTC = timezone.utc
PARIS = ZoneInfo("Europe/Paris")
RIYADH = ZoneInfo("Asia/Riyadh")


def _now(h=18):
    return datetime(2026, 10, 7, h, 0, tzinfo=PARIS).astimezone(UTC)


def test_day_window_demain_egale_tomorrow():
    assert timewin.day_window("2026-10-08", _now(), PARIS) == timewin.window("tomorrow", _now(), PARIS)


def test_day_window_aujourdhui_commence_maintenant():
    start, end = timewin.day_window("2026-10-07", _now(), PARIS)
    assert start == _now() and end == datetime(2026, 10, 8, 0, 0, tzinfo=PARIS).astimezone(UTC)


def test_day_window_apres_minuit_jeddah_appartient_a_la_veille():
    # 01h00 à Jeddah (coupure 5) : on est encore le « 7 » logique, la fenêtre du 8 démarre à 05h00 le 8.
    now = datetime(2026, 10, 8, 1, 0, tzinfo=RIYADH).astimezone(UTC)
    start, end = timewin.day_window("2026-10-08", now, RIYADH, cutoff=5)
    assert start == datetime(2026, 10, 8, 5, 0, tzinfo=RIYADH).astimezone(UTC)
    assert end - start == timedelta(days=1)


@pytest.mark.parametrize("bad", ["2026-10-06", "2027-03-01", "2026-13-01", "abc"])
def test_day_window_refuse_passe_lointain_et_invalide(bad):
    with pytest.raises(ValueError):
        timewin.day_window(bad, _now(), PARIS)


def test_ics_pas_de_duree_inventee_et_texte_echappe():
    e = {"id": 7, "city_id": "paris", "title": "Jazz; live, ce soir", "start": "2026-10-07T17:30:00+00:00",
         "end": None, "venue": "Le Duc", "address": "1 rue X", "lat": 48.86, "lon": 2.35,
         "description": "ligne1\nligne2", "status": "active"}
    out = ics.calendar([e], "https://eventmap.example", "Paris")
    assert out.startswith("BEGIN:VCALENDAR\r\n") and out.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261007T173000Z" in out and "DTEND" not in out
    bs = "\\"
    assert f"SUMMARY:Jazz{bs}; live{bs}, ce soir" in out and f"DESCRIPTION:ligne1{bs}nligne2" in out
    assert "URL:https://eventmap.example/paris/e/7" in out


def test_ics_lignes_pliees_a_75_octets():
    e = {"id": 1, "city_id": "paris", "title": "é" * 120, "start": "2026-10-07T17:30:00+00:00", "status": "active"}
    assert all(len(line.encode()) <= 75 for line in ics.calendar([e], "https://x.y", "Paris").split("\r\n"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    chemin = str(tmp_path / "t.db")
    con = db.connect(chemin)
    t = datetime.now(UTC) + timedelta(hours=3)
    db.upsert_events(con, [db.Event(source="fixture", source_id="a", title="Concert test", start=t, lat=48.86, lon=2.35,
                             venue="Salle test")])
    con.commit()
    con.close()
    vraie = db.session
    monkeypatch.setattr(db, "session", lambda *a, **k: vraie(chemin))
    from fastapi.testclient import TestClient
    return TestClient(main.app)


def test_route_calendar_ics(client):
    ev = client.get("/api/events", params={"city": "paris", "when": "week", "radius": 8}).json()["events"]
    assert ev
    r = client.get("/api/calendar.ics", params={"city": "paris", "ids": str(ev[0]["id"])})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert "attachment" in r.headers["content-disposition"] and "SUMMARY:Concert test" in r.text


def test_route_calendar_ics_refuse_ids_invalides_ou_inconnus(client):
    assert client.get("/api/calendar.ics", params={"ids": "1;DROP"}).status_code == 422
    assert client.get("/api/calendar.ics", params={"ids": "999999"}).status_code == 404
    assert client.get("/api/calendar.ics", params={"city": "jeddah", "ids": "1"}).status_code == 404


def test_route_events_date_precise(client):
    today = datetime.now(PARIS).date().isoformat()
    ok = client.get("/api/events", params={"city": "paris", "date": today, "radius": 8})
    assert ok.status_code == 200
    assert client.get("/api/events", params={"city": "paris", "date": "2020-01-01"}).status_code == 400


def test_funnel_compte_sans_donnee_personnelle(client):
    for _ in range(3):
        assert client.post("/api/funnel", params={"city": "paris", "step": "event_open"}).status_code == 204
    client.post("/api/funnel", params={"city": "paris", "step": "inconnue"})       # ignorée
    client.post("/api/funnel", params={"city": "jeddah", "step": "event_open"})    # ville éteinte : ignorée
    steps = client.get("/api/funnel", params={"days": 7}).json()["steps"]
    assert steps["event_open"] == 3 and sum(steps.values()) == 3
    with db.session() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(funnel)")}
    assert cols == {"day", "city_id", "step", "n"}      # rien qui identifie une personne
