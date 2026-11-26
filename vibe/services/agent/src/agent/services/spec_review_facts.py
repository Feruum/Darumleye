"""Small, source-grounded numeric and temporal index for expert review.

The index records literals, not findings. Comparisons are search hints that
must be checked against the complete conditions and original quotations.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
from itertools import combinations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.services.spec_document import Specification
    from agent.services.spec_review import Requirement


FACT_CONTEXT_INSTRUCTIONS = """
The optional fact_context indexes literal quantities, bounds, dates and times
in the supplied requirements. Normalized values are arithmetic conversions,
not substitute quotes. comparison_candidates are search hints, not defects:
different values may belong to distinct conditions, fields, actors or time
windows. Check both original clauses, their scope, exceptions and business
meaning before reporting a contradiction. Never infer an omission from the
absence of a fact in this limited index. Cite original source evidence, not
the normalized representation or candidate itself. Probe values suggest
hypothetical inputs, never expected results or confirmed counterexamples.
"""

SCENARIO_WITNESS_INSTRUCTIONS = """
For proposed conflicting or untestable obligations, prefer a minimal modeled
witness when the document supplies enough information: specify the initial
state, concrete input or boundary value and action in scenario; in outcomes,
state the result under each cited rule and the decision that remains
undetermined. Check explicit exceptions, precedence and whether the same
preconditions apply before concluding that results conflict. Include an
ordinary consistent boundary case in your reasoning as a negative control;
do not report it as a defect. Do not invent assumptions as requirements or
claim an actual system test was executed. The witness is supplementary to
the verified finding citations.
"""

_NUMBER = r"[-−+]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?"
_UNIT = (r"₸|тенге|тиын(?:а|ов)?|kzt|руб(?:лей|ля|ль)?|₽|usd|доллар(?:а|ов)?|"
         r"%|процент(?:а|ов)?|миллисекунд(?:а|ы|у)?|мс|ms|секунд(?:а|ы|у)?|сек|sec|"
         r"минут(?:а|ы|у)?|мин|min|час(?:а|ов)?|hours?|дн(?:ей|я)|день|days?|"
         r"месяц(?:а|ев)?|months?|штук(?:а|и)?|шт")
_QUANTITY = re.compile(rf"(?<![\w−+\-])(?P<number>{_NUMBER})\s*(?P<unit>{_UNIT})(?![\w])", re.I)
_RANGE = re.compile(rf"\bот\s+(?P<lower>{_NUMBER})(?:\s*(?P<lower_unit>{_UNIT})(?![\w]))?\s+до\s+(?P<upper>{_NUMBER})\s*(?P<unit>{_UNIT})(?![\w])", re.I)
_MONTHS = dict(zip(("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"), range(1, 13)))
_DATE = re.compile(rf"(?<![\w])(?P<day>\d{{1,2}})\s+(?P<month>{'|'.join(_MONTHS)})(?![\w])", re.I)
_TIME = re.compile(r"(?<![\d:])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])")
_STOP = frozenset({"должен", "должна", "должно", "должны", "равен", "равна", "равно", "значение", "система", "тенге", "тиын", "kzt", "рублей", "минут", "секунд", "часов", "процент", "менее", "более", "либо", "между", "после", "перед"})
_MAX_PER_REQUIREMENT = 12
_MAX_CONTEXT_FACTS = 32
_MAX_CONTEXT_CANDIDATES = 12
_MAX_COMPARISON_FACTS = 96


@dataclass(frozen=True)
class Fact:
    id: str
    requirement_id: str
    kind: str
    value: str
    unit: str
    relation: str
    start: int
    end: int
    quote: str
    context: str


def _normalized_quantity(raw: str, unit: str) -> tuple[str, str]:
    number = Decimal(re.sub(r"[ \u00a0\u202f]", "", raw).replace(",", ".").replace("−", "-"))
    unit = unit.casefold()
    if unit in {"₸", "тенге", "kzt"}:
        unit, scale = "KZT_minor", Decimal(100)
    elif unit.startswith("тиын"):
        unit, scale = "KZT_minor", Decimal(1)
    elif unit.startswith("миллисекунд") or unit in {"мс", "ms"}:
        unit, scale = "second", Decimal("0.001")
    elif unit.startswith("секунд") or unit in {"сек", "sec"}:
        unit, scale = "second", Decimal(1)
    elif unit.startswith("минут") or unit in {"мин", "min"}:
        unit, scale = "second", Decimal(60)
    elif unit.startswith(("час", "hour")):
        unit, scale = "second", Decimal(3600)
    elif unit in {"день", "дня", "дней", "day", "days"}:
        unit, scale = "day", Decimal(1)
    elif unit.startswith(("месяц", "month")):
        unit, scale = "month", Decimal(1)
    elif unit.startswith("руб") or unit == "₽":
        unit, scale = "RUB", Decimal(1)
    elif unit.startswith("доллар") or unit == "usd":
        unit, scale = "USD", Decimal(1)
    elif unit.startswith("процент") or unit == "%":
        unit, scale = "percent", Decimal(1)
    elif unit.startswith("штук") or unit == "шт":
        unit, scale = "count", Decimal(1)
    else:
        scale = Decimal(1)
    # Decimal's default context has only 28 digits of precision. Do not
    # silently round a source literal during a unit conversion.
    with localcontext() as ctx:
        ctx.prec = max(28, len(number.as_tuple().digits) + 8)
        return format((number * scale).normalize(), "f"), unit


def _relation(text: str, start: int) -> str:
    prefix = text[max(0, start - 32):start].casefold().rstrip()
    if re.search(r"\b(?:не более|не выше|максимум|до)\s*$", prefix):
        return "upper_bound"
    if re.search(r"\b(?:не менее|не ниже|минимум|от)\s*$", prefix):
        return "lower_bound"
    if re.search(r"(?:\bравен|\bравна|\bравно|\bровно|=)\s*$", prefix):
        return "equals"
    return "mentioned"


def build_fact_index(specification: Specification, requirements: list[Requirement]) -> tuple[Fact, ...]:
    facts: list[Fact] = []
    for requirement in requirements:
        evidence = requirement.evidence
        # Fail closed for unreliable extracted evidence. Facts must not
        # introduce a second, less strict source-validation path.
        specification.verify_quote(evidence.start, evidence.end, evidence.quote)
        text = evidence.quote
        candidates: list[tuple[int, int, str, str, str, str]] = []
        ranges = list(_RANGE.finditer(text))
        for match in ranges:
            for group, relation in (("lower", "lower_bound"), ("upper", "upper_bound")):
                unit_text = (match["lower_unit"] or match["unit"]) if group == "lower" else match["unit"]
                value, unit = _normalized_quantity(match[group], unit_text)
                candidates.append((match.start(), match.end(), "quantity", value, unit, relation))
        for match in _QUANTITY.finditer(text):
            if any(match.start() < span.end() and match.end() > span.start() for span in ranges):
                continue
            value, unit = _normalized_quantity(match["number"], match["unit"])
            candidates.append((match.start(), match.end(), "quantity", value, unit, _relation(text, match.start())))
        for match in _DATE.finditer(text):
            day, month = int(match["day"]), _MONTHS[match["month"].casefold()]
            if 1 <= day <= (29 if month == 2 else 30 if month in {4, 6, 9, 11} else 31):
                candidates.append((match.start(), match.end(), "date", f"{month:02d}-{day:02d}", "calendar_day", "mentioned"))
        for match in _TIME.finditer(text):
            hour, minute = map(int, match.group().split(":"))
            candidates.append((match.start(), match.end(), "time", f"{hour:02d}:{minute:02d}", "day_time", "mentioned"))
        for index, (start, end, kind, value, unit, relation) in enumerate(sorted(candidates)[:_MAX_PER_REQUIREMENT], 1):
            facts.append(Fact(f"F-{requirement.id}-{index:02d}", requirement.id, kind, value, unit, relation,
                              evidence.start, evidence.end, text[start:end], text[max(0, start - 96):end + 96]))
    return tuple(facts)


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[^\W\d_]+", text.casefold()) if len(term) >= 4 and term not in _STOP}


def _comparisons(facts: list[Fact]) -> list[dict]:
    pairs = []
    vocabulary = {fact.id: _terms(fact.context) for fact in facts}
    for left, right in combinations(facts, 2):
        if left.requirement_id == right.requirement_id or left.kind != right.kind or left.unit != right.unit:
            continue
        relations = {left.relation, right.relation}
        if left.relation == right.relation and left.relation != "mentioned" and left.value != right.value:
            reason = "different_values"
        elif (left.kind == "quantity" and left.value == right.value and "equals" in relations
              and relations & {"lower_bound", "upper_bound"}):
            reason = "shared_boundary"
        else:
            continue
        shared = vocabulary[left.id] & vocabulary[right.id]
        if len(shared) < 2:
            continue
        pairs.append((len(shared), {"left": left.id, "right": right.id, "reason": reason,
                                   "probe_values": list(dict.fromkeys((left.value, right.value))), "unit": left.unit,
                                   "shared_terms": sorted(shared)[:6], "status": "needs_context"}))
    pairs.sort(key=lambda pair: (pair[1]["reason"] != "shared_boundary", -pair[0], pair[1]["left"], pair[1]["right"]))
    return [pair for _, pair in pairs[:_MAX_CONTEXT_CANDIDATES]]


def select_fact_context(index: tuple[Fact, ...], requirement_ids: set[str], *, max_chars: int = 6500) -> dict:
    """Select only allowed requirement IDs, with a strict serialized size bound."""
    scoped = [fact for fact in index if fact.requirement_id in requirement_ids]
    chosen: list[Fact] = []
    result = {"facts": [], "comparison_candidates": [], "truncated": False}

    def fits(payload: dict) -> bool:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= max_chars

    if not fits(result):
        raise ValueError("Fact context budget is smaller than its metadata")
    # Prefer a broad set of requirements over all numeric mentions of one
    # long requirement. Admit a bounded number of complete comparison hints
    # before general mentions consume the available context.
    per_requirement = {}
    for fact in scoped:
        per_requirement.setdefault(fact.requirement_id, []).append(fact)
    interleaved = [items[offset] for offset in range(_MAX_PER_REQUIREMENT)
                   for items in per_requirement.values() if offset < len(items)]
    comparable = [fact for fact in interleaved if fact.relation != "mentioned"][:_MAX_COMPARISON_FACTS]
    comparable_by_id = {fact.id: fact for fact in comparable}
    for pair in _comparisons(comparable):
        added = [comparable_by_id[id_] for id_ in (pair["left"], pair["right"])
                 if comparable_by_id[id_] not in chosen]
        if len(chosen) + len(added) > _MAX_CONTEXT_FACTS:
            continue
        proposal = {**result, "facts": [*result["facts"], *(asdict(fact) for fact in added)],
                    "comparison_candidates": [*result["comparison_candidates"], pair]}
        if not fits(proposal):
            continue
        result = proposal
        chosen.extend(added)
    for fact in interleaved[:_MAX_CONTEXT_FACTS]:
        if fact in chosen or len(chosen) >= _MAX_CONTEXT_FACTS:
            continue
        proposal = {**result, "facts": [*result["facts"], asdict(fact)]}
        if not fits(proposal):
            continue
        result = proposal
        chosen.append(fact)
    result["truncated"] = len(chosen) < len(scoped)
    return result
