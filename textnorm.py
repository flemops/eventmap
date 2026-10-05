"""Normalisation de texte pour la RECHERCHE et le DÉDOUBLONNAGE — jamais pour
l'affichage (le texte affiché reste celui de la source, à l'identique).

Objectif : qu'un même lieu ou un même événement ne devienne pas deux objets
uniquement parce qu'il est écrit en arabe et en anglais, avec ou sans
voyelles brèves, avec « أ » ou « ا », en chiffres arabes-indiens ou latins.

Ce que fait `norm_key` :
  - NFKC (formes de présentation arabes → lettres de base) ;
  - retire le tatweel (ـ), les voyelles brèves/diacritiques (U+064B–U+065F,
    U+0670), les marques bidirectionnelles invisibles ;
  - unifie les alef (أ إ آ ٱ → ا), ى → ي, ة → ه, ؤ → و, ئ → ي ;
  - chiffres arabes-indiens et persans → chiffres latins ;
  - retire les accents latins, passe en minuscules, remplace toute ponctuation
    par une espace.

C'est volontairement lossy : deux mots arabes distincts peuvent se confondre
(ة/ه). Pour comparer des NOMS DE LIEUX ou des titres au sein d'une même ville
et d'un même créneau, ce risque est négligeable ; ne l'utilisez pas ailleurs.
"""

from __future__ import annotations

import re
import unicodedata

_BIDI_AND_FORMAT = dict.fromkeys(
    [0x061C, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF]
    + list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A))
)
_ARABIC_DIACRITICS = dict.fromkeys(list(range(0x064B, 0x0660)) + [0x0670, 0x0640])
_LETTER_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي",
    "ک": "ك", "ی": "ي",                       # variantes persanes courantes
})
_DIGITS = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGITS.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_PUNCT = re.compile(r"[^\w]+", re.UNICODE)
_ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")


def has_arabic(s: str | None) -> bool:
    return bool(s and _ARABIC_RE.search(s))


def norm_key(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_BIDI_AND_FORMAT).translate(_ARABIC_DIACRITICS)
    s = s.translate(_LETTER_MAP).translate(_DIGITS)
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _PUNCT.sub(" ", s).strip()
