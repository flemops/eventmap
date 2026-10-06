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

| **Portail Open Data saoudien** (`open.data.gov.sa`, API ouverte, licences ouvertes) | Injoignable depuis ce poste le 06/10/2026 (aucune réponse : filtrage géographique probable). D'après la recherche, le seul jeu « événements culturels » publié concerne **AlUla** (dernière mise à jour août 2022), pas Jeddah. | Licences ouvertes par jeu de données. | **Aucun jeu d'événements de Jeddah identifié** — à reprendre depuis une adresse saoudienne ou si Hamdy en connaît un |
| **Flux iCalendar / OpenAgenda pour Jeddah** | Recherche du 06/10/2026 : aucun agenda public jeddawi trouvé (seulement des agendas d'institutions, ex. KAUST). | — | **Aucune source** |

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

## Sources écartées de la V1 (décisions de la phase 13, définitives pour cette V1)

| Source | Raison | Réouverture seulement si… |
|---|---|---|
| GEA Open Data | cadre open data compatible, mais aucun jeu d'événements Jeddah actuel et concret identifié | un identifiant de jeu / une URL de téléchargement précis, avec événements actuels (dates + lieux) |
| Plateforme nationale Open Data saoudienne | catalogue et API existent, aucun jeu d'événements Jeddah actuel identifié | un jeu précis avec licence, dates, lieux, fraîcheur et Jeddah vérifiables |
| NEC Open Data | politique favorable, mais aucun fichier/flux public d'événements Jeddah | NEC publie un jeu événementiel public concret |
| NEC National Calendar | accès réservé aux entités gouvernementales (Nafath) : non public, non contournable | l'accès n'est plus réservé aux entités gouvernementales |
| Ticketmaster (Discovery API) | ni illimité ni librement réutilisable : quota 5 000 appels/jour, accès révocable, stockage limité, retrait exigible sous 24 h, réplication/monétisation restreintes | jamais pour la V1 (décision produit) |
| MAHAM Mexpo | API publique mais aucune licence explicite de republication (« endpoint public » ≠ licence) | licence ou autorisation écrite explicite |
| Pages publiques GEA/Enjoy, Details.sa, autres agendas web (dont Tathkara) | utiles pour découvrir, non ingérables sans licence/API/autorisation de stockage et de republication ; Tathkara ne sert que de **fixtures QA** (13.56) | licence, API ou autorisation écrite |
| Red Sea Museum / Ministry of Culture | aucun flux public structuré avec droits de réutilisation (13.58) | un flux/API/jeu réutilisable apparaît |
| Visit Saudi, Hayy Jameel (Art Jameel), webook.com | voir le tableau ci-dessus | accord écrit / API partenaire |

## Quand rouvrir Jeddah (critères exacts)

Une source n'entre en V1 que si **toutes** ces conditions sont vérifiées, noir sur blanc :

1. **gratuite** durablement (aucune dépendance commerciale restrictive) ;
2. **automatisable** (flux, API ou jeu téléchargeable ; aucun contournement d'accès) ;
3. de **vrais événements actuels**, avec au minimum titre + date/heure + lieu/ville + URL source ;
4. **Jeddah identifiable**, fraîcheur exploitable ;
5. **droits explicites** de stockage et de republication (licence ouverte, ou autorisation écrite
   couvrant collecte + stockage + republication). « Accessible publiquement » ne vaut pas « réutilisable ».

Alors seulement : `authorization: ok` dans `feeds.yaml`, connecteur, source ajoutée à
`cities.yaml: jeddah.sources`, puis `EVENTMAP_CITIES_ENABLED=jeddah` et `deploy/verify_prod.py`.
Jusque-là : Jeddah éteinte, aucune fixture exposée comme donnée réelle, aucune métrique ni
affirmation « Jeddah est en ligne » (README, portfolio, pages publiques).

## Tests de régression 13.31 et 13.56 — fixtures QA, pas des sources

`tests/test_phase13_regression.py` rejoue sans réseau des cas réels figés : PFL MENA 10 (annonce
Jeddah du 19/06/2026, report officiel du 17/06, occurrence Riyad du 10/07 : l'ancienne occurrence ne
reste jamais « active », la source officielle gagne, la provenance reste, Riyad n'est pas fusionnée
avec Jeddah, un refresh tardif de l'agrégateur ne ressuscite rien) ; Dream Beach (série quotidienne
07:00 → 03:00 en `Asia/Riyadh`, passage de minuit, aucune occurrence fantôme) et Mangrove Beach
(uniquement les dates fournies).
