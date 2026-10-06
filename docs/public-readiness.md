# Audit « public-ready » (06/10/2026)

**Statut : le dépôt est PRIVÉ. Le passer en public est une décision de son propriétaire** ; ce document
dit ce qui a été vérifié, ce qui reste à décider, et ce qu'il faut faire juste avant.

## Ce qui a été vérifié (historique Git complet, toutes branches)

| Contrôle | Méthode | Résultat |
|---|---|---|
| Secrets (jetons GitHub/AWS/Slack, clés privées, `*_KEY=` renseignées) | `git grep` sur tous les commits | aucun |
| Adresses IP (VM, hôtes) | `git grep` IPv4 sur tous les commits | aucune adresse d'infrastructure (seules des adresses de test/SSRF publiques ou réservées) |
| Courriels personnels | `git grep` sur les domaines usuels | aucun |
| Chemins locaux (`C:\Users`, `D:\CLAUDE`, `/home/…`) | `git grep` | aucun |
| Fichiers de données / secrets (`.db`, `.env`, `.log`, `.key`, `.pem`) | `git log --name-only` | jamais commités ; `.gitignore` les couvre |
| Gros fichiers binaires (> 300 ko) | `git rev-list --objects` | aucun |
| Secrets, à chaque PR | `gitleaks` dans `ci.yml` ; lancer `ci.yml` à la main (`workflow_dispatch`) sur la branche par défaut en fait un balayage de tout l'historique | vert sur la PR de la phase 16 |

À **refaire juste avant une publication** (l'historique continue de bouger) : relancer `gitleaks` en
balayage complet et les `git grep` ci-dessus.

## Ce qui est visible si le dépôt devient public (à connaître, pas à cacher)

* **Noms d'hôtes publics** (`eventmap.hamdy-tabsissi.com`, le portfolio) : déjà publics par nature.
* **Prénom du propriétaire** dans des commentaires et des brouillons de texte (`cultures.yaml` : « brouillon
  factuel — à réécrire par … ») : informatif, sans donnée sensible. Les brouillons de présentation des
  instituts culturels y sont du contenu éditorial affiché par le site.
* **Topologie d'exploitation** (`deploy/`) : une VM Linux, un utilisateur système, `/opt/eventmap`,
  nginx + systemd, les mesures de durcissement. Pédagogique et sans secret ; aucun chemin de certificat
  privé n'est lu par le dépôt (seuls les *chemins* de configuration nginx figurent).
* **Données tierces** : les fixtures de `tests/fixtures/contracts/` sont de vrais enregistrements
  (ODbL Ville de Paris, Licence Ouverte v1.0 OpenAgenda, un agenda ICS FICEP, une page de salle). Les deux
  premiers sont librement réutilisables avec attribution ; pour la page de salle, seul le bloc JSON-LD public
  (titre, date, lieu) est conservé. Si une source objecte : supprimer sa fixture et son test de contrat.
* Un brouillon de **demande d'autorisation** (`docs/multi-ville.md`) est écrit pour être relu et envoyé par le propriétaire.

## Décision à prendre : licence du CODE (séparée de celle des données)

Aucune licence n'est commitée : **par défaut, tous droits réservés** (personne n'a le droit de réutiliser le
code). Ne pas en choisir une à la place du propriétaire ; options :

| Option | Effet |
|---|---|
| **MIT** | réutilisation libre y compris commerciale, mention du copyright ; la plus simple, la plus lue par les recruteurs |
| **Apache-2.0** | comme MIT + licence de brevets explicite |
| **AGPL-3.0** | tout service dérivé exposé en réseau doit publier ses sources ; dissuade la réutilisation commerciale fermée |
| **Tous droits réservés** (statu quo, repo public « source visible ») | on peut lire, pas réutiliser ; signal moins « open source » |

Dans tous les cas, les licences des **données** restent celles de `feeds.yaml` / `docs/sources.md`.

## Check-list le jour de la publication

1. Décider la licence du code et commiter `LICENSE`.
2. Rejouer l'audit ci-dessus (balayage `gitleaks` complet).
3. Relire les brouillons éditoriaux (`cultures.yaml`) et la demande d'autorisation.
4. Activer CodeQL (voir D20) et la protection de branche (gratuites sur un dépôt public).
5. Ajouter la capture d'écran du produit au README si elle manque encore.
6. Passer la visibilité en public (`gh repo edit flemops/eventmap --visibility public --accept-visibility-change-consequences`).
