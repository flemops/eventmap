# Runtime Python

| Où | Version | Preuve |
|---|---|---|
| Production (VM Ubuntu 22.04, aarch64) | **3.12.15** depuis le 07/10/2026 (Python autonome `uv` dans `/opt/pybuild/python`, venv `/opt/eventmap/.venv`) | `GET /health` → `release.python` |
| CI, porte de qualité des PR | **3.12** (lint, tests, couverture) + **3.13** (tests) ; le tag `prod` se déplace après les tests 3.12 | `.github/workflows/ci.yml`, `prod-tag.yml` |
| Plancher déclaré | `requires-python = ">=3.12"` | `pyproject.toml` |

## Migration 3.10 → 3.12 (faite le 07/10/2026)

1. `uv` installé depuis PyPI dans un venv jetable (`/opt/pybuild/uvenv`), `uv python install 3.12` → `/opt/pybuild/python/cpython-3.12.15-linux-aarch64-gnu`. Aucune source apt modifiée.
2. Venv de test `/opt/pybuild/test` : `pip install -r requirements.txt` et `import main` OK.
3. Bascule : `systemctl stop eventmap`, `mv .venv .venv-310`, nouveau venv 3.12 + `pip install -r requirements.txt`, `systemctl start eventmap`, attente de `/health` (arrêt ≈ 1 min) ; retour arrière automatique si `/health` ne répond pas. Vérifié : `/health` → `python: 3.12.15`, `status: ok`.
4. **Rollback manuel** (tant que `/opt/eventmap/.venv-310` existe) : `systemctl stop eventmap && mv .venv .venv-312 && mv .venv-310 .venv && systemctl start eventmap`. Aucun format de données ne change entre versions de Python.
5. Les déploiements `app-pull` recréent `.venv` seulement s'il manque (`deploy/install.sh`), désormais avec le Python 3.12 de `/opt/pybuild` s'il existe.

Supprimer `.venv-310` et `/opt/pybuild/test` après une semaine sans incident.
