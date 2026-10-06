"""Phase 13 — tests de régression 13.31 (PFL MENA 10) et 13.56 (récurrence).

Sans réseau, sans donnée réelle écrite en production : les « faits figés » du
cahier des charges (PFL MENA 10 ; Dream Beach / Mangrove Beach) servent de
FIXTURES QA. Aucune source Jeddah n'est admise en V1 : Jeddah reste éteinte en
production, ces tests prouvent seulement que le moteur sait, le jour venu,
gérer ces cas sans fabriquer de faux événement.

    python -m pytest tests/test_phase13_regression.py -v
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import cities
import db
import main
import pipeline
import sources
import timewin

UTC = timezone.utc
RIYADH = ZoneInfo("Asia/Riyadh")
JEDDAH = cities.get("jeddah")


@pytest.fixture
def con(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


@pytest.fixture
def base_temp(tmp_path, monkeypatch):
    chemin = str(tmp_path / "route.db")
    db.connect(chemin).close()
    vraie_session = db.session
    monkeypatch.setattr(db, "session", lambda *a, **k: vraie_session(chemin))
    return chemin


@pytest.fixture
def client(base_temp):
    from fastapi.testclient import TestClient
    return TestClient(main.app)


@pytest.fixture
def jeddah_on(monkeypatch):
    monkeypatch.setenv("EVENTMAP_CITIES_ENABLED", "jeddah")


@pytest.fixture
def priorites():
    """Priorités de sources, toujours remises à zéro (état global du module)."""
    def _set(mapping):
        sources.set_priorities(mapping)
    yield _set
    sources.set_priorities({})


def _freeze(monkeypatch, instant: datetime) -> None:
    """Fige « maintenant » pour les routes (main.datetime.now)."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(main, "datetime", Frozen)


def _put(path, events):
    c = db.connect(path)
    db.upsert_events(c, events)
    c.commit()
    c.close()


def _search(con, now: datetime, when="today", *, statuses=("active",), city="jeddah"):
    c = cities.get(city)
    start, end = timewin.window(when, now.astimezone(UTC), c.tz, weekend_days=c.weekend_days,
                                cutoff=c.night_cutoff_hour)
    return db.search(con, city_id=city, lat=c.center[0], lon=c.center[1], radius_km=c.max_radius_km,
                     start_from=start, start_to=end, statuses=statuses, limit=300)


# ===================================================================== 13.31
# PFL MENA 10 — faits figés (cahier des charges 13.31) :
#   A) ancienne occurrence annoncée Jeddah, 19/06/2026, King Abdullah Sports City
#      Sports Hall (source agrégateur, active) ;
#   B) source officielle PFL du 17/06/2026 : postponed ;
#   C) occurrence officielle ultérieure : Riyadh, 10/07/2026.
# L'heure de début (19:00 locale) n'est pas un fait documenté : fixture.

AGREG, OFFICIELLE = "fixture:agregateur", "fixture:pfl-officiel"
PFL_JEDDAH = datetime(2026, 6, 19, 19, 0, tzinfo=RIYADH)
PFL_RIYADH = datetime(2026, 7, 10, 19, 0, tzinfo=RIYADH)
KASC = dict(lat=21.6, lon=39.15)                    # King Abdullah Sports City (approx.)
RIYADH_CENTRE = dict(lat=24.7136, lon=46.6753)
HALL = "King Abdullah Sports City Sports Hall"


def _pfl(jeddah_start=PFL_JEDDAH, riyadh_start=PFL_RIYADH):
    ancienne = db.Event(source=AGREG, source_id="pfl-mena-10", title="PFL MENA 10", start=jeddah_start,
                        venue=HALL, city_id="jeddah", status="active", **KASC,
                        updated_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC))
    report = db.Event(source=OFFICIELLE, source_id="pfl-mena-10", title="PFL MENA 10", start=jeddah_start,
                      venue=HALL, city_id="jeddah", status="postponed", **KASC,
                      url="https://pflmma.com/index.php/news/pfl-mena-10-in-jeddah-postponed-updated-event-details-to-follow",
                      updated_at=datetime(2026, 6, 17, 9, 0, tzinfo=UTC))
    riyad = db.Event(source=OFFICIELLE, source_id="pfl-mena-10", title="PFL MENA 10", start=riyadh_start,
                     venue="Riyadh (salle à confirmer)", city_id="riyadh", status="active", **RIYADH_CENTRE,
                     url="https://pflmma.com/events", updated_at=datetime(2026, 6, 17, 9, 0, tzinfo=UTC))
    return ancienne, report, riyad


