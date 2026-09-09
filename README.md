# EventMap — Paris ce soir

<!-- déploiement continu (pull-based) actif depuis le 09/09/2026, rollback vérifié, script durci -->


Agrégateur d'événements **Paris intra-muros**, conçu contre la paralysie du
choix : **une vue « ce soir, à 2 km » par défaut, jamais un catalogue.**

## Ce que ça fait

- Rapatrie « Que faire à Paris » (Ville de Paris, ODbL) toutes les 6 h —
  ~2 500 événements à venir, tous géolocalisés — et éclate chaque créneau en
  une ligne.
- Expose `GET /api/events?lat&lon&radius&when=today|tomorrow|weekend|week`
  avec filtres prix et catégorie. Recherche géo par haversine en SQL.
- Sert un front mobile-first (Leaflet + OpenStreetMap, zéro build, zéro clé)
  centré sur Châtelet, rayon 1 à 8 km (« Tout Paris »).
- **Tolère les pannes** : si la source tombe, l'ancien contenu reste servi
  et `/health` passe en `degraded` au lieu de planter.

## Lancer en local

```bash
python -m venv .venv && . .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

L'API répond sur http://127.0.0.1:8000, le premier refresh démarre 5 s après
(≈ 35 s). `/health` indique l'état.

```bash
pytest -q                                   # 18 tests, sans réseau
curl -X POST localhost:8000/api/refresh      # forcer un refresh
```

## Variables d'environnement

| Variable | Rôle | Défaut |
|---|---|---|
| `EVENTMAP_DB` | Chemin SQLite | `data/eventmap.db` |
| `EVENTMAP_REFRESH_SECONDS` | Intervalle de refresh | `21600` (6 h) |
| `EVENTMAP_HORIZON_DAYS` | Fenêtre d'ingestion | `90` |
| `EVENTMAP_CONTACT` | URL dans le `User-Agent` | URL du dépôt |

`OPENAGENDA_KEY` et `ANTHROPIC_API_KEY` activent des connecteurs optionnels
(`sources.py`, `scrape.py`) qui ne sont pas utilisés dans le périmètre actuel.

## Structure

```
main.py           FastAPI, /api/events, refresh_loop, /health
db.py             schéma SQLite, upsert idempotent, recherche géo
sources.py        client HTTP poli (1 req/s/domaine), aggregate() tolérante
                  aux pannes, dedupe(), parser iCal et OpenAgenda (inactifs)
sources_paris.py  connecteur Que faire à Paris — la source unique
discover.py       sonde .ics + JSON-LD sur un domaine (outil, hors refresh)
scrape.py         extraction LLM, désactivé, jamais automatique
feeds.yaml        vide — périmètre Paris, QFAP suffit
docs/sources.md   la source : endpoint, licence, champs, mapping, sondage banlieue
docs/decisions.md pourquoi les choses sont comme elles sont
deploy/           unité systemd durcie + bloc nginx + install.sh
tests/            pytest, sans réseau
```

## Périmètre

**Paris intra-muros uniquement**, par choix (décision D13). Le brief initial
visait un triangle Nanterre–Paris–Montreuil ; le sondage des sources de
banlieue n'a rien donné d'exploitable (détail dans `docs/sources.md`), et
Que faire à Paris couvre Paris à elle seule. Le code reste capable d'agréger
plusieurs sources si le périmètre s'élargit.

## Licence des données

Que faire à Paris : **ODbL**, Ville de Paris — attribution affichée dans le front.
