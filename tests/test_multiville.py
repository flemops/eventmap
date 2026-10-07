"""Tests du moteur multi-ville : sans réseau, sans donnée réelle de Jeddah.

Les événements « de Jeddah » ci-dessous sont des FIXTURES DE TEST, jamais écrites
dans une base de production : ils servent à prouver le comportement du moteur
(isolation des villes, fuseaux, arabe, statuts), pas à remplir la carte.
"""

import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import cities
import db
import main
import pipeline
import registry
import safety
import sources
import textnorm
import timewin
import venues

PARIS = ZoneInfo("Europe/Paris")
RIYADH = ZoneInfo("Asia/Riyadh")
UTC = timezone.utc


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


def _put(path, events):
    c = db.connect(path)
    db.upsert_events(c, events)
    c.commit()
    c.close()


def _jed(title, start, **kw):
    kw.setdefault("lat", 21.5433)
    kw.setdefault("lon", 39.1728)
    return db.Event(source="fixture:test", source_id=kw.pop("source_id", title), title=title,
                    start=start, city_id="jeddah", **kw)


# ============================================================ villes / interrupteurs

def test_jeddah_est_construite_mais_eteinte_par_defaut():
    assert cities.get("jeddah") is not None
    assert cities.active("jeddah") is None
    assert [c.id for c in cities.all_active()] == ["paris"]


def test_interrupteurs_d_environnement(monkeypatch):
    monkeypatch.setenv("EVENTMAP_CITIES_ENABLED", "jeddah")
    assert cities.active("jeddah") is not None
    # L'extinction GAGNE toujours sur l'allumage.
    monkeypatch.setenv("EVENTMAP_CITIES_DISABLED", "jeddah,paris")
    assert cities.active("jeddah") is None and cities.active("paris") is None


def test_ville_inconnue_et_casse():
    assert cities.get("atlantide") is None
    assert cities.active("PARIS").id == "paris"
    assert cities.get(None).id == "paris"


def test_geolocalisation_ne_devine_pas(jeddah_on):
    assert cities.nearest_active(48.86, 2.35).id == "paris"
    assert cities.nearest_active(21.54, 39.17).id == "jeddah"
    assert cities.nearest_active(30.0, 31.2) is None        # Le Caire : aucune ville
    assert cities.nearest_active(48.86, 2.35, within_km=0.1) is None


def test_config_invalide_refusee():
    base = {"cities": {"paris": {"country": "FR", "timezone": "Europe/Paris", "currency": "EUR",
                                 "map_center": {"lat": 1, "lon": 1}}}}
    cities._parse(base)
    bad = json.loads(json.dumps(base))
    bad["cities"]["paris"]["weekend_days"] = [5, 0]
    with pytest.raises(ValueError):
        cities._parse(bad)
    bad = json.loads(json.dumps(base))
    bad["cities"]["paris"]["timezone"] = "Mars/Olympus"
    with pytest.raises((KeyError, ValueError)):
        cities._parse(bad)


# ====================================================================== fenêtres

def _w(when, local_dt, city_id):
    c = cities.get(city_id)
    s, e = timewin.window(when, local_dt.astimezone(UTC), c.tz, weekend_days=c.weekend_days,
                          cutoff=c.night_cutoff_hour)
    return s.astimezone(c.tz), e.astimezone(c.tz)


def test_paris_inchange_par_rapport_a_l_ancienne_formule():
    """Paris (coupure à 0) doit se comporter exactement comme avant le multi-ville."""
    for jour in range(1, 15):                       # deux semaines de départs à 19h
        now = datetime(2026, 10, jour, 19, 0, tzinfo=PARIS)
        for when in ("today", "tomorrow", "weekend", "week"):
            local = now.astimezone(PARIS)
            day0 = local.replace(hour=0, minute=0, second=0, microsecond=0)
            if when == "today":
                exp = (local, day0 + timedelta(days=1))
            elif when == "tomorrow":
                exp = (day0 + timedelta(days=1), day0 + timedelta(days=2))
            elif when == "weekend":
                monday = day0 + timedelta(days=(7 - local.weekday()))
                exp = ((local if local.weekday() >= 5 else monday - timedelta(days=2)), monday)
            else:
                exp = (local, day0 + timedelta(days=7))
            got = main._window(when, now.astimezone(UTC))
            assert got == (exp[0].astimezone(UTC), exp[1].astimezone(UTC)), (jour, when)


def test_weekend_de_jeddah_est_vendredi_samedi():
    # Mercredi 7 oct. 2026, 15h à Jeddah
    s, e = _w("weekend", datetime(2026, 10, 7, 15, 0, tzinfo=RIYADH), "jeddah")
    assert (s.weekday(), s.hour) == (4, 5)              # vendredi 05h00 (la nuit d'avant appartient à jeudi)
    assert (e.weekday(), e.hour) == (6, 5)              # dimanche 05h00 : samedi soir inclus
    # Déjà en plein week-end : on part de maintenant.
    now = datetime(2026, 10, 9, 20, 0, tzinfo=RIYADH)   # vendredi
    s, e = _w("weekend", now, "jeddah")
    assert s == now and (e.weekday(), e.hour) == (6, 5)
    # Dimanche : le prochain week-end est dans cinq jours.
    s, e = _w("weekend", datetime(2026, 10, 11, 10, 0, tzinfo=RIYADH), "jeddah")
    assert s.weekday() == 4 and s.day == 16


def test_ce_soir_traverse_minuit_a_jeddah():
    # 23h50 : « ce soir » dure jusqu'à 05h le lendemain.
    s, e = _w("today", datetime(2026, 10, 7, 23, 50, tzinfo=RIYADH), "jeddah")
    assert e == datetime(2026, 10, 8, 5, 0, tzinfo=RIYADH)
    # 00h30 : on est encore dans la soirée d'hier, qui se termine à 05h le MÊME jour.
    s, e = _w("today", datetime(2026, 10, 8, 0, 30, tzinfo=RIYADH), "jeddah")
    assert e == datetime(2026, 10, 8, 5, 0, tzinfo=RIYADH)
    # 05h00 pile : une nouvelle journée commence.
    s, e = _w("today", datetime(2026, 10, 8, 5, 0, tzinfo=RIYADH), "jeddah")
    assert e == datetime(2026, 10, 9, 5, 0, tzinfo=RIYADH)


def test_demain_commence_la_ou_ce_soir_finit(jeddah_on):
    now = datetime(2026, 10, 7, 23, 59, tzinfo=RIYADH)
    _, end_today = _w("today", now, "jeddah")
    start_tom, end_tom = _w("tomorrow", now, "jeddah")
    assert start_tom == end_today
    assert end_tom - start_tom == timedelta(days=1)


def test_now_est_une_fenetre_courte():
    now = datetime(2026, 10, 7, 21, 0, tzinfo=RIYADH)
    s, e = _w("now", now, "jeddah")
    assert s == now and e - s == timewin.NOW_SPAN


def test_changement_d_heure_a_paris():
    # Passage à l'heure d'été : dimanche 29 mars 2026. « demain » depuis samedi dure 23 h.
    s, e = _w("tomorrow", datetime(2026, 3, 28, 12, 0, tzinfo=PARIS), "paris")
    assert (e.astimezone(UTC) - s.astimezone(UTC)) == timedelta(hours=23)


def test_when_invalide_est_refuse():
    with pytest.raises(ValueError):
        timewin.window("hier", datetime.now(UTC), PARIS)


# ===================================================================== arabe

