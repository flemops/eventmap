# Carte des tests, par risque

Les tests tournent sans réseau (`pytest`). Le nombre de tests n'est volontairement pas figé ici : la CI
l'affiche à chaque exécution, et la couverture est mesurée (`pytest --cov`, plancher dans la CI).

| Risque | Ce qui est protégé | Où |
|---|---|---|
| Ingestion / connecteurs | extraction d'un enregistrement QFAP, ODS, ICS, JSON-LD ; pagination, panne non masquée, plafonds | `tests/test_core.py`, `tests/test_multiville.py` (`test_ods_*`) |
| **Contrat amont** | un enregistrement **réel** par source (fixtures datées) : un champ renommé échoue sans réseau | `tests/test_contracts_invariants.py`, `tests/fixtures/contracts/` |
| Normalisation | nettoyage, URL sûres, SSRF à chaque redirection, statut déduit du titre | `test_pipeline_*`, `test_ssrf_*` |
| Dates / fuseaux | « ce soir / demain / week-end » par ville, passage à minuit, changement d'heure, `Z` = UTC | `test_changement_d_heure_a_paris`, `test_un_Z_est_de_l_UTC_*`, invariant date naïve |
| Déduplication | forte/faible/traduction, réversibilité, la source prioritaire gagne, volume | `test_dedup_*`, `test_la_source_prioritaire_*` |
| Multi-ville | une requête d'une ville ne renvoie jamais une autre ville (cas fixes **et** invariant aléatoire à graine fixe) | `test_une_requete_paris_ne_renvoie_jamais_jeddah`, `test_invariant_aucun_evenement_*` |
| Last-known-good | une source en panne ou qui répond vide ne vide pas la base | `test_invariant_last_known_good_*` |
| Migrations SQLite | ancienne base → schéma actuel, idempotence, **ancien code sur base migrée** (retour arrière sûr) | `test_migration_*`, `test_un_ancien_code_continue_*` |
| API | filtres, validation des paramètres, ville éteinte, HEAD/GET | `test_route_api_*`, `test_parametres_invalides_*` |
| Santé / alertes | `/health` toujours 200, `?strict=1` en 503, chaque alerte nommée, version déployée | `test_chaque_alerte_*`, `test_health_*` |
| Rendu | pages, SEO, JSON-LD, échappement des injections | `test_pages_*`, `test_injection_*` |
| Régression métier | cas réels de la phase 13 (report officiel, séries, passage 23h59/00h00) | `tests/test_phase13_regression.py` |
| **Navigateur réel (E2E)** | première visite → Tonight, filtres Free/recherche, fiche + Échap, My Evening (chevauchement, partage, `.ics`), favoris persistants, aucun débordement et **axe-core sans violation grave** à 1440/1280/1024/390 px | `tests/e2e/` (`e2e.yml`, Chromium via Playwright, base jetable) |
| Déploiement (service réel) | **hors pytest**, lecture seule : `deploy/verify_prod.py` (+ `post-deploy.yml`) | `deploy/verify_prod.py` |
| Performance | **hors pytest** : `deploy/bench.py` | `docs/performance.md` |

## Règles

* Pas d'assertion sur une valeur volatile de production (nombre d'événements, dates absolues du jour) :
  des propriétés (« aucun événement d'une autre ville »), ou une fenêtre fixe (`WIN` des tests de contrat).
* Une fixture de contrat se renouvelle en recapturant un enregistrement (même requête que le connecteur) :
  le diff montre exactement ce que la source a changé.
* **E2E** : `pip install -r requirements-dev.txt -r requirements-e2e.txt && playwright install chromium && pytest tests/e2e`.
  Ignoré quand Playwright est absent (la porte de qualité `ci.yml` ne l'installe pas) ; `e2e.yml` le rejoue quand
  l'interface change et une fois par semaine. Les événements d'essai sont préfixés `[E2E]` : aucune donnée réelle.
* **Trou connu** : les parcours « première visite → Jeddah » (15.58) n'existent pas tant que Jeddah est éteinte ;
  la comparaison visuelle au pixel et le lecteur d'écran réel restent manuels (Phase 12).
