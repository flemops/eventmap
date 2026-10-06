# EventMap multi-ville

Un seul moteur sert toutes les villes. Aucune ville n'est codée dans le programme :
tout ce qui varie est dans `cities.yaml`, `feeds.yaml` et `venues/<ville>.yaml`.

```
cities.yaml ─┐                         ┌─ /api/events?city=…   (lecture, UNE ville)
feeds.yaml  ─┼─ registry.py ─ refresh ─┤
venues/*.yaml┘     │                   └─ pages /, /{ville}, /{ville}/carte, /{ville}/e/{id}
                   ▼
 connecteur → pipeline.normalize → upsert → dedup_inter_source → linkcheck
 (qfap, ics…)  nettoyage · lieu · statut    (city_id, provenance)  (4 passes)
```

## Modèle de données (`events`, une ligne par créneau)

Clé unique `(source, source_id, start)`. Colonnes ajoutées par migration **additive**
(`db._migrate`, jamais de DROP) : `city_id` (défaut `paris`), `country_code`, `currency`,
`price_min/max`, `booking_url`, `lang`, `i18n` (JSON `{ar:{title,description}}`), `status`
(`active|cancelled|postponed|expired|stale`), `geo_source` (`source|venue_ref`), `venue_id`,
provenance (`first_seen`, `last_changed`, `content_hash`, `dedup_reason`), `link_status`.
`ingested_at` joue le rôle de « dernière observation ». Index `(city_id, start)`.

* **Toute lecture porte sur une ville** : `db.search(city_id=…)` ajoute toujours
  `city_id = :city_id`. Un événement étiqueté Jeddah placé au centre de Paris ne sort pas
  pour Paris (test `test_une_requete_paris_ne_renvoie_jamais_jeddah`).
* Statuts : un événement annulé / reporté / périmé ne sort jamais par défaut.
  `stale` est calculé à la lecture (`last_seen` plus vieux que `stale_after_hours`) et
  signalé à l'utilisateur, jamais masqué.

## Ingestion

`registry.SourceSpec` : une source tourne seulement si `enabled` **et** `authorization: ok`
**et** sa ville est allumée **et** elle n'est pas coupée. Politique par source :
`refresh_hours` (cadence), `timeout_s`, `retries` (attente exponentielle plafonnée à 30 s,
pas de reprise sur 4xx ni URL refusée), `min_interval_s` (par domaine), ETag /
Last-Modified pour l'iCalendar. Une source lente ne bloque pas les autres.

* **Sécurité** (`safety.py`) : texte nettoyé (balises, entités, contrôles, marques
  bidirectionnelles « override »), URL http(s) seulement et sans identifiants, SSRF refusé
  (adresses privées / loopback / lien local / métadonnées cloud) **à chaque redirection** —
  les redirections sont suivies à la main (5 sauts).
* **Normalisation** (`pipeline.py`) : ville/pays/devise, lieu rattaché au référentiel,
  coordonnées du référentiel **seulement si la source n'en donne pas**, coordonnées
  invraisemblables écartées et comptées, statut déduit du titre, versions arabe/anglaise
  d'une même source fusionnées (texte de la source, jamais de traduction automatique).
* **Séries** (`sources.parse_ics`) : `RRULE` développée dans la fenêtre, `EXDATE` exclus,
  `RECURRENCE-ID` (occurrence déplacée ou annulée) remplace l'occurrence générée au lieu de
  s'y ajouter, `STATUS:CANCELLED` respecté. Une plage de dates n'est jamais transformée en
  « actif tous les jours » si la source dit autre chose.

## Fusion / déduplication (`sources.dedup_inter_source`, ville par ville)

