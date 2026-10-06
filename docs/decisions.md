# Décisions d'architecture

Format : contexte → décision → conséquences. Une entrée par décision qui a
coûté une hésitation ou qui surprendrait quelqu'un qui reprend le code.

---

## D1 — Reconstruire de zéro plutôt que compléter l'existant

**Contexte.** Le brief présentait cinq modules « déjà écrits ». Recherche
exhaustive le 22/08 : disques locaux, quatre dépôts GitHub, VM Oracle — aucune
trace. Ils ont vraisemblablement été rédigés dans un autre chat et jamais
enregistrés.

**Décision.** Tout réécrire, avec les décisions du brief (éclatement des
occurrences, contrainte d'unicité, dédoublonnage) intégrées au schéma dès le
départ plutôt que greffées sur un existant inconnu.

**Conséquence.** Pas de risque d'incompatibilité entre un `main.py` neuf et
des modules fantômes. Le coût est d'avoir perdu le travail précédent, s'il a
existé.

---

## D2 — Une ligne par créneau, `UNIQUE(source, source_id, start)`

**Contexte.** Sur l'API Que faire à Paris, `occurrences` est une chaîne
`début_fin;début_fin;…`. Mesuré : médiane 1 créneau, maximum **535** (une expo
quotidienne sur 18 mois). `date_start`/`date_end` ne décrivent que l'enveloppe.

**Décision.** Éclater chaque créneau en une ligne. La clé d'unicité inclut
`start`, ce qui rend l'upsert idempotent : rejouer un refresh ne crée rien.

**Conséquence.** « Ce soir » devient un simple `WHERE` sur `start`/`end`, sans
parser de tableau à la requête. En contrepartie la table est ~3,5× plus grosse
que le nombre d'events (10 387 lignes pour 2 892 events). Négligeable en SQLite.

---

## D3 — Fenêtre d'ingestion de 90 jours

**Contexte.** Sans borne, l'event à 535 créneaux produirait 535 lignes dont
500 inutiles pendant des mois.

**Décision.** On n'ingère que les créneaux entre J−1 et J+90. Un refresh toutes
les 6 h fera glisser la fenêtre ; rien n'est perdu.

**Conséquence.** La base reste petite et les requêtes rapides. Un utilisateur
ne peut pas regarder à quatre mois — ce n'est pas le produit (« ce soir », pas
un catalogue).

---

## D4 — Un event pertinent *chevauche* la fenêtre, il n'y *commence* pas forcément

**Contexte.** Premier test réel à 23 h : « ce soir » renvoyait des événements
commencés à 9 h du matin. Ils étaient bien dans la base, mais le filtre
`start BETWEEN` les incluait parce qu'ils avaient commencé dans la journée —
alors qu'une expo ouverte 10 h–19 h doit sortir à 17 h et pas à 23 h.

**Décision.** Filtre `COALESCE(end, start + 3h) > maintenant AND start < fin
de fenêtre`. Sans `end` connu, on suppose 3 h de durée. Tri par
`MAX(start, maintenant)` : ce qui commence bientôt d'abord, la distance ensuite.

**Conséquence.** Une séance de 19 h apparaît avant une expo ouverte depuis
10 h, même si l'expo est plus proche. C'est le comportement attendu pour
« qu'est-ce que je fais ce soir ».

---

## D5 — Limitation de débit *par domaine*, dans le client HTTP, pas dans les connecteurs

**Contexte.** La VM sert aussi le portfolio. Une IP bannie par un site
municipal est gênante ; bannie par Opendatasoft, c'est la source principale
qui tombe.

**Décision.** `PoliteClient` tient un verrou et un horodatage par hôte :
≤ 1 requête/s quel que soit le nombre de connecteurs concurrents qui visent
le même domaine. Les connecteurs n'ont rien à savoir.

**Conséquence.** Rapatrier QFAP (30 pages) prend ~30 s au lieu de 3 s. Pour un
refresh toutes les 6 h c'est sans importance, et c'est la garantie qu'aucun
bug de concurrence en amont ne peut mitrailler un domaine.

---

## D6 — Un connecteur ne rattrape jamais ses propres exceptions

**Contexte.** Tentation classique : `try/except: return []` dans chaque
connecteur pour « être robuste ».

**Décision.** Interdit. Un connecteur lève ; c'est `aggregate()` qui, via
`gather(return_exceptions=True)` + `wait_for`, transforme l'échec en
`SourceResult(ok=False, error=…)`.