@pytest.mark.parametrize("a,b", [
    ("أحمد", "احمد"),                   # alef avec hamza / sans
    ("مَدينَة", "مدينة"),              # voyelles brèves
    ("جـــدة", "جدة"),                  # tatweel
    ("مدرسة", "مدرسه"),                # ة / ه
    ("٢٠٢٦", "2026"),                  # chiffres arabes-indiens
    ("Al-Balad", "al balad"),
    ("Café", "cafe"),
    ("‏جدة‎", "جدة"),        # marques bidirectionnelles
])
def test_normalisation_arabe_et_latin(a, b):
    assert textnorm.norm_key(a) == textnorm.norm_key(b)


def test_normalisation_ne_confond_pas_deux_mots_differents():
    assert textnorm.norm_key("جدة") != textnorm.norm_key("مكة")
    assert textnorm.norm_key("") == "" and textnorm.norm_key(None) == ""


def test_titres_arabes_differents_ne_sont_pas_des_doublons(con):
    """Régression évitée : l'ancienne normalisation passait par ASCII et vidait tout titre
    arabe — deux titres différents devenaient deux chaînes vides, donc « identiques »."""
    t = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)
    db.upsert_events(con, [
        _jed("حفل موسيقي", t, source_id="a"),
        db.Event(source="fixture:autre", source_id="b", title="معرض الفن التشكيلي", start=t,
                 city_id="jeddah", lat=21.5433, lon=39.1728),
    ])
    assert sources.dedup_inter_source(con, now=t - timedelta(days=1)) == {"forte": 0, "faible": 0, "traduction": 0}


def test_meme_evenement_en_arabe_et_en_anglais_devient_un_seul(con):
    t = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)
    db.upsert_events(con, [
        db.Event(source="fixture:en", source_id="1", title="Jazz Night", start=t, city_id="jeddah",
                 lat=21.4860, lon=39.1877, venue_id="al-balad", category="music", description="Live jazz"),
        db.Event(source="fixture:ar", source_id="9", title="ليلة الجاز", start=t, city_id="jeddah",
                 lat=21.4861, lon=39.1877, venue_id="al-balad", category="music", description="جاز مباشر"),
    ])
    n = sources.dedup_inter_source(con, now=t - timedelta(days=1))
    assert n["traduction"] == 1
    rows = db.search(con, city_id="jeddah", lat=21.4860, lon=39.1877, radius_km=5,
                     start_from=t - timedelta(hours=1), start_to=t + timedelta(hours=2))
    assert len(rows) == 1                                  # UN événement visible
    assert rows[0]["title"] == "Jazz Night"                # langue par défaut de la ville
    assert json.loads(rows[0]["i18n"])["ar"]["title"] == "ليلة الجاز"


def test_traduction_exige_le_meme_lieu(con):
    t = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)
    db.upsert_events(con, [
        db.Event(source="fixture:en", source_id="1", title="Jazz Night", start=t, city_id="jeddah",
                 lat=21.4860, lon=39.1877, category="music"),
        db.Event(source="fixture:ar", source_id="9", title="ليلة الجاز", start=t, city_id="jeddah",
                 lat=21.70, lon=39.15, category="music"),       # 25 km plus loin
    ])
    assert sources.dedup_inter_source(con, now=t - timedelta(days=1))["traduction"] == 0


# ================================================================== référentiel

def test_referentiel_des_lieux_unifie_arabe_et_anglais():
    a = venues.resolve("jeddah", "Al-Balad")
    b = venues.resolve("jeddah", "البلد")
    c = venues.resolve("jeddah", "al balad")
    assert a and a.id == b.id == c.id == "al-balad"
    assert venues.resolve("jeddah", "Un lieu inconnu") is None
    assert venues.resolve("paris", "Al-Balad") is None          # référentiel propre à chaque ville


def test_aucun_alias_ne_designe_deux_lieux():
    venues.load(force=True)            # lève ValueError si un alias est ambigu
    ids = [v.id for v in venues.all_for("jeddah")]
    assert len(ids) == len(set(ids)) >= 5


def test_coordonnees_du_referentiel_sont_a_jeddah():
    c = cities.get("jeddah")
    for v in venues.all_for("jeddah"):
        assert cities._haversine_km(v.lat, v.lon, c.center[0], c.center[1]) < 40, v.id
        assert v.osm, f"{v.id}: pas d'identifiant OSM pour vérifier"


# =================================================================== pipeline

def test_pipeline_nettoie_et_refuse_le_dangereux():
    t = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)
    evs = [
        db.Event(source="s", source_id="1", start=t, city_id="jeddah", lat=21.54, lon=39.17,
                 title="<script>alert(1)</script>Concert &lt;b&gt;fort&lt;/b&gt;",
                 description="<img src=x onerror=alert(1)>Texte", venue="  Al-Balad ",
                 url="javascript:alert(1)", booking_url="https://example.org/tickets"),
        db.Event(source="s", source_id="2", start=t, city_id="jeddah", title="   ", lat=21.5, lon=39.1),
        db.Event(source="s", source_id="3", start=t, city_id="jeddah", title="Hors ville", lat=48.86, lon=2.35),
        db.Event(source="s", source_id="4", start=t, city_id="jeddah", title="Null Island", lat=0, lon=0),
    ]
    out, dropped = pipeline.normalize(evs, "jeddah")
    assert [e.source_id for e in out] == ["1"]
    assert dropped["sans_titre"] == 1 and dropped["hors_ville"] == 2
    e = out[0]
    assert "<" not in e.title and "script" not in e.title.lower().replace("alert", "") or "<" not in e.title
    assert "<" not in (e.description or "")
    assert e.url is None                              # javascript: refusé
    assert e.booking_url == "https://example.org/tickets"
    assert e.venue_id == "al-balad" and e.country_code == "SA"


def test_pipeline_complete_la_geo_depuis_le_referentiel_sans_ecraser_la_source():
    t = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)
    sans = db.Event(source="s", source_id="1", start=t, city_id="jeddah", title="A", venue="البلد")
    avec = db.Event(source="s", source_id="2", start=t, city_id="jeddah", title="B", venue="Al-Balad",
                    lat=21.4900, lon=39.1900)
    out, _ = pipeline.normalize([sans, avec], "jeddah")
    by = {e.source_id: e for e in out}
    assert by["1"].geo_source == "venue_ref" and by["1"].lat == pytest.approx(21.4860371)
    assert (by["2"].lat, by["2"].geo_source) == (21.49, "source")      # la source gagne


@pytest.mark.parametrize("titre,statut", [
    ("[CANCELLED] Jazz Night", "cancelled"), ("Concert annulé", "cancelled"),
    ("حفل ملغي", "cancelled"), ("Show postponed", "postponed"), ("حفل مؤجل", "postponed"),
    ("Jazz Night", "active"), ("Cancel culture talk", "active"),
])
def test_statut_deduit_du_titre(titre, statut):
    assert pipeline.infer_status(titre) == statut


def test_fusion_des_traductions_d_une_meme_source():
    t = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)
    en = db.Event(source="s", source_id="7", start=t, city_id="jeddah", title="Opening night",
                  lang="en", lat=21.54, lon=39.17)
    ar = db.Event(source="s", source_id="7", start=t, city_id="jeddah", title="ليلة الافتتاح",
                  lang="ar", lat=21.54, lon=39.17)
    out, _ = pipeline.normalize([ar, en], "jeddah")
    assert len(out) == 1 and out[0].title == "Opening night" and out[0].lang == "en"
    assert out[0].i18n["ar"]["title"] == "ليلة الافتتاح"          # texte de la source, pas traduit


