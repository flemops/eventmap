# Sources de données

> **Périmètre depuis le 23/08/2026 : Paris intra-muros, source unique = Que
> faire à Paris** (décision D13). Le connecteur filtre `address_city="Paris"`
> côté API : 2534 events à venir au moment du recentrage. Les sections 2 à 5
> décrivent des connecteurs écrits et testés mais **inactifs** ; la section
> « Sondage banlieue » archive ce qui a été constaté sur les domaines du brief
> initial, pour ne pas refaire le travail si le périmètre s'élargit.

Ce document fait foi pour chaque source branchée dans l'agrégateur : ce qui a
été **vérifié concrètement** (et quand), sous quelle licence, avec quelles
limites, et comment les champs sont projetés dans le schéma interne.

Schéma interne cible (table `events`, voir `db.py`) :

| Colonne      | Type    | Note                                                   |
|--------------|---------|--------------------------------------------------------|
| `source`     | TEXT    | identifiant court de la source (`qfap`, `ics`, …)      |
| `source_id`  | TEXT    | identifiant stable chez la source                      |
| `start`      | TEXT    | ISO 8601 **UTC**, une ligne par créneau                |
| `end`        | TEXT    | ISO 8601 UTC, nullable                                 |
| `title`      | TEXT    |                                                        |
| `description`| TEXT    | texte brut, HTML retiré                                |
| `venue`      | TEXT    | nom du lieu                                            |
| `address`    | TEXT    | adresse postale lisible                                |
| `city`       | TEXT    |                                                        |
| `lat`, `lon` | REAL    | WGS84, nullable (event sans géoloc = non affiché carte)|
| `price_type` | TEXT    | `free` / `paid` / `free_conditional` / `unknown`       |
| `category`   | TEXT    | catégorie normalisée (voir mapping ci-dessous)         |
| `url`        | TEXT    | lien vers la page source                               |
| `updated_at` | TEXT    | date de dernière modification **chez la source**       |

Contrainte : `UNIQUE(source, source_id, start)` — le refresh est idempotent.

---

## 1. Que faire à Paris — Ville de Paris (Opendatasoft)

**Statut : source principale. Vérifiée le 22/08/2026.**

| | |
|---|---|
| Endpoint | `https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/que-faire-a-paris-/records` |
| Métadonnées | `…/catalog/datasets/que-faire-a-paris-` (sans `/records`) |
| Licence | **Open Database License (ODbL)** — https://opendatacommons.org/licenses/odbl/ |
| Éditeur | Direction de la Communication — Ville de Paris |
| Clé API | aucune |
| Quota | aucun documenté ; on s'impose ≤ 1 req/s et un `User-Agent` identifiable |
| Volume | `total_count` = **2965** events (2989 au rechargement 20 min plus tard : le jeu bouge en continu) |
| Fraîcheur | `modified` = 2026-08-22T20:15Z — mise à jour quotidienne confirmée |

### Obligations ODbL

L'ODbL est une licence *share-alike* sur les **bases de données**, pas sur les
contenus individuels. Concrètement pour ce projet :

- **Attribution** : mentionner « Source : Que faire à Paris — Ville de Paris,
  ODbL » dans le front et dans cette doc. Fait.
- **Partage à l'identique** : si l'on redistribue une base *dérivée* (notre
  SQLite agrégée), elle doit l'être sous ODbL. Le dépôt ne versionne pas la
  base (`*.db` dans `.gitignore`), et l'API sert des résultats filtrés, pas un
  dump. On reste dans le cas d'usage, pas de redistribution.
- **Pas de verrou technique** sur une éventuelle redistribution. Non concerné.

### Sur robots.txt

`https://opendata.paris.fr/robots.txt` interdit aux crawlers `/explore/download`
et `/explore/dataset/*/download` — c'est-à-dire **l'export de fichiers par
l'interface web**. L'endpoint `/api/explore/v2.1/…` n'est pas listé, et
robots.txt s'adresse de toute façon aux robots d'indexation, pas aux clients
d'une API publiée pour un usage programmatique. Ce qui fait foi est la
licence ODbL ci-dessus.

### Pagination — vérifié

- `limit` est **plafonné à 100** : `limit=101` → HTTP 400 `InvalidRESTParameterError`.
- Il faut boucler sur `offset` par pas de 100. Pour 3000 events ≈ 30 requêtes,
  soit ~30 s à 1 req/s. Acceptable pour un refresh toutes les 6 h.
- Le filtre `where=` (syntaxe ODSQL) et `group_by=` fonctionnent et permettent
  de compter sans rapatrier : `limit=0` + `total_count`.