**Conséquence.** L'information « cette source est tombée » remonte jusqu'à
`/health` et jusqu'à `mark_feed`, qui désactive un flux après 5 échecs. Avec
un `return []` silencieux, un flux mort serait martelé à chaque refresh pour
toujours. C'est vérifié par `test_aggregate_isolates_failures`.

---

## D7 — Ne pas purger une source qui a échoué

**Contexte.** Après un refresh, on supprime les lignes non revues pour
refléter les annulations. Mais si la source était injoignable, « non revu »
ne veut rien dire.

**Décision.** `purge_stale` n'est appelé que pour une source en `ok=True` et
hors réponse `304 Not Modified`.

**Conséquence.** Une panne Opendatasoft de 12 h laisse l'ancien contenu en
place au lieu de vider la carte. Un event annulé met au pire un cycle de plus
à disparaître.

---

## D8 — Catégories normalisées sur 9 valeurs, par mots-clés

**Contexte.** QFAP a `qfap_tags` (« Atelier;Conférence;Théâtre ») et
`universe_tags` ; les .ics ont `CATEGORIES` libres ; le JSON-LD a `@type`.
Aucun vocabulaire commun.

**Décision.** Une table de mots-clés → `music | theatre | cinema | expo | kids
| workshop | talk | sport | market | other`, premier match gagnant. Même
fonction pour toutes les sources.

**Conséquence.** Le filtre catégorie du front est stable. Le classement est
approximatif (un « atelier théâtre » sort en `theatre`) — assumé, c'est un
filtre grossier, pas une taxonomie.

---

## D9 — Dédoublonnage inter-sources : titre normalisé + jour + < 200 m

**Contexte.** Le même concert apparaîtra dans QFAP et dans le .ics de la
salle. Pas d'identifiant commun.

**Décision.** Clé floue : titre en minuscules sans accents ni ponctuation,
même date, et — si les deux ont une géoloc — distance < 200 m. Priorité à la
version géolocalisée puis la mieux décrite. Seuil d'une heure sur l'heure de
début pour ne pas fusionner deux séances d'un même film.

**Conséquence.** Imparfait par construction : deux événements homonymes le
même jour dans le même pâté de maisons seront fusionnés à tort. Documenté,
accepté — le coût d'un doublon affiché est inférieur à celui d'un faux
négatif qui cacherait un événement.

---

## D10 — Le JSON-LD `schema.org/Event` est détecté mais ne sert à rien ici

**Contexte.** Hypothèse du brief : beaucoup de sites exposent des `Event` en
JSON-LD pour le SEO.

**Décision.** Le détecteur est écrit et testé (`discover.py`). Sondage réel
des 13 domaines du brief : cinq ont du JSON-LD, **aucun n'a un seul `Event`**
— uniquement `WebPage`, `BreadcrumbList`, `Organization`.

**Conséquence.** Le code reste (il ne coûte rien et d'autres domaines en
auront), mais il ne faut pas compter dessus pour la couverture. La couche 2
passera par des parsers RSS dédiés ou du scrape.

---

## D11 — `scrape.py` (LLM) désactivé par défaut et jamais automatique

**Contexte.** Un scrape LLM coûte de l'argent à chaque refresh, n'est pas
déterministe, et la licence du contenu extrait est rarement claire.

**Décision.** Le module existe, mais ne tourne que pour une entrée
`type: llm` explicite dans `feeds.yaml`, et jamais depuis `refresh_loop`.

**Conséquence.** Nanterre restera vide tant qu'on n'aura pas soit un accord
avec la ville pour un export, soit accepté le coût d'un scrape ciblé de
`nanterre.fr/agenda`.

---

## D12 — Garde-fous systemd sur la VM partagée

**Contexte.** La VM héberge le portfolio destiné au CV. Le vrai risque n'est
pas un bug fonctionnel, c'est une boucle de refresh qui sature le CPU, remplit
le disque ou fait bannir l'IP.

**Décision.** Unité dédiée, utilisateur sans shell, `MemoryMax=512M`,
`CPUQuota=50%`, `Nice=10`, `ProtectSystem=strict` avec `ReadWritePaths`
limité à `/opt/eventmap/data`, logs journald plafonnés, port
`127.0.0.1:8000` jamais exposé, sous-domaine nginx séparé, et désactivation
automatique d'un flux après 5 échecs.

**Conséquence.** Dans le pire cas (source qui boucle, flux qui renvoie 50 Mo),
EventMap est tué par le cgroup et redémarre ; le portfolio ne voit rien.

---

## D13 — Périmètre recentré sur Paris intra-muros

