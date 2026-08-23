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
