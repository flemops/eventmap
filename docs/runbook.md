# Runbook de récupération humaine (14.27)

À n'ouvrir que si l'automatisation ne peut raisonnablement pas agir (voir D23 dans `decisions.md`). Tout le reste se
répare seul : retry, dernier contenu conservé, quarantaine, circuit breaker, rollback par tag.

## 0. Savoir quoi regarder (2 commandes, lecture seule)

```bash
curl -s https://eventmap.hamdy-tabsissi.com/health | python -m json.tool | head -60   # alertes, sources, version servie
curl -s "https://eventmap.hamdy-tabsissi.com/health?strict=1" -o /dev/null -w "%{http_code}\n"   # 200 = aucune alerte, 503 = au moins une
```

## 1. Alerte → réponse

| Alerte (`/health`, issue `alerte-donnees`) | Ce que l'automatisme a déjà fait | Action humaine, seulement si elle persiste |
|---|---|---|
| `ingestion_errors`, `stale_data` | retries avec gigue, dernier contenu conservé, bannière « sources en retard » | ouvrir l'URL de la source ; si le format a changé, mettre à jour le connecteur (la fixture `tests/fixtures/contracts/` le montre) |
| `source_breaker` | source coupée (6 h, puis ×2 jusqu'à 24 h) et sondée seule | vérifier que la source existe encore ; sinon `enabled: false` dans `feeds.yaml` |
| `quarantine` | lot écarté, ancien contenu servi ; accepté après 3 lots de suite | décider si le rétrécissement est réel (alors rien) ou un parseur cassé (corriger) |
| `db_integrity` | rien (la base est peut-être abîmée) | § 3 |
| `disk_low` / `refresh_failed` « disque presque plein » | cycle suspendu, contenu servi inchangé | libérer de l'espace (logs, anciennes sauvegardes) ; le cycle reprend seul |
| `refresh_stalled` | — | `sudo systemctl restart eventmap`, puis `journalctl -u eventmap -n 100 --no-pager` |

Rejouer une collecte sans FastAPI (14.3) : `cd /opt/eventmap && sudo -u <user du service> env $(cat /etc/eventmap.env | xargs) .venv/bin/python main.py refresh` — un cycle complet, résumé « n/m sources OK » sur stdout, code de sortie 1 si aucune source n'a répondu. Il n'a pas le verrou du service web : à lancer quand la boucle interne n'est pas en cours (`/health` → `refresh.running` à `false`). Le service garde sa boucle interne (pas de timer séparé : deux collecteurs doubleraient les requêtes aux sources sans problème mesuré).

Watchdog (14.9, posé sur la VM le 07/10/2026) : `eventmap-watchdog.timer` (toutes les minutes) exécute `/usr/local/bin/eventmap-watchdog.sh` (versionné dans `deploy/`) : 3 échecs de `/health` de suite → `systemctl restart eventmap` (3 redémarrages/h au plus) ; alerte `refresh_stalled` → 1 redémarrage / 6 h ; disque ≥ 90 % tracé dans le journal. Journal : `journalctl -t eventmap-watchdog`. Essai à blanc : voir l'en-tête du script.

## 2. Mauvais déploiement : revenir au commit sain

Voir `release.md` : `git log --first-parent prod`, puis `git tag -f prod <sha-sain> && git push -f origin prod` ; la VM tire le tag.
Les migrations sont additives : l'ancien code tourne sur la base migrée. Contrôle : `/health` → `release.commit`.

## 3. Base abîmée ou perdue

1. Garder une copie de l'existant : `cp /opt/eventmap/data/eventmap.db /opt/eventmap/data/eventmap.db.abimee` (hors
   suppression : on ne supprime rien tant que le service n'est pas revenu).
2. Restaurer la dernière sauvegarde (Litestream/restic, voir le portfolio) **ou**, sans sauvegarde, repartir à neuf :
   `sudo systemctl stop eventmap && mv /opt/eventmap/data/eventmap.db /opt/eventmap/data/eventmap.db.perdue && sudo systemctl start eventmap`.
3. Au démarrage le schéma est recréé (`db.connect()`), puis le premier cycle part 5 s après (`refresh_loop`) et repeuple chaque
   ville allumée depuis ses sources (testé : `test_base_perdue_se_reconstruit_sans_edition_manuelle`). Compter quelques minutes ;
   `/health` indique `refresh.running`. Ce qui n'est PAS reconstruit : les compteurs anonymes du funnel et l'historique de santé
   des sources.

## 4. Configuration nginx invalide

`sudo nginx -t` indique la ligne fautive. Revenir à la dernière configuration valide (`deploy/nginx/`), puis `sudo systemctl reload nginx`.
