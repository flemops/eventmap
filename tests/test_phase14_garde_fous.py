"""Phase 14 — garde-fous d'autonomie : quarantaine d'un lot anormal (14.6), intégrité SQLite (14.8)
et scénarios de panne (14.25/14.26). Sans réseau ; données FACTICES."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import db
import main
import pipeline
import registry
import sources

UTC = timezone.utc


# ------------------------------------------------------------------ règle pure

@pytest.mark.parametrize("prev,prev_geo,valid,geo,attendu", [
    (100, 0, 25, 0, "volume"),            # effondrement : 25 % du lot précédent
    (100, 0, 30, 0, None),                # exactement 30 % : toléré
    (100, 0, 80, 0, None),                # baisse normale
    (10, 0, 1, 0, None),                  # source trop petite pour juger un effondrement
    (100, 0, 0, 0, None),                 # lot vide : traité ailleurs (purge annulée)
    (100, 0, 100, 70, "géocodage"),       # géocodage soudain cassé
    (100, 50, 100, 70, None),             # déjà mauvais avant : pas une nouveauté
    (0, 0, 20, 15, "géocodage"),          # première fois, mais 75 % sans coordonnées
    (0, 0, 5, 5, None),                   # trop petit pour juger
])
def test_batch_anomaly(prev, prev_geo, valid, geo, attendu):
    why = pipeline.batch_anomaly(prev, prev_geo, valid, geo)
    assert (why is None) if attendu is None else (why and why.startswith(attendu))


# ------------------------------------------------------------------ quarantaine dans le cycle

def _evs(n, source="qfap", offset=0, lat=48.86, start_days=2):
    t = datetime.now(UTC) + timedelta(days=start_days)
    return [db.Event(source=source, source_id=f"{source}-{offset + i}", title=f"E{offset + i}", start=t,
                     lat=lat, lon=2.35) for i in range(n)]


@pytest.fixture
def base(tmp_path, monkeypatch):
    path = str(tmp_path / "q.db")
    db.connect(path).close()
    orig = db.session
    monkeypatch.setattr(db, "session", lambda *a, **k: orig(path))
    return path


def _by_key():
    return {s.key: s for s in registry.load() if s.key == "qfap"}


def _cycle(n, when):
    return main._persist([sources.SourceResult(name="qfap", events=_evs(n, offset=1000))], _by_key(), {}, when)


def _count(path):
    con = db.connect(path)
    try:
        return con.execute("SELECT COUNT(*) FROM events WHERE source='qfap'").fetchone()[0]
    finally:
        con.close()


def _health(path):
    con = db.connect(path)
    try:
        return dict(con.execute("SELECT * FROM source_health WHERE source='qfap'").fetchone())
    finally:
        con.close()


def test_un_lot_effondre_est_mis_en_quarantaine_sans_rien_purger(base):
    t0 = datetime.now(UTC)
    main._persist([sources.SourceResult(name="qfap", events=_evs(60))], {}, {}, t0)      # lot sain de référence
    assert _count(base) == 60
    _cycle(5, t0 + timedelta(hours=6))                                                   # parseur cassé : 5 événements
    assert _count(base) == 60                       # rien écrit, rien purgé : le dernier contenu sain reste servi
    h = _health(base)
    assert h["quarantine_streak"] == 1 and "volume" in h["quarantine_reason"]
    assert h["last_valid"] == 60                    # la référence reste le dernier lot sain


def test_la_quarantaine_se_leve_apres_trois_lots_ecartes(base):
    t0 = datetime.now(UTC)
    main._persist([sources.SourceResult(name="qfap", events=_evs(60))], {}, {}, t0)
    for i in range(1, pipeline.QUARANTINE_MAX_STREAK + 1):
        _cycle(5, t0 + timedelta(hours=6 * i))
        assert _health(base)["quarantine_streak"] == i and _count(base) == 60
    _cycle(5, t0 + timedelta(hours=6 * 4))          # le 4e lot identique est la nouvelle normale
    h = _health(base)
    assert h["quarantine_streak"] == 0 and h["last_valid"] == 5
    assert _count(base) == 5                        # la source a réellement rétréci : purge normale


def test_un_lot_sain_apres_quarantaine_remet_la_serie_a_zero(base):
    t0 = datetime.now(UTC)
    main._persist([sources.SourceResult(name="qfap", events=_evs(60))], {}, {}, t0)
    _cycle(5, t0 + timedelta(hours=6))
    assert _health(base)["quarantine_streak"] == 1
    main._persist([sources.SourceResult(name="qfap", events=_evs(58))], {}, {}, t0 + timedelta(hours=12))
    assert _health(base)["quarantine_streak"] == 0 and _health(base)["quarantine_reason"] is None


def test_alerte_quarantine_seulement_apres_deux_cycles(base):
    t0 = datetime.now(UTC)
    main._persist([sources.SourceResult(name="qfap", events=_evs(60))], _by_key(), {}, t0)
    con = db.connect(base)

    def kinds():
        return {a["kind"] for a in main.compute_alerts(con, datetime.now(UTC) + timedelta(hours=1))}

    _cycle(5, t0 + timedelta(hours=6))
    assert "quarantine" not in kinds()              # anti-bruit (14.20) : un seul lot écarté ne réveille personne
    _cycle(5, t0 + timedelta(hours=12))
    assert "quarantine" in kinds()
    con.close()


# ------------------------------------------------------------------ intégrité SQLite

def test_integrity_ok_sur_une_base_saine_et_signale_une_base_corrompue(tmp_path):
    path = str(tmp_path / "i.db")
    con = db.connect(path)
    db.upsert_events(con, _evs(200))
    con.commit()
    assert db.integrity(con) == "ok"
    con.close()
    raw = bytearray(open(path, "rb").read())
    for i in range(4096 * 2, 4096 * 2 + 600):      # corrompt le contenu d'une page de données
        raw[i] = 0xFF
    open(path, "wb").write(raw)
    for ext in ("-wal", "-shm"):
        try:
            (tmp_path / f"i.db{ext}").unlink()
        except FileNotFoundError:
            pass
    c2 = sqlite3.connect(path)
    verdict = db.integrity(c2)
    c2.close()
    assert verdict != "ok"


def test_alerte_db_integrity_quand_quick_check_echoue(base, monkeypatch):
    monkeypatch.setattr(db, "integrity", lambda con: "page 12 : tree 2 page 12 cell 0: invalid page number")
    con = db.connect(base)
    alerts = main.compute_alerts(con, datetime.now(UTC))
    con.close()
    assert any(a["kind"] == "db_integrity" for a in alerts)


def test_backup_to_produit_une_copie_lisible(tmp_path):
    con = db.connect(str(tmp_path / "s.db"))
    db.upsert_events(con, _evs(10))
    con.commit()
    dest = str(tmp_path / "copie.db")
    db.backup_to(con, dest)
    con.close()
    c2 = db.connect(dest)
    assert c2.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 10 and db.integrity(c2) == "ok"
    c2.close()


# ------------------------------------------------------------------ scénarios de panne 14.25 / 14.26

@pytest.mark.parametrize("resultat", [
    sources.SourceResult(name="qfap", events=[], error="timeout"),          # timeout
    sources.SourceResult(name="qfap", events=[], error="HTTP 503"),         # source HTTP morte
    sources.SourceResult(name="qfap", events=[]),                           # 0 événement (parseur cassé en silence)
], ids=["timeout", "http-morte", "zero-evenement"])
def test_une_panne_de_source_garde_le_dernier_contenu_sain(base, resultat):
    t0 = datetime.now(UTC)
    main._persist([sources.SourceResult(name="qfap", events=_evs(40))], {}, {}, t0)
    main._persist([resultat], {}, {}, t0 + timedelta(hours=6))
    assert _count(base) == 40


def test_une_base_verrouillee_ne_tue_pas_la_lecture(base):
    """Un écrivain qui tient le verrou n'empêche pas l'API de lire (WAL) : les visiteurs voient la dernière donnée."""
    t0 = datetime.now(UTC)
    main._persist([sources.SourceResult(name="qfap", events=_evs(20))], {}, {}, t0)
    writer = sqlite3.connect(base, timeout=0.1)
    writer.execute("BEGIN IMMEDIATE")
    try:
        reader = db.connect(base)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 20
        reader.close()
    finally:
        writer.rollback()
        writer.close()


# ------------------------------------------------------------------ circuit breaker (14.7) et alerte anti-bruit (14.20)

def test_alerte_breaker_seulement_a_partir_de_la_seconde_coupure(base, monkeypatch):
    monkeypatch.setattr(db, "FEED_MAX_ERRORS", 2)
    con = db.connect(base)
    db.upsert_feed(con, "https://x/a.ics", "ics", feed_id="x-a")
    for _ in range(2):
        db.mark_feed(con, "https://x/a.ics", ok=False, error="boom")           # 1re coupure : niveau 1
    con.commit()

    def kinds():
        return {a["kind"] for a in main.compute_alerts(con, datetime.now(UTC))}

    assert "source_breaker" not in kinds()           # une première coupure se répare seule : on ne réveille personne
    db.mark_feed(con, "https://x/a.ics", ok=False, error="boom")               # la sonde rate : niveau 2
    con.commit()
    assert "source_breaker" in kinds()
    con.close()


def test_le_cycle_sonde_une_source_coupee_une_fois_le_delai_ecoule(base, monkeypatch):
    monkeypatch.setattr(db, "FEED_MAX_ERRORS", 1)
    con = db.connect(base)
    spec = next(s for s in registry.load() if s.kind != "qfap" and s.runnable())
    db.upsert_feed(con, spec.url, spec.kind, name=spec.name, feed_id=spec.id, city=spec.city_id)
    db.mark_feed(con, spec.url, ok=False, error="boom")
    con.commit()
    fetchers, _ = main._build_fetchers(con, datetime.now(UTC))
    assert spec.url not in fetchers                                            # coupée : pas interrogée
    fetchers, _ = main._build_fetchers(con, datetime.now(UTC) + timedelta(hours=7))
    assert spec.url in fetchers                                                # délai écoulé : sondée
    con.close()


def test_fraicheur_attendue_propre_a_une_source(monkeypatch):
    """14.4 : `stale_after_hours` d'une source l'emporte sur celui de la ville ; absent = valeur de la ville."""
    import yaml
    spec = registry.load()[0]
    assert spec.stale_after_hours is None or spec.stale_after_hours > 0
    raw = {"feeds": [{"id": "z", "city": "paris", "url": "https://x/z.ics", "type": "ics", "stale_after_hours": 12}]}
    import pathlib
    import tempfile
    f = pathlib.Path(tempfile.mkdtemp()) / "feeds.yaml"
    f.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setattr(registry, "FEEDS_FILE", f)
    assert [s.stale_after_hours for s in registry.load() if s.id == "z"] == [12.0]


def test_disque_presque_plein_suspend_le_cycle_et_leve_l_alerte(base, monkeypatch):
    """14.18 : sous le seuil, aucun cycle n'écrit (le contenu servi reste) ; à 2x le seuil, alerte `disk_low`."""
    import asyncio
    monkeypatch.setattr(db, "free_space_mb", lambda path=None: 50.0)
    monkeypatch.setattr(main, "MIN_FREE_MB", 200)
    res = asyncio.run(main.refresh())
    assert res == [] and main._refresh_state["last_results"][0]["error"].startswith("cycle suspendu")
    con = db.connect(base)
    kinds = {a["kind"] for a in main.compute_alerts(con, datetime.now(UTC))}
    assert {"disk_low", "refresh_failed"} <= kinds
    monkeypatch.setattr(db, "free_space_mb", lambda path=None: 1000.0)
    assert "disk_low" not in {a["kind"] for a in main.compute_alerts(con, datetime.now(UTC))}
    con.close()