# ==================================================================== sécurité

@pytest.mark.parametrize("url,ok", [
    ("https://example.org/a?b=1", True), ("http://example.org", True),
    ("javascript:alert(1)", False), ("data:text/html,<b>", False), ("file:///etc/passwd", False),
    ("//example.org", False), ("https://user:pw@example.org/", False), ("https://exa mple.org/", False),
    ("https://example.org/\x00", False), ("", False), (None, False), ("ftp://example.org", False),
])
def test_safe_url(url, ok):
    assert (safety.safe_url(url) is not None) is ok


def _resolver(ip):
    return lambda host, port: [(2, 1, 6, "", (ip, 0))]


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "172.16.0.1", "0.0.0.0"])
def test_ssrf_adresses_non_publiques_refusees(ip):
    with pytest.raises(safety.UnsafeUrl):
        safety.ensure_public_url("https://exemple.test/feed.ics", resolver=_resolver(ip))


def test_ssrf_adresse_publique_acceptee_et_domaine_inconnu_refuse():
    safety.ensure_public_url("https://exemple.test/", resolver=_resolver("93.184.216.34"))
    import socket

    def boom(host, port):
        raise socket.gaierror("nope")
    with pytest.raises(safety.UnsafeUrl):
        safety.ensure_public_url("https://exemple.test/", resolver=boom)


def test_les_redirections_vers_le_reseau_interne_sont_refusees():
    import httpx

    def handler(request):
        if request.url.host == "public.test":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="secret")

    async def go():
        async with sources.PoliteClient(min_interval=0) as c:
            c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
            return await c.get("https://public.test/feed.ics")

    async def fake_ensure(url):
        if "169.254" in url:
            raise safety.UnsafeUrl("non publique")

    orig = safety.ensure_public_url_async
    safety.ensure_public_url_async = fake_ensure
    try:
        with pytest.raises(safety.UnsafeUrl):
            asyncio.run(go())
    finally:
        safety.ensure_public_url_async = orig


# ========================================================= iCalendar : séries

def _ics(body):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//\r\n" + body + "END:VCALENDAR\r\n").encode()


def test_ics_exdate_exclut_un_jour_de_la_serie():
    raw = _ics("BEGIN:VEVENT\r\nUID:serie-1\r\nSUMMARY:Atelier\r\nDTSTART:20261010T180000Z\r\n"
               "DTEND:20261010T200000Z\r\nRRULE:FREQ=DAILY;COUNT=4\r\n"
               "EXDATE:20261011T180000Z\r\nEND:VEVENT\r\n")
    w = (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))
    evs = sources.parse_ics(raw, source="ics:x", window=w)
    days = sorted(e.start.day for e in evs)
    assert days == [10, 12, 13]                       # le 11 est EXCLU, pas « tous les jours »


def test_ics_occurrence_modifiee_et_annulee():
    raw = _ics("BEGIN:VEVENT\r\nUID:s2\r\nSUMMARY:Cours\r\nDTSTART:20261010T180000Z\r\nDTEND:20261010T190000Z\r\n"
               "RRULE:FREQ=DAILY;COUNT=3\r\nEND:VEVENT\r\n"
               "BEGIN:VEVENT\r\nUID:s2\r\nRECURRENCE-ID:20261011T180000Z\r\nSUMMARY:Cours\r\n"
               "DTSTART:20261011T200000Z\r\nDTEND:20261011T210000Z\r\nEND:VEVENT\r\n"
               "BEGIN:VEVENT\r\nUID:s2\r\nRECURRENCE-ID:20261012T180000Z\r\nSUMMARY:Cours\r\n"
               "DTSTART:20261012T180000Z\r\nSTATUS:CANCELLED\r\nEND:VEVENT\r\n")
    w = (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))
    evs = {e.start.day: e for e in sources.parse_ics(raw, source="ics:x", window=w)}
    assert evs[10].status == "active"
    assert evs[11].start.hour == 20 and evs[11].status == "active"     # horaire déplacé
    assert evs[12].status == "cancelled"
    assert len(evs) == 3                                               # remplacé, pas doublé


def test_ics_statut_cancelled():
    raw = _ics("BEGIN:VEVENT\r\nUID:c1\r\nSUMMARY:Gala\r\nDTSTART:20261010T180000Z\r\nSTATUS:CANCELLED\r\nEND:VEVENT\r\n")
    w = (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))
    assert sources.parse_ics(raw, source="ics:x", window=w)[0].status == "cancelled"


# =========================================================== base : migration

def test_migration_conserve_les_donnees_paris_et_est_idempotente(tmp_path):
    chemin = str(tmp_path / "old.db")
    old = sqlite3.connect(chemin)
    old.executescript("""
        CREATE TABLE events (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
            start TEXT NOT NULL, end TEXT, title TEXT NOT NULL, description TEXT, venue TEXT, address TEXT,
            city TEXT, lat REAL, lon REAL, price_type TEXT NOT NULL DEFAULT 'unknown',
            category TEXT NOT NULL DEFAULT 'other', url TEXT, updated_at TEXT, ingested_at TEXT NOT NULL,
            doublon_de INTEGER REFERENCES events(id), UNIQUE (source, source_id, start));
        CREATE TABLE source_health (source TEXT PRIMARY KEY, last_count INTEGER NOT NULL DEFAULT 0,
            last_nonempty TEXT, empty_streak INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
        CREATE TABLE feeds (url TEXT PRIMARY KEY, name TEXT, city TEXT, kind TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, last_ok TEXT, last_error TEXT,
            error_count INTEGER NOT NULL DEFAULT 0, etag TEXT, last_modified TEXT);
    """)
    for i in range(50):
        old.execute("INSERT INTO events (source, source_id, start, title, lat, lon, ingested_at) "
                    "VALUES ('qfap', ?, '2026-10-10T18:00:00+00:00', ?, 48.86, 2.35, '2026-10-05T10:00:00+00:00')",
                    (str(i), f"Événement {i}"))
    old.execute("INSERT INTO feeds (url, kind) VALUES ('https://x/f.ics', 'ics')")
    old.commit()
    old.close()

    for _ in range(2):                                           # idempotente
        c = db.connect(chemin)
        total = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        villes = {r[0] for r in c.execute("SELECT DISTINCT city_id FROM events")}
        statuts = {r[0] for r in c.execute("SELECT DISTINCT status FROM events")}
        feed_city = c.execute("SELECT city_id FROM feeds").fetchone()[0]
        assert (total, villes, statuts, feed_city) == (50, {"paris"}, {"active"}, "paris")
        assert c.execute("SELECT COUNT(*) FROM events WHERE first_seen IS NULL").fetchone()[0] == 0
        idx = {r["name"] for r in c.execute("PRAGMA index_list(events)")}
        assert "idx_events_city_start" in idx
        c.close()


def test_provenance_date_la_derniere_modification_reelle(con):
    t = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)
    e = db.Event(source="s", source_id="1", start=t, title="A", lat=1, lon=1)
    db.upsert_events(con, [e])
    r1 = dict(con.execute("SELECT first_seen, last_changed, content_hash FROM events").fetchone())
    import time
    time.sleep(1.1)
    db.upsert_events(con, [e])                                   # revu à l'identique
    r2 = dict(con.execute("SELECT first_seen, last_changed, content_hash, ingested_at FROM events").fetchone())
    assert r2["last_changed"] == r1["last_changed"] and r2["first_seen"] == r1["first_seen"]
    assert r2["ingested_at"] > r1["first_seen"]                  # « dernière observation » avance
    e.title = "A (modifié)"
    db.upsert_events(con, [e])
    r3 = dict(con.execute("SELECT last_changed FROM events").fetchone())
    assert r3["last_changed"] > r1["last_changed"]               # changement détecté