def _etat_attendu_pfl(con, jeddah_start=PFL_JEDDAH, riyadh_start=PFL_RIYADH):
    """Les invariants 13.31, vérifiés après CHAQUE cycle."""
    avant = jeddah_start - timedelta(hours=3)
    # 1. L'ancienne occurrence Jeddah n'est ni « Tonight » ni « à venir ».
    assert _search(con, avant, "today") == []
    assert _search(con, jeddah_start - timedelta(days=2), "week") == []
    # 2. Une seule ligne visible pour Jeddah (statut explicite) : l'officielle, reportée.
    tout = _search(con, avant, "today", statuses=())
    assert len(tout) == 1
    assert tout[0]["source"] == OFFICIELLE and tout[0]["status"] == "postponed"
    # 3. L'ancienne ligne est conservée (provenance), liée à l'officielle, jamais « active ».
    rows = {r["source"]: dict(r) for r in con.execute(
        "SELECT * FROM events WHERE city_id = 'jeddah'")}
    ancienne, officielle = rows[AGREG], rows[OFFICIELLE]
    assert ancienne["doublon_de"] == officielle["id"] and ancienne["dedup_reason"] == "forte"
    assert ancienne["status"] == "postponed" and officielle["status"] == "postponed"
    assert officielle["updated_at"].startswith("2026-06-17")      # la date de la source officielle
    assert officielle["url"].startswith("https://pflmma.com/")
    # 4. Riyad : ligne distincte, active, d'une AUTRE ville, jamais fusionnée avec Jeddah.
    riyad = dict(con.execute("SELECT * FROM events WHERE city_id = 'riyadh'").fetchone())
    assert riyad["status"] == "active" and riyad["doublon_de"] is None
    assert riyad["start"].startswith(riyadh_start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M"))
    assert riyad["id"] not in (ancienne["id"], officielle["id"])
    assert ancienne["doublon_de"] != riyad["id"] and officielle["doublon_de"] is None
    assert _search(con, riyadh_start - timedelta(hours=3), "today", statuses=(), city="jeddah") == []
    # Riyad ne se voit que depuis Riyad (ville non configurée : requête directe).
    vus = db.search(con, city_id="riyadh", lat=RIYADH_CENTRE["lat"], lon=RIYADH_CENTRE["lon"], radius_km=30,
                    start_from=riyadh_start - timedelta(hours=3), start_to=riyadh_start + timedelta(hours=6))
    assert [v["id"] for v in vus] == [riyad["id"]]


@pytest.mark.parametrize("prio", [{OFFICIELLE: 9, AGREG: 1}, {}], ids=["officielle-prioritaire", "priorites-egales"])
def test_13_31_pfl_mena_10_le_report_officiel_gagne(con, priorites, prio):
    priorites(prio)
    db.upsert_events(con, list(_pfl()))
    sources.dedup_inter_source(con, now=datetime(2026, 6, 17, 12, 0, tzinfo=UTC))
    _etat_attendu_pfl(con)


@pytest.mark.parametrize("ordre", ["agregateur-puis-officielle", "officielle-puis-agregateur"])
def test_13_31_l_ordre_d_arrivee_ne_change_rien(con, priorites, ordre):
    priorites({OFFICIELLE: 9, AGREG: 1})
    ancienne, report, riyad = _pfl()
    lots = [[ancienne], [report, riyad]] if ordre.startswith("agregateur") else [[report, riyad], [ancienne]]
    for lot in lots:
        db.upsert_events(con, lot)
        sources.dedup_inter_source(con, now=datetime(2026, 6, 17, 12, 0, tzinfo=UTC))
    _etat_attendu_pfl(con)


def test_13_31_un_refresh_ulterieur_ne_ressuscite_pas_l_evenement_stale(con, priorites):
    """L'agrégateur continue de publier l'ancienne date « active » (flux en retard) :
    cycle après cycle, upsert + purge des lignes non revues + dédup, comme main._persist."""
    priorites({OFFICIELLE: 9, AGREG: 1})
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    db.upsert_events(con, list(_pfl()))
    sources.dedup_inter_source(con, now=now)
    _etat_attendu_pfl(con)
    for cycle in range(1, 4):
        ancienne, report, riyad = _pfl()
        cycle_start = datetime.now(UTC)
        # Le flux en retard republie l'ancienne occurrence, active, à chaque cycle.
        db.upsert_events(con, [ancienne])
        if cycle != 2:                                  # un cycle sur trois : l'officielle n'est pas revue
            db.upsert_events(con, [report, riyad])
        for src in (AGREG,):                            # purge d'une source dont le refresh a réussi
            db.purge_stale(con, src, cycle_start - timedelta(seconds=5))
        sources.dedup_inter_source(con, now=now + timedelta(hours=cycle))
        _etat_attendu_pfl(con)
    # Contrôle négatif : l'agrégateur seul (sans report officiel) RESTE affiché.
    seul = db.connect(":memory:")
    db.upsert_events(seul, [_pfl()[0]])
    sources.dedup_inter_source(seul, now=now)
    assert [r["title"] for r in _search(seul, PFL_JEDDAH - timedelta(hours=3), "today")] == ["PFL MENA 10"]
    seul.close()


def test_13_31_statut_reporte_deduit_d_un_titre_officiel():
    assert pipeline.infer_status("PFL MENA 10 — Jeddah event postponed") == "postponed"
    assert pipeline.infer_status("PFL MENA 10") == "active"


def test_13_31_api_tonight_upcoming_et_detail(client, base_temp, jeddah_on, priorites, monkeypatch):
    priorites({OFFICIELLE: 9, AGREG: 1})
    now = datetime(2026, 6, 19, 15, 0, tzinfo=UTC)               # le soir annoncé de l'ancienne occurrence
    _freeze(monkeypatch, now)
    _put(base_temp, list(_pfl()))
    c = db.connect(base_temp)
    sources.dedup_inter_source(c, now=now)
    c.commit()
    ids = {r["source"]: r["id"] for r in c.execute("SELECT id, source FROM events WHERE city_id = 'jeddah'")}
    c.close()
    for when in ("now", "today", "tomorrow", "weekend", "week"):
        r = client.get("/api/events", params={"city": "jeddah", "radius": 30, "when": when}).json()
        assert r["events"] == [], when
    # Les deux identifiants (ancien et officiel) disent « postponed » avec leur provenance.
    for src, eid in ids.items():
        d = client.get(f"/api/events/{eid}", params={"city": "jeddah"}).json()
        assert d["status"] == "postponed", src
    ancien = client.get(f"/api/events/{ids[AGREG]}", params={"city": "jeddah"}).json()
    assert ancien["provenance"]["duplicate_of"] == ids[OFFICIELLE] and ancien["provenance"]["reason"] == "forte"
    officiel = client.get(f"/api/events/{ids[OFFICIELLE]}", params={"city": "jeddah"}).json()
    assert officiel["provenance"]["source"] == OFFICIELLE
    assert officiel["provenance"]["source_updated_at"].startswith("2026-06-17")
    # Riyad n'est pas une ville du produit : 404, pas de fusion avec Jeddah.
    assert client.get("/api/events", params={"city": "riyadh"}).status_code == 404


# ===================================================================== 13.56
# Dream Beach : 20/08 → 07/12/2026, tous les jours 07:00 → 03:00 le lendemain.
# Mangrove Beach : occurrences explicitement datées 07, 08, 09 et 10/10/2026
# (heures 18:00 → 23:00 : fixture, seul le jour est un fait documenté).
# Fixtures QA uniquement : pas d'adapter Tathkara, pas d'ingestion.

DREAM_DEBUT = datetime(2026, 8, 20, tzinfo=RIYADH)
DREAM_FIN = datetime(2026, 12, 7, tzinfo=RIYADH)
FENETRE = (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))