**Contexte.** Le brief visait le triangle Nanterre–Paris–Montreuil. Deux
constats mesurés le 23/08 : Que faire à Paris a 0 event à Nanterre et 14 à
Montreuil ; et le sondage de 13 domaines de banlieue n'a produit aucun flux
exploitable (ni .ics, ni JSON-LD `Event`, trois domaines disparus). Couvrir
la banlieue aurait exigé soit du scrape LLM coûteux et fragile, soit des
accords avec les villes — hors de proportion pour le bénéfice.

**Décision.** Paris intra-muros uniquement. Filtre `address_city="Paris"`
côté API Opendatasoft, centre par défaut Châtelet, rayon plafonné à 8 km.
`feeds.yaml` vidé mais conservé avec les pistes. L'architecture multi-sources
reste en place : rien n'est supprimé, tout est désactivé.

**Conséquence.** Le produit tient sa promesse dès le premier jour : ~2 500
events géolocalisés, à jour quotidiennement, sur une seule source fiable et
sous licence claire. Le coût est d'abandonner l'« avantage compétitif » de
l'annuaire de flux municipaux — qui, dans les faits, n'avait rien à
annuaire. Un élargissement futur se fera source par source, en ajoutant des
entrées à `feeds.yaml`, sans toucher au code.

---

## D14 — Culture d'un lieu : exclusif → majoritaire + mots-clés d'exclusion

**Contexte.** D2-D13 et l'en-tête historique de `cultures.yaml` posaient une
règle stricte : un lieu n'entre que si TOUT ce qu'il programme relève de la
culture annoncée. Appliquée aux 10 lieux du flux FICEP branché le 03/09
(voir D-suivante), elle en éliminait 6 sur 10 pour une minorité
d'événements multi-pays — et elle mordait déjà, sans que personne l'ait vu,
sur l'Institut suédois déjà en base (ciné-club coproduit avec 4 autres pays).

**Décision.** Option C, tranchée par Hamdy le 03/09/2026 (voir
`mapping-ficep.md`) : un lieu entre s'il est *majoritairement* dédié à sa
culture, plus une liste de mots-clés d'exclusion (`exclusions:` dans
`cultures.yaml`) qui retire l'attribut événement par événement quand le
titre/la description trahit un événement multi-pays. Le libellé affiché
suit : « Lieux dédiés à la culture X », jamais « Culture X » — la facette
qualifie le lieu, pas l'événement.

**Conséquence.** Faux positif assumé (une soirée cinéma soudanais peut
s'afficher sous « Allemagne ») plutôt qu'un lieu entier écarté pour une
exception. Le nombre d'événements écartés par l'exclusion est exposé dans
`/health` (`cultures.excluded_events`) — sans ce chiffre, une règle trop
large ou trop étroite passerait inaperçue.

---

## D15 — Dédoublonnage inter-sources réversible, remplace la fusion pré-upsert de D9

**Contexte.** D9 fusionnait les doublons inter-sources AVANT l'upsert : le
perdant n'était simplement jamais écrit en base, sans trace, sans lien —
irréversible par construction. Correct tant qu'une seule source alimentait
la base ; insuffisant dès que FICEP (03/09) crée de vrais doublons inter-
sources à grande échelle et que le paquet de déploiement exige un garde-fou
explicite : « la fusion ne supprime jamais un enregistrement ».

**Décision.** Toutes les sources sont upsertées telles quelles. Une passe
`sources.dedup_inter_source()`, exécutée après l'upsert, marque les
doublons via une colonne `doublon_de` (jamais de DELETE) : `db.search`
filtre à la lecture. Trois passes documentées dans `paquet-deploiement.md`
§3 — l'exacte (même `source`+`source_id`, déjà garantie par la contrainte
UNIQUE), la forte (titres ≥ 0,85, ± 30 min, < 150 m — fusionne aussi les
champs par priorité : prix QFAP, géo FICEP, description QFAP) et la faible
(titres ≥ 0,92, même jour, même lieu — lien seul, sans réécrire les champs).

**Conséquence.** Corriger un faux positif de rapprochement est un simple
`UPDATE events SET doublon_de = NULL`, pas une réingestion. La passe
recalcule tout à chaque cycle sur les événements à venir (reset puis
reclassement), donc un changement de priorité entre deux sources ou une
source qui disparaît ne laisse pas de lien orphelin. Les compteurs par passe
sont exposés dans `/health.dedup` pour repérer une passe qui s'emballe.
L'ancienne `sources.dedupe()` (fusion en mémoire, pré-upsert) est supprimée :
elle n'a plus d'appelant et sa suppression évite un second mécanisme de
dédoublonnage à maintenir en parallèle.