# ================================================== dédup : priorité et statuts

def test_la_source_prioritaire_gagne_et_un_annule_se_propage(con, monkeypatch):
    t = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)
    sources.set_priorities({"fixture:officielle": 9, "fixture:agregateur": 1})
    db.upsert_events(con, [
        db.Event(source="fixture:agregateur", source_id="a", title="Grand concert", start=t,
                 lat=21.54, lon=39.17, city_id="jeddah", status="active"),
        db.Event(source="fixture:officielle", source_id="b", title="Grand concert", start=t,
                 lat=21.54, lon=39.17, city_id="jeddah", status="cancelled"),
    ])
    sources.dedup_inter_source(con, now=t - timedelta(days=1))
    sources.set_priorities({})
    rows = db.search(con, city_id="jeddah", lat=21.54, lon=39.17, radius_km=5,
                     start_from=t - timedelta(hours=1), start_to=t + timedelta(hours=2), statuses=())
    assert len(rows) == 1 and rows[0]["source"] == "fixture:officielle"
    assert rows[0]["status"] == "cancelled"
    # Et un événement annulé ne sort JAMAIS par défaut.
    assert db.search(con, city_id="jeddah", lat=21.54, lon=39.17, radius_km=5,
                     start_from=t - timedelta(hours=1), start_to=t + timedelta(hours=2)) == []


# ============================================================ API : isolation

def test_une_requete_paris_ne_renvoie_jamais_jeddah(client, base_temp, jeddah_on):
    now = datetime.now(UTC) + timedelta(hours=2)
    _put(base_temp, [
        db.Event(source="qfap", source_id="p", title="Paris event", start=now, lat=48.8584, lon=2.347),
        # Piège : un événement ÉTIQUETÉ Jeddah mais placé au centre de Paris.
        db.Event(source="fixture:test", source_id="j", title="Jeddah event", start=now, lat=48.8584, lon=2.347,
                 city_id="jeddah"),
        _jed("Jeddah vraie", now),
    ])
    p = client.get("/api/events", params={"city": "paris", "radius": 8, "when": "week"}).json()
    assert [e["title"] for e in p["events"]] == ["Paris event"]
    j = client.get("/api/events", params={"city": "jeddah", "radius": 30, "when": "week"}).json()
    assert [e["title"] for e in j["events"]] == ["Jeddah vraie"]
    # Sans paramètre ville : Paris, comme avant (compatibilité).
    d = client.get("/api/events", params={"radius": 8, "when": "week"}).json()
    assert d["city"] == "paris" and [e["title"] for e in d["events"]] == ["Paris event"]


def test_ville_eteinte_inconnue_ou_rayon_trop_grand(client, jeddah_on, monkeypatch):
    assert client.get("/api/events", params={"city": "atlantide"}).status_code == 404
    assert client.get("/api/events", params={"city": "paris", "radius": 20}).status_code == 422
    assert client.get("/api/events", params={"city": "jeddah", "radius": 20}).status_code == 200
    monkeypatch.setenv("EVENTMAP_CITIES_DISABLED", "jeddah")                    # kill switch
    assert client.get("/api/events", params={"city": "jeddah"}).status_code == 404
    assert client.get("/api/events", params={"city": "paris"}).status_code == 200   # Paris intacte


def test_api_cities_ne_liste_que_les_villes_allumees(client, monkeypatch):
    assert [c["id"] for c in client.get("/api/cities").json()["cities"]] == ["paris"]
    monkeypatch.setenv("EVENTMAP_CITIES_ENABLED", "jeddah")
    ids = [c["id"] for c in client.get("/api/cities").json()["cities"]]
    assert ids == ["paris", "jeddah"]
    j = next(c for c in client.get("/api/cities").json()["cities"] if c["id"] == "jeddah")
    assert (j["timezone"], j["currency"], j["weekend_days"]) == ("Asia/Riyadh", "SAR", [4, 5])
    assert "sources" not in j                                 # jamais de détail interne


def test_le_front_recoit_les_prix_et_la_langue(client, base_temp, jeddah_on):
    now = datetime.now(UTC) + timedelta(hours=2)
    _put(base_temp, [_jed("Jazz Night", now, price_type="paid", price_min=150.0, price_max=300.0, currency="SAR",
                          booking_url="https://example.org/t", lang="en",
                          i18n={"ar": {"title": "ليلة الجاز", "description": "جاز"}}, category="music")])
    e = client.get("/api/events", params={"city": "jeddah", "radius": 30, "when": "week", "lang": "ar"}).json()["events"][0]
    assert e["title"] == "ليلة الجاز" and e["title_original"] == "Jazz Night" and e["lang"] == "ar"
    assert (e["price_min"], e["price_max"], e["currency"], e["is_free"]) == (150.0, 300.0, "SAR", False)
    assert e["languages"] == ["ar", "en"] and "i18n" not in e
    e = client.get("/api/events", params={"city": "jeddah", "radius": 30, "when": "week", "lang": "en"}).json()["events"][0]
    assert e["title"] == "Jazz Night"


def test_categories_de_jeddah_sans_categorie_vide(client, base_temp, jeddah_on):
    now = datetime.now(UTC) + timedelta(hours=2)
    _put(base_temp, [_jed("Concert", now, category="music"), _jed("Expo", now, category="expo", source_id="x")])
    c = client.get("/api/categories", params={"city": "jeddah"}).json()
    assert {g["key"] for g in c["groups"]} == {"concerts", "culture-art"}
    # Filtrer par groupe : « culture-art » regroupe expo + théâtre + rencontres.
    r = client.get("/api/events", params={"city": "jeddah", "radius": 30, "when": "week", "category": "culture-art"}).json()
    assert [e["title"] for e in r["events"]] == ["Expo"]


def test_evenement_unique_et_provenance(client, base_temp, jeddah_on):
    now = datetime.now(UTC) + timedelta(hours=2)
    _put(base_temp, [_jed("Gala", now)])
    con = db.connect(base_temp)
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    con.close()
    r = client.get(f"/api/events/{eid}", params={"city": "jeddah"})
    assert r.status_code == 200
    prov = r.json()["provenance"]
    assert prov["source"] == "fixture:test" and prov["first_seen"] and prov["last_seen"]
    assert client.get(f"/api/events/{eid}", params={"city": "paris"}).status_code == 404   # autre ville


# ================================================================ états honnêtes

def test_etat_des_donnees_distingue_vide_de_panne(base_temp, jeddah_on, monkeypatch):
    now = datetime.now(UTC)
    con = db.connect(base_temp)
    c = cities.get("paris")
    assert main._data_state(con, c, now)["state"] == "unknown"
    db.record_source_health(con, "qfap", 120, now, city_id="paris")
    db.record_source_health(con, "https://openagenda.com/agendas/61665301/events.v2.ics?relative[]=upcoming",
                            10, now, city_id="paris")
    assert main._data_state(con, c, now)["state"] == "ok"
    db.record_source_failure(con, "qfap", "boom", now, city_id="paris")
    db.record_source_failure(con, "qfap", "boom", now, city_id="paris")
    assert main._data_state(con, c, now)["state"] == "partial"
    con.execute("UPDATE source_health SET last_nonempty = '2020-01-01T00:00:00+00:00', error_streak = 3")
    assert main._data_state(con, c, now)["state"] == "stale"
    j = cities.get("jeddah")
    assert main._data_state(con, j, now)["state"] == "no_sources"       # ville sans source ≠ « rien ce soir »
    con.close()