def _ics(body: str) -> bytes:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//\r\n" + body + "END:VCALENDAR\r\n").encode()


DREAM_ICS = _ics(
    "BEGIN:VEVENT\r\nUID:dream-beach\r\nSUMMARY:Dream Beach\r\n"
    "DTSTART;TZID=Asia/Riyadh:20260820T070000\r\nDTEND;TZID=Asia/Riyadh:20260821T030000\r\n"
    "RRULE:FREQ=DAILY;UNTIL=20261207T040000Z\r\nGEO:21.5433;39.1728\r\nEND:VEVENT\r\n")


def _mangrove_ics(jours=(7, 8, 9, 10)):
    corps = "".join(
        f"BEGIN:VEVENT\r\nUID:mangrove-beach\r\nSUMMARY:Mangrove Beach\r\n"
        f"DTSTART;TZID=Asia/Riyadh:202610{j:02d}T180000\r\nDTEND;TZID=Asia/Riyadh:202610{j:02d}T230000\r\n"
        f"GEO:21.5433;39.1728\r\nEND:VEVENT\r\n" for j in jours)
    return _ics(corps)


def _ingere(con, raw, source):
    evs = sources.parse_ics(raw, source=source, window=FENETRE)
    evs, _ = pipeline.normalize(evs, "jeddah")
    db.upsert_events(con, evs)
    return evs


