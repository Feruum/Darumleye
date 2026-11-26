"""Literal clause index and bounded formal overlap checks for explicit rules.

This is deliberately not a natural-language theorem prover. A Z3 witness
establishes overlap and incompatible outcomes in a narrowly parsed model;
the expert must still check the original text and any business exceptions.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from itertools import combinations
from typing import TYPE_CHECKING

from agent.services.spec_review_facts import Fact

if TYPE_CHECKING:
    from agent.services.spec_document import Specification
    from agent.services.spec_review import Requirement


SEMANTIC_CONTEXT_INSTRUCTIONS = """
The semantic_context contains literal entity, condition, field and outcome
mentions derived from verified requirement quotes. They are navigation hints,
not an exhaustive extraction or a business ontology. Similar entity names
do not prove identical scope; different names do not prove distinct scope.
Formal witnesses report satisfiable overlap and incompatible outcomes ONLY
under the explicitly parsed rule model. Independently check the quoted
requirements, qualifier meaning, units, inclusivity, conditions, exceptions
and precedence. A formal witness is supplementary, not an automatic finding
or severity decision. Do not infer a defect from an absent witness. Cite the
original source and explain the real expected outcomes in Russian.
"""

_STATUS = re.compile(r"\b(?P<field>со статусом|в статусе|с признаком)\s*[«\"](?P<value>[^»\"\n]{1,60})[»\"]", re.I)
_IF = re.compile(r"\b(?:если|при условии|когда)\s+[^,;.!?\n]{2,120}", re.I)
_FIELD = re.compile(r"`([A-Za-z_][A-Za-z0-9_.-]{0,63})`")
_OUTCOMES = {
    "утверждается": ("approval", "approved"), "одобряется": ("approval", "approved"),
    "отклоняется": ("approval", "rejected"), "активна": ("activity", "active"),
    "активен": ("activity", "active"), "действует": ("activity", "active"),
    "отменяется": ("activity", "cancelled"), "разрешается": ("permission", "allowed"),
    "запрещается": ("permission", "forbidden"), "отправляется": ("delivery", "sent"),
    "не отправляется": ("delivery", "not_sent"),
}
_OUTCOME = re.compile(r"\b(?:" + "|".join(re.escape(word) for word in sorted(_OUTCOMES, key=len, reverse=True)) + r")\b", re.I)
_ENTITY = re.compile(r"[^\W\d_]+(?:[ -][^\W\d_]+){0,3}$")
_ENTITY_START_STOP = {"если", "когда", "при", "для", "после", "в", "на", "до", "не"}
_MAX_CLAUSE_FACTS = 12
_MAX_RULES = 96
_MAX_WITNESSES = 12
_MAX_SOLVER_PAIRS = 48


def _normalize(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def _body(requirement: Requirement) -> str:
    text = requirement.evidence.quote.lstrip().lstrip("*-").lstrip()
    if requirement.source_id and text.startswith(requirement.source_id):
        text = text[len(requirement.source_id):].lstrip(". :*—-")
    return text.strip()


def _entity(text: str) -> str | None:
    subject = text.strip()
    if not subject or not _ENTITY.fullmatch(subject) or _normalize(subject).split()[0] in _ENTITY_START_STOP:
        return None
    return subject


@dataclass(frozen=True)
class ClauseFact:
    requirement_id: str
    kind: str
    field: str
    value: str
    quote: str
    start: int
    end: int


@dataclass(frozen=True)
class FormalRule:
    requirement_id: str
    entity: str
    condition_field: str
    condition_value: str
    family: str
    outcome: str
    unit: str | None
    bounds: tuple[tuple[str, str], ...]
    evidence: dict


@dataclass(frozen=True)
class FormalWitness:
    requirement_ids: tuple[str, str]
    entity: str
    inputs: dict[str, str]
    left_outcome: str
    right_outcome: str
    status: str
    evidence: tuple[dict, dict]


def build_clause_index(specification: Specification, requirements: list[Requirement]) -> tuple[ClauseFact, ...]:
    facts = []
    for requirement in requirements:
        evidence = requirement.evidence
        specification.verify_quote(evidence.start, evidence.end, evidence.quote)
        body = _body(requirement)
        rows = []
        status = _STATUS.search(body)
        outcome = _OUTCOME.search(body)
        # Only take a source-local subject before a recognized predicate.
        if status:
            subject = _entity(body[:status.start()])
        elif outcome:
            subject = _entity(body[:outcome.start()].split(",")[-1])
        else:
            subject = None
        if subject:
            rows.append(("entity", "subject", _normalize(subject), subject))
        for match in _STATUS.finditer(body):
            field = "flag" if _normalize(match["field"]) == "с признаком" else "status"
            rows.append(("condition", field, _normalize(match["value"]), match.group()))
        for match in _IF.finditer(body):
            rows.append(("condition", "literal", _normalize(match.group()), match.group()))
        for match in _FIELD.finditer(body):
            rows.append(("interface_field", "identifier", match[1], match.group()))
        for match in _OUTCOME.finditer(body):
            family, value = _OUTCOMES[_normalize(match.group())]
            rows.append(("outcome", family, value, match.group()))
        for kind, field, value, quote in dict.fromkeys(rows[:_MAX_CLAUSE_FACTS]):
            specification.verify_quote(evidence.start, evidence.end, quote)
            facts.append(ClauseFact(requirement.id, kind, field, value, quote, evidence.start, evidence.end))
    return tuple(facts)


def _parse_rule(specification: Specification, requirement: Requirement, quantities: list[Fact]) -> FormalRule | None:
    """Parse only complete, explicit status [+ scalar] -> outcome rules."""
    text = _body(requirement)
    statuses = list(_STATUS.finditer(text))
    outcomes = list(_OUTCOME.finditer(text))
    if len(statuses) != 1 or len(outcomes) != 1:
        return None
    status, outcome = statuses[0], outcomes[0]
    subject = _entity(text[:status.start()])
    if not subject or outcome.start() <= status.end():
        return None
    # An unparsed suffix or extra condition cannot count as unconditional.
    suffix = _normalize(text[outcome.end():]).rstrip(". ")
    if suffix not in {"", "автоматически", "; исключений нет", "автоматически; исключений нет"}:
        return None
    middle = text[status.end():outcome.start()].strip()
    bounds: list[tuple[str, str]] = []
    unit = None
    if middle:
        if not quantities or len(quantities) > 2 or any(f.relation == "mentioned" for f in quantities):
            return None
        if len({fact.unit for fact in quantities}) != 1:
            return None
        if any(fact.quote not in middle for fact in quantities):
            return None
        unit = quantities[0].unit
        remainder = middle
        for quote in dict.fromkeys(fact.quote for fact in quantities):
            remainder = remainder.replace(quote, "", 1)
        remainder = _normalize(remainder)
        if not re.fullmatch(r"и (?:суммой|суммой|значением|количеством)(?: (?:ровно|не менее|не более|не выше|не ниже|от|до))?(?: (?:включительно|исключительно))?", remainder):
            return None
        inclusive = "включительно" in remainder or bool(re.search(r"\bне (?:менее|более|выше|ниже)\b", remainder))
        for fact in quantities:
            operator = {"equals": "=", "lower_bound": ">=" if inclusive else ">",
                        "upper_bound": "<=" if inclusive else "<"}[fact.relation]
            bounds.append((operator, fact.value))
    elif quantities:
        # Numeric information present outside the parsed condition is not used.
        return None
    evidence = requirement.evidence
    specification.verify_quote(evidence.start, evidence.end, text)
    family, result = _OUTCOMES[_normalize(outcome.group())]
    field = "flag" if _normalize(status["field"]) == "с признаком" else "status"
    return FormalRule(requirement.id, _normalize(subject), field, _normalize(status["value"]),
                      family, result, unit, tuple(bounds),
                      {"start": evidence.start, "end": evidence.end, "quote": text})


def verify_rule_conflicts(specification: Specification, requirements: list[Requirement],
                          numeric_index: tuple[Fact, ...]) -> tuple[FormalWitness, ...]:
    import z3

    quantities = {}
    for fact in numeric_index:
        if fact.kind == "quantity":
            quantities.setdefault(fact.requirement_id, []).append(fact)
    rules = [rule for req in requirements
             if (rule := _parse_rule(specification, req, quantities.get(req.id, []))) is not None][:_MAX_RULES]
    witnesses = []
    attempted = 0
    for left, right in combinations(rules, 2):
        if (left.entity != right.entity or left.condition_field != right.condition_field
                or left.family != right.family or left.outcome == right.outcome
                or (left.unit and right.unit and left.unit != right.unit)):
            continue
        if attempted >= _MAX_SOLVER_PAIRS or len(witnesses) >= _MAX_WITNESSES:
            break
        attempted += 1
        state = z3.String("condition_value")
        value = z3.Real("quantity")
        result = z3.String("outcome")
        solver = z3.Solver()
        solver.set(timeout=100)
        for rule in (left, right):
            solver.add(state == z3.StringVal(rule.condition_value))
            for operator, raw in rule.bounds:
                target = z3.RealVal(raw)
                solver.add({"=": value == target, "<": value < target, "<=": value <= target,
                            ">": value > target, ">=": value >= target}[operator])
        if solver.check() != z3.sat:
            continue
        overlap = solver.model()
        # Both rules constrain the same literal; Z3's string printer escapes
        # non-ASCII characters, so retain the normalized source value.
        inputs = {left.condition_field: left.condition_value}
        unit = left.unit or right.unit
        if unit:
            inputs.update(value=str(overlap.eval(value, model_completion=True)), unit=unit)
        # Witness of satisfiable common input plus impossibility of a single
        # shared outcome satisfying both modeled obligations.
        solver.add(result == z3.StringVal(left.outcome), result == z3.StringVal(right.outcome))
        if solver.check() == z3.unsat:
            witnesses.append(FormalWitness((left.requirement_id, right.requirement_id), left.entity,
                                           inputs, left.outcome, right.outcome,
                                           "overlap_and_incompatible_outcomes", (left.evidence, right.evidence)))
    return tuple(witnesses)


def select_semantic_context(index: tuple[ClauseFact, ...], witnesses: tuple[FormalWitness, ...],
                            allowed_ids: set[str], *, max_chars: int = 3800) -> dict:
    result = {"clauses": [], "formal_witnesses": [], "truncated": False}

    def fits(proposed):
        return len(json.dumps(proposed, ensure_ascii=False, separators=(",", ":"))) <= max_chars

    for witness in witnesses:
        if not set(witness.requirement_ids) <= allowed_ids:
            continue
        proposed = {**result, "formal_witnesses": [*result["formal_witnesses"], asdict(witness)]}
        if fits(proposed):
            result = proposed
        else:
            result["truncated"] = True
    for fact in index:
        if fact.requirement_id not in allowed_ids:
            continue
        proposed = {**result, "clauses": [*result["clauses"], asdict(fact)]}
        if fits(proposed):
            result = proposed
        else:
            result["truncated"] = True
    return result
