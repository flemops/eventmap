"""Tests de contrat des connecteurs (fixtures = vrais enregistrements amont) et invariants de données.

Pourquoi des fixtures réelles : si une source renomme un champ, ces tests échouent SANS réseau, avant
qu'une ingestion de production ne s'en aperçoive. Les fixtures de tests/fixtures/contracts/ ont été
captées le 06/10/2026 (un enregistrement par source ; QFAP : ODbL Ville de Paris, OpenAgenda :
Licence Ouverte v1.0). Les rafraîchir = remplacer le fichier par une capture récente."""
import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
import sources
import sources_jsonld
import sources_ods
import sources_paris

FIX = Path(__file__).parent / "fixtures" / "contracts"
UTC = timezone.utc
# Fenêtre fixe (pas « maintenant ») : le test ne périme pas quand les dates des fixtures passent.
WIN = (datetime(2026, 10, 1, tzinfo=UTC), datetime(2027, 6, 1, tzinfo=UTC))
PARIS_BBOX = (48.80, 48.92, 2.20, 2.50)


def _check_event(e: db.Event) -> None:
    """Contrat minimal commun à toute source : ce que le reste du pipeline suppose."""
    assert e.source and e.source_id and e.title.strip()
    assert e.start.tzinfo is not None and e.start.utcoffset() == timedelta(0), "date naïve ou non UTC"
    assert e.end is None or e.end.tzinfo is not None
    assert e.price_type in {"free", "paid", "free_conditional", "unknown"}
    assert e.category
    if e.lat is not None:
        assert PARIS_BBOX[0] <= e.lat <= PARIS_BBOX[1] and PARIS_BBOX[2] <= e.lon <= PARIS_BBOX[3]


def test_contrat_qfap_enregistrement_reel():
    payload = json.loads((FIX / "qfap.json").read_text(encoding="utf-8"))
    assert {"total_count", "results"} <= payload.keys()
    events = [e for rec in payload["results"] for e in sources_paris.to_events(rec, WIN)]
    assert events, "aucun créneau extrait : le schéma amont a changé ?"
    for rec in payload["results"]:
        assert set(sources_paris.REQUIRED) <= rec.keys()    # les clés exigées existent toujours
    for e in events:
        _check_event(e)


def test_contrat_openagenda_ods_enregistrement_reel():
    payload = json.loads((FIX / "ods.json").read_text(encoding="utf-8"))
    events = [e for rec in payload["results"] for e in sources_ods.record_to_events(rec, "ods_openagenda:t", WIN)]
    assert events, "aucun créneau extrait : le schéma Opendatasoft a changé ?"
    for e in events:
        _check_event(e)


def test_contrat_ics_ficep_evenement_reel():
    events = sources.parse_ics((FIX / "ficep.ics").read_bytes(), source="ics:ficep", window=WIN)
    assert len(events) == 1
    e = events[0]
    _check_event(e)
    assert e.title == "Aalto Fashion Showroom" and e.lat == pytest.approx(48.8508)
    assert e.start == datetime(2026, 10, 7, 9, 0, tzinfo=UTC)      # DTSTART en Z = vraie UTC


def test_contrat_jsonld_bataclan_page_reelle():
    html = (FIX / "bataclan_page.html").read_text(encoding="utf-8")
    opts = {"tz": "Europe/Paris", "naive_utc_label": False, "default_category": "music",
            "venue": {"name": "Bataclan", "address": "50 boulevard Voltaire, 75011 Paris", "lat": 48.8631, "lon": 2.3709}}
    events = sources_jsonld.events_from_page(html, "https://www.bataclan.fr/evenement/a2h_2026-11-20", opts, "jsonld:bataclan")
    assert len(events) == 1
    e = events[0]
    _check_event(e)
    assert e.title == "A2H" and e.venue == "Bataclan"
    assert e.start == datetime(2026, 11, 20, 18, 0, tzinfo=UTC)    # « Z » = UTC réelle (décision documentée dans feeds.yaml)


# ------------------------------------------------------------------ migrations

def test_un_ancien_code_continue_d_ecrire_dans_une_base_migree(tmp_path):
    """Retour arrière applicatif sûr : la migration est additive (colonnes à défaut, jamais de DROP).
    Le code de la version précédente, qui ne connaît pas les nouvelles colonnes, doit pouvoir
    insérer et relire dans la base déjà migrée."""
    con = db.connect(str(tmp_path / "m.db"))
    con.execute("INSERT INTO events (source, source_id, start, title, ingested_at) "
                "VALUES ('old', '1', '2026-10-10T18:00:00+00:00', 'Ancien code', '2026-10-05T10:00:00+00:00')")
    row = con.execute("SELECT city_id, status FROM events WHERE source='old'").fetchone()
    assert (row["city_id"], row["status"]) == ("paris", "active")      # défauts additifs
    old_cols = {"id", "source", "source_id", "start", "end", "title", "description", "venue", "address", "city",
                "lat", "lon", "price_type", "category", "url", "updated_at", "ingested_at", "doublon_de"}
    now_cols = {r["name"] for r in con.execute("PRAGMA table_info(events)")}
    assert old_cols <= now_cols, "une colonne historique a disparu : retour arrière impossible"