def _locaux(rows, titre=None):
    return [r["start"] for r in rows if titre is None or r["title"] == titre]


def test_13_56_dream_beach_110_occurrences_sans_fantome():
    evs = sources.parse_ics(DREAM_ICS, source=AGREG, window=FENETRE)
    debuts = sorted(e.start.astimezone(RIYADH) for e in evs)
    assert len(debuts) == 110 and len(set(debuts)) == 110            # aucune duplication
    assert debuts[0] == datetime(2026, 8, 20, 7, 0, tzinfo=RIYADH)
    assert debuts[-1] == datetime(2026, 12, 7, 7, 0, tzinfo=RIYADH)
    assert all(b - a == timedelta(days=1) for a, b in zip(debuts, debuts[1:]))     # une par jour, sans trou
    assert all(e.end - e.start == timedelta(hours=20) for e in evs)                 # 07:00 → 03:00 lendemain
    assert not any(d.date() in (datetime(2026, 8, 19).date(), datetime(2026, 12, 8).date()) for d in debuts)


def test_13_56_dream_beach_rattachee_a_la_bonne_nuit():
    cutoff = JEDDAH.night_cutoff_hour
    for e in sources.parse_ics(DREAM_ICS, source=AGREG, window=FENETRE):
        debut, fin = e.start.astimezone(RIYADH), e.end.astimezone(RIYADH)
        jour = debut.replace(hour=0, minute=0, second=0, microsecond=0)
        assert timewin.logical_day(debut, cutoff) == jour
        assert timewin.logical_day(fin, cutoff) == jour               # 03:00 le lendemain = MÊME nuit
        assert fin.date() == (debut + timedelta(days=1)).date()


def test_13_56_passage_23h59_00h00_sans_doublon_ni_saut(con):
    _ingere(con, DREAM_ICS, AGREG)
    jour = datetime(2026, 10, 7, tzinfo=RIYADH)
    attendu = datetime(2026, 10, 7, 7, 0, tzinfo=RIYADH).astimezone(UTC).isoformat()

    def starts(h, m, d=0):
        return [(r["id"], r["start"]) for r in _search(con, (jour + timedelta(days=d)).replace(hour=h, minute=m))]

    avant, apres = starts(23, 59), starts(0, 0, 1)
    assert len(avant) == 1 and avant == apres                        # même ligne des deux côtés de minuit
    assert avant[0][1].startswith(attendu[:16])
    assert starts(2, 59, 1) == avant                                 # encore la soirée du 7, jusqu'à 03:00
    assert starts(3, 1, 1) == []                                     # 03:01-07:00 : rien, pas de fantôme
    suivante = starts(5, 0, 1)
    assert len(suivante) == 1 and suivante != avant                  # 05:00 : nouvelle journée = occurrence du 8
    assert suivante[0][1].startswith("2026-10-08T04:00")
    # Sur une semaine : chaque jour exactement une fois.
    semaine = _search(con, jour.replace(hour=12), "week")
    jours = [datetime.fromisoformat(r["start"]).astimezone(RIYADH).date() for r in semaine]
    assert jours == sorted(set(jours)) and len(jours) == len(semaine)
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 110
    assert con.execute("SELECT COUNT(*) FROM (SELECT 1 FROM events GROUP BY source, source_id, start "
                       "HAVING COUNT(*) > 1)").fetchone()[0] == 0


