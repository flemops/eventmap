"""Tests unitaires sans réseau : parsing, fenêtres temporelles, dédoublonnage,
idempotence de l'upsert, recherche géo. `pytest -q` depuis la racine."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from fastapi import HTTPException

import cultures
import db
import discover
import main
import sources
import sources_paris
from main import _window

TZ = ZoneInfo("Europe/Paris")


# ------------------------------------------------------------- occurrences

def test_parse_occurrences_multi():
    raw = "2026-09-18T14:00:00+02:00_2026-09-18T15:00:00+02:00;2026-09-19T14:00:00+02:00_2026-09-19T15:00:00+02:00"
    out = sources_paris.parse_occurrences(raw)
    assert len(out) == 2
    assert out[0][0].isoformat() == "2026-09-18T14:00:00+02:00"
    assert out[1][1].isoformat() == "2026-09-19T15:00:00+02:00"


def test_parse_occurrences_tolerates_garbage():
    assert sources_paris.parse_occurrences("") == []
    assert sources_paris.parse_occurrences(None) == []
    assert sources_paris.parse_occurrences("pas-une-date_x;2026-01-01T10:00:00+01:00_") == [
        (datetime.fromisoformat("2026-01-01T10:00:00+01:00"), None)
    ]


def test_qfap_record_explodes_into_one_row_per_slot():
    now = datetime.now(timezone.utc)
    window = (now - timedelta(days=1), now + timedelta(days=90))
    d1, d2 = now + timedelta(days=2), now + timedelta(days=5)
    rec = {
        "id": "42", "title": "Concert test", "lead_text": "<p>Résumé</p>",
        "occurrences": f"{d1.isoformat()}_{(d1 + timedelta(hours=2)).isoformat()};{d2.isoformat()}_{(d2 + timedelta(hours=2)).isoformat()}",
        "lat_lon": {"lat": 48.86, "lon": 2.35}, "price_type": "gratuit",
        "qfap_tags": "Concert;Musique", "address_city": "Paris", "url": "https://x",
    }
    evs = sources_paris.to_events(rec, window)
    assert len(evs) == 2
    assert {e.source_id for e in evs} == {"42"}
    assert evs[0].price_type == "free" and evs[0].category == "music"
    assert evs[0].description == "Résumé"


def test_qfap_missing_key_is_loud_but_empty_value_is_silent():
    window = (datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(days=1))
    with pytest.raises(KeyError):
        sources_paris.to_events({"id": "1", "title": "x"}, window)          # clé absente
    assert sources_paris.to_events({"id": "1", "title": "x", "occurrences": ""}, window) == []


# ------------------------------------------------------------------ fenêtres

@pytest.mark.parametrize("weekday,expect_start_is_now", [(0, False), (4, False), (5, True), (6, True)])
def test_weekend_window(weekday, expect_start_is_now):
    # 2026-08-17 est un lundi
    base = datetime(2026, 8, 17, 15, 0, tzinfo=TZ) + timedelta(days=weekday)
    start, end = _window("weekend", base.astimezone(timezone.utc))
    assert end.astimezone(TZ).weekday() == 0 and end.astimezone(TZ).hour == 0   # lundi 00:00
    if expect_start_is_now:
        assert start == base.astimezone(timezone.utc)
    else:
        assert start.astimezone(TZ).weekday() == 5 and start.astimezone(TZ).hour == 0


def test_today_starts_now_not_midnight():
    base = datetime(2026, 8, 20, 18, 30, tzinfo=TZ)
    start, end = _window("today", base.astimezone(timezone.utc))
    assert start == base.astimezone(timezone.utc)
    assert end.astimezone(TZ) == datetime(2026, 8, 21, 0, 0, tzinfo=TZ)


# ----------------------------------------------------------------- dedupe

def _ev(title, start, lat=48.86, lon=2.35, src="a", desc=None, source_id=None,
       venue=None, price_type="unknown"):
    return db.Event(source=src, source_id=source_id or title, start=start, title=title,
                    lat=lat, lon=lon, description=desc, venue=venue, price_type=price_type)


def test_parse_ics_uid_splits_on_double_slash():
    """OpenAgenda (FICEP) encode l'occurrence après « // » dans l'UID :
    35008205//20260917T080000Z -> identifiant stable 35008205."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    dt = (now + timedelta(days=1)).strftime("%Y%m%dT%H%M%SZ")
    ics = f"""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:35008205//{dt}
SUMMARY:Concert
DTSTART:{dt}
END:VEVENT
END:VCALENDAR"""
    evs = sources.parse_ics(ics, source="ics", window=(now, now + timedelta(days=20)))
    assert evs[0].source_id == "35008205"


