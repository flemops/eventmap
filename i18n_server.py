"""Textes rendus CÔTÉ SERVEUR (titres, descriptions, choix de ville).

Les textes de l'interface dynamique vivent dans static/i18n.js (même vocabulaire).
Ici, seulement ce qui doit être dans le HTML de la réponse : <title>, description,
aperçus de lien, listes indexables. Chaque entrée est du texte rédigé, pas une
traduction automatique ; l'arabe est à faire relire par un locuteur natif avant
d'être promu (voir docs/multi-ville.md).
"""

from __future__ import annotations

_FR = {
    "noscript": "Cette liste est la version sans JavaScript des événements de ce soir.",
    "home_title": "EventMap — sortir à {city}, par culture et par catégorie",
    "home_desc": ("Les événements parisiens de la semaine, rangés par culture d'origine et par catégorie, "
                  "avec une carte des sorties gratuites dans tout {city}."),
    "home_h1": "Ce soir à {city}",
    "map_title": "Ce soir à {city} — EventMap",
    "map_desc": ("La carte des sorties à {city} ce soir, demain, le week-end ou sur sept jours : "
                 "événements gratuits ou payants, filtrables par catégorie et par distance."),
    "tonight_in": "Ce soir à {city}",
    "official_page": "Page officielle de l'événement",
    "explore": "Explorer",
    "gate_title": "EventMap — où sortez-vous ?",
    "gate_desc": "Choisissez votre ville : Paris ou Djeddah.",
    "gate_h1": "Où sortez-vous ?",
    "soon": "D'autres villes bientôt",
    "cs_label": "Ville", "cs_live": "En ligne", "cs_soon": "Bientôt",
    "cs_ready_title": "{city} arrive bientôt",
    "cs_ready": "Nous préparons une couverture locale fiable des événements.",
    "sources": "Sources", "maps": "Cartes",
    "nf_title": "Page introuvable", "nf_msg": "Cette page ou cette ville n'est pas disponible. Choisissez une ville :",
}

_EN = {
    "noscript": "This list is the no-JavaScript version of tonight's events.",
    "home_title": "EventMap — going out in {city}, by culture and category",
    "home_desc": "This week's events in {city}, grouped by culture of origin and category, with a map of free outings.",
    "home_h1": "Tonight in {city}",
    "map_title": "Tonight in {city} — EventMap",
    "map_desc": ("A map of what's on in {city} tonight, tomorrow, this weekend or over seven days: "
                 "free or paid events, filterable by category and distance."),
    "tonight_in": "Tonight in {city}",
    "official_page": "Official event page",
    "explore": "Explore",
    "gate_title": "EventMap — where are you going out?",
    "gate_desc": "Pick your city: Paris or Jeddah.",
    "gate_h1": "Where are you going out?",
    "soon": "More cities coming soon",
    "cs_label": "City", "cs_live": "Live", "cs_soon": "Coming soon",
    "cs_ready_title": "{city} is coming soon",
    "cs_ready": "We're working on reliable local event coverage.",
    "sources": "Sources", "maps": "Maps",
    "nf_title": "Page not found", "nf_msg": "This page or city isn't available. Pick a city:",
}

_AR = {
    "noscript": "هذه قائمة فعاليات هذا المساء بدون JavaScript.",
    "home_title": "EventMap — الخروج في {city} حسب الثقافة والفئة",
    "home_desc": "فعاليات هذا الأسبوع في {city}، مرتبة حسب الثقافة والفئة، مع خريطة للفعاليات المجانية.",
    "home_h1": "هذا المساء في {city}",
    "map_title": "هذا المساء في {city} — EventMap",
    "map_desc": ("خريطة ما يجري في {city} هذا المساء أو غدًا أو في عطلة نهاية الأسبوع أو خلال سبعة أيام: "
                 "فعاليات مجانية أو مدفوعة، يمكن تصفيتها حسب الفئة والمسافة."),
    "tonight_in": "هذا المساء في {city}",
    "official_page": "الصفحة الرسمية للفعالية",
    "explore": "استكشف",
    "gate_title": "EventMap — أين تريد الخروج؟",
    "gate_desc": "اختر مدينتك: باريس أو جدة.",
    "gate_h1": "أين تريد الخروج؟",
    "soon": "مدن أخرى قريبًا",
    "cs_label": "المدينة", "cs_live": "متاحة", "cs_soon": "قريبًا",
    "cs_ready_title": "{city} قريبًا",
    "cs_ready": "نعمل على تغطية محلية موثوقة للفعاليات.",
    "sources": "المصادر", "maps": "الخرائط",
    "nf_title": "الصفحة غير موجودة", "nf_msg": "هذه الصفحة أو المدينة غير متاحة. اختر مدينة:",
}

_ALL = {"fr": _FR, "en": _EN, "ar": _AR}


def strings(lang: str) -> dict:
    return _ALL.get(lang, _EN)
