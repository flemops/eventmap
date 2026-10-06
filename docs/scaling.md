# Comment ça passerait à l'échelle (et à quel seuil)

Principe : **ne rien implémenter avant qu'un seuil mesuré soit franchi.** Aujourd'hui EventMap est un
service unique (FastAPI, un worker `uvicorn`, SQLite en WAL) sur une VM partagée ; c'est suffisant et
choisi (`docs/decisions.md`). Cette page sépare ce qui existe de ce qui serait conditionnel.

## Architecture actuelle (nécessaire et suffisante)

Un processus : API + boucle de rafraîchissement (le travail d'écriture tourne dans un fil à part pour ne
pas geler l'API, voir `main._persist`). SQLite en WAL (lectures concurrentes pendant l'écriture). Nginx
devant (limitation de débit, en-têtes), systemd borne la mémoire et le CPU.

## Seuils de déclenchement

Les seuils ci-dessous sont des **critères de décision** à vérifier par mesure (`deploy/bench.py`,
`/health`, journaux), pas des mesures déjà prises. Les valeurs de référence viennent de `docs/performance.md`.

| Signal mesuré | Seuil | Premier levier (le moins coûteux) | Évolution conditionnelle |
|---|---|---|---|
| Latence `/api/events` p95 sur la VM | > 500 ms soutenu, ou `db.search` > 300 ms p95 | borne sargable sur `start` (index `(city_id, start)`) puis index composite si `EXPLAIN` le justifie | pré-calcul/caching des fenêtres courantes ; au-delà, PostgreSQL + PostGIS (recherche géographique indexée) |
| Lignes par ville | ≫ 300 000 (le temps de recherche est linéaire : ~0,5 à 1,5 ms par millier de lignes, mesuré à 30 k et 100 k) | purge plus agressive (`purge_past`), horizon d'ingestion plus court | PostgreSQL |
| Contention en écriture | lectures bloquées par un cycle d'ingestion (latence `/health` > 2 s pendant un cycle, ou erreurs `database is locked`) | ingestion par lots plus petits | PostgreSQL (MVCC) ou une base séparée pour l'ingestion |
| Durée d'un cycle de refresh | > l'intervalle de refresh de la source la plus fréquente. **Premier suspect : `dedup_inter_source`**, super-linéaire (11 s à 30 000 lignes, 134 s à 100 000 sur le jeu synthétique) | regrouper les candidats par jour et par cellule géographique avant de comparer ; ne dédupliquer que les lignes nouvelles ou modifiées | file de tâches + workers séparés |
| Nombre de villes / sources | une ville ou source ajoutée **sans changer de code** reste le cas normal ; seuil = temps de cycle ci-dessus | — | workers par ville |
| Trafic | `limit_req` nginx atteint régulièrement, ou CPU de la VM saturé par l'API | cache HTTP court (nginx `proxy_cache`) sur `/api/events` | cache applicatif (Redis) ; CDN pour les statiques |
| Disponibilité | un SLO de disponibilité formel > ce qu'une VM unique peut tenir | — | second nœud + base managée (PostgreSQL) |
| Stockage de médias | EventMap ne stocke aucune image aujourd'hui | — | stockage objet seulement si des médias sont un jour hébergés |

## Ce qui est volontairement absent

Kubernetes, Docker, Kafka/RabbitMQ, microservices, Redis, un front à build : aucun problème mesuré ne les
justifie, et chacun ajouterait de l'exploitation à une VM qui héberge déjà d'autres services.
