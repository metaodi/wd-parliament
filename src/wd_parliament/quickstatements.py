"""Render suggestions as QuickStatements V1 commands. Pure.

Safety rule
-----------
**Only a suggestion whose member was matched by P1307 is ever emitted**, and
only when every value the command needs is known and unambiguous.
:func:`is_mechanical` is the single predicate encoding that rule, so it can be
tested in one place and cannot be worked around elsewhere. Name-matched members
are report-only, always — an identity established from a label is a good enough
reason to ask a human to look, never a good enough reason to write.

Beyond provenance, a kind is only mechanical if applying it *adds* information.
``FIX_START_DATE``, ``REVIEW_ENDED`` and ``REVIEW_PARTY`` all mean "an existing
value looks wrong", and QuickStatements can only add statements, not correct
them: emitting those would create a second, contradictory value rather than fix
the first. They stay in the report for a human.

Format
------
V1 syntax, pipe-separated, as consumed by QuickStatements' ``#v1=`` URL form::

    Q121160|P39|Q18510612|P580|+2015-12-07T00:00:00Z/11|P768|Q11943|
      S854|"https://www.parlament.ch/de/biografie/wd/1108"|S813|+2026-07-29T00:00:00Z/11

Every line carries a reference: ``S854`` (reference URL) pointing at the
member's parlament.ch biography, and ``S813`` (retrieved) with the run date.

Note on qualifier additions
---------------------------
QuickStatements matches an existing statement by its property and main value.
Restating ``QID|P39|Qposition|…`` therefore *merges* new qualifiers into the
existing statement — but only while that pairing is unique on the item. Under
the ``period`` statement model a member has several P39 statements for the same
seat, so a qualifier-only line would be ambiguous; :func:`is_mechanical`
requires such lines to carry the P2937 term that identifies which statement is
meant, and declines them otherwise.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional, Sequence

from .models import (
    KIND_ADD_END_DATE,
    KIND_ADD_MEMBERSHIP,
    KIND_ADD_QUALIFIER,
    KIND_ADD_START_DATE,
    KIND_ADD_TERM,
    MODEL_PERIOD,
    QID_FROM_IDENTIFIER,
    QuickStatement,
    Suggestion,
)

SEPARATOR = "|"

# Day precision. Wikidata time values are "+YYYY-MM-DDT00:00:00Z/<precision>",
# and 11 is "day" — the precision parlament.ch's dates actually carry.
DAY_PRECISION = 11

# The kinds that add information and can therefore be applied mechanically.
# Everything else is a "review this" finding; see the module docstring.
MECHANICAL_KINDS = frozenset(
    {
        KIND_ADD_MEMBERSHIP,
        KIND_ADD_START_DATE,
        KIND_ADD_END_DATE,
        KIND_ADD_TERM,
        KIND_ADD_QUALIFIER,
    }
)

# Kinds whose command is a qualifier-only addition to an existing statement,
# i.e. the ambiguity discussed in the module docstring.
_QUALIFIER_ONLY_KINDS = frozenset(
    {KIND_ADD_START_DATE, KIND_ADD_END_DATE, KIND_ADD_TERM, KIND_ADD_QUALIFIER}
)


def format_date(value: date) -> str:
    """A Wikidata time literal at day precision."""
    return f"+{value.isoformat()}T00:00:00Z/{DAY_PRECISION}"


def is_mechanical(suggestion: Suggestion, statement_model: str = MODEL_PERIOD) -> bool:
    """Whether ``suggestion`` may be turned into a QuickStatements command.

    The one place the safety rule lives. All of the following must hold:

    - the member was matched by the **identifier**, not by name;
    - the identifier property's value is one a probe has confirmed to be the
      source's person id (``identifier_verified``);
    - a target Q-ID is known;
    - the kind adds information rather than contradicting an existing value;
    - the payload carries everything the command needs;
    - a qualifier-only addition targets a member with exactly one P39
      statement for the seat, or (under the ``period`` model) carries the P2937
      term identifying which of several same-seat statements is meant.
    """
    if suggestion.qid_source != QID_FROM_IDENTIFIER:
        return False
    if not suggestion.person_qid:
        return False
    if suggestion.kind not in MECHANICAL_KINDS:
        return False
    if not suggestion.payload.get("biography"):
        return False
    # A second, independent source contradicts the first about this member.
    # Which side is right is not decided here and does not need to be: a value
    # two sources dispute is exactly the value that must not be written without
    # a human looking. Set by ``diff`` on every suggestion for that member.
    if suggestion.payload.get("sources_disagree"):
        return False
    # The identifier the match was made through is Wikidata-asserted, but that
    # its *value* is the source's person id has not been measured — the config
    # says so with ``identifier_verified: false``. Provenance is what the gate
    # above tests and it is only half the claim: an identifier from a different
    # id space matches exactly and matches the wrong person, so the edit would
    # be correct data on the wrong item. Set by ``diff`` on every suggestion for
    # such a member.
    if suggestion.payload.get("identifier_unverified"):
        return False

    payload = suggestion.payload
    if not payload.get("position"):
        return False

    if suggestion.kind == KIND_ADD_MEMBERSHIP and not payload.get("start"):
        # A membership with no start date is not worth writing mechanically.
        return False
    if suggestion.kind == KIND_ADD_START_DATE and not payload.get("start"):
        return False
    if suggestion.kind == KIND_ADD_END_DATE and not payload.get("end"):
        return False
    if suggestion.kind == KIND_ADD_TERM and not payload.get("terms"):
        return False
    if suggestion.kind == KIND_ADD_QUALIFIER and not (
        payload.get("district") or payload.get("group")
    ):
        return False

    if suggestion.kind in _QUALIFIER_ONLY_KINDS:
        # The member already holds several P39 statements for this seat (they
        # left and returned), so property + main value does not identify one.
        if payload.get("ambiguous_statement"):
            return False
        # Under the period model every multi-term member has several
        # same-seat statements by construction, so the term qualifier is the
        # only thing that can pin the command down.
        if statement_model == MODEL_PERIOD and not payload.get("terms"):
            return False
    return True


def _reference(biography: str, retrieved: date) -> List[str]:
    return ["S854", f'"{biography}"', "S813", format_date(retrieved)]


def render_suggestion(
    suggestion: Suggestion,
    retrieved: date,
    statement_model: str = MODEL_PERIOD,
) -> Optional[QuickStatement]:
    """Render one suggestion, or ``None`` when it is not mechanical."""
    if not is_mechanical(suggestion, statement_model):
        return None

    payload = suggestion.payload
    parts: List[str] = [str(suggestion.person_qid), "P39", str(payload["position"])]

    if suggestion.kind == KIND_ADD_MEMBERSHIP:
        parts += ["P580", format_date(payload["start"])]  # type: ignore[arg-type]
        if payload.get("end"):
            parts += ["P582", format_date(payload["end"])]  # type: ignore[arg-type]
        if payload.get("district"):
            parts += ["P768", str(payload["district"])]
        if payload.get("group"):
            parts += ["P4100", str(payload["group"])]
        for term in payload.get("terms") or []:
            parts += ["P2937", str(term)]
    elif suggestion.kind == KIND_ADD_START_DATE:
        parts += _term_parts(payload)
        parts += ["P580", format_date(payload["start"])]  # type: ignore[arg-type]
    elif suggestion.kind == KIND_ADD_END_DATE:
        parts += _term_parts(payload)
        parts += ["P582", format_date(payload["end"])]  # type: ignore[arg-type]
    elif suggestion.kind == KIND_ADD_TERM:
        for term in payload.get("terms") or []:
            parts += ["P2937", str(term)]
    elif suggestion.kind == KIND_ADD_QUALIFIER:
        parts += _term_parts(payload)
        if payload.get("district"):
            parts += ["P768", str(payload["district"])]
        if payload.get("group"):
            parts += ["P4100", str(payload["group"])]
    else:  # pragma: no cover - guarded by is_mechanical
        return None

    parts += _reference(str(payload["biography"]), retrieved)
    return QuickStatement(
        line=SEPARATOR.join(parts),
        suggestion_kind=suggestion.kind,
        person_qid=str(suggestion.person_qid),
        member_label=suggestion.member_label,
    )


def _term_parts(payload: dict) -> List[str]:
    """P2937 qualifiers that pin a command to one statement, if configured."""
    parts: List[str] = []
    for term in payload.get("terms") or []:
        parts += ["P2937", str(term)]
    return parts


def render(
    suggestions: Sequence[Suggestion],
    retrieved: Optional[date] = None,
    statement_model: str = MODEL_PERIOD,
) -> List[QuickStatement]:
    """Render every mechanical suggestion, in the order given. Pure."""
    retrieved = retrieved or date.today()
    out: List[QuickStatement] = []
    for suggestion in suggestions:
        rendered = render_suggestion(suggestion, retrieved, statement_model)
        if rendered is not None:
            out.append(rendered)
    return out


def render_file(
    suggestions: Sequence[Suggestion],
    retrieved: Optional[date] = None,
    statement_model: str = MODEL_PERIOD,
    generated_at: str = "",
) -> str:
    """The full ``docs/suggestions.qs`` text, header comment included."""
    retrieved = retrieved or date.today()
    statements = render(suggestions, retrieved, statement_model)
    total = len(suggestions)
    header = [
        "# wd-parliament — QuickStatements V1",
        "#",
        f"# Generated: {generated_at or retrieved.isoformat()}",
        f"# Statement model: {statement_model}",
        f"# {len(statements)} of {total} suggestions are mechanical.",
        "#",
        "# Only members matched by their Swiss parliament ID (P1307) are",
        "# emitted here; anything matched by name is report-only. Review the",
        "# reports before applying, and try one or two lines by hand first.",
        "#",
        "# This file is pipe-separated V1 syntax. QuickStatements' paste box",
        "# expects tabs, so convert with:  tr '|' '\\t' < suggestions.qs",
        "#",
    ]
    body = [s.line for s in statements]
    return "\n".join(header + body) + "\n"