def test_migration_deux_fois_ne_change_pas_le_schema(tmp_path):
    path = str(tmp_path / "idem.db")
    db.connect(path).close()
    snap = sqlite3.connect(path).execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
    db.connect(path).close()
    assert sqlite3.connect(path).execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall() == snap


# ------------------------------------------------------------------ invariants

def _seed(con, rnd: random.Random, n: int = 300) -> datetime:
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    evs = []
    for i in range(n):
        start = now + timedelta(hours=rnd.randint(-10, 200))
        evs.append(db.Event(
            source="s", source_id=str(i), start=start, end=start + timedelta(hours=3), title=f"E{i}",
            lat=48.8584 + rnd.uniform(-0.05, 0.05), lon=2.347 + rnd.uniform(-0.08, 0.08),
            city_id=rnd.choice(["paris", "jeddah"]), price_type=rnd.choice(["free", "paid"])))
    db.upsert_events(con, evs)
    con.commit()
    return now


def test_invariant_aucun_evenement_d_une_autre_ville_quels_que_soient_centre_et_rayon(tmp_path):
    rnd = random.Random(42)
    con = db.connect(str(tmp_path / "i.db"))
    now = _seed(con, rnd)
    for _ in range(40):
        for city in ("paris", "jeddah"):
            rows = db.search(con, lat=rnd.uniform(48.7, 49.0), lon=rnd.uniform(2.1, 2.6), radius_km=rnd.choice([1, 2, 8, 50]),
                             start_from=now, start_to=now + timedelta(days=rnd.randint(1, 9)), city_id=city, limit=500)
            assert {r["city_id"] for r in rows} <= {city}


def test_invariant_un_doublon_canonique_n_est_jamais_visible(tmp_path):
    rnd = random.Random(1)
    con = db.connect(str(tmp_path / "d.db"))
    now = _seed(con, rnd, 120)
    ids = [r["id"] for r in con.execute("SELECT id FROM events WHERE city_id='paris' LIMIT 40")]
    for i in ids[1:]:
        con.execute("UPDATE events SET doublon_de = ? WHERE id = ?", (ids[0], i))
    con.commit()
    rows = db.search(con, lat=48.8584, lon=2.347, radius_km=50, start_from=now - timedelta(days=1),
                     start_to=now + timedelta(days=30), limit=1000)
    visible = {r["id"] for r in rows}
    assert not (visible & set(ids[1:]))


@pytest.mark.parametrize("resultat", [
    sources.SourceResult(name="qfap", events=[], error="HTTP 503"),     # source en panne
    sources.SourceResult(name="qfap", events=[]),                       # répond OK mais vide (parseur cassé en silence)
], ids=["panne", "reponse-vide"])
def test_invariant_last_known_good_une_source_defaillante_ne_vide_pas_la_base(tmp_path, monkeypatch, resultat):
    """D7 : un cycle où la source échoue, ou répond vide, ne supprime rien de son dernier contenu connu."""
    import main
    path = str(tmp_path / "l.db")
    con = db.connect(path)
    now = datetime.now(UTC)
    db.upsert_events(con, [db.Event(source="qfap", source_id=str(i), start=now + timedelta(days=2), title=f"E{i}",
                                    lat=48.86, lon=2.35) for i in range(20)])
    con.commit()
    con.close()
    orig = db.session
    monkeypatch.setattr(db, "session", lambda: orig(path))
    main._persist([resultat], {}, {}, now + timedelta(hours=6))
    con = db.connect(path)
    assert con.execute("SELECT COUNT(*) FROM events WHERE source='qfap'").fetchone()[0] == 20


@pytest.mark.parametrize("naif", ["2026-10-10T20:00:00", "2026-10-10 20:00"])
def test_invariant_une_date_naive_est_interpretee_dans_le_fuseau_de_la_source(naif):
    tz = sources_jsonld.ZoneInfo("Europe/Paris")
    d = sources_jsonld._parse_start(naif, tz, False)
    assert d is not None and d.tzinfo is not None
    assert d.astimezone(UTC).hour == 18      # 20 h à Paris en octobre (UTC+2), pas 20 h UTC
