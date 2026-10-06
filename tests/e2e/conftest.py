"""E2E : navigateur réel (Chromium via Playwright) contre un serveur de test à base jetable.

Ignoré si Playwright n'est pas installé (la CI principale ne l'installe pas) :
    pip install -r requirements-e2e.txt && playwright install chromium && pytest tests/e2e
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

HERE = Path(__file__).resolve().parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url(tmp_path_factory):
    port = _free_port()
    db_path = tmp_path_factory.mktemp("e2e") / "e2e.db"
    env = {**os.environ, "EVENTMAP_DB": str(db_path)}
    proc = subprocess.Popen([sys.executable, str(HERE / "server_stub.py"), str(port)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/health", timeout=1)
            break
        except Exception:  # noqa: BLE001 — le serveur démarre
            if proc.poll() is not None:
                raise RuntimeError(proc.stdout.read().decode()) from None
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("serveur E2E non démarré")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    # Portée « module » : le Playwright synchrone garde une boucle asyncio vivante tant qu'il tourne ;
    # l'arrêter dès la fin du module évite de casser les tests `asyncio.run(...)` qui passent après.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-GB")
    # Les tuiles OpenStreetMap ne sont pas le sujet : pas de réseau externe dans les tests.
    ctx.route("**/tile.openstreetmap.org/**", lambda r: r.abort())
    p = ctx.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    yield p
    ctx.close()