### Champs — noms exacts vérifiés sur la réponse réelle

Le brief mentionnait `tags` : **ce champ n'existe pas**. Les deux champs réels
sont `qfap_tags` (fin, séparateur `;`) et `universe_tags` (grossier).

| Champ source        | Type observé | Valeur observée | → interne |
|---------------------|--------------|-----------------|-----------|
| `id`                | str          | `'111329'`      | `source_id` |
| `title`             | str          |                 | `title` |
| `lead_text`         | str          | résumé court    | `description` (préféré à `description` qui est du HTML long) |
| `description`       | str (HTML)   |                 | fallback si `lead_text` vide, HTML retiré |
| `date_start`        | str ISO      | `'2026-09-18T15:00:00+00:00'` | **non utilisé** (voir occurrences) |
| `date_end`          | str ISO      |                 | **non utilisé** |
| `occurrences`       | **str**      | voir ci-dessous | `start` / `end`, **une ligne par créneau** |
| `address_name`      | str          | nom du lieu     | `venue` |
| `address_street`    | str          |                 | `address` |
| `address_zipcode`   | str          | `'75016'`       | concaténé dans `address` |
| `address_city`      | str          | `'Paris'`       | `city` |
| `lat_lon`           | **dict**     | `{'lon': 2.2467, 'lat': 48.8614}` | `lat`, `lon` |
| `price_type`        | str          | 3 valeurs       | `price_type` normalisé |
| `qfap_tags`         | str `;`-sép. | `'Atelier;Conférence;Théâtre'` | `category` (premier tag reconnu) |
| `universe_tags`     | str          | `'spectacle'`   | fallback `category` |
| `url`               | str          | page paris.fr   | `url` |
| `updated_at`        | str ISO      |                 | `updated_at` |

`lat_lon` vaut `None` pour 145 events (~5 %) : ils sont ingérés mais sans
coordonnées, donc absents de la carte et de la recherche par rayon.

### Format de `occurrences` — le champ critique

Ce n'est **pas un tableau** mais une chaîne :

```
début_fin;début_fin;début_fin…
2026-09-18T14:00:00+02:00_2026-09-18T15:00:00+02:00;2026-09-19T14:00:00+02:00_…
```

- Séparateur de créneaux : `;`
- Séparateur début/fin : `_`
- Fuseau : heure locale avec offset (`+02:00`), à convertir en UTC à l'ingestion.

Mesuré sur un échantillon de 100 events : médiane **1** créneau, maximum
**535** (une exposition quotidienne sur 18 mois), 1284 créneaux au total.
C'est ce qui justifie d'éclater en une ligne par créneau : `date_start` /
`date_end` ne décrivent que l'enveloppe globale et sont inutilisables pour
« ce soir ».

### `price_type` — valeurs distinctes (group_by vérifié)

| Source                     | Count | → interne          |
|----------------------------|-------|--------------------|
| `gratuit`                  | 1416  | `free`             |
| `payant`                   | 1430  | `paid`             |
| `gratuit sous condition`   | 119   | `free_conditional` |

### Couverture géographique — LE résultat décisif

Comptes exacts par `where=address_city="…"` :

| Commune     | Events |
|-------------|--------|
| Paris       | 2590   |
| Montreuil   | 14     |
| Puteaux     | 1      |
| **Nanterre**| **0**  |
| Saint-Ouen  | 0      |
| Bagnolet    | 0      |
| Vincennes   | 0      |

**Cette source couvre le sommet « Paris » du triangle et quasiment rien
d'autre.** Pour Nanterre elle est vide. La couche 2 (flux .ics municipaux et
de salles, étape 4) n'est donc pas un bonus : c'est la seule façon d'avoir du
contenu sur deux des trois sommets.

---

## 2. OpenAgenda

**Statut : vérifié le 22/08/2026 — nécessite une clé.**

`GET https://api.openagenda.com/v2/agendas?size=1` sans clé → **HTTP 403**
`{"message":"could not find user or agenda matching key"}`.

L'API v2 exige une clé (`key=`) obtenue en créant un compte. Le connecteur
est écrit et lit la clé dans `OPENAGENDA_KEY` ; **sans clé il est simplement
inactif** et l'agrégation continue sans lui. Les agendas publics y sont sous
licence au choix du propriétaire de l'agenda — à vérifier agenda par agenda
avant d'en ajouter un dans `feeds.yaml`.

---

## 3. Flux iCalendar (.ics)

**Statut : connecteur générique écrit ; annuaire à constituer (étape 4).**