def test_parse_ics_reads_geo():
    """Régression du 03/09 : icalendar>=6 renvoie un `str` depuis
    `vGeo.to_ical()`, pas des `bytes` — un `.decode()` non gardé faisait
    échouer silencieusement le parsing GEO de 100 % des événements FICEP."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    dt = (now + timedelta(days=1)).strftime("%Y%m%dT%H%M%SZ")
    ics = f"""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:test-geo
SUMMARY:Concert
DTSTART:{dt}
GEO:48.858764;2.342319
END:VEVENT
END:VCALENDAR"""
    evs = sources.parse_ics(ics, source="ics", window=(now, now + timedelta(days=20)))
    assert evs[0].lat == pytest.approx(48.858764)
    assert evs[0].lon == pytest.approx(2.342319)


# ----------------------------------------------------------- base + search

@pytest.fixture
def con(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


# ----------------------------------------------------- dédup inter-sources

def test_dedup_forte_merges_and_search_hides_loser(con):
    """Non-régression du paquet du 03/09 : l'expo Sumo de la MCJP, présente
    dans QFAP et FICEP, doit produire UN événement visible, en gardant la
    géo de FICEP (plus fiable, GEO natif) et le prix de QFAP (FICEP ne le
    connaît pas)."""
    t = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)
    qfap = db.Event(source="qfap", source_id="qfap-sumo", title="Sumo, forces sacrées",
                    start=t, venue="Maison de la culture du Japon à Paris",
                    lat=48.8540, lon=2.2920, price_type="free",
                    description="Description QFAP, plus détaillée")
    ficep = db.Event(
        source="ics:https://openagenda.com/agendas/61665301/events.v2.ics?relative[]=upcoming",
        source_id="35008205", title="Sumo, forces sacrées", start=t + timedelta(minutes=10),
        venue="Maison de la Culture du Japon à Paris",  # casse différente : même lieu
        lat=48.8546, lon=2.2926, price_type="unknown", description="via OpenAgenda",
    )
    db.upsert_events(con, [qfap, ficep])
    counts = sources.dedup_inter_source(con, now=t - timedelta(days=1))
    assert counts == {"forte": 1, "faible": 0}

    visible = db.search(con, lat=48.8546, lon=2.2926, radius_km=1,
                        start_from=t - timedelta(hours=1), start_to=t + timedelta(hours=1))
    assert len(visible) == 1
    assert visible[0]["title"] == "Sumo, forces sacrées"
    assert (visible[0]["lat"], visible[0]["lon"]) == (48.8546, 2.2926)   # géo FICEP conservée
    assert visible[0]["price_type"] == "free"                            # prix QFAP conservé


def test_dedup_keeps_distant_or_different_titled_events(con):
    t = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)
    db.upsert_events(con, [
        db.Event(source="qfap", source_id="a", title="Concert Jazz", start=t, lat=48.86, lon=2.35),
        db.Event(source="ics:x", source_id="b", title="Concert Jazz", start=t, lat=48.90, lon=2.40),  # ~6 km
        db.Event(source="ics:x", source_id="c", title="Exposition photo", start=t, lat=48.861, lon=2.351),
    ])
    counts = sources.dedup_inter_source(con, now=t - timedelta(days=1))
    assert counts == {"forte": 0, "faible": 0}
    assert db.stats(con)["total"] == 3


def test_dedup_reversible_never_deletes(con):
    t = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)
    db.upsert_events(con, [
        db.Event(source="qfap", source_id="a", title="Nuit blanche", start=t, lat=48.86, lon=2.35),
        db.Event(source="ics:x", source_id="b", title="Nuit blanche", start=t, lat=48.8605, lon=2.3505),
    ])
    sources.dedup_inter_source(con, now=t - timedelta(days=1))
    assert db.stats(con)["total"] == 2   # les deux lignes existent toujours
    loser = con.execute("SELECT id FROM events WHERE doublon_de IS NOT NULL").fetchone()
    assert loser is not None
    con.execute("UPDATE events SET doublon_de = NULL WHERE id = ?", (loser["id"],))
    assert db.stats(con)["total"] == 2   # un simple UPDATE suffit à annuler le lien


def test_upsert_is_idempotent(con):
    t = datetime.now(timezone.utc) + timedelta(hours=2)
    evs = [_ev("A", t), _ev("A", t + timedelta(days=1))]
    assert db.upsert_events(con, evs) == 2
    db.upsert_events(con, evs)
    assert db.stats(con)["total"] == 2


def test_search_returns_ongoing_events_not_only_starting(con):
    """Une expo ouverte 10h-19h doit sortir pour « ce soir » à 17h."""
    now = datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc)
    expo = db.Event(source="a", source_id="expo", title="Expo", start=now - timedelta(hours=7),
                    end=now + timedelta(hours=2), lat=48.86, lon=2.35)
    finished = db.Event(source="a", source_id="fin", title="Finie", start=now - timedelta(hours=7),
                        end=now - timedelta(hours=1), lat=48.86, lon=2.35)
    db.upsert_events(con, [expo, finished])
    hits = db.search(con, lat=48.86, lon=2.35, radius_km=1, start_from=now, start_to=now + timedelta(hours=7))
    assert [h["title"] for h in hits] == ["Expo"]


def test_search_respects_radius_and_filters(con):
    t = datetime.now(timezone.utc) + timedelta(hours=1)
    db.upsert_events(con, [
        db.Event(source="a", source_id="near", title="Près", start=t, lat=48.860, lon=2.350, price_type="free", category="music"),
        db.Event(source="a", source_id="far", title="Loin", start=t, lat=48.950, lon=2.450, price_type="free"),
        db.Event(source="a", source_id="paid", title="Payant", start=t, lat=48.861, lon=2.351, price_type="paid"),
        db.Event(source="a", source_id="nogeo", title="Sans géo", start=t),
    ])
    args = dict(lat=48.86, lon=2.35, radius_km=2, start_from=t - timedelta(hours=1), start_to=t + timedelta(hours=1))
    assert {h["title"] for h in db.search(con, **args)} == {"Près", "Payant"}
    assert [h["title"] for h in db.search(con, price_type="free", **args)] == ["Près"]
    assert [h["title"] for h in db.search(con, category="music", **args)] == ["Près"]


def test_mark_feed_disables_after_max_errors(con, monkeypatch):
    monkeypatch.setattr(db, "FEED_MAX_ERRORS", 3)
    db.upsert_feed(con, "https://x/a.ics", "ics")
    assert not db.mark_feed(con, "https://x/a.ics", ok=False, error="boom")
    assert not db.mark_feed(con, "https://x/a.ics", ok=False, error="boom")
    assert db.mark_feed(con, "https://x/a.ics", ok=False, error="boom")       # 3e échec → désactivé
    assert db.list_feeds(con, enabled_only=True) == []
    db.mark_feed(con, "https://x/a.ics", ok=True)
    assert db.list_feeds(con, enabled_only=False)[0]["error_count"] == 0


# ----------------------------------------------------------- agrégation

def test_aggregate_isolates_failures():
    import asyncio

    async def good(_c):
        return [_ev("ok", datetime.now(timezone.utc))]

    async def bad(_c):
        raise RuntimeError("source down")

    async def slow(_c):
        await asyncio.sleep(5)
        return []

    res = asyncio.run(sources.aggregate({"good": good, "bad": bad, "slow": slow}, per_source_timeout=0.2))
    by = {r.name: r for r in res}
    assert by["good"].ok and len(by["good"].events) == 1
    assert not by["bad"].ok and "source down" in by["bad"].error
    assert not by["slow"].ok and "Timeout" in by["slow"].error


# ---------------------------------------------------------------- JSON-LD

def test_jsonld_event_extraction():
    html = """<html><head><script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[{"@type":"MusicEvent","name":"Soirée",
     "startDate":"2026-10-01T20:00:00+02:00","location":{"@type":"Place","name":"La Marbrerie",
     "geo":{"latitude":48.857,"longitude":2.437},"address":{"addressLocality":"Montreuil"}},
     "offers":{"price":"0"},"url":"https://ex/ev"}]}
    </script></head></html>"""
    evs = discover.extract_jsonld_events(html, "https://ex/agenda")
    assert len(evs) == 1
    e = evs[0]
    assert e.title == "Soirée" and e.venue == "La Marbrerie" and e.city == "Montreuil"
    assert e.price_type == "free" and e.category == "music" and e.lat == 48.857


# -------------------------------------------------------------- iCalendar

def test_parse_ics_expands_rrule_within_window():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    dt = (now + timedelta(days=1)).strftime("%Y%m%dT%H%M%SZ")
    ics = f"""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:weekly@x
