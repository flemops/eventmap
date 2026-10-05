# Jeddah — évaluation des sources (06/10/2026)

Règle : une source n'est interrogée que si sa réutilisation est **autorisée** (licence
ouverte, CGU qui le permettent, ou accord écrit) et accessible de façon **stable**.
`robots.txt` seul ne suffit pas : il dit ce qu'un robot peut *fetcher*, pas ce qu'on a
le droit de *republier*. Résultat au 06/10/2026 : **aucune source jeddawie n'est
utilisable automatiquement**. Le moteur est prêt, la ville reste éteinte
(`cities.yaml`, `enabled: false`) : afficher une carte vide ou alimentée à la main
serait pire que ne rien afficher.

| Source | Accès technique | Conditions | Verdict |
|---|---|---|---|
| **Visit Saudi** — `visitsaudi.com/en/jeddah/events` | `robots.txt` autorise les pages (`/api/`, `/bin/api/`, `/cms/` interdits). 12 pages d'événements Jeddah au 06/10. JSON-LD `Event` sans date ; dates/prix/coordonnées dans des blocs `data-props` du HTML. Dates **sans heure**. | Conditions d'utilisation : licence limitée à l'usage « personnel non commercial » ; « aucune partie de notre plateforme ne peut être reproduite ou stockée sur un autre site sans accord écrit préalable » ; pas d'usage commercial sans licence. | **Bloquée** sans accord écrit |
| **Hayy Jameel** (Art Jameel) — `hayyjameel.org/whats-on/` | WordPress, sitemap `whats-on` (870 pages dont l'historique depuis 2020). Aucun balisage `Event`, dates dans le gabarit (jour / mois / année / heures séparés). API REST fermée (401). `hayyjameel.art` : simple page d'accueil, `Disallow-Training`. | « Tous les droits sont réservés » ; interdiction de reproduire, de dupliquer, d'exploiter le contenu ; licence écrite à la seule discrétion de la société pour tout usage non personnel. | **Bloquée** sans licence écrite |
| **webook.com** | Application monopage, aucun balisage `Event`, pas de flux. Les données passent par `api.webook.com`, interface privée non documentée. `robots.txt` n'interdit que les pages de paiement. | Pas de licence de réutilisation publiée ; pas d'API partenaire publique. | **Bloquée** : pas de méthode stable *et* autorisée. Rien à bricoler. |
| **Ministry of Culture / Red Sea Museum / Jeddah Historic District Program** | Recherche du 06/10/2026 : programmes annoncés par la presse (Arab News, SPA) et par l'application mobile « Historic Jeddah » ; aucun flux iCalendar/JSON ni page d'événements structurée publique identifiée. Les domaines officiels testés (`moc.gov.sa`, `redseamuseum.sa`, `jeddahhistoric.sa`…) ne répondent pas depuis ici (DNS). | Non évaluables : pas de page source stable à examiner. | **Aucune source exploitable trouvée** — à reprendre si un organisme publie un flux ou accorde un accès |

## Ce qui est possible aujourd'hui

* **Référentiel de lieux** : `venues/jeddah.yaml`, coordonnées OpenStreetMap (ODbL),
  vérifiables par l'identifiant OSM de chaque ligne. Aucune donnée d'événement.
* **Moteur** complet testé avec des fixtures (jamais en production) : arabe/anglais,
  fuseau `Asia/Riyadh`, week-end vendredi-samedi, soirée après minuit, statuts
  annulé/reporté, séries avec jours exclus, provenance, interrupteurs.

## Ce qui débloque Jeddah (action humaine)

1. **Demander un accord écrit** à Saudi Tourism Authority (Visit Saudi) et à Art Jameel
   pour l'affichage de titre, date, lieu et lien vers la page officielle, avec
   attribution et renvoi systématique vers la source. Un courriel type est prêt à
   relire : voir `docs/multi-ville.md`, section « Demande d'autorisation ».
2. **Ou** trouver un organisateur qui publie un flux iCalendar / JSON ouvert (OpenAgenda
   a des agendas saoudiens ?) — non trouvé au 06/10/2026.
3. Une fois un accord obtenu : passer `authorization: ok` dans `feeds.yaml`, écrire le
   connecteur (JSON-LD ou `data-props`), ajouter la source à `cities.yaml: jeddah.sources`,
   allumer la ville avec `EVENTMAP_CITIES_ENABLED=jeddah`.

Rien de ce qui précède n'est à faire par l'agent : envoyer une demande d'autorisation
au nom de Hamdy est un envoi externe, qui demande son accord explicite.
