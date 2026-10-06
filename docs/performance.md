# Performance et limites mesurées (SQLite)

Mesures produites par `python deploy/bench.py` (base **synthétique**, aucune donnée de production,
aucun accès réseau). Machine : poste Windows 11, Python 3.12.10, SQLite embarqué, 06/10/2026. Les valeurs
sont des mesures de CETTE machine, à comparer entre elles (régression), pas des garanties de production
(le service de la VM est plafonné à `CPUQuota=50%` par systemd : s'attendre à des latences plus élevées, non mesurées ici).

## Volume

Production le 06/10/2026 : environ 27 600 lignes d'événements, une ville active (valeur lue sur
`/health`, `db.total`). Le benchmark par défaut génère 30 000 lignes pour rester proche de cet ordre.

## Résultats (médiane / p95, ms)

| Chemin | 30 000 lignes | 100 000 lignes |
|---|---|---|
| `db.search` « ce soir, 2 km » | 52 / 77 | 145 / 205 |
| `db.search` « week-end, 8 km » | 60 / 81 | 149 / 206 |
| `db.search` semaine + catégorie | 59 / 82 | 198 / 228 |
| `db.search` 90 jours, 8 km, gratuit | 139 / 224 | 497 / 627 |
| `GET /api/events` (de bout en bout) | 95 / 184 | 200 / 284 |
| `GET /api/cities` | 5,5 / 8,4 | 9,8 / 17 |
| `GET /health` | 183 / 225 | 365 / 435 |
| Ingestion (`upsert_events`) | 9 200 lignes/s | 9 600 lignes/s |

Le temps est **linéaire** dans le nombre de lignes de la ville : environ 0,5 à 1,5 ms par millier de lignes
pour une recherche courante.

## `EXPLAIN QUERY PLAN`

```
SEARCH events USING INDEX idx_events_city_status (city_id=? AND status=?)
```

SQLite choisit l'index `(city_id, status)` puis évalue la fenêtre de temps et la distance haversine sur
chaque ligne de la ville. Les filtres temporels passent par `julianday()` (voir le piège de format dans
`db.search`), donc ne peuvent pas utiliser `idx_events_start`. **Aucun index n'a été ajouté** : le plan est
sain pour le volume actuel et rien n'a démontré qu'un index de plus aiderait.

## Premier levier si la recherche devient trop lente (avant tout changement de SGBD)

Ajouter une borne **sargable** sur la colonne brute (`start < :start_to_iso`, valable car toutes les dates
sont stockées en ISO UTC de même format) pour que `idx_events_city_start` écarte l'essentiel des lignes
avant le calcul de distance. À mesurer avec `deploy/bench.py` avant et après ; non appliqué tant que le
budget n'est pas menacé.

## Garde-fou de régression

`python deploy/bench.py --budget` échoue (code 1) si un chemin dépasse un budget **large** (recherche
250 ms p95, `/api/events` 400 ms, `/api/cities` 50 ms, `/health` 400 ms, à 30 000 lignes) : il détecte un
facteur dix, pas une variation de 20 %. Il n'est pas dans la CI (le bruit d'un runner partagé donnerait des
faux échecs) ; il se lance avant de toucher `db.search` ou le schéma.
