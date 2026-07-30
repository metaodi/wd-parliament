"""Compare parlament.ch's sitting members with Wikidata's P39 statements.

Pure: everything here takes plain dataclasses and returns
:class:`~.models.Suggestion` objects.

Unlike wd-squads, whose Wikipedia source is a best-effort human-maintained
list, parlament.ch is authoritative: it gives exact joining and leaving dates
and an ``Active`` flag. That is what lets this module make a claim wd-squads
cannot — not merely "a statement is missing" but "a statement *disagrees* with
the source" (``FIX_START_DATE``, ``REVIEW_ENDED``, ``REVIEW_PARTY``).

The statement model
-------------------
Wikidata can model a seat two ways, and getting it backwards would emit
hundreds of duplicate statements — the worst failure available to this tool. So
the choice is config, not code (``statement_model`` in
``config/parliament.yaml``):

``tenure``
    One P39 statement per continuous tenure. P580/P582 span the whole tenure
    and P2937 is repeated once per legislative period covered.

``period``
    One P39 statement per legislative period, each carrying its own P2937 and
    its own dates clipped to that period.

:func:`expected_statements` is the single place that difference lives; the rest
of the diff works off whatever it returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

from .config import Config
from .models import (
    KIND_ADD_END_DATE,
    KIND_ADD_IDENTIFIER,
    KIND_ADD_MEMBERSHIP,
    KIND_ADD_QUALIFIER,
    KIND_ADD_START_DATE,
    KIND_ADD_TERM,
    KIND_FIX_START_DATE,
    KIND_NO_WIKIDATA_ITEM,
    KIND_REVIEW_ENDED,
    KIND_REVIEW_PARTY,
    MODEL_PERIOD,
    QID_FROM_NAME,
    Body,
    Member,
    Period,
    PositionStatement,
    Suggestion,
    WikidataPerson,
)
from .period_overlap import assign_periods, clip_to_period


def _item_url(qid: str) -> str:
    return f"https://www.wikidata.org/wiki/{qid}"


@dataclass
class ExpectedStatement:
    """The P39 statement that *should* exist, per the configured model."""

    start: Optional[date] = None
    end: Optional[date] = None
    period: Optional[Period] = None  # None in the ``tenure`` model
    periods: List[Period] = field(default_factory=list)  # the P2937 values

    @property
    def label(self) -> str:
        return self.period.label if self.period else "the whole tenure"


def expected_statements(
    member: Member, periods: Sequence[Period], statement_model: str
) -> List[ExpectedStatement]:
    """What Wikidata should hold for ``member``, under ``statement_model``."""
    covered = assign_periods(member, periods)
    if statement_model == MODEL_PERIOD:
        statements = []
        for period in covered:
            start, end = clip_to_period(member, period)
            statements.append(
                ExpectedStatement(
                    start=start, end=end, period=period, periods=[period]
                )
            )
        return statements
    return [
        ExpectedStatement(
            start=member.start_date,
            end=member.date_leaving,
            period=None,
            periods=covered,
        )
    ]


def _term_qids(periods: Sequence[Period], config: Config) -> List[str]:
    """Configured P2937 Q-IDs for ``periods``; unmapped periods are skipped."""
    out: List[str] = []
    for period in periods:
        qid = config.terms.get(period.number)
        if qid and qid not in out:
            out.append(qid)
    return out


def match_statement(
    expected: ExpectedStatement,
    statements: Sequence[PositionStatement],
    config: Config,
    used: Optional[set] = None,
) -> Optional[PositionStatement]:
    """Find the existing statement that fills ``expected``, if any.

    In the ``period`` model a statement is identified by its P2937 term first
    (the unambiguous signal) and by date overlap only as a fallback, since many
    statements predate the term qualifier. In the ``tenure`` model there is a
    single expected statement, so any statement for the seat matches.
    """
    used = used if used is not None else set()
    available = [s for s in statements if s.statement_id not in used]
    if not available:
        return None

    if expected.period is None:
        # tenure model: prefer an open statement, else the latest-starting one.
        open_statements = [s for s in available if s.is_open]
        pool = open_statements or available
        return max(pool, key=lambda s: (s.start or date.min))

    wanted_terms = set(_term_qids(expected.periods, config))
    if wanted_terms:
        for statement in available:
            if wanted_terms & set(statement.terms):
                return statement

    # Fall back to date overlap against the period's own span.
    period = expected.period
    for statement in available:
        if statement.start is None and statement.end is None:
            continue
        start = statement.start or date.min
        end = statement.end or date.max
        p_start = period.start or date.min
        p_end = period.end or date.max
        if start <= p_end and p_start <= end:
            return statement

    # An undated statement can only be the one we mean if there is exactly one.
    undated = [s for s in available if s.start is None and s.end is None]
    if len(undated) == 1 and len(available) == 1:
        return undated[0]
    return None


def _base_suggestion(
    kind: str, body: Body, member: Member, detail: str, **kwargs
) -> Suggestion:
    links = {"position": _item_url(body.position_qid)}
    if member.qid:
        links["item"] = _item_url(member.qid)
    return Suggestion(
        kind=kind,
        body=body,
        member_label=member.full_name,
        detail=detail,
        person_qid=member.qid,
        person_number=member.person_number,
        qid_source=member.qid_source,
        canton=member.canton_abbreviation,
        parl_group=member.parl_group_abbreviation,
        links=links,
        **kwargs,
    )


def _verify_note(member: Member) -> str:
    """Ask the reader to check an identity that was established by name."""
    if member.qid_source == QID_FROM_NAME:
        return (
            " The item was matched by name and birth date, not by the Swiss "
            "parliament ID, so please confirm it is the right person."
        )
    return ""


def _date_str(value: Optional[date]) -> str:
    return value.isoformat() if value else "unknown"


def compute_suggestions(
    body: Body,
    members: Sequence[Member],
    people: Dict[str, WikidataPerson],
    periods: Sequence[Period],
    config: Config,
) -> List[Suggestion]:
    """Produce the suggested edits for one chamber. Pure.

    ``people`` maps Q-ID → :class:`WikidataPerson` and must already contain
    everyone holding ``body.position_qid``, whether or not they matched a
    sitting member — the second pass below relies on it.
    """
    suggestions: List[Suggestion] = []
    seen_qids: set = set()

    # 1) parlament.ch -> Wikidata.
    for member in members:
        if not member.qid:
            suggestions.append(
                _base_suggestion(
                    KIND_NO_WIKIDATA_ITEM,
                    body,
                    member,
                    f"'{member.full_name}' sits in the {body.label} "
                    f"(parlament.ch #{member.person_number}) but no Wikidata item "
                    "was found, by Swiss parliament ID (P1307) or by name. They "
                    "may need a new item.",
                    payload={"biography": config.biography_url_for(member.person_number)},
                )
            )
            continue

        seen_qids.add(member.qid)
        person = people.get(member.qid) or WikidataPerson(qid=member.qid)
        suggestions.extend(_member_suggestions(body, member, person, periods, config))

    # 2) Wikidata -> parlament.ch: people Wikidata still lists as sitting.
    #
    # This pass asserts "parlament.ch does not list this person", which is only
    # a claim we are entitled to make if parlament.ch told us who it *does*
    # list. Given an empty member list the pass would flag every seat holder on
    # Wikidata — 2,234 of them in the run of 2026-07-29 — so it is skipped
    # rather than run against nothing.
    if not members:
        suggestions.sort(key=lambda s: (s.priority, s.member_label.casefold()))
        return suggestions

    active_qids = {m.qid for m in members if m.qid and m.active}
    for qid, person in sorted(people.items()):
        if qid in active_qids:
            continue
        open_statements = [
            s for s in person.statements_for(body.position_qid) if s.is_open
        ]
        if not open_statements:
            continue
        suggestions.append(
            Suggestion(
                kind=KIND_ADD_END_DATE,
                body=body,
                member_label=person.label or qid,
                person_qid=qid,
                detail=(
                    f"Wikidata records an open '{body.label}' membership (no end "
                    "date), but parlament.ch does not list this person as a "
                    "sitting member. They have most likely left; add an end date "
                    "(P582). parlament.ch gives no leaving date here because the "
                    "person is outside the current-members set, so the date has "
                    "to be looked up by hand."
                ),
                links={"item": _item_url(qid), "position": _item_url(body.position_qid)},
                payload={"statement_id": open_statements[0].statement_id},
            )
        )

    suggestions.sort(key=lambda s: (s.priority, s.member_label.casefold()))
    return suggestions


def _member_suggestions(
    body: Body,
    member: Member,
    person: WikidataPerson,
    periods: Sequence[Period],
    config: Config,
) -> List[Suggestion]:
    """Every suggestion arising from one matched member."""
    out: List[Suggestion] = []
    verify = _verify_note(member)
    biography = config.biography_url_for(member.person_number)

    # The highest-leverage edit: give the item its identifier so that every
    # future run joins exactly instead of guessing from a name.
    if member.qid_source == QID_FROM_NAME and not person.parliament_id:
        out.append(
            _base_suggestion(
                KIND_ADD_IDENTIFIER,
                body,
                member,
                f"Add the Swiss parliament ID (P1307) '{member.person_number}'. "
                "The item was found by name and birth date; recording the "
                "identifier makes every future comparison exact." + verify,
                payload={
                    "parliament_id": str(member.person_number),
                    "biography": biography,
                },
            )
        )

    statements = person.statements_for(body.position_qid)
    expected = expected_statements(member, periods, config.statement_model)
    used: set = set()
    # A member who left and returned has several P39 statements for the same
    # seat. QuickStatements matches an existing statement by property + main
    # value, which then no longer identifies one of them, so a qualifier-only
    # command could land on the wrong statement. ~2.8% of National Council
    # items are in this position, so it is worth flagging rather than assuming
    # away; ``quickstatements.is_mechanical`` refuses those commands.
    ambiguous = len(statements) > 1

    district_qid = config.canton_qid(member.canton_abbreviation)
    group_qid = config.parl_group_qid(member.parl_group_abbreviation)

    for exp in expected:
        statement = match_statement(exp, statements, config, used)
        term_qids = _term_qids(exp.periods, config)

        if statement is None:
            out.append(
                _base_suggestion(
                    KIND_ADD_MEMBERSHIP,
                    body,
                    member,
                    f"Add a 'position held' (P39) statement → {body.label} "
                    f"({body.position_qid}) for {exp.label}. parlament.ch lists "
                    f"them as sitting since {_date_str(exp.start)}." + verify,
                    payload={
                        "position": body.position_qid,
                        "start": exp.start,
                        "end": exp.end,
                        "district": district_qid,
                        "group": group_qid,
                        "terms": term_qids,
                        "biography": biography,
                    },
                )
            )
            continue

        used.add(statement.statement_id)
        out.extend(
            _statement_suggestions(
                body,
                member,
                statement,
                exp,
                term_qids,
                district_qid,
                group_qid,
                biography,
                verify,
                ambiguous,
            )
        )

    out.extend(_party_suggestions(body, member, person, config, verify))
    return out


def _statement_suggestions(
    body: Body,
    member: Member,
    statement: PositionStatement,
    exp: ExpectedStatement,
    term_qids: Sequence[str],
    district_qid: Optional[str],
    group_qid: Optional[str],
    biography: str,
    verify: str,
    ambiguous: bool = False,
) -> List[Suggestion]:
    """Checks against one existing P39 statement.

    ``ambiguous`` marks a member holding several P39 statements for this seat;
    it is stamped onto every payload below so the QuickStatements renderer can
    refuse commands that could not say which statement they mean.
    """
    out: List[Suggestion] = []

    # Closed on Wikidata, still sitting per parlament.ch.
    if member.active and statement.end is not None and exp.end is None:
        out.append(
            _base_suggestion(
                KIND_REVIEW_ENDED,
                body,
                member,
                f"Wikidata ends this membership on {_date_str(statement.end)}, but "
                "parlament.ch still lists the member as sitting. Either the end "
                "date is wrong, or they left and returned (which needs a separate "
                "statement)." + verify,
                payload={"statement_id": statement.statement_id, "biography": biography},
            )
        )
    # Open on Wikidata, but the source says the tenure has ended.
    elif statement.is_open and exp.end is not None:
        out.append(
            _base_suggestion(
                KIND_ADD_END_DATE,
                body,
                member,
                f"Add an end date (P582) of {_date_str(exp.end)}; the membership is "
                "open on Wikidata but parlament.ch gives a leaving date." + verify,
                payload={
                    "position": body.position_qid,
                    "end": exp.end,
                    "terms": list(term_qids),
                    "statement_id": statement.statement_id,
                    "biography": biography,
                },
            )
        )

    # Start date: missing, or disagreeing with the authoritative source.
    if statement.start is None:
        if exp.start is not None:
            out.append(
                _base_suggestion(
                    KIND_ADD_START_DATE,
                    body,
                    member,
                    f"Add a start date (P580) of {_date_str(exp.start)}; the "
                    "membership is open but undated, which makes 'who sits today' "
                    "queries unreliable." + verify,
                    payload={
                        "position": body.position_qid,
                        "start": exp.start,
                        "terms": list(term_qids),
                        "statement_id": statement.statement_id,
                        "biography": biography,
                    },
                )
            )
    elif exp.start is not None and statement.start != exp.start:
        out.append(
            _base_suggestion(
                KIND_FIX_START_DATE,
                body,
                member,
                f"Wikidata's start date (P580) is {_date_str(statement.start)}, but "
                f"parlament.ch gives {_date_str(exp.start)}. Check which is right "
                "before changing it — a mid-term replacement often joins on a "
                "different day from the one a Wikipedia list records." + verify,
                payload={
                    "position": body.position_qid,
                    "start": exp.start,
                    "wikidata_start": statement.start,
                    "statement_id": statement.statement_id,
                    "biography": biography,
                },
            )
        )

    # Parliamentary term (P2937).
    missing_terms = [q for q in term_qids if q not in statement.terms]
    if missing_terms:
        out.append(
            _base_suggestion(
                KIND_ADD_TERM,
                body,
                member,
                "Add the parliamentary term (P2937) qualifier(s) "
                f"{', '.join(missing_terms)} — the tenure covers "
                f"{', '.join(p.label for p in exp.periods)}." + verify,
                payload={
                    "position": body.position_qid,
                    "terms": missing_terms,
                    "statement_id": statement.statement_id,
                    "biography": biography,
                },
            )
        )

    # Electoral district (P768) and parliamentary group (P4100).
    missing: Dict[str, str] = {}
    if district_qid and district_qid not in statement.districts:
        missing["district"] = district_qid
    if group_qid and group_qid not in statement.groups:
        missing["group"] = group_qid
    if missing:
        parts = []
        if "district" in missing:
            parts.append(
                f"electoral district (P768) → {member.canton_abbreviation} "
                f"({missing['district']})"
            )
        if "group" in missing:
            parts.append(
                f"parliamentary group (P4100) → {member.parl_group_abbreviation} "
                f"({missing['group']})"
            )
        out.append(
            _base_suggestion(
                KIND_ADD_QUALIFIER,
                body,
                member,
                "Add the missing qualifier(s): " + "; ".join(parts) + "." + verify,
                payload={
                    "position": body.position_qid,
                    "district": missing.get("district"),
                    "group": missing.get("group"),
                    "terms": list(term_qids),
                    "statement_id": statement.statement_id,
                    "biography": biography,
                },
            )
        )

    if ambiguous:
        for suggestion in out:
            suggestion.payload["ambiguous_statement"] = True
    return out


def _party_suggestions(
    body: Body,
    member: Member,
    person: WikidataPerson,
    config: Config,
    verify: str,
) -> List[Suggestion]:
    """Compare P102 against ``PartyAbbreviation``.

    Party membership is a separate statement, not a P39 qualifier, and it is
    the noisiest of the checks: parlament.ch records a cantonal section where
    Wikidata often records the national party, and vice versa. It is therefore
    always ``REVIEW_*`` — reported for a human, never emitted mechanically.
    """
    party_qid = config.party_qid(member.party_abbreviation)
    if not party_qid:
        return []  # unmapped party: skip rather than guess
    if party_qid in person.parties:
        return []
    if not person.parties:
        detail = (
            f"No open 'member of political party' (P102) statement, but "
            f"parlament.ch gives {member.party_abbreviation} "
            f"({member.party_name or '—'}) → {party_qid}."
        )
    else:
        detail = (
            f"Wikidata's open P102 value(s) {', '.join(person.parties)} do not "
            f"include {member.party_abbreviation} ({member.party_name or '—'}) → "
            f"{party_qid} as given by parlament.ch. This is often a cantonal "
            "section vs. national party difference rather than an error."
        )
    return [
        _base_suggestion(
            KIND_REVIEW_PARTY, body, member, detail + verify,
            payload={
                "party": party_qid,
                "biography": config.biography_url_for(member.person_number),
            },
        )
    ]
