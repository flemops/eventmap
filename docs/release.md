# Release, provenance et retour arrière

## Chaîne

```
PR ──► ci.yml (porte de qualité) ──► merge sur master ──► prod-tag.yml ──► tag `prod` ──► la VM tire `prod`
                                                              │                              │
                                       lint + tests (Python de la VM) + couverture        post-deploy.yml
                                       + « les dépendances de prod seules suffisent »     attend /health.release.commit
                                                                                          puis rejoue deploy/verify_prod.py
```

* Le tag `prod` n'est déplacé **que** par `prod-tag.yml`, après une validation verte du commit
  (workflow réutilisable `flemops/ci-templates`, épinglé sur un SHA — voir plus bas).
* Aucun secret d'accès à la VM n'existe côté GitHub : le déploiement est *pull* (la VM tire le tag).

## Quel commit tourne ?

`GET /health` → `release.commit` (12 premiers caractères du commit, lus dans `.git` par `release.py`) et
`release.python`. Comparer avec `git rev-parse --short=12 prod`. `post-deploy.yml` fait cette comparaison
automatiquement après chaque déplacement du tag, puis exécute `deploy/verify_prod.py` (lecture seule).

## Retour arrière

1. Choisir le commit précédent sain : `git log --first-parent prod` (chaque merge de PR est un commit).
2. Déplacer le tag : `git tag -f prod <sha> && git push -f origin prod` (le tag `prod` est le seul
   endroit où un force-push est la procédure normale ; jamais sur `master`).
3. La VM tire le tag ; `requirements.txt` est épinglé en `==` pour que le rollback réinstalle exactement
   les versions de l'époque (voir son en-tête). Les migrations de schéma sont **additives** : l'ancien
   code fonctionne sur la base déjà migrée (`test_un_ancien_code_continue_d_ecrire_dans_une_base_migree`).
4. Vérifier : `EXPECTED_COMMIT=<sha> python deploy/verify_prod.py`.

Versions : pas de numéro sémantique. Le commit (donc le tag `prod` + l'historique des merges) est la version ;
introduire des releases numérotées n'apporterait rien tant qu'il n'y a qu'un seul déploiement.

## Supply chain

| Contrôle | Où | Politique |
|---|---|---|
| Dépendances Python | `pip-audit --strict` dans `ci.yml` | une vulnérabilité connue dans `requirements.txt` fait échouer la PR ; un faux positif sans correctif se tolère par `--ignore-vuln <ID>` **avec justification dans le workflow**, jamais en coupant l'étape |
| SBOM | artefact `sbom` de `ci.yml` (CycloneDX) | inventaire des dépendances d'exécution, conservé 30 jours |
| Mises à jour | `.github/dependabot.yml` (pip + actions) | une PR groupée par mois et par écosystème |
| Actions GitHub tierces | épinglées sur le **SHA de commit** + tag en commentaire | compromis : une mise à jour est une PR Dependabot à relire, en échange de l'impossibilité de voir un tag déplacé exécuter du code nouveau |
| Gabarit CI `ci-templates` | `prod-tag.yml` épinglé sur un SHA | le gabarit décide si un commit peut être déployé : un changement du gabarit ne doit pas être silencieux. Dependabot propose le nouveau SHA ; relire son diff avant d'accepter |
| Secrets | `gitleaks` (commits de la PR) ; audit d'historique complet à la demande (`docs/public-readiness.md`) | `.env`, clés et bases sont dans `.gitignore` ; aucun secret en clair dans les workflows |
| Permissions | `contents: read` par défaut ; élévations locales à un job, commentées | `prod-tag.yml` a `contents: write` (déplacer le tag) |

## Ce qui n'est pas vérifié depuis ce dépôt

Le script de déploiement **côté VM** (celui qui tire le tag `prod`, applique `pip install -r requirements.txt` et
déclenche son propre retour arrière automatique) vit hors de ce dépôt (atelier d'exploitation). La phase 16 n'a
pas eu d'accès SSH à la VM : la procédure de retour arrière manuelle ci-dessus découle du fonctionnement
documenté (tag `prod` = ce qui est déployé, dépendances épinglées), pas d'un essai sur la VM.