def test_13_56_hors_serie_immediat(con):
    _ingere(con, DREAM_ICS, AGREG)
    assert _search(con, datetime(2026, 8, 19, 20, 0, tzinfo=RIYADH)) == []            # veille de la série
    assert len(_search(con, datetime(2026, 8, 20, 20, 0, tzinfo=RIYADH))) == 1       # premier jour
    assert len(_search(con, datetime(2026, 12, 7, 20, 0, tzinfo=RIYADH))) == 1       # dernier jour
    assert len(_search(con, datetime(2026, 12, 8, 2, 59, tzinfo=RIYADH))) == 1       # fin de la dernière nuit
    assert _search(con, datetime(2026, 12, 8, 3, 1, tzinfo=RIYADH)) == []            # après : plus rien
    assert _search(con, datetime(2026, 12, 8, 20, 0, tzinfo=RIYADH), "week") == []


def test_13_56_mangrove_exactement_les_dates_fournies(con):
    evs = sources.parse_ics(_mangrove_ics(), source=AGREG, window=FENETRE)
    assert sorted(e.start.astimezone(RIYADH).day for e in evs) == [7, 8, 9, 10]
    # Un intervalle (07/10 → 10/10) SANS récurrence n'est pas développé en jours supplémentaires.
    intervalle = _ics("BEGIN:VEVENT\r\nUID:m2\r\nSUMMARY:Mangrove Beach\r\n"
                      "DTSTART;TZID=Asia/Riyadh:20261007T180000\r\nDTEND;TZID=Asia/Riyadh:20261010T230000\r\n"
                      "END:VEVENT\r\n")
    assert len(sources.parse_ics(intervalle, source=AGREG, window=FENETRE)) == 1

    _ingere(con, _mangrove_ics(), AGREG)
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 4
    # Dates immédiatement hors série : la veille et le lendemain ne montrent rien.
    assert _search(con, datetime(2026, 10, 6, 20, 0, tzinfo=RIYADH)) == []
    assert _search(con, datetime(2026, 10, 11, 20, 0, tzinfo=RIYADH)) == []
    for j in (7, 8, 9, 10):
        r = _search(con, datetime(2026, 10, j, 20, 0, tzinfo=RIYADH))
        assert len(r) == 1 and datetime.fromisoformat(r[0]["start"]).astimezone(RIYADH).day == j


def test_13_56_l_ui_et_la_recherche_ne_montrent_que_les_occurrences_valides(
        client, base_temp, jeddah_on, monkeypatch):
    c = db.connect(base_temp)
    _ingere(c, DREAM_ICS, AGREG)
    _ingere(c, _mangrove_ics(), "fixture:mangrove")
    c.commit()
    c.close()

    def vus(instant, when):
        _freeze(monkeypatch, instant)
        r = client.get("/api/events", params={"city": "jeddah", "radius": 30, "when": when})
        assert r.status_code == 200
        return [(e["title"], datetime.fromisoformat(e["start"]).astimezone(RIYADH).strftime("%m-%d %H:%M"))
                for e in r.json()["events"]]

    # Soirée du 10/10 : dernière date de Mangrove + occurrence du jour de Dream Beach, une fois chacune.
    assert vus(datetime(2026, 10, 10, 20, 0, tzinfo=RIYADH), "today") == [
        ("Dream Beach", "10-10 07:00"), ("Mangrove Beach", "10-10 18:00")]
    # Le lendemain : plus de Mangrove ; Dream Beach continue, sans doublon à minuit.
    assert vus(datetime(2026, 10, 11, 20, 0, tzinfo=RIYADH), "today") == [("Dream Beach", "10-11 07:00")]
    assert vus(datetime(2026, 10, 10, 23, 59, tzinfo=RIYADH), "today") == vus(
        datetime(2026, 10, 11, 0, 0, tzinfo=RIYADH), "today")
    # Avant la série Mangrove : seule Dream Beach ; une semaine pleine : 4 Mangrove exactement.
    assert vus(datetime(2026, 10, 6, 20, 0, tzinfo=RIYADH), "today") == [("Dream Beach", "10-06 07:00")]
    semaine = vus(datetime(2026, 10, 6, 12, 0, tzinfo=RIYADH), "week")
    assert [d for t, d in semaine if t == "Mangrove Beach"] == [
        "10-07 18:00", "10-08 18:00", "10-09 18:00", "10-10 18:00"]
    assert len({d for t, d in semaine if t == "Dream Beach"}) == len([1 for t, _ in semaine if t == "Dream Beach"])
    # Après la fin de Dream Beach : plus aucune occurrence.
    assert vus(datetime(2026, 12, 8, 20, 0, tzinfo=RIYADH), "today") == []