| | |
|---|---|
| Format | RFC 5545, parsé avec la bibliothèque `icalendar` |
| Licence | **celle de chaque éditeur** — un flux public d'une mairie est en général réutilisable, mais ce n'est pas automatique. Chaque entrée de `feeds.yaml` doit porter sa licence ou `unknown`. |
| Quota | ≤ 1 req/domaine/s, `User-Agent` avec URL de contact, cache par `ETag`/`Last-Modified` |
| Géoloc | champ `GEO` rare ; fallback `LOCATION` → géocodage différé (non implémenté : on ingère sans coordonnées) |

Mapping : `UID` → `source_id`, `DTSTART`/`DTEND` → `start`/`end` (les `RRULE`
sont développées sur 90 jours), `SUMMARY` → `title`, `LOCATION` → `venue`,
`URL` → `url`, `LAST-MODIFIED` → `updated_at`.

---

## 4. Pages HTML via JSON-LD `schema.org/Event`

**Statut : détecteur écrit dans `discover.py`.**

Beaucoup de sites exposent `<script type="application/ld+json">` avec
`@type: Event` pour le SEO. C'est structuré, stable, et ne demande aucun LLM.
Mapping direct : `name` → `title`, `startDate`/`endDate` → `start`/`end`,
`location.name` → `venue`, `location.geo` → `lat`/`lon`, `offers.price == 0`
→ `free`, `url` → `url`. Le `source_id` est un hash de `url + startDate`.

---

## 5. Extraction HTML par LLM (`scrape.py`)

**Statut : dernier recours, désactivé par défaut.**

Coûteux, non déterministe, et pose la question de la licence du contenu
scrappé. N'est appelé que pour un domaine explicitement listé dans
`feeds.yaml` avec `type: llm`, jamais automatiquement. Nécessite une clé
d'API LLM en variable d'environnement.

---

## Note sur la version de l'API Opendatasoft

Les noms de champs ci-dessus sont ceux observés sur **Explore API v2.1** le
22/08/2026. Opendatasoft a déjà renommé des champs entre versions ; le
connecteur lève une erreur explicite si un champ obligatoire (`id`, `title`,
`occurrences`) manque, plutôt que d'ingérer silencieusement des lignes vides.

---

## Sondage banlieue — archive du 23/08/2026 (hors périmètre)

Treize domaines du brief initial (Nanterre, Montreuil, 92, 93) sondés avec
`discover.py` puis à la main. **Résultat : aucun flux structuré exploitable.**

| Domaine | Constat |
|---|---|
| `montreuil.fr` | Drupal. JSON-LD présent mais seulement `WebPage`/`BreadcrumbList`, aucun `Event`. Ni .ics ni RSS sur `/agenda`. |
| `lamarbrerie.fr` | WordPress. **RSS `/feed/` exploitable** : 30 items catégorie « Agenda ». Mais `pubDate` = date de publication, pas de l'event. `/events/?ical=1` → 404 (The Events Calendar absent). |
| `instantschavires.com` | WordPress. **RSS `/feed/` exploitable** : catégories CONCERTS/MUSIQUE/EXPOSITIONS. Même limite sur `pubDate`. |
| `maisondelamusique.eu` | WordPress, mais le RSS ne contient qu'un « Hello world! » de 2025. Programmation en HTML pur sur `/saison/`. |
| `meliesmontreuil.fr` | CMS propriétaire, rien. |
| `lechinois.com` | Rien. |
| `nanterre.fr` | TYPO3. Aucun JSON-LD, pas de .ics ni RSS (`/agenda?format=ics` renvoie du HTML). |
| `theatre-nanterre-amandiers.fr` | **NXDOMAIN.** Le vrai domaine est `nanterre-amandiers.com` (WordPress, `/feed/` → 404). |
| `nanterre-lagora.fr` | **NXDOMAIN.** `lagora-nanterre.fr` non plus. |
| `est-ensemble.fr` | **NXDOMAIN** sans `www.` ; `www.est-ensemble.fr` répond mais sans flux. |
| `seine-saint-denis.fr` | Redirige vers `/shield` (anti-bot). |
| `hauts-de-seine.fr` | TYPO3, pas de flux. |
| `vallee-culture.hauts-de-seine.fr` | **Certificat TLS expiré** au sondage. |
| OpenAgenda | Un agenda « Sortir & bouger à Montreuil » (uid 14898606), 1 event, API avec clé uniquement. |

Deux pistes si le périmètre s'élargit un jour : un parser RSS dédié pour
La Marbrerie et Les Instants Chavirés (extraire la date de l'event du titre
ou de la page), et une demande d'export iCal à la DSI de Nanterre.
