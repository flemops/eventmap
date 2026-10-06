# Performance et limites mesurées (SQLite)

Mesures produites par `python deploy/bench.py` (base **synthétique**, aucune donnée de production,
aucun accès réseau). Machine : poste Windows 11, Python 3.12.10, SQLite embarqué, 06/10/2026. Les valeurs
sont des mesures de CETTE machine, à comparer entre elles (régression), pas des garanties de production
(le service de la VM est plafonné à `CPUQuota=50%` par systemd : s'attendre à des latences plus élevées, non mesurées ici).

## Volume

Production le 06/10/2026 : environ 27 600 lignes d'événements, une ville active (valeur lue sur
`/health`, `db.total`). Le benchmark par défaut génère 30 000 lignes pour rester proche de cet ordre.

## Résultats (médiane / p95, ms) — une exécution du 06/10/2026

| Chemin | 30 000 lignes | 100 000 lignes |
|---|---|---|
| `db.search` « ce soir, 2 km » | 25 / 29 | 106 / 118 |
| `db.search` « week-end, 8 km » | 33 / 38 | 126 / 151 |
| `db.search` semaine + catégorie | 30 / 36 | 153 / 229 |
| `db.search` 90 jours, 8 km, gratuit | 96 / 103 | 516 / 689 |
| `GET /api/events` (de bout en bout) | 45 / 53 | 102 / 112 |
| `GET /api/cities` | 4,3 / 5,7 | 3,9 / 5,2 |
| `GET /health` | 130 / 180 | 248 / 264 |
| Ingestion (`upsert_events`) | ~19 700 lignes/s | ~8 100 lignes/s |
| **`dedup_inter_source`** (déduplication, tout le jeu) | **11,3 s** | **134 s** |

Le bruit entre deux exécutions sur ce poste est important (les mêmes recherches ont mesuré deux fois plus
lent lors d'une exécution précédente, pendant que d'autres processus tournaient) : comparer des ordres de
grandeur et des rapports, pas des millisecondes.

* **La recherche est linéaire** dans le nombre de lignes de la ville : de l'ordre de 1 à 5 ms par millier de
  lignes selon la fenêtre demandée.
* **La déduplication est super-linéaire** (×12 pour ×3,3 lignes, soit un comportement proche du quadratique
  sur ce jeu). C'est le vrai point de croissance : elle tourne dans le fil d'écriture du refresh (jamais dans
  une requête utilisateur), donc elle allonge la durée d'un **cycle**, pas la latence de l'API. Le jeu synthétique
  est dense (toutes les lignes dans ~13 km sur 90 jours, titres aléatoires) : à comparer avec la durée réelle des
  cycles de production avant de conclure.

## `EXPLAIN QUERY PLAN`

```
SEARCH events USING INDEX idx_events_city_status (city_id=? AND status=?)
```

SQLite choisit l'index `(city_id, status)` puis évalue la fenêtre de temps et la distance haversine sur
chaque ligne de la ville. Les filtres temporels passent par `julianday()` (voir le piège de format dans
`db.search`), donc ne peuvent pas utiliser `idx_events_start`. **Aucun index n'a été ajouté** : le plan est
sain pour le volume actuel et rien n'a démontré qu'un index de plus aiderait. La sélection de la
déduplication (`WHERE start >= ?`) utilise `idx_events_start`.

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