def test_health_par_ville_et_sources_non_lancees(client):
    corps = client.get("/health").json()
    assert corps["cities"]["paris"]["enabled"] is True
    j = corps["cities"]["jeddah"]
    assert j["enabled"] is False
    raisons = {x["id"]: x["reason"] for x in j["not_running"]}
    assert "refused" in raisons["visitsaudi-jeddah"] and "refused" in raisons["hayyjameel"]
    assert "pending" in raisons["webook"]


# ============================================ registre / interrupteurs de source

def test_une_source_non_autorisee_ne_tourne_jamais(monkeypatch):
    specs = {s.id: s for s in registry.load()}
    assert specs["qfap"].runnable() and specs["ficep"].runnable()
    monkeypatch.setenv("EVENTMAP_CITIES_ENABLED", "jeddah")
    for sid in ("visitsaudi-jeddah", "hayyjameel", "webook"):
        assert not specs[sid].runnable()                        # même ville allumée : autorisation requise
    monkeypatch.setenv("EVENTMAP_SOURCES_DISABLED", "ficep")
    assert not specs["ficep"].runnable() and specs["qfap"].runnable()


def test_registre_coherent_avec_les_villes():
    assert registry.validate(registry.load()) == []


def test_kill_switch_de_source_et_de_ville_dans_le_refresh(con, monkeypatch):
    fetchers, by_key = main._build_fetchers(con)
    specs = {s.id: s for s in registry.load()}
    attendues = {s.key for s in specs.values() if s.runnable()}
    assert set(by_key) == attendues and len(attendues) >= 4          # qfap + ficep + bataclan + openagenda-idf
    monkeypatch.setenv("EVENTMAP_SOURCES_DISABLED", ",".join(i for i in specs if i != "qfap"))
    fetchers, by_key = main._build_fetchers(con)
    assert set(by_key) == {"qfap"}
    monkeypatch.setenv("EVENTMAP_CITIES_DISABLED", "paris")
    assert main._build_fetchers(con)[1] == {}


def test_cadence_une_source_recente_n_est_pas_relancee(con):
    now = datetime.now(UTC)
    db.record_source_health(con, "qfap", 100, now - timedelta(hours=1), city_id="paris")
    _, by_key = main._build_fetchers(con, now)
    assert "qfap" not in by_key                 # refresh_hours = 6, dernier succès il y a 1 h
    db.record_source_health(con, "qfap", 100, now - timedelta(hours=6), city_id="paris")
    _, by_key = main._build_fetchers(con, now)
    assert "qfap" in by_key


def test_timeout_et_reprise_par_source():
    appels = {"n": 0}

    async def capricieuse(client):
        appels["n"] += 1
        if appels["n"] < 2:
            raise RuntimeError("panne passagère")
        return [db.Event(source="s", source_id="1", title="ok", start=datetime.now(UTC))]

    async def lente(client):
        await asyncio.sleep(5)
        return []

    res = asyncio.run(sources.aggregate({"a": capricieuse, "b": lente},
                                        policies={"a": {"retries": 1, "timeout_s": 5}, "b": {"timeout_s": 0.2}}))
    by = {r.name: r for r in res}
    assert by["a"].ok and appels["n"] == 2             # une reprise a suffi
    assert not by["b"].ok and "Timeout" in by["b"].error   # la lente n'a pas bloqué la première


# ====================================================================== pages

def test_jeddah_eteinte_rend_404_et_n_est_pas_dans_le_sitemap(client):
    assert client.get("/jeddah").status_code == 404
    assert client.get("/ar/jeddah").status_code == 404
    assert "jeddah" not in client.get("/sitemap.xml").text


def test_pages_de_jeddah_langues_et_sens(client, jeddah_on):
    en = client.get("/jeddah/carte").text
    assert '<html lang="en" dir="ltr">' in en
    # L'accueil de Jeddah EST la carte : /jeddah/carte est le même document, canonique vers /jeddah.
    assert 'rel="canonical" href="https://eventmap.hamdy-tabsissi.com/jeddah"' in en
    assert 'hreflang="ar" href="https://eventmap.hamdy-tabsissi.com/ar/jeddah"' in en
    assert 'hreflang="x-default"' in en and "Tonight in Jeddah" in en
    ar = client.get("/ar/jeddah/carte").text
    assert '<html lang="ar" dir="rtl">' in ar and "هذا المساء في جدة" in ar
    assert client.get("/ar/paris").status_code == 404          # Paris n'a pas d'interface arabe
    assert client.get("/en/jeddah").status_code == 404         # la langue par défaut n'a pas de préfixe
    assert client.get("/jeddah/inconnu").status_code == 404


def test_choix_de_ville_quand_il_y_en_a_deux(client, jeddah_on):
    html = client.get("/").text
    assert "Find something worth doing tonight." in html and 'href="/paris"' in html and 'href="/jeddah"' in html
    assert "More cities coming soon" in html
    assert 'rel="canonical" href="https://eventmap.hamdy-tabsissi.com/"' in html
    sm = client.get("/sitemap.xml").text
    for chemin in ("/", "/paris", "/fr/paris", "/jeddah", "/ar/jeddah"):
        assert f"<loc>https://eventmap.hamdy-tabsissi.com{chemin}</loc>" in sm
    assert "/carte" not in sm          # doublons de l'accueil (même canonical)


def test_contenu_indexable_et_donnees_structurees(client, base_temp, jeddah_on):
    # Un événement EN COURS (commencé il y a 10 min, fini dans 2 h) : il chevauche toujours « ce soir »,
    # quelle que soit l'heure à laquelle le test tourne (un « dans 1 h » sortait de la soirée de Jeddah
    # entre 04 h et 05 h, heure locale — test écrit à 03 h, cassé à 04 h 35).
    now = datetime.now(UTC) - timedelta(minutes=10)
    _put(base_temp, [_jed("Jazz Night", now, end=now + timedelta(hours=2), venue="Al-Balad", category="music", price_type="free")])
    html = client.get("/jeddah/carte").text
    assert "Jazz Night" in html                                  # dans le HTML, sans JavaScript
    ld = json.loads(html.split('<script type="application/ld+json">')[1].split("</script>")[0])
    evt = next(x for x in ld["@graph"] if x["@type"] == "Event")
    assert evt["name"] == "Jazz Night" and evt["isAccessibleForFree"] is True
    assert evt["location"]["address"]["addressCountry"] == "SA"
    assert evt["startDate"].endswith("+03:00")                   # fuseau de Jeddah
    assert "geo" in evt["location"]


def test_fiche_evenement_partageable(client, base_temp, jeddah_on):
    now = datetime.now(UTC) + timedelta(hours=1)
    _put(base_temp, [_jed("Gala", now, url="https://example.org/gala")])
    con = db.connect(base_temp)
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    con.close()
    r = client.get(f"/jeddah/e/{eid}")
    assert r.status_code == 200 and "Gala" in r.text
    assert f'rel="canonical" href="https://eventmap.hamdy-tabsissi.com/jeddah/e/{eid}"' in r.text
    assert client.get(f"/paris/e/{eid}").status_code == 404      # id d'une autre ville
    assert client.get("/jeddah/e/99999").status_code == 404
    assert client.get("/jeddah/e/abc").status_code == 404


