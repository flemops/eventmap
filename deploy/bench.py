"""Benchmark léger et reproductible des chemins critiques (base synthétique, aucun réseau, aucune prod).

    python deploy/bench.py                 # volume proche de la production (30 000 lignes)
    python deploy/bench.py --rows 100000   # test de marge
    python deploy/bench.py --budget        # code de sortie 1 si un seuil de régression majeure est franchi

Mesure : (1) EXPLAIN QUERY PLAN de la recherche, (2) latence de db.search sur 4 requêtes types,
(3) latence des endpoints /api/events, /api/cities, /health via TestClient (sans réseau), (4) temps
d'ingestion (upsert) du jeu complet. La base est créée dans un dossier temporaire puis jetée.

Les nombres produits sont des MESURES de la machine qui lance le script, pas des garanties :
les citer avec leur date, leur machine et le volume. Les seuils de --budget sont volontairement
larges (détecter un facteur 10, pas une variation de 20 %)."""
import argparse
import logging
import os
import random
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CENTER = (48.8584, 2.3470)
CATS = ["music", "theatre", "exhibition", "film", "dance", "family", "other"]
BUDGET_MS = {"search": 250.0, "api_events": 400.0, "api_cities": 50.0, "health": 400.0}


def build(path: str, rows: int, seed: int = 7) -> float:
    import db
    rnd = random.Random(seed)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    events = []
    for i in range(rows):
        start = now + timedelta(hours=rnd.randint(-24, 90 * 24))
        events.append(db.Event(
            source=rnd.choice(["qfap", "ficep", "bataclan", "openagenda-idf"]),
            source_id=f"bench-{i}", start=start,
            end=start + timedelta(hours=rnd.choice([2, 3, 6, 8])),
            title=f"Événement synthétique {i}", venue=f"Lieu {i % 400}",
            lat=CENTER[0] + rnd.uniform(-0.06, 0.06), lon=CENTER[1] + rnd.uniform(-0.09, 0.09),
            price_type=rnd.choice(["free", "paid", "unknown"]), category=rnd.choice(CATS),
            city_id="paris"))
    con = db.connect(path)
    t = time.perf_counter()
    db.upsert_events(con, events)
    con.commit()
    con.close()
    return time.perf_counter() - t


def timed(fn, n: int = 30) -> tuple[float, float]:
    fn()   # échauffement (cache SQLite, imports paresseux)
    xs = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t) * 1000)
    xs.sort()
    return statistics.median(xs), xs[max(0, int(len(xs) * 0.95) - 1)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=30_000)
    ap.add_argument("--budget", action="store_true", help="échouer si un seuil de régression majeure est dépassé")
    args = ap.parse_args()
    logging.disable(logging.INFO)   # httpx + uvicorn bavards : on ne veut que les mesures

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "bench.db")
        os.environ["EVENTMAP_DB"] = path
        import db
        ing = build(path, args.rows)
        print(f"ingestion (upsert) de {args.rows} lignes : {ing:.2f} s ({args.rows / ing:.0f} lignes/s)")

        con = db.connect(path)
        now = datetime.now(timezone.utc)
        cases = {
            "ce soir, 2 km": dict(lat=CENTER[0], lon=CENTER[1], radius_km=2, start_from=now, start_to=now + timedelta(hours=12)),
            "week-end, 8 km": dict(lat=CENTER[0], lon=CENTER[1], radius_km=8, start_from=now, start_to=now + timedelta(days=3)),
            "semaine + catégorie": dict(lat=CENTER[0], lon=CENTER[1], radius_km=4, start_from=now,
                                        start_to=now + timedelta(days=7), category="music"),
            "90 jours, 8 km, gratuit": dict(lat=CENTER[0], lon=CENTER[1], radius_km=8, start_from=now,
                                           start_to=now + timedelta(days=90), price_type="free"),
        }
        worst_search = 0.0
        print("\ndb.search (médiane / p95, ms) :")
        for name, kw in cases.items():
            med, p95 = timed(lambda kw=kw: db.search(con, **kw))
            worst_search = max(worst_search, p95)
            print(f"  {name:<26} {med:7.1f} / {p95:7.1f}   ({len(db.search(con, **kw))} lignes)")

        print("\nEXPLAIN QUERY PLAN (recherche « ce soir, 2 km ») :")
        plan = con.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM events WHERE city_id=:c AND lat IS NOT NULL AND doublon_de IS NULL "
            "AND julianday(COALESCE(end,start)) + 0.125 > julianday(:f) AND julianday(start) < julianday(:t) "
            "AND status IN ('active')", {"c": "paris", "f": now.isoformat(), "t": (now + timedelta(hours=12)).isoformat()}
        ).fetchall()
        for r in plan:
            print("  ", r["detail"])
        con.close()

        from fastapi.testclient import TestClient

        import main
        client = TestClient(main.app)
        results = {}
        for name, url in {"api_events": "/api/events?city=paris&when=today&radius=2&lat=48.8584&lon=2.347",
                          "api_cities": "/api/cities", "health": "/health"}.items():
            med, p95 = timed(lambda url=url: client.get(url), n=20)
            results[name] = p95
            print(f"\n{url.split('?')[0]:<14} médiane {med:7.1f} ms / p95 {p95:7.1f} ms")

    if args.budget:
        bad = [k for k, v in {"search": worst_search, **results}.items() if v > BUDGET_MS[k]]
        if bad:
            print(f"\nSEUIL DÉPASSÉ : {bad} (budget {BUDGET_MS})")
            return 1
        print("\nbudgets respectés")
    return 0


if __name__ == "__main__":
    sys.exit(main())
