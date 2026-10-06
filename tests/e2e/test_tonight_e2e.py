"""Phase 15.58 — parcours critiques dans un vrai navigateur (Chromium).

Jeddah est éteinte (aucune source admise, docs/jeddah-sources.md) : les parcours « première visite → Jeddah »
n'existent pas tant qu'elle l'est ; ils s'ajouteront ici le jour où `EVENTMAP_CITIES_ENABLED=jeddah` aura des données.
"""

import re

import pytest
from axe_playwright_python.sync_playwright import Axe

pytestmark = pytest.mark.e2e
WEEK = "/?when=week"          # fenêtre 7 jours : indépendante de l'heure à laquelle les tests tournent


def titles(page):
    return page.locator("#list button.ev h3").all_inner_texts()


def test_premiere_visite_ouvre_tonight_a_paris(page, base_url):
    page.goto(base_url + "/")
    page.wait_for_selector("#list button.ev")
    assert "Tonight in Paris" in page.locator("#h1").inner_text()
    assert page.title().startswith("Tonight in Paris")
    # aucune distance n'est affichée tant que l'utilisateur ne s'est pas localisé (15.5)
    assert not re.search(r"\b\d+([.,]\d)? ?km\b|\b\d+ ?m\b", page.locator("#list").inner_text())
    assert page.errors == []


def test_free_et_recherche_filtrent_la_liste(page, base_url):
    page.goto(base_url + WEEK)
    page.wait_for_selector("#list button.ev")
    page.click("#free")                                   # défaut = Free ; bascule vers tous
    page.wait_for_function("document.querySelectorAll('#list button.ev').length > 4")
    assert any("Expo payante" in t for t in titles(page))
    page.click("#open-filters")
    page.fill("#fp-search", "expo")
    page.wait_for_function("[...document.querySelectorAll('#list button.ev h3')].every(h => /expo/i.test(h.textContent))")
    assert titles(page) and all("expo" in t.lower() for t in titles(page))
    assert "q=expo" in page.url


def test_fiche_s_ouvre_et_se_ferme_au_clavier(page, base_url):
    page.goto(base_url + WEEK)
    page.wait_for_selector("#list button.ev")
    page.locator("#list button.ev").first.click()
    assert page.locator("#detail").is_visible() and page.locator("#detail h2").is_visible()
    assert page.locator("#detail a:has-text('Directions')").get_attribute("href").startswith("https://www.google.com/maps/dir/")
    page.keyboard.press("Escape")
    assert page.locator("#detail").is_hidden() and page.locator("#list").is_visible()


def test_my_evening_chevauchement_partage_et_agenda(page, base_url):
    page.goto(base_url + WEEK)
    page.wait_for_selector("#list button.ev")
    page.click("#free")                                       # Paris ouvre sur « gratuit » : on veut aussi le payant
    page.wait_for_selector("#list button.ev:has-text('Expo payante')")
    for needle in ("Concert gratuit", "Expo payante"):        # +1h→+3h et +2h→+5h : se chevauchent
        page.locator("#list button.ev", has_text=needle).first.click()
        page.click("#eve-add")
        page.click("#detail .back")
    assert page.locator("#eve-n").inner_text() == "2"
    page.click("#eve-btn")
    page.wait_for_selector(".timeline li")
    assert page.locator(".timeline li").count() == 2
    assert page.locator(".tl-meta .tag.warn").count() == 1       # chevauchement signalé
    assert "not estimated" in page.locator(".eve-note").inner_text()
    ics = page.locator("#eve-cal").get_attribute("href")
    resp = page.request.get(base_url + ics)
    assert resp.status == 200 and resp.text().count("BEGIN:VEVENT") == 2
    # lien de partage = lecture seule, sans stockage local
    ids = re.search(r"ids=([\d%C,]+)", ics).group(1).replace("%2C", ",")
    ctx2 = page.context.browser.new_context(viewport={"width": 1440, "height": 900})
    ctx2.route("**/tile.openstreetmap.org/**", lambda r: r.abort())
    p2 = ctx2.new_page()
    p2.goto(f"{base_url}/paris/carte?evening={ids}")
    p2.wait_for_selector(".timeline li")
    assert p2.locator(".timeline li").count() == 2 and p2.locator(".tl-rm").count() == 0
    ctx2.close()
    # retrait
    page.locator(".tl-rm").first.click()
    page.wait_for_function("document.querySelectorAll('.timeline li').length === 1")


def test_sauvegarde_et_persistance(page, base_url):
    page.goto(base_url + WEEK)
    page.wait_for_selector("#list button.ev")
    page.locator("#list button.ev").first.click()
    page.click("#save")
    page.reload()
    page.wait_for_selector("#list button.ev")
    assert page.locator("#saved-btn").is_visible() and page.locator("#saved-n").inner_text() == "1"


@pytest.mark.parametrize("width", [1440, 1280, 1024, 390])
def test_pas_de_debordement_et_axe_sans_violation_grave(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900 if width > 500 else 800})
    page.goto(base_url + WEEK)
    page.wait_for_selector("#list button.ev")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    res = Axe().run(page)
    graves = [v for v in res.response["violations"] if v["impact"] in ("serious", "critical")]
    assert not graves, [(v["id"], v["impact"], [n["target"] for n in v["nodes"]][:3]) for v in graves]


def test_donnees_difficiles_ne_cassent_pas_la_liste(page, base_url):
    """Sans fin connue, sans description, prix inconnu : la carte se rend, sans « Free » inventé (15.20/15.59)."""
    page.goto(base_url + WEEK)
    page.wait_for_selector("#list button.ev")
    page.click("#free")                                    # tous les prix
    carte = page.locator("#list button.ev", has_text="Cinéma").first
    assert "Free" not in carte.inner_text()
    assert page.errors == []
