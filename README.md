# EventMap FR

Agrégateur d'événements pour le triangle Nanterre ↔ Paris ↔ Montreuil, conçu
contre la paralysie du choix : **une vue « ce soir » par défaut, jamais un
catalogue.**

## Ce que ça fait

- Rapatrie les sources toutes les 6 h (Que faire à Paris, flux iCal, OpenAgenda
  avec clé), éclate chaque créneau en une ligne, dédoublonne entre sources.
- Expose `GET /api/events?lat&lon&radius&when=today|tomorrow|weekend|week`
  avec filtres prix et catégorie. Recherche géo par haversine en SQL.
- Sert un front mobile-first (Leaflet + OpenStreetMap, zéro build, zéro clé)
  qui ouvre sur « ce soir, à 5 km ».
- **Tolère les pannes** : une source qui tombe est journalisée et marquée,
  les autres continuent. Après 5 échecs consécutifs un flux est désactivé.

## Lancer en local

```bash
python -m venv .venv && . .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

L'API répond sur http://127.0.0.1:8000, le premier refresh démarre 5 s après
(≈ 45 s pour rapatrier Que faire à Paris). `/health` indique l'état.

```bash
pytest -q                                   # 18 tests, sans réseau
python discover.py montreuil.fr nanterre.fr  # sonder un domaine
curl -X POST localhost:8000/api/refresh      # forcer un refresh
```

## Variables d'environnement

| Variable | Rôle | Défaut |
|---|---|---|
| `EVENTMAP_DB` | Chemin SQLite | `data/eventmap.db` |
| `EVENTMAP_FEEDS` | Annuaire des flux | `feeds.yaml` |
| `EVENTMAP_REFRESH_SECONDS` | Intervalle de refresh | `21600` (6 h) |
| `EVENTMAP_HORIZON_DAYS` | Fenêtre d'ingestion | `90` |
| `EVENTMAP_FEED_MAX_ERRORS` | Échecs avant désactivation d'un flux | `5` |
| `EVENTMAP_CONTACT` | URL dans le `User-Agent` | URL du dépôt |
| `OPENAGENDA_KEY` | Active le connecteur OpenAgenda | — |
| `ANTHROPIC_API_KEY` | Active `scrape.py` (jamais automatique) | — |

## Structure

```
main.py           FastAPI, /api/events, refresh_loop, /health
db.py             schéma SQLite, upsert idempotent, recherche géo, état des flux
sources.py        client HTTP poli (1 req/s/domaine), parser iCal, OpenAgenda,
                  aggregate() tolérante aux pannes, dedupe()
sources_paris.py  connecteur Que faire à Paris (pagination, occurrences)
discover.py       sonde .ics + JSON-LD schema.org/Event sur un domaine
scrape.py         extraction LLM, dernier recours, désactivé par défaut
feeds.yaml        annuaire des sources de la couche 2 — l'actif du projet
docs/sources.md   chaque source : endpoint, licence, champs, mapping
docs/decisions.md pourquoi les choses sont comme elles sont
deploy/           unité systemd durcie + bloc nginx
tests/            pytest, sans réseau
```

## État de la couverture (23/08/2026)

| Sommet | Source | Events |
|---|---|---|
| Paris | Que faire à Paris | ~2 600 |
| Montreuil | Que faire à Paris + RSS salles (à parser) | 14 + ~40 |
| **Nanterre** | **aucune source structurée trouvée** | **0** |

Le détail domaine par domaine est dans `feeds.yaml`. Nanterre est le chantier
ouvert : ni .ics, ni RSS, ni JSON-LD sur `nanterre.fr`. Voir `docs/decisions.md` D10–D11.

## Licences des données

Que faire à Paris : **ODbL**, Ville de Paris — attribution affichée dans le
front. Les autres sources portent leur licence dans `feeds.yaml` (`unknown`
tant qu'elle n'est pas vérifiée).
