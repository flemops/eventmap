# Runtime Python

| Où | Version | Preuve |
|---|---|---|
| Production (VM Ubuntu 22.04) | **3.10** (venv créé par `python3 -m venv`, voir `deploy/install.sh`) ; exposée par `GET /health` → `release.python` dès le prochain déploiement | `deploy/install.sh`, `.github/workflows/prod-tag.yml` (`version: "3.10"`) |
| CI, porte de qualité des PR | **3.12** (lint, tests, couverture) + matrice **3.10** et **3.13** (tests) | `.github/workflows/ci.yml` |
| Poste de développement documenté | 3.12 | `README.md` |
| Plancher déclaré | `requires-python = ">=3.10"` | `pyproject.toml` |

## Pourquoi 3.10 reste dans la matrice

Python 3.10 sort du support amont en **octobre 2026**. La VM est encore dessus : tant qu'elle n'est pas
migrée, la CI doit prouver que le code tourne sur ce qui est déployé. Le tag `prod` ne se déplace que si
les tests passent sur 3.10 (`prod-tag.yml`).

## État de la migration

* **Prouvé** (CI, sans toucher la prod) : toute la suite passe sur 3.10, 3.12 et 3.13, avec les dépendances
  épinglées de `requirements.txt` (les mêmes versions que la VM).
* **Reste à faire sur la VM** : installer une version supportée (3.12 : paquet `python3.12` via le dépôt
  `deadsnakes`, ou `uv python install 3.12`), recréer `/opt/eventmap/.venv`, `pip install -r requirements.txt`,
  redémarrer `eventmap`, puis laisser `post-deploy.yml` / `deploy/verify_prod.py` confirmer
  (`release.python` doit afficher 3.12.x). Cette étape demande un accès SSH à la VM : elle n'a pas été
  faite dans le cadre de la phase 16 (accès indisponible depuis le poste de travail utilisé).
* **Rollback** : garder l'ancien venv 3.10 à côté (`.venv-310`) jusqu'à validation ; un retour =
  repointer l'unité systemd (`ExecStart`) sur l'ancien venv et redémarrer. Aucun format de données ne
  change entre versions de Python (SQLite et JSON).
* Une fois la VM sur 3.12 : passer `version` à `"3.12"` dans `prod-tag.yml`, retirer `3.10` de la matrice.
