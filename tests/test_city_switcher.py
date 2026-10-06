"""Sélecteur de ville : Paris « Live », Jeddah « Coming soon » (texte seulement).

Jeddah reste éteinte : aucune route, aucune API, aucune donnée. Le sélecteur ne dit que
la vérité — le moteur est prêt, les données ne sont pas là.
"""

import pytest

import cities
import db
import main
import render


@pytest.fixture
def client(tmp_path, monkeypatch):
    chemin = str(tmp_path / "t.db")
    db.connect(chemin).close()
    vraie = db.session
    monkeypatch.setattr(db, "session", lambda *a, **k: vraie(chemin))
    from fastapi.testclient import TestClient
    return TestClient(main.app)


@pytest.mark.parametrize("path", ["/", "/paris", "/paris/carte"])
def test_le_selecteur_montre_paris_live_et_jeddah_coming_soon(client, path):
    html = client.get(path).text
    assert 'class="cityswitch"' in html
    assert 'aria-current="page">Paris <span class="cs-badge live">En ligne</span>' in html
    assert "Jeddah" in html and "Bientôt" in html and "Coming soon" not in html
    assert "Jeddah arrive bientôt" in html and "couverture locale fiable" in html
    assert "cityswitch.js" in html


def test_jeddah_n_est_ni_un_lien_ni_une_route_ni_une_ville_de_l_api(client):
    html = client.get("/").text
    nav = html[html.index('<nav class="cityswitch"'):html.index("</nav>", html.index('<nav class="cityswitch"'))]
    assert 'href="/jeddah' not in nav and "/jeddah" not in nav.replace("cs-msg-jeddah", "")
    btn = nav[nav.index('<button'):nav.index("</button>")]
    assert 'aria-disabled="true"' in btn and "href" not in btn
    assert client.get("/jeddah").status_code == 404
    assert client.get("/api/events", params={"city": "jeddah"}).status_code == 404
    assert [c["id"] for c in client.get("/api/cities").json()["cities"]] == ["paris"]
    assert "jeddah" not in client.get("/sitemap.xml").text.lower()
    assert client.get("/api/cities").json()["default"] == "paris"


def test_aucun_evenement_ni_donnee_jeddah_dans_les_pages(client):
    for path in ("/", "/paris/carte"):
        html = client.get(path).text
        # Les seules occurrences de « Jeddah » : le bouton, l'id du message et le message lui-même.
        reste = html.replace("cs-msg-jeddah", "").replace("Jeddah arrive bientôt", "")                     .replace('Jeddah <span class="cs-badge">Bientôt', "")
        assert "jeddah" not in reste.lower(), path
        assert "fixture" not in html.lower()
    assert client.get("/health").json()["cities"]["jeddah"]["enabled"] is False


def test_les_trois_langues_ont_le_texte():
    j = cities.get("jeddah")
    p = cities.get("paris")
    for lang, needle in (("fr", "Jeddah arrive bientôt"), ("en", "Jeddah is coming soon"),
                         ("ar", "جدة قريبًا")):
        html = render.city_switch_html(p, lang)
        assert needle in html and j.name(lang) in html
    assert "Coming soon" in render.city_switch_html(p, "en")
    assert "We&#x27;re working on reliable local event coverage." in render.city_switch_html(p, "en")


def test_une_ville_sans_teaser_ou_allumee_change_le_rendu(monkeypatch):
    p = cities.get("paris")
    # Allumée : un vrai lien, plus de bouton « Coming soon ».
    monkeypatch.setenv("EVENTMAP_CITIES_ENABLED", "jeddah")
    html = render.city_switch_html(p, "fr")
    assert 'href="/jeddah"' in html and "Bientôt" not in html and "<button" not in html
    monkeypatch.delenv("EVENTMAP_CITIES_ENABLED")
    # Sans teaser : la ville éteinte disparaît (une seule ville → pas de sélecteur du tout).
    import dataclasses
    monkeypatch.setitem(cities.load(), "jeddah", dataclasses.replace(cities.get("jeddah"), teaser=False))
    assert render.city_switch_html(p, "fr") == ""


def test_la_carte_expose_le_panneau_filtres_et_un_selecteur_details(client):
    html = client.get("/paris/carte").text
    assert 'id="filters-panel"' in html and 'role="dialog"' in html
    assert '<details class="cs">' in html and "<summary>Paris" in html
    assert 'data-when="today"' in html and 'data-when="weekend"' in html and 'id="free"' in html
    assert "cityswitch.js" in html and "app.js" in html
    # Le Jeddah « bientôt » reste du texte : aucun lien, aucune route.
    assert 'href="/jeddah' not in html