def test_base_perdue_se_reconstruit_sans_edition_manuelle(tmp_path):
    """14.24 : une base absente → schéma recréé au premier connect(), puis un lot de source la repeuple (voir runbook)."""
    path = str(tmp_path / "neuve.db")
    con = db.connect(path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "feeds", "source_health", "funnel"} <= tables
    db.upsert_events(con, _evs(5))
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 5
    con.close()


def test_historique_de_sante_cumule_les_cycles_du_jour(base):
    """14.19 : un cycle réussi puis un lot écarté puis un échec = une ligne du jour, compteurs et dernier état justes."""
    t0 = datetime.now(UTC)
    main._persist([sources.SourceResult(name="qfap", events=_evs(60))], _by_key(), {}, t0)
    _cycle(5, t0 + timedelta(minutes=1))                                       # effondré : quarantaine
    main._persist([sources.SourceResult(name="qfap", events=[], error="TimeoutError")], _by_key(), {}, t0 + timedelta(minutes=2))
    con = db.connect(base)
    r = dict(con.execute("SELECT * FROM source_health_history WHERE source='qfap'").fetchone())
    assert (r["runs"], r["ok"], r["quarantined"], r["failed"]) == (3, 1, 1, 1)
    assert r["last_error"] == "TimeoutError" and r["last_ok"] and r["last_failure"]
    from fastapi.testclient import TestClient
    assert TestClient(main.app).get("/api/health/history").json()["rows"][0]["source"] == "qfap"
    con.close()
