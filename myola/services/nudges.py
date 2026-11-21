"""Today: one gentle reminder at most, and the greeting.

Pure functions over what the day view already knows (no queries). The tone is the app's:
consistency, not pressure. No calories, no targets, no judgement, nothing shown on past days.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.utils.translation import gettext as _

# A meal counts as "missed so far" only after this local hour (and only on today).
MEAL_AFTER_HOUR = {"breakfast": 11, "lunch": 15, "dinner": 21}
START_HOUR = 9  # "Your diary is open" when nothing is logged by then


@dataclass(frozen=True)
class Nudge:
    key: str
    icon: str
    title: str
    text: str
    url: str = ""
    action: str = ""


def greeting(now: dt.datetime) -> str:
    hour = now.hour
    if 5 <= hour < 12:
        return _("Good morning")
    if 12 <= hour < 18:
        return _("Good afternoon")
    return _("Good evening")


def pick_nudge(
    *,
    is_today: bool,
    now: dt.datetime,
    groups,
    water_ml: int,
    scan_url: str,
) -> Nudge | None:
    """The single most useful reminder for today, or None.

    ``groups`` are the day's meal groups (``key``, ``label``, ``entries``, ``scan_url``).
    Priority: nothing logged yet → the latest meal that has passed without an entry → no water.
    """
    if not is_today:
        return None
    logged = {g.key for g in groups if g.entries}
    if not logged and now.hour >= START_HOUR:
        return Nudge(
            "start",
            "notebook-pen",
            _("Your diary is open"),
            _("Nothing logged yet today. Scan the first thing you eat, it takes a few seconds."),
            scan_url,
            _("Scan a barcode"),
        )
    missed = [
        g for g in groups
        if g.key in MEAL_AFTER_HOUR and g.key not in logged and now.hour >= MEAL_AFTER_HOUR[g.key]
    ]  # fmt: skip
    if logged and missed:
        g = missed[-1]
        return Nudge(
            f"meal-{g.key}",
            g.icon,
            _("Add %(meal)s?") % {"meal": g.label.lower()},
            _("If you had %(meal)s, you can still add it. Skipping a meal is fine too.")
            % {"meal": g.label.lower()},
            g.scan_url,
            _("Scan for %(meal)s") % {"meal": g.label.lower()},
        )
    if not water_ml and now.hour >= 12:
        return Nudge(
            "water",
            "glass-water",
            _("A glass of water?"),
            _("No water logged today. Tap +250 ml in Water when you have one."),
            "#water-h",
            _("Go to Water"),
        )
    return None
