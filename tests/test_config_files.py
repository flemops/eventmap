"""Validation des fichiers de configuration et de déploiement : une erreur ici casserait la CI, le
déploiement ou la sécurité sans qu'aucun test applicatif ne le voie."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SHA40 = re.compile(r"^[0-9a-f]{40}$")
YAML_FILES = sorted(
    p for p in list(ROOT.glob("*.yaml")) + list(ROOT.glob("venues/*.yaml")) + list(ROOT.glob(".github/**/*.yml"))
)
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_les_yaml_se_chargent(path):
    assert yaml.safe_load(path.read_text(encoding="utf-8")) is not None


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_chaque_workflow_a_declencheur_jobs_et_permissions(path):
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert True in wf or "on" in wf, "pas de déclencheur (PyYAML lit `on:` comme True)"
    assert wf.get("jobs"), "aucun job"
    assert "permissions" in wf, "permissions non déclarées (le défaut du dépôt serait trop large)"


def _uses(wf: dict):
    for job in wf["jobs"].values():
        if "uses" in job:
            yield job["uses"]
        for step in job.get("steps", []):
            if "uses" in step:
                yield step["uses"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_toute_action_tierce_est_epinglee_sur_un_sha(path):
    """Supply chain : un tag ou une branche peut être déplacé, un SHA de commit non."""
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    for ref in _uses(wf):
        if ref.startswith("./"):
            continue
        _, _, version = ref.partition("@")
        assert SHA40.match(version), f"{ref} n'est pas épinglée sur un SHA de commit"


def test_l_unite_systemd_garde_son_durcissement():
    unit = (ROOT / "deploy" / "eventmap.service").read_text(encoding="utf-8")
    for directive in ("NoNewPrivileges=true", "ProtectSystem=strict", "PrivateTmp=true", "MemoryMax=",
                      "RestrictAddressFamilies=", "ReadWritePaths=/opt/eventmap/data", "User=eventmap"):
        assert directive in unit, f"{directive} a disparu de l'unité systemd"
    assert "--host 127.0.0.1" in unit, "le service doit rester lié à localhost (nginx devant)"


def test_nginx_garde_ses_garde_fous():
    conf = (ROOT / "deploy" / "nginx" / "eventmap.conf").read_text(encoding="utf-8")
    assert "limit_req zone=eventmap_api" in conf
    assert re.search(r"location /api/refresh\s*\{\s*deny all;", conf), "/api/refresh doit rester fermé côté nginx"
    # Toute location qui sert du contenu inclut les en-têtes de sécurité (piège add_header documenté dans le fichier).
    assert conf.count("include /etc/nginx/snippets/eventmap-entetes.conf;") >= 3
    csp = (ROOT / "deploy" / "nginx" / "snippets" / "eventmap-csp.conf").read_text(encoding="utf-8")
    assert "script-src 'self'" in csp and "unsafe-inline'" not in csp.split("style-src")[0]