---

## D16 — FICEP : premier flux `feeds.yaml` actif, filtré par boîte englobante

**Contexte.** `feeds.yaml` était vide depuis D13 (« conservé avec les
pistes »). FICEP (10 instituts culturels étrangers, via OpenAgenda, licence
ouverte, 149 VEVENT vérifiés le 03/09) est le premier flux réellement
branché. Deux pièges trouvés en le sourçant : (1) le flux déborde de son
périmètre déclaré — quelques entrées à Arles, Bruxelles, Lyon ; (2) l'UID
OpenAgenda encode l'occurrence après « // » (`35008205//20260917T080000Z`),
ce qui rend l'identifiant illisible en dehors du stockage si on ne le
découpe pas.

**Décision.** `feeds.yaml` gagne un champ optionnel `geo_bbox` par flux
(lat/lon min/max), lu dans `main._build_fetchers` et appliqué après le parse
ICS dans `_ics_fetcher` — un événement hors boîte est écarté et journalisé,
jamais cru sur parole. `sources.parse_ics` découpe systématiquement l'UID au
premier « // » (`stable_id` de repli si l'UID est absent) : sans « // »
c'est un no-op, donc sans risque pour les flux déjà en place.

**Conséquence.** Élargir à un nouveau flux hors Île-de-France n'exige qu'une
entrée `feeds.yaml` avec le bon `geo_bbox`, sans toucher au code — dans
l'esprit de D13. Le filtre géo est une boîte large (marge sur la petite
couronne) : c'est le rayon de recherche de `/api/events` (8 km max depuis
Châtelet) qui fait le tri fin, pas ce filtre d'ingestion.

---

## D17 — Cache-busting `?v=<hash>` sur `accueil.js`/`app.js` : Cloudflare sert du JS périmé malgré un déploiement propre

**Contexte.** Trouvé le 03/09/2026 en vérifiant CE déploiement dans un vrai
navigateur (pas seulement au curl, cf. la mise en garde du 02/09 sur la carte
cassée) : après `git pull` + `systemctl restart`, `curl` sur l'origine
montrait le nouveau contenu, mais le navigateur chargeait encore l'ancien
`accueil.js`. Cause : `cf-cache-status: HIT`, `Cache-Control: public,
max-age=14400` — Cloudflare sert son cache d'edge (4 h) pour les extensions
statiques et ignore le `max-age=600` posé par nginx pour `location /`. Un
restart de service ne purge rien côté CDN.

**Décision.** `accueil.html`/`index.html` référencent leurs scripts propres
avec un hash de contenu en query string (`accueil.js?v=<sha256 tronqué>`,
même principe que `osint.css?v=…` côté portfolio) — jamais les vendor
(`leaflet.js`/`leaflet.css`, qui ne changent pas). Une URL différente est une
entrée de cache différente pour Cloudflare : pas besoin de purge, l'edge
n'a jamais vu cette URL.

**Conséquence.** Toute future modification de `accueil.js` ou `app.js` DOIT
recalculer son hash (`sha256sum static/accueil.js`, 10 premiers caractères
hex suffisent) et mettre à jour la query string dans le HTML correspondant —
sinon le déploiement est invisible en navigateur jusqu'à 4 h malgré un
`curl`/`/health` parfaitement verts. Oublier ce détail reproduit exactement
le piège du 02/09 (page cassée sans erreur console visible, curl pourtant OK).

---

## D18 — Refonte UX de la carte (06/10/2026) : hiérarchie ville → ce soir → sélection → carte + liste

**Décision.** L'en-tête ne montre plus que : marque, ville (`Paris ▾`, un `<details>` rendu serveur),
titre, et `Tonight · Weekend · Free · Filters`. Tomorrow, 7 jours, catégorie, distance vivent dans
le panneau Filtres (feuille du bas sur mobile, popover sur ordinateur). Jetons de design uniques
(`--sp-*`, `--r-*`, `--fs-*`, `--sh-*`) dans `common.css`.

**« Top picks tonight »** : classement déterministe côté client (`pickScore` dans `app.js`), sans score
affiché : événement actif, à jour, localisé, avec lieu + description + lien officiel valide, prix connu,
qui commence dans les 90 min passées ou plus tard ; proximité seulement si l'utilisateur s'est
géolocalisé. La section n'apparaît que si la liste compte ≥ 6 événements ET ≥ 3 sont assez complets
(seuil 4 points) : sinon, pas de faux classement.