def test_injection_dans_un_titre_est_echappee_partout(client, base_temp, jeddah_on):
    now = datetime.now(UTC) + timedelta(hours=1)
    piege = '"><script>alert(1)</script></script><img src=x onerror=alert(1)>'
    _put(base_temp, [_jed(piege, now, description=piege, venue=piege)])
    con = db.connect(base_temp)
    eid = con.execute("SELECT id FROM events").fetchone()[0]
    con.close()
    for chemin in ("/jeddah", "/jeddah/carte", f"/jeddah/e/{eid}", "/ar/jeddah"):
        html = client.get(chemin).text
        # Aucune balise issue de la source ne survit : seul du texte inerte (« &lt; », « < »).
        assert "<script>alert" not in html and "<img src=x" not in html
        assert "</script></script>" not in html
        # Les <script> présents sont les nôtres : JSON-LD, config, fichiers /static.
        for ouvrant in html.split("<script")[1:]:
            assert ouvrant.startswith((' type="application/ld+json">', ' type="application/json" id="em-city">', ' src="/static/')), ouvrant[:60]


def test_head_et_get_sur_les_nouvelles_pages(client, jeddah_on):
    for chemin in ("/", "/paris", "/jeddah", "/ar/jeddah/carte"):
        assert client.head(chemin).status_code == 200


def test_une_seule_ville_active_garde_slash_comme_canonique(client):
    html = client.get("/").text
    assert 'rel="canonical" href="https://eventmap.hamdy-tabsissi.com/"' in html
    assert client.get("/paris").text.count('rel="canonical" href="https://eventmap.hamdy-tabsissi.com/"') == 1


# ============================================================ liens morts (13.47)

def _client_with(handler):
    import httpx
    c = sources.PoliteClient(min_interval=0, public_only=False)
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return c


def test_verdict_d_un_lien():
    import httpx

    import linkcheck

    async def go():
        codes = {"/gone": 404, "/ok": 200, "/robots": 403, "/boom": 503}
        c = _client_with(lambda req: httpx.Response(codes[req.url.path]))
        async with c:
            return [await linkcheck.check_url(c, f"https://exemple.test{p}") for p in codes]

    assert asyncio.run(go()) == ["dead", "ok", "ok", "unknown"]


def test_lien_mort_masque_les_boutons_mais_garde_la_trace(client, base_temp, jeddah_on):
    import httpx

    import linkcheck
    now = datetime.now(UTC) + timedelta(hours=2)
    _put(base_temp, [_jed("Gala", now, url="https://exemple.test/gone", booking_url="https://exemple.test/gone"),
                     _jed("Concert", now, source_id="c2", url="https://exemple.test/ok")])

    async def go():
        c = _client_with(lambda req: httpx.Response(404 if req.url.path == "/gone" else 200))
        con = db.connect(base_temp)
        async with c:
            res = await linkcheck.run(con, c, "jeddah", datetime.now(UTC))
        con.commit()
        con.close()
        return res

    assert asyncio.run(go()) == {"checked": 2, "dead": 1}
    evs = {e["title"]: e for e in client.get("/api/events", params={"city": "jeddah", "radius": 30, "when": "week"}).json()["events"]}
    assert evs["Gala"]["link_dead"] is True and evs["Gala"]["url"] is None and evs["Gala"]["booking_url"] is None
    assert "link_dead" not in evs["Concert"] and evs["Concert"]["url"] == "https://exemple.test/ok"
    con = db.connect(base_temp)
    assert con.execute("SELECT url FROM events WHERE title = 'Gala'").fetchone()[0] == "https://exemple.test/gone"  # trace conservée
    con.close()


def test_linkcheck_respecte_son_budget_et_ne_reverifie_pas_trop_vite(con):
    import httpx

    import linkcheck
    t = datetime.now(UTC) + timedelta(hours=3)
    db.upsert_events(con, [_jed(f"E{i}", t + timedelta(minutes=i), source_id=str(i), url=f"https://exemple.test/{i}") for i in range(10)])

    async def go(budget):
        c = _client_with(lambda req: httpx.Response(200))
        async with c:
            return await linkcheck.run(con, c, "jeddah", datetime.now(UTC), budget=budget)

    assert asyncio.run(go(4))["checked"] == 4
    assert asyncio.run(go(100))["checked"] == 6          # les 4 premiers ne sont pas revus avant 2 jours


# ================================================== l'API reste joignable pendant un cycle

def test_la_phase_d_ecriture_du_refresh_ne_gele_pas_la_boucle(monkeypatch, base_temp):
    """Régression constatée en production le 06/10/2026 : l'écriture du cycle (normalisation,
    upsert, dédoublonnage) tournait DANS la boucle d'événements et /health restait muet
    ~45 s. Elle doit tourner dans un fil à part."""
    import time as _time

    def lourde(*a, **k):
        _time.sleep(0.8)                                   # travail synchrone qui occupe le CPU
        return 0, 0, {"forte": 0, "faible": 0, "traduction": 0}, 0, {}

    async def rien(*a, **k):
        return []

    monkeypatch.setattr(main, "_persist", lourde)
    monkeypatch.setattr(sources, "aggregate", rien)
    monkeypatch.setattr(main, "_build_fetchers", lambda con, now=None: ({}, {}))

    async def go():
        retards = []

        async def battement():
            last = asyncio.get_running_loop().time()
            for _ in range(30):
                await asyncio.sleep(0.05)
                now = asyncio.get_running_loop().time()
                retards.append(now - last - 0.05)
                last = now

        await asyncio.gather(main.refresh(), battement())
        return max(retards)

    assert asyncio.run(go()) < 0.35        # sans fil à part : ~0,8 s


def test_dedup_rapide_et_identique_sur_un_gros_volume(con):
    """Le dédoublonnage normalisait titres et lieux à CHAQUE comparaison (7 s pour 16 000
    lignes en local). Même résultat, normalisation faite une fois par ligne."""
    import time as _time
    base = datetime(2026, 10, 10, 18, 0, tzinfo=UTC)
    evs = []
    for i in range(3000):
        evs.append(db.Event(source="qfap", source_id=f"q{i}", title=f"Spectacle numéro {i} au théâtre {i % 40}",
                            start=base + timedelta(minutes=(i % 90) * 7), lat=48.85 + (i % 50) / 1000, lon=2.35,
                            venue=f"Salle {i % 40}"))
    for i in range(0, 3000, 100):                         # 30 vrais doublons d'une autre source
        evs.append(db.Event(source="ics:x", source_id=f"f{i}", title=f"Spectacle numéro {i} au théâtre {i % 40}",
                            start=base + timedelta(minutes=(i % 90) * 7 + 5), lat=48.85 + (i % 50) / 1000, lon=2.35,
                            venue=f"Salle {i % 40}"))
    db.upsert_events(con, evs)
    t = _time.perf_counter()
    n = sources.dedup_inter_source(con, now=base - timedelta(days=1))
    assert _time.perf_counter() - t < 8.0
    # Résultat de référence de l'ALGORITHME D'AVANT l'optimisation sur ces mêmes données
    # (mesuré le 06/10/2026 avec le code de master) : 14 forts + 16 faibles. Les « faibles »
    # sont de vrais rapprochements de l'algorithme (titres à un chiffre près dans le même lieu).
    assert (n["forte"], n["faible"], n["traduction"]) == (14, 16, 0)



# ==================================================== alertes par ville et par source (13.53)

def _iso(dt):
    return dt.isoformat(timespec="seconds")