SUMMARY:Atelier hebdo
DTSTART:{dt}
DTEND:{(now + timedelta(days=1, hours=2)).strftime("%Y%m%dT%H%M%SZ")}
RRULE:FREQ=WEEKLY;COUNT=4
LOCATION:MJC
END:VEVENT
END:VCALENDAR"""
    evs = sources.parse_ics(ics, source="ics", window=(now, now + timedelta(days=20)))
    assert len(evs) == 3                                   # 4 occurrences, 3 dans 20 jours
    assert all(e.source_id == "weekly@x" for e in evs)
    assert evs[0].end - evs[0].start == timedelta(hours=2)
    assert evs[0].category == "workshop"


# ------------------------------------------------------ cultures multiples
#
# L'accueil demandait une culture par requete, soit 14 requetes pour ~25
# creneaux (1 s cote client, mesure du 05/09/2026). `/api/events?culture=` en
# accepte desormais plusieurs, separees par des virgules. Ces tests verrouillent
# le contrat : le mode a une seule cle ne doit RIEN changer, et le mode groupe
# ne doit perdre aucun evenement.


@pytest.fixture
def cultures_factices(monkeypatch):
    """Table culture -> lieux figee. Ces tests portent sur la resolution du
    parametre, pas sur le contenu editorial de cultures.yaml : ils ne doivent
    pas casser le jour ou une culture est ajoutee au fichier."""
    lieux = {
        "japon": ["Maison de la culture du Japon", "Fondation du Japon"],
        "suede": ["Institut suédois"],
        "pologne": ["Institut polonais"],
    }
    monkeypatch.setattr(cultures, "venues_for", lambda cle: list(lieux.get(cle, [])))
    return lieux


def test_culture_unique_se_comporte_comme_avant(cultures_factices):
    cles, venues = main._venues_pour_cultures("japon")
    assert cles == ["japon"]
    assert venues == ["Maison de la culture du Japon", "Fondation du Japon"]


def test_cultures_multiples_donnent_l_union_des_lieux(cultures_factices):
    cles, venues = main._venues_pour_cultures("japon,suede,pologne")
    assert cles == ["japon", "suede", "pologne"]
    assert venues == [
        "Maison de la culture du Japon", "Fondation du Japon",
        "Institut suédois", "Institut polonais",
    ]


def test_culture_repetee_ne_duplique_pas_les_lieux(cultures_factices):
    """Dedoublonnage par `dict.fromkeys` et non par `set` : l'ordre doit rester
    deterministe, la clause SQL generee en depend."""
    _, venues = main._venues_pour_cultures("japon,japon")
    assert venues == ["Maison de la culture du Japon", "Fondation du Japon"]


def test_espaces_et_separateurs_vides_sont_toleres(cultures_factices):
    cles, venues = main._venues_pour_cultures("  japon , , suede  ")
    assert cles == ["japon", "suede"]
    assert len(venues) == 3


@pytest.mark.parametrize("valeur", [None, ""])
def test_parametre_absent_ne_filtre_rien(valeur, cultures_factices):
    """Le piege : renvoyer `[]` plutot que `None` passerait une liste vide a
    `db.search`, qui ne filtrerait alors RIEN — on croirait filtrer et on
    rendrait la base entiere."""
    cles, venues = main._venues_pour_cultures(valeur)
    assert cles == []
    assert venues is None


@pytest.mark.parametrize("valeur", [",", " , , ", "  ,"])
def test_parametre_fourni_mais_sans_cle_reste_un_404(valeur, cultures_factices):
    """Non-regression : avant le multi-cles, `?culture=,` levait un 404 (la
    chaine etait passee telle quelle a `venues_for`, qui ne trouvait rien).
    Le decoupage sur les virgules ne doit pas transformer cette saisie
    malformee en absence de filtre, sinon elle rendrait la base entiere."""
    with pytest.raises(HTTPException) as e:
        main._venues_pour_cultures(valeur)
    assert e.value.status_code == 404


@pytest.mark.parametrize("valeur", ["klingon", "japon,klingon", "klingon,japon"])
def test_culture_inconnue_leve_404_seule_ou_en_liste(valeur, cultures_factices):
    with pytest.raises(HTTPException) as e:
        main._venues_pour_cultures(valeur)
    assert e.value.status_code == 404
    assert "klingon" in e.value.detail


def test_culture_cle_absente_quand_une_seule_culture(monkeypatch):
    """Le mode mono-culture doit rendre exactement la reponse d'avant : aucun
    champ ajoute."""
    monkeypatch.setattr(cultures, "for_venue", lambda *a, **k: {"cle": "japon"})
    rows = [{"venue": "Maison de la culture du Japon"}]
    main._ajoute_culture_cle(rows, ["japon"])
    assert "culture_cle" not in rows[0]


def test_culture_cle_presente_des_deux_cultures(monkeypatch):
    monkeypatch.setattr(cultures, "for_venue",
                        lambda v, *a, **k: {"cle": "japon"} if v else None)
    rows = [{"venue": "Maison de la culture du Japon"}, {"venue": None}]
    main._ajoute_culture_cle(rows, ["japon", "suede"])
    assert rows[0]["culture_cle"] == "japon"
    assert rows[1]["culture_cle"] is None


def test_culture_cle_ignore_les_mots_cles_d_exclusion(monkeypatch):
    """Non-regression du regroupement : `_ajoute_culture_cle` doit appeler
    `for_venue` SANS titre ni description. Un evenement multi-pays perd son
    attribut `culture` (option C, cf. cultures.yaml) mais reste ramene par le
    filtre SQL, qui porte sur le LIEU — si on lui passait le titre ici, il
    n'aurait plus de cle de regroupement et le client le perdrait en silence."""
    recus = []

    def faux_for_venue(*args, **kw):
        recus.append((args, kw))
        return {"cle": "allemagne"}

    monkeypatch.setattr(cultures, "for_venue", faux_for_venue)
    rows = [{"venue": "Goethe-Institut", "title": "Festival de cinéma soudanais"}]
    main._ajoute_culture_cle(rows, ["allemagne", "japon"])
    assert rows[0]["culture_cle"] == "allemagne"
    assert recus == [(("Goethe-Institut",), {})]