**La carte ne relance plus la recherche toute seule.** Un déplacement net (> ⅓ du rayon) propose
« Search this area » ; avant, chaque pan rechargeait au bout de 500 ms et refermait la fiche.

**Favoris** : `localStorage` (`em_saved_<ville>`, identifiants seuls), vue « Saved » rechargée via
`/api/events/{id}` ; les événements passés ou disparus sont oubliés. Aucun compte.

---

## Backlog — hors périmètre, consigné pour ne pas l'oublier

- **Élargissement hors Paris** : voir D13 et `docs/sources.md` § sondage
  banlieue. Deux RSS WordPress exploitables à Montreuil (Marbrerie, Instants
  Chavirés) si un parser dédié est écrit.

- **Contrainte transport** (fiche Notion) : « rentrer avant le dernier train ».
  Navitia `/journeys?datetime_represents=arrival` depuis le lieu vers le
  domicile, calculé une fois par lieu et mis en cache. C'est la contrainte
  qu'aucune app ne calcule. Dépend d'une clé Navitia.
- **Parser RSS dédié** pour La Marbrerie et Les Instants Chavirés : extraire
  la date de l'événement depuis le titre/contenu, pas depuis `pubDate`.
- **Géocodage différé** des .ics sans `GEO` (Nominatim, 1 req/s, cache).
- **Sessions persistantes** : sans importance tant que l'accès est par lien.

---

## D19 — Phase 16 : qualité et supply chain sans changer de stack (06/10/2026)

**Contexte.** Rendre le dépôt démontrable (qualité, CI, supply chain) sans réécriture ni nouvelle
technologie « pour faire moderne ».

**Décisions.**

* **Ruff : lint seulement, pas `ruff format`.** Jeu court (E/F/B/I/UP) ; le formateur réécrirait 25 fichiers
  sur 30 (colonnes alignées, lignes denses volontaires) pour un gain nul. Évalué, mesuré, écarté.
* **Couverture : plancher non régressif à 78 %**, fixé sous la mesure de départ (80 % en branches, 06/10/2026)
  — pas 90 % arbitraire. Monter le plancher quand la mesure monte, jamais l'inverse.
* **Typage (Pyright/mypy) : non ajouté.** Évalué : la base n'est pas annotée uniformément ; une passe de
  typage stricte serait une grosse PR cosmétique. À démarrer module par module (`db.py`, `pipeline.py`) si un
  défaut de type réel apparaît.
* **CodeQL : essayé.** Résultat et critère de réactivation en D20.
* **Branche `master` conservée** (pas de passage à `main`) : la migration toucherait le gabarit CI, la VM
  et d'autres chantiers en cours pour un gain nul.
* **Stratégie de merge unique : commit de merge** (squash et rebase désactivés) : l'historique garde le
  découpage en commits cohérents d'une PR, et c'est ce que l'historique contient déjà.
* **Pas de `src/eventmap/` ni de découpage de `main.py`.** Mesuré (complexité cyclomatique, seuil 12) :
  une seule fonction très complexe (`sources.dedup_inter_source`, 28), pure et très testée ; `main.py` est
  organisé en sections claires, et ses fonctions d'état (`_refresh_state`, `compute_alerts`, `_data_state`)
  sont couplées par ~50 tests qui s'appuient sur `main.*`. Déplacer du code coûterait plus qu'il ne rapporterait.
  Critère de réouverture : un second point d'entrée (CLI, worker) qui doit réutiliser le refresh sans importer FastAPI.
* **SQLite conservé** : mesures et seuils dans `docs/performance.md` et `docs/scaling.md`.
* **Dépendances** : les six dépendances d'exécution sont toutes importées (vérifié) ; `uvicorn[standard]`
  est gardé pour ses extras de performance (uvloop, httptools) sur le chemin chaud.

## D20 — CodeQL sur dépôt privé

**Contexte.** CodeQL a été ajouté (`.github/workflows/codeql.yml`) pour Python et JavaScript.

**Décision.** Voir le résultat dans la PR de la phase 16 : s'il ne peut pas publier ses résultats sur un
dépôt privé sans GitHub Advanced Security, le workflow est retiré plutôt que laissé rouge, et réactivé au
passage du dépôt en public (gratuit). Dans l'intervalle, les contrôles actifs sont Ruff (E/F/B), `pip-audit`
(dépendances), gitleaks (secrets), et les en-têtes/CSP vérifiés par `deploy/verify_prod.py`.