def test_aucune_alerte_sur_un_service_sain(client, base_temp, monkeypatch):
    con = db.connect(base_temp)
    now = datetime.now(UTC)
    db.record_source_health(con, "qfap", 120, now, city_id="paris")
    db.record_source_health(con, registry.load()[1].url, 10, now, city_id="paris")
    con.commit()
    con.close()
    monkeypatch.setitem(main._refresh_state, "last_run", _iso(now))
    r = client.get("/health", params={"strict": 1})
    assert r.status_code == 200 and r.json()["alerts"] == []


@pytest.mark.parametrize("prepare,kind", [
    (lambda c, now: [db.record_source_failure(c, "qfap", "x", now, city_id="paris") for _ in range(3)], "ingestion_errors"),
    (lambda c, now: [db.record_source_health(c, "qfap", 0, now, city_id="paris") for _ in range(2)], "silent_source"),
    (lambda c, now: c.execute("UPDATE source_health SET last_nonempty = '2020-01-01T00:00:00+00:00' WHERE source='qfap'"), "stale_data"),
    (lambda c, now: db.record_source_health(c, "qfap", 100, now, city_id="paris", valid=100, geo_missing=60), "geocoding"),
])
def test_chaque_alerte_est_nommee_et_donne_503_en_mode_strict(client, base_temp, monkeypatch, prepare, kind):
    con = db.connect(base_temp)
    now = datetime.now(UTC)
    db.record_source_health(con, "qfap", 120, now - timedelta(minutes=1), city_id="paris")
    db.record_source_health(con, registry.load()[1].url, 10, now, city_id="paris")
    prepare(con, now)
    con.commit()
    con.close()
    monkeypatch.setitem(main._refresh_state, "last_run", _iso(now))
    r = client.get("/health", params={"strict": 1})
    assert r.status_code == 503
    assert kind in {a["kind"] for a in r.json()["alerts"]}
    # Sans strict : le meme etat reste 200 - la sonde de disponibilite ne sonne pas.
    r = client.get("/health")
    assert r.status_code == 200 and kind in {a["kind"] for a in r.json()["alerts"]}


def test_alerte_refresh_arrete_et_periode_de_grace(base_temp, monkeypatch):
    con = db.connect(base_temp)
    now = datetime.now(UTC)
    monkeypatch.setitem(main._refresh_state, "last_run", _iso(now - timedelta(hours=14)))
    assert "refresh_stalled" in {a["kind"] for a in main.compute_alerts(con, now)}
    monkeypatch.setitem(main._refresh_state, "last_run", _iso(now - timedelta(hours=5)))
    assert "refresh_stalled" not in {a["kind"] for a in main.compute_alerts(con, now)}
    # Tout juste demarre, aucun cycle encore : pas d'alerte (le premier part 5 s apres le demarrage).
    monkeypatch.setitem(main._refresh_state, "last_run", None)
    monkeypatch.setattr(main, "_STARTED", now - timedelta(minutes=2))
    assert main.compute_alerts(con, now) == []
    # Mais 30 minutes plus tard sans cycle ni donnee : on sonne.
    monkeypatch.setattr(main, "_STARTED", now - timedelta(minutes=30))
    kinds = {a["kind"] for a in main.compute_alerts(con, now)}
    assert {"refresh_stalled", "never_ingested"} <= kinds
    con.close()


def test_une_ville_eteinte_ou_une_source_non_autorisee_ne_sonne_pas(base_temp, monkeypatch):
    con = db.connect(base_temp)
    now = datetime.now(UTC)
    monkeypatch.setattr(main, "_STARTED", now - timedelta(hours=5))
    monkeypatch.setitem(main._refresh_state, "last_run", _iso(now))
    assert not [a for a in main.compute_alerts(con, now) if a["city"] == "jeddah"]
    monkeypatch.setenv("EVENTMAP_CITIES_ENABLED", "jeddah")      # allumee mais sans source autorisee
    assert not [a for a in main.compute_alerts(con, now) if a["city"] == "jeddah"]
    con.close()


def test_ville_eteinte_rend_une_404_lisible_pas_du_json_brut(client):
    r = client.get("/jeddah")
    assert r.status_code == 404 and "text/html" in r.headers["content-type"]
    assert "Page not found" in r.text and 'href="/paris"' in r.text and "noindex" in r.text
    assert "jeddah" not in r.text.lower()                  # une ville éteinte n'est pas annoncée
    assert client.head("/jeddah").status_code == 404


def test_le_lieu_s_affiche_dans_la_langue_de_l_interface(client, base_temp, jeddah_on):
    now = datetime.now(UTC) + timedelta(hours=2)
    ev = _jed("Jazz", now, venue="Jeddah Yacht Club", lat=21.6529588, lon=39.1015469)
    ev.venue_id = "jeddah-yacht-club"
    _put(base_temp, [ev])
    par_langue = {lg: client.get("/api/events", params={"city": "jeddah", "radius": 30, "when": "week", "lang": lg}).json()["events"][0]["venue"]
                  for lg in ("en", "ar")}
    assert par_langue == {"en": "Jeddah Yacht Club", "ar": "نادي جدة لليخوت"}


# ============================================= connecteur sitemap + JSON-LD (Bataclan, 2.7)

_PAGE = ('<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"Event",'
         '"name":"A2H","startDate":"2026-11-20T18:00:00.000Z","endDate":null,'
         '"eventStatus":"https://schema.org/EventScheduled",'
         '"location":{"@type":"Place","name":"Bataclan"},"description":"<p>Rap &amp; soul</p>",'
         '"offers":{"@type":"Offer","url":"https://billetterie.example/a2h","priceCurrency":"EUR"}}</script></head></html>')
_OPTS = {"include": "/evenement/", "exclude": "_en$", "date_in_url": r"_(\d{4}-\d{2}-\d{2})$", "tz": "Europe/Paris",
         "naive_utc_label": False, "default_category": "music",
         "venue": {"name": "Bataclan", "address": "50 boulevard Voltaire, 75011 Paris", "lat": 48.8631, "lon": 2.3709}}


def test_un_Z_est_de_l_UTC_sauf_option_explicite():
    import sources_jsonld
    ev = sources_jsonld.events_from_page(_PAGE, "https://www.bataclan.fr/evenement/a2h_2026-11-20", _OPTS, "jsonld_sitemap:x")[0]
    # Le Bataclan donne « 18:00:00.000Z » : c'est de l'UTC (vérifié sur son déroulé horaire), soit 19:00 à Paris en novembre.
    assert ev.start.astimezone(PARIS).strftime("%H:%M") == "19:00"
    assert ev.start.utcoffset() is not None
    assert (ev.title, ev.source_id, ev.venue, ev.lat, ev.category) == ("A2H", "a2h_2026-11-20", "Bataclan", 48.8631, "music")
    assert ev.booking_url == "https://billetterie.example/a2h" and ev.currency == "EUR"
    # L'option existe pour un site qui écrirait l'heure LOCALE avec un « Z » : alors le Z est ignoré.
    ev2 = sources_jsonld.events_from_page(_PAGE, "https://x/evenement/a", {**_OPTS, "naive_utc_label": True}, "s")[0]
    assert ev2.start.astimezone(PARIS).strftime("%H:%M") == "18:00"


def test_statuts_schema_org():
    import sources_jsonld
    for url, attendu in (("https://schema.org/EventCancelled", "cancelled"), ("https://schema.org/EventPostponed", "postponed"),
                         ("https://schema.org/EventScheduled", "active")):
        html = _PAGE.replace("https://schema.org/EventScheduled", url)
        assert sources_jsonld.events_from_page(html, "https://x/evenement/a", _OPTS, "s")[0].status == attendu


