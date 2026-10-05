"""Fenêtres temporelles « Maintenant / Ce soir / Demain / Week-end / 7 jours »,
calculées dans le fuseau de la VILLE, jamais dans celui du serveur ni du
navigateur.

Deux notions propres à une ville (cities.yaml) :

* `night_cutoff_hour` — la soirée ne s'arrête pas à minuit. Avec 5, il est
  « ce soir » jusqu'à 05h00 : à 00h30 on est encore dans la soirée d'hier, et un
  événement à 01h00 y appartient. Avec 0 (Paris, historique) la journée est
  coupée à minuit, comme avant le multi-ville.
* `weekend_days` — Paris : samedi-dimanche. Jeddah : vendredi-samedi.

Tout se fait en arithmétique de date locale (aware, même tzinfo) : zoneinfo
gère les changements d'heure. Le résultat est converti en UTC pour la base.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

WHENS = ("now", "today", "tomorrow", "weekend", "week")
NOW_SPAN = timedelta(hours=2)


def logical_day(local: datetime, cutoff: int) -> datetime:
    """Minuit local du jour auquel appartient `local`, une fois la soirée
    prolongée : à 02h00 avec une coupure à 5, c'est encore la veille."""
    day0 = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return day0 if local.hour >= cutoff else day0 - timedelta(days=1)


def window(when: str, now: datetime, tz, *, weekend_days=(5, 6), cutoff: int = 0
           ) -> tuple[datetime, datetime]:
    if when not in WHENS:
        raise ValueError(f"when invalide: {when!r} ({'|'.join(WHENS)})")
    local = now.astimezone(tz)
    ld = logical_day(local, cutoff)
    cut = timedelta(hours=cutoff)

    def day_end(d0: datetime) -> datetime:
        return d0 + timedelta(days=1) + cut

    if when == "now":
        start, end = local, local + NOW_SPAN
    elif when == "today":
        # « ce soir » commence maintenant, pas à minuit : inutile de proposer
        # un événement commencé il y a trois heures.
        start, end = local, day_end(ld)
    elif when == "tomorrow":
        start, end = day_end(ld), day_end(ld + timedelta(days=1))
    elif when == "weekend":
        first, last = weekend_days
        if first <= ld.weekday() <= last:
            start = local                                  # déjà en cours
            end = day_end(ld + timedelta(days=last - ld.weekday()))
        else:
            ahead = (first - ld.weekday()) % 7
            first_day = ld + timedelta(days=ahead)
            start = first_day + cut                        # la nuit d'avant appartient à la veille
            end = day_end(first_day + timedelta(days=last - first))
    else:  # week
        start, end = local, ld + timedelta(days=7) + cut
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