Forte (titre ≥ 0,85, ± 30 min, < 150 m) · faible (≥ 0,92, même lieu) · **traduction**
(même créneau, même lieu — référentiel ou < 150 m —, même catégorie, écritures différentes
arabe/latin → un seul événement, l'autre titre rangé dans `i18n`). Le survivant est la source
de plus haute `priority`, puis le plus ancien ; un « annulé » d'une source au moins aussi
prioritaire gagne. Rien n'est supprimé : `doublon_de` + `dedup_reason`, réversible.
Normalisation de comparaison : `textnorm.norm_key` (alef, ى/ي, ة/ه, tatweel, voyelles
brèves, chiffres arabes-indiens, marques bidi). *Piège corrigé* : l'ancienne normalisation
passait par l'ASCII et rendait tout titre arabe « vide », donc identique à un autre.

## Fenêtres temporelles (`timewin.py`)

Calculées dans le fuseau de la ville. `night_cutoff_hour` : à 5 pour Jeddah, il est « ce soir »
jusqu'à 05 h (un événement à 01 h est dans la soirée) ; 0 pour Paris (comportement
historique, vérifié identique à l'ancienne formule sur deux semaines). `weekend_days` :
Paris 5-6, Jeddah 4-5. `now` = 2 h glissantes.

## Pages et URL

`/` choix de ville (≥ 2 villes allumées) **ou** l'accueil de l'unique ville (canonique `/`) ·
`/{ville}`, `/{ville}/carte`, `/{ville}/e/{id}` · `/{lang}/{ville}…` pour les langues non
par défaut (`/ar/jeddah`). `/carte` → 301 `/paris/carte`. Le stockage local ne retient que la
dernière ville et la langue ; l'URL fait foi. Rendu serveur : titre, description, canonical,
hreflang, JSON-LD `Event` (fuseau local, `geo`, gratuité / offre), liste HTML des événements
de ce soir.

## Exploitation

| Besoin | Geste |
|---|---|
| Allumer une ville | `EVENTMAP_CITIES_ENABLED=jeddah` dans `/etc/eventmap.env` + `systemctl restart eventmap` |
| **Couper une ville** (gagne toujours) | `EVENTMAP_CITIES_DISABLED=jeddah` + restart — Paris n'est pas touchée (testé) |
| Couper une source | `EVENTMAP_SOURCES_DISABLED=ficep` + restart |
| État par ville et par source | `curl -s localhost:8000/health` → `cities.<ville>.{data,sources,anomalies,not_running}` |
| Forcer un cycle | `curl -X POST localhost:8000/api/refresh` (localhost seulement) |
| Rôle d'un événement | `GET /api/events/{id}?city=…` → `provenance` |
| Vérifier un déploiement | `python deploy/verify_prod.py` (51 contrôles sur le service réel, sans accès VM) |
| Alertes de données | `GET /health?strict=1` : 503 + liste `alerts` (voir ci-dessous) |

`data.state` : `unknown` (rien interrogé) · `ok` · `partial` (une source en retard ou en
erreur) · `stale` (plus aucune donnée fraîche → `/health` passe `degraded`) · `no_sources`.
Le front distingue « aucun événement » d'une panne de données (13.51).

## Surveillance et alertes (13.53)

Deux sondes distinctes pour qu'un problème de données n'ait jamais l'air d'une panne :

| Sonde | Répond | Signifie |
|---|---|---|
| `GET /health` | 200 tant que l'application tourne | le service est joignable (disponibilité) |
| `GET /health?strict=1` | **503** + `alerts` dès qu'il y a une alerte | les données sont malades |

Alertes (`main.compute_alerts`, par ville ALLUMÉE et par source AUTORISÉE ; une ville éteinte ou une source
non autorisée n'est pas une panne) : `refresh_stalled` (plus de cycle depuis > 2 intervalles + 30 min),
`refresh_failed`, `never_ingested` (passé 20 min de grâce après démarrage), `ingestion_errors` (3 cycles
d'erreur), `silent_source` (2 cycles vides), `stale_data` (> `stale_after_hours`), `geocoding` (> 30 % sans
coordonnées).

**Qui regarde** : `.github/workflows/surveillance.yml` interroge les deux sondes toutes les 2 h depuis GitHub,
ouvre une issue `alerte-donnees` à la première alerte, la commente tant qu'elle dure et la ferme au retour à
la normale (GitHub prévient par courriel). Pas de nouvelle stack : Uptime Kuma est derrière Cloudflare Access
(compte admin à créer à la main) et Observatory n'envoie pas d'alertes.

*Optionnel — Uptime Kuma* (à faire par Hamdy, une fois connecté) : monitor HTTP `https://eventmap.hamdy-tabsissi.com/health`
(disponibilité) + monitor HTTP `https://eventmap.hamdy-tabsissi.com/health?strict=1`, code attendu 200, intervalle
30 min, 3 tentatives avant alerte ; le corps de la réponse nomme la ville, la source et la cause.

**Retour arrière** (testé le 06/10/2026 sur une base de 16 151 lignes réelles) : la migration
est additive. L'ancien code (`master` avant le multi-ville) ouvre la base migrée, répond à
`/health` et `/api/events`, écrit et dédoublonne sans erreur. Revenir en arrière = redéployer le
tag précédent ; aucune restauration de base n'est nécessaire.

## Images externes (13.48)

**Aucune image externe n'est copiée ni affichée.** La CSP (`img-src 'self' data:` + tuiles
OpenStreetMap) bloque d'ailleurs tout hotlinking. Si une source autorise un jour la
réutilisation d'images, il faudra : licence écrite, attribution visible, copie locale mise en
cache, image par défaut — et un changement de CSP assumé.

## Ajouter une troisième ville

1. `cities.yaml` : une entrée (fuseau, devise, langues, centre, rayons, week-end, coupure de
   soirée, `enabled: false` au départ).
2. `venues/<ville>.yaml` : lieux, coordonnées d'une source vérifiable (OSM), alias.
3. `feeds.yaml` : ses sources, avec `city`, `authorization` (vérifier licence **et** CGU), `priority`,
   cadence ; les ajouter à `cities.<ville>.sources`.
4. Si la langue d'interface est nouvelle : `static/i18n.js` + `i18n_server.py`.
5. `pytest` (le registre est validé : ville inconnue, source orpheline, alias ambigu).
6. Allumer avec `EVENTMAP_CITIES_ENABLED`.

Aucune ligne de Python n'est à modifier.

## Demande d'autorisation (brouillon, à relire et à envoyer par Hamdy)

> Objet : Affichage de titres et dates d'événements à Jeddah sur EventMap
>
> Bonjour, je développe EventMap (https://eventmap.hamdy-tabsissi.com), une carte gratuite,
> sans publicité ni compte, qui aide à choisir une sortie. Je souhaiterais afficher, pour chaque
> événement de Jeddah que vous publiez, uniquement : titre, date et heure, lieu, et un lien
> visible vers votre page officielle (et votre billetterie), avec mention de votre nom comme
> source. Je ne copie ni images ni descriptions longues, je relève vos pages au plus une fois
> par jour avec un User-Agent identifiable. Auriez-vous l'amabilité de m'indiquer si vous
> l'autorisez, par écrit, et par quel canal (flux, API partenaire) vous préférez que je procède ?
> Merci d'avance.

## Go / no-go Jeddah (mis à jour le 06/10/2026)

**NO-GO pour l'ouverture publique de Jeddah.** Critères du plan, un par un :

| Critère | État vérifié |
|---|---|
| Aucune régression Paris critique | OK — 51 contrôles sur le service réel (`deploy/verify_prod.py`), base de 16 000+ événements intacte après migration, `/health` ≤ 0,5 s pendant un cycle de refresh (après correction d'un gel de ~45 s, PR #7) |
| Routes / deep links stables | OK pour Paris ; Jeddah répond 404 lisible tant qu'elle est éteinte |
| EN + AR + RTL | OK sur fixtures (parcours complet mobile et bureau) ; relecture arabe par un locuteur natif **non faite** |
| Accessibilité | axe-core : 0 violation sur 6 pages, thèmes clair et sombre ; lecteur d'écran réel **non testé** |
| Rollback testé | OK (ancien code sur base migrée ; interrupteurs ville/source) |
| Monitoring actif | OK — `/health?strict=1` + sonde GitHub Actions (issue ouverte/fermée testée de bout en bout) ; Uptime Kuma non branché (accès Access) |
| Sources autorisées et traçables | **NON** — aucune source de Jeddah n'est autorisée (voir `docs/jeddah-sources.md`) |
| Données Jeddah fraîches, horaires et prix fiables | **NON** — aucune donnée réelle |
| Données réellement disponibles | **NON** |

Ce n'est pas un échec d'ingénierie : afficher des données non autorisées, ou une carte vide présentée comme
« rien ce soir », serait pire que ne pas ouvrir. Le GO devient possible dès qu'**une** source autorisée existe :
`authorization: ok` + connecteur + `EVENTMAP_CITIES_ENABLED=jeddah`, puis relancer `deploy/verify_prod.py`.