def _transport(pages, sitemap):
    import httpx

    def handler(req):
        p = req.url.path
        if p == "/sitemap.xml":
            return httpx.Response(200, text=sitemap)
        return httpx.Response(200, text=pages[p]) if p in pages else httpx.Response(404)
    return httpx.MockTransport(handler)


def test_sitemap_filtre_les_pages_passees_et_les_copies_en_anglais():
    import httpx

    import sources_jsonld
    sm = "".join(f"<url><loc>https://salle.test{p}</loc></url>" for p in (
        "/evenement/a2h_2026-11-20", "/evenement/a2h_2026-11-20_en", "/evenement/ancien_2020-01-01", "/programmation"))
    vus = []

    def handler(req):
        vus.append(req.url.path)
        return httpx.Response(200, text=sm if req.url.path == "/sitemap.xml" else _PAGE)

    async def go():
        c = sources_jsonld.PoliteClient(min_interval=0, public_only=False)
        c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        async with c:
            return await sources_jsonld.fetch(c, "https://salle.test/sitemap.xml", _OPTS, "jsonld_sitemap:t", today=date(2026, 10, 6))

    evs = asyncio.run(go())
    assert [e.source_id for e in evs] == ["a2h_2026-11-20"]
    assert vus == ["/sitemap.xml", "/evenement/a2h_2026-11-20"]       # ni le passé, ni la copie _en, ni la page de liste


def test_une_panne_silencieuse_du_parseur_n_est_pas_une_liste_vide():
    import httpx

    import sources_jsonld

    async def run(sitemap, page_html, status=200):
        def handler(req):
            return httpx.Response(200, text=sitemap) if req.url.path == "/sitemap.xml" else httpx.Response(status, text=page_html)
        c = sources_jsonld.PoliteClient(min_interval=0, public_only=False)
        c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        async with c:
            return await sources_jsonld.fetch(c, "https://salle.test/sitemap.xml", _OPTS, "t", today=date(2026, 10, 6))

    un = "<url><loc>https://salle.test/evenement/a_2026-11-20</loc></url>"
    with pytest.raises(ValueError):
        asyncio.run(run("", ""))                               # sitemap illisible
    with pytest.raises(ValueError):
        asyncio.run(run(un, "<html>refonte : plus de JSON-LD</html>"))   # pages lues, plus aucun Event
    with pytest.raises(RuntimeError):
        asyncio.run(run(un, "", status=500))                   # toutes les pages en échec


def test_la_source_bataclan_est_declaree_et_lancable():
    specs = {s.id: s for s in registry.load()}
    b = specs["bataclan"]
    assert b.kind == "jsonld_sitemap" and b.city_id == "paris" and b.authorization == "ok" and b.runnable()
    assert b.options["naive_utc_label"] is False and b.options["venue"]["lat"] == 48.8631
    assert registry.validate(list(specs.values())) == []


# ================================================== OpenAgenda via Opendatasoft (2.5)

_REC = {"uid": "96691677", "title_fr": "Exposition de Matthieu Ricard", "description_fr": "<p>Photographies</p>",
        "conditions_fr": "Entrée libre", "keywords_fr": "exposition;photo", "location_name": "Galerie Livinec",
        "location_address": "24 rue de Penthièvre, 75008 Paris", "location_city": "Paris",
        "location_coordinates": {"lon": 2.315075, "lat": 48.872728}, "canonicalurl": "https://openagenda.com/x/events/y",
        "updatedat": "2026-06-17T10:15:38+00:00",
        "timings": json.dumps([{"begin": "2026-10-29T11:00:00+01:00", "end": "2026-10-29T19:00:00+01:00"},
                               {"begin": "2026-10-31T11:00:00+01:00", "end": "2026-10-31T19:00:00+01:00"},
                               {"begin": "2020-01-01T11:00:00+01:00", "end": "2020-01-01T19:00:00+01:00"}])}
_WIN = (datetime(2026, 10, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))


def test_ods_chaque_creneau_reel_devient_une_ligne_et_le_passe_est_ignore():
    import sources_ods
    evs = sources_ods.record_to_events(_REC, "ods_openagenda:t", _WIN)
    assert [e.start.astimezone(PARIS).strftime("%d/%m %H:%M") for e in evs] == ["29/10 11:00", "31/10 11:00"]   # pas le 30, pas 2020
    e = evs[0]
    assert (e.source_id, e.venue, e.lat, e.price_type, e.url) == ("96691677", "Galerie Livinec", 48.872728, "free", "https://openagenda.com/x/events/y")
    assert e.category == "expo"


@pytest.mark.parametrize("cond,attendu", [
    ("Entrée libre", "free"), ("Gratuit", "free"), ("10 € plein tarif", "paid"), ("Billetterie en ligne", "paid"),
    ("Gratuit pour les moins de 12 ans, 10 € sinon", "unknown"), (None, "unknown"), ("Sur inscription", "unknown")])
def test_ods_le_prix_n_est_deduit_que_si_la_source_est_sans_ambiguite(cond, attendu):
    import sources_ods
    assert sources_ods._price_type(cond) == attendu


def test_ods_enregistrements_inexploitables_ignores_sans_planter():
    import sources_ods
    for rec in ({}, {**_REC, "uid": None}, {**_REC, "title_fr": " "}, {**_REC, "location_coordinates": {}},
                {**_REC, "timings": "pas du json"}, {**_REC, "timings": None}):
        assert sources_ods.record_to_events(rec, "s", _WIN) == []


def test_ods_plafond_de_creneaux_par_evenement():
    import sources_ods
    rec = {**_REC, "timings": json.dumps([{"begin": f"2026-11-{d:02d}T10:00:00+01:00"} for d in range(1, 29)] * 4)}
    assert len(sources_ods.record_to_events(rec, "s", _WIN)) == sources_ods.MAX_SLOTS_PER_EVENT


def test_ods_pagination_et_pannes_non_masquees():
    import httpx

    import sources_ods
    pages = {0: [{**_REC, "uid": str(i)} for i in range(100)], 100: [{**_REC, "uid": "last"}]}

    def make(handler):
        async def go():
            c = sources.PoliteClient(min_interval=0, public_only=False)
            c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
            async with c:
                return await sources_ods.fetch(c, {}, "ods_openagenda:t")
        return go

    def ok(req):
        off = int(req.url.params["offset"])
        return httpx.Response(200, json={"total_count": 101, "results": pages.get(off, [])})

    # fenêtre d'ingestion = maintenant → on déplace les créneaux de la fixture dans le futur proche
    fut = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    for lst in pages.values():
        for r in lst:
            r["timings"] = json.dumps([{"begin": fut, "end": None}])
    evs = asyncio.run(make(ok)())
    assert len(evs) == 101 and len({e.source_id for e in evs}) == 101

    with pytest.raises(ValueError):                                      # filtre cassé = 0 enregistrement
        asyncio.run(make(lambda req: httpx.Response(200, json={"total_count": 0, "results": []}))())
    with pytest.raises(ValueError):                                      # champ « timings » renommé
        asyncio.run(make(lambda req: httpx.Response(200, json={"total_count": 1, "results": [{**_REC, "timings": None}]}))())
    with pytest.raises(httpx.HTTPStatusError):                           # API en erreur = source en erreur
        asyncio.run(make(lambda req: httpx.Response(500))())


def test_la_source_openagenda_est_declaree_sous_licence_ouverte():
    s = {x.id: x for x in registry.load()}["openagenda-idf"]
    assert (s.kind, s.city_id, s.authorization, s.priority) == ("ods_openagenda", "paris", "ok", 4)
    assert "Licence Ouverte" in s.attribution and s.runnable()
