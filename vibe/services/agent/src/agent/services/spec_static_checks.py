"""Small deterministic checks for explicit, source-local specification facts.

These rules do not infer business semantics from keywords. Numeric bounds
must be introduced as an allowed range, and comparisons are made only between
identical or explicitly convertible units. Everything else is left to review.
"""

import re
from decimal import Decimal

_NUMBER = r"[−-]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[,.]\d+)?"
_RANGE = re.compile(
    rf"\b(?:допустим\w*|диапазон\w*|разреш[её]нн\w*)[^.!?\n]{{0,100}}?\bот\s+"
    rf"(?P<lo>{_NUMBER})\s*(?P<unit1>[а-яёa-z₸%]+)?\s+до\s+"
    rf"(?P<hi>{_NUMBER})(?!\d|[.,:]\d)(?:\s*(?P<unit2>[а-яёa-z₸%]+))?", re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r":\s*(?:TBD|TBC|TODO|уточняется|не заполнено|\?{3,})(?=\s*(?:[.;|]|$))", re.IGNORECASE)
_UNIT_SCALE = {
    "₸": ("kzt", Decimal(100)), "kzt": ("kzt", Decimal(100)), "тенге": ("kzt", Decimal(100)),
    **{unit: ("kzt", Decimal(1)) for unit in ("тиын", "тиына", "тиыны", "тиынов", "тиынах")},
    "кг": ("mass", Decimal(1000)), "г": ("mass", Decimal(1)),
    "kg": ("mass", Decimal(1000)), "g": ("mass", Decimal(1)),
    "мс": ("time", Decimal(1)), "ms": ("time", Decimal(1)),
    "сек": ("time", Decimal(1000)), "с": ("time", Decimal(1000)), "s": ("time", Decimal(1000)),
    "мин": ("time", Decimal(60000)), "min": ("time", Decimal(60000)),
}


def _number(value: str) -> Decimal:
    return Decimal(re.sub(r"\s+", "", value).replace(",", ".").replace("−", "-"))


def inverted_ranges(text: str) -> list[str]:
    """Return literal allowed ranges for which the lower bound exceeds the upper."""
    matches = []
    for match in _RANGE.finditer(text):
        # Calendar and clock intervals are not scalar numeric ranges. A
        # month boundary (31.01 -> 01.02) is a perfectly valid interval.
        if re.search(r"\b(?:дат\w*|календар\w*|врем[яени]+\s+суток)\b", match.group(0), re.IGNORECASE):
            continue
        # A numerical descending interval may be cyclic (clock / angular
        # ranges), a displayed scale, an explicitly disabled state, or a
        # negative example. Do not turn these into an impossible input domain.
        context = text[max(0, match.start() - 64):min(len(text), match.end() + 120)]
        if re.search(r"следующ(?:его|ие|их)\s+(?:дня|суток)|через\s+полночь|по\s+часовой\s+стрелке|против\s+часовой\s+стрелки|циклич\w*|через\s+ноль|никак\w*\s+\w+(?:\s+\w+){0,2}\s+не\s+разреш\w*|(?:намеренно|преднамеренно)\s+пуст\w*|пример\w*\s+недопустим\w*", context, re.IGNORECASE):
            continue
        if re.search(r"\bшкал\w*\b", context, re.IGNORECASE) and not re.search(r"\b(?:допустим\w*|разреш[её]нн\w*)\b", match.group(0), re.IGNORECASE):
            continue
        lo, hi = _number(match["lo"]), _number(match["hi"])
        unit1, unit2 = (match["unit1"] or "").casefold(), (match["unit2"] or "").casefold()
        if unit2 in {"включительно", "исключительно"}:
            unit2 = ""
        # If one bound omits its unit, it inherits the common range unit.
        if not unit1 or not unit2 or unit1 == unit2:
            incompatible = lo > hi
        elif unit1 in _UNIT_SCALE and unit2 in _UNIT_SCALE and _UNIT_SCALE[unit1][0] == _UNIT_SCALE[unit2][0]:
            incompatible = lo * _UNIT_SCALE[unit1][1] > hi * _UNIT_SCALE[unit2][1]
        else:
            continue
        if incompatible:
            matches.append(match.group(0))
    return matches


def unfilled_values(text: str) -> list[str]:
    """Only explicit placeholder values, not mentions or template headings."""
    return [match.group(0).strip() for match in _PLACEHOLDER.finditer(text)]
