"""Bound long review prompts while keeping exact original evidence coordinates."""

import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent.services.spec_document import Specification

if TYPE_CHECKING:
    from agent.services.spec_review import Requirement

LARGE_DOCUMENT_CHARACTERS = 24_000
LARGE_REQUIREMENT_COUNT = 160
CONTEXT_CHARACTERS = 20_000
SECTION_CHARACTERS = 12_000
SET_GROUP_SIZE = 24
MAX_CONTEXT_UNITS = 500
REQUIREMENT_GROUP_CHARACTERS = 12_000


def is_large_document(specification: Specification, requirement_count: int = 0) -> bool:
    return (
        sum(len(unit) + 1 for unit in specification.units) > LARGE_DOCUMENT_CHARACTERS
        or requirement_count > LARGE_REQUIREMENT_COUNT
        or sum(bool(unit.strip()) for unit in specification.units)
        > LARGE_REQUIREMENT_COUNT
    )


def terms(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"\w+", text.casefold())
        if len(word) >= 3 or word.isdigit()
    }


def requirement_terms(requirement: "Requirement") -> set[str]:
    return terms(requirement.text + " " + requirement.evidence.quote)


def requirement_batches(
    requirements: list["Requirement"], max_count: int = SET_GROUP_SIZE
) -> list[list["Requirement"]]:
    batches = []
    current, size = [], 0
    for item in requirements:
        length = len(item.text) + len(item.evidence.quote)
        if current and (
            len(current) >= max_count or size + length > REQUIREMENT_GROUP_CHARACTERS
        ):
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += length
    if current:
        batches.append(current)
    return batches


def selected_units(
    specification: Specification,
    requirements: list["Requirement"],
    budget: int = CONTEXT_CHARACTERS,
) -> list[dict]:
    """Keep cited units first, then nearby and lexically related original units."""
    units = specification.numbered_units()
    mandatory = list(
        dict.fromkeys(
            index
            for item in requirements
            for index in range(item.evidence.start, item.evidence.end + 1)
        )
    )
    nearby = list(
        dict.fromkeys(
            index
            for position in mandatory
            for index in (position - 1, position + 1)
            if 1 <= index <= len(units)
        )
    )
    vocabulary = set().union(*(requirement_terms(item) for item in requirements))
    ranked = sorted(
        (index for index, unit in enumerate(specification.units, 1) if unit.strip()),
        key=lambda index: (
            -len(terms(specification.units[index - 1]) & vocabulary),
            index,
        ),
    )
    chosen, size = set(), 0
    for index in [*mandatory, *nearby, *ranked]:
        if (
            index in chosen
            or not 1 <= index <= len(units)
            or not specification.units[index - 1].strip()
        ):
            continue
        if len(chosen) >= MAX_CONTEXT_UNITS:
            break
        length = len(specification.units[index - 1]) + 1
        if size + length <= budget or not chosen:
            chosen.add(index)
            size += length
    # Each requirement also carries its exact short evidence; no fabricated or
    # truncated source unit is introduced when other units do not fit.
    return [units[index - 1] for index in sorted(chosen)]


@dataclass(frozen=True)
class SetScope:
    kind: str
    units: list[dict]
    requirements: list["Requirement"]
    primary_ids: list[str]


def section_ranges(specification: Specification):
    start, size, count = 1, 0, 0
    for index, unit in enumerate(specification.units, 1):
        if index > start and (
            size + len(unit) + 1 > SECTION_CHARACTERS or count >= MAX_CONTEXT_UNITS
        ):
            yield start, index - 1
            start, size, count = index, 0, 0
        size += len(unit) + 1
        count += bool(unit.strip())
    yield start, len(specification.units)


def set_scopes(
    specification: Specification, requirements: list["Requirement"]
) -> list[SetScope]:
    """Cover each source section, plus bounded comparisons against other sections."""
    scopes = []
    units = specification.numbered_units()
    for start, end in section_ranges(specification):
        section_units = [
            unit for unit in units[start - 1 : end] if str(unit["text"]).strip()
        ]
        if not section_units:
            continue
        local = [
            item
            for item in requirements
            if item.evidence.start <= end and item.evidence.end >= start
        ]
        batches = requirement_batches(local) or [[]]
        for batch in batches:
            scopes.append(
                SetScope("section", section_units, batch, [item.id for item in batch])
            )

    groups = requirement_batches(requirements)
    if len(groups) == 1 and len(scopes) > 1:
        scopes.append(
            SetScope(
                "cross_section",
                selected_units(specification, requirements),
                requirements,
                [item.id for item in requirements],
            )
        )
        return scopes

    token_sets = {item.id: requirement_terms(item) for item in requirements}
    frequency = Counter(
        term for vocabulary in token_sets.values() for term in vocabulary
    )
    for primary in groups:
        primary_ids = {item.id for item in primary}
        vocabulary = set().union(*(token_sets[item.id] for item in primary))

        def score(item: "Requirement"):
            return sum(1 / frequency[term] for term in token_sets[item.id] & vocabulary)

        ranked = sorted(
            (item for item in requirements if item.id not in primary_ids),
            key=lambda item: (-score(item), item.evidence.start, item.id),
        )
        candidates = next(iter(requirement_batches(ranked)), [])
        if candidates:
            group = [*primary, *candidates]
            scopes.append(
                SetScope(
                    "cross_section",
                    selected_units(specification, group),
                    group,
                    [item.id for item in primary],
                )
            )
    return scopes
