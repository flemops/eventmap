"""Prouve que l'application s'importe avec les seules dépendances de PRODUCTION (requirements.txt).

Lancé par la CI dans un venv vierge : un import qui ne marcherait qu'avec un outil de dev
(pytest, ruff...) passerait les tests mais casserait la VM, où le rollback serait le seul filet.
    python deploy/check_imports.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402,F401

print("import main : OK")
