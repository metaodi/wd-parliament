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

Seat checks and person checks
-----------------------------
Most of this module is about the **seat**: a P39 statement, its dates and its
qualifiers, all compared *by value* because parlament.ch is authoritative about
them. :func:`_person_data_suggestions` is the exception — it is about the
**person** (P19, P1321, P106, P102, P856, P1971) and compares only *presence*,
because those values arrive as free text and matching text to an item is a
judgement this tool does not make. Keeping the two apart is what lets the seat
checks stay assertive and the person checks stay report-only.
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
    KIND_ADD_PERSON_DATA,
    KIND_ADD_QUALIFIER,
    KIND_ADD_START_DATE,
    KIND_ADD_TERM,
    KIND_DUPLICATE_IDENTIFIER,
    KIND_FIX_START_DATE,
    KIND_NO_WIKIDATA_ITEM,
    KIND_REVIEW_ENDED,
    KIND_REVIEW_PARTY,
    MODEL_PERIOD,
    P_POLITICAL_PARTY,
    QID_FROM_NAME,
    Body,
    Member,
    Period,
    PersonDataCheck,
    PositionStatement,
    Suggestion,
    Tenure,
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


def _person_number(identifier: Optional[str]) -> Optional[int]:
    """Wikidata's identifier value as the source's person number. Pure.

    The reverse walk knows a person only through Wikidata, so this is the one
    bridge back to the source: the P1307 (or P14527) value *is* the source's
    id — Parmelin's ``PersonNumber=1108`` against P1307 = 1108, checked
    directly, which is the same equality ``resolve.match_by_identifier`` joins
    on. A value that is not a plain number is not that id, so it yields
    ``None`` and the suggestion simply carries no link and no dates rather than
    a link to some other member's biography.
    """
    if identifier is None:
        return None
    text = str(identifier).strip()
    if not text.isdigit():
        return None
    return int(text)


def compute_suggestions(
    body: Body,
    members: Sequence[Member],
    people: Dict[str, WikidataPerson],
    periods: Sequence[Period],
    config: Config,
    today: Optional[date] = None,
    tenures: Optional[Dict[tuple, Tenure]] = None,
) -> List[Suggestion]:
    """Produce the suggested edits for one chamber. Pure.

    ``people`` maps Q-ID → :class:`WikidataPerson` and must already contain
    everyone holding ``body.position_qid``, whether or not they matched a
    sitting member — the second pass below relies on it.

    ``tenures`` maps ``(person number, council)`` → :class:`Tenure`, from the
    source's historic table. It is what lets the second pass name the dates of
    a member who has already left, instead of telling the reader to look them
    up by hand. Optional: left out, that pass degrades to exactly the text it
    printed before.
    """
    today = today or date.today()
    suggestions: List[Suggestion] = []
    seen_qids: set = set()
    reported_conflicts: set = set()

    # 1) parlament.ch -> Wikidata.
    for member in members:
        # A conflict, not a gap. Raised before anything else about this member,
        # and *instead of* NO_WIKIDATA_ITEM: the identifier join refused to
        # arbitrate, so they may look unmatched, and telling somebody to create
        # an item for a person who already has two would make a third.
        if member.duplicate_identifier_qids:
            reported_conflicts.add(str(member.person_number))
            suggestions.append(
                _duplicate_identifier_suggestion(body, member, config)
            )
            if not member.qid:
                continue

        if not member.qid:
            suggestions.append(
                _base_suggestion(
                    KIND_NO_WIKIDATA_ITEM,
                    body,
                    member,
                    f"'{member.full_name}' sits in the {body.label} "
                    f"({config.source_name} #{member.person_number}) but no "
                    f"Wikidata item was found, by {config.identifier_property} "
                    "or by name. They may need a new item.",
                    payload={"biography": config.biography_url_for(member.person_number)},
                )
            )
            continue

        seen_qids.add(member.qid)
        person = people.get(member.qid) or WikidataPerson(qid=member.qid)
        suggestions.extend(
            _member_suggestions(body, member, person, periods, config, today)
        )

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

    # 2a) One identifier, several items — among the seat holders themselves.
    # The pass above only sees identifiers a *sitting* member carries; a
    # collision between two items about departed people is the same data
    # problem and would otherwise never be said out loud.
    for identifier, claimants in sorted(
        _identifier_collisions(people, body.position_qid).items()
    ):
        if identifier in reported_conflicts:
            continue
        suggestions.append(
            _orphan_conflict_suggestion(body, identifier, claimants, config)
        )

    active_qids = {m.qid for m in members if m.qid and m.active}
    # The identifiers sitting members carry, whether or not the join could use
    # them. A member with a duplicated identifier has **no** ``qid`` — the join
    # refused to pick one — so ``active_qids`` misses both claimants and the
    # walk below would report each of them as having left. That is a confident,
    # wrong claim about somebody who is in office today, and it is the failure
    # mode this whole pass was already once guilty of at scale.
    active_identifiers = {str(m.person_number) for m in members if m.active}
    for qid, person in sorted(people.items()):
        if qid in active_qids:
            continue
        if (person.parliament_id or "").strip() in active_identifiers:
            continue
        open_statements = [
            s for s in person.statements_for(body.position_qid) if s.is_open
        ]
        if not open_statements:
            continue
        suggestions.append(
            _departed_suggestion(
                body, qid, person, open_statements[0], config, tenures or {}
            )
        )

    suggestions.sort(key=lambda s: (s.priority, s.member_label.casefold()))
    return suggestions


def _identifier_collisions(
    people: Dict[str, WikidataPerson], position_qid: str
) -> Dict[str, List[WikidataPerson]]:
    """Identifier → the items holding this seat that claim it, when several do.

    Pure. Restricted to items holding ``position_qid`` so a chamber's report
    only raises conflicts about that chamber's seats; the same collision seen
    from the other chamber is that chamber's to report.
    """
    by_identifier: Dict[str, List[WikidataPerson]] = {}
    for person in people.values():
        identifier = (person.parliament_id or "").strip()
        if not identifier or not person.statements_for(position_qid):
            continue
        by_identifier.setdefault(identifier, []).append(person)
    return {k: v for k, v in by_identifier.items() if len(v) > 1}


def _duplicate_identifier_suggestion(
    body: Body, member: Member, config: Config
) -> Suggestion:
    """One identifier, several Wikidata items — a conflict a human must resolve.

    Report-only, and not because the evidence is weak: it is the strongest
    finding this tool makes, an outright contradiction in Wikidata's own data.
    It is report-only because **every** repair is destructive in a way
    QuickStatements cannot express — merging two items, or removing an
    identifier from one of them — and because which item is the real person is
    exactly the judgement the join refused to make.

    The member may still carry a ``qid`` here: the name fallback runs after the
    identifier join gives up, and can land on one of the claimants. That does
    not settle the conflict, so it is raised either way and the detail says
    which item the rest of the run went on to use.
    """
    qids = list(member.duplicate_identifier_qids)
    used = (
        f" The rest of this report uses {member.qid}, matched by name and birth "
        "date — that is a guess about which of them is this person, not an "
        "answer."
        if member.qid
        else " No other suggestion is made for this member: until the conflict "
        "is resolved there is no item to make one about."
    )
    return _base_suggestion(
        KIND_DUPLICATE_IDENTIFIER,
        body,
        member,
        f"{config.identifier_property} '{member.person_number}' is claimed by "
        f"{len(qids)} Wikidata items: {', '.join(qids)}. The identifier should "
        "be unique, so either they are duplicates that need merging or one "
        f"carries the wrong value. {config.source_name} lists a single person "
        f"under that number. The identifier join skips this member rather than "
        f"picking one of the items.{used}",
        payload={
            "duplicate_qids": qids,
            "parliament_id": str(member.person_number),
            "biography": config.biography_url_for(member.person_number),
        },
    )


def _orphan_conflict_suggestion(
    body: Body,
    identifier: str,
    people: Sequence[WikidataPerson],
    config: Config,
) -> Suggestion:
    """The same conflict, for an identifier no *sitting* member carries.

    Two items claiming one identifier is a data problem whether or not the
    person is still in office, and the members table only covers today's ~246.
    Without this pass a collision between two historic items would go unsaid
    for as long as nobody happens to be elected with that number.
    """
    qids = sorted(p.qid for p in people)
    labels = ", ".join(f"{p.label or p.qid} ({p.qid})" for p in sorted(
        people, key=lambda p: p.qid
    ))
    return Suggestion(
        kind=KIND_DUPLICATE_IDENTIFIER,
        body=body,
        member_label=labels,
        detail=(
            f"{config.identifier_property} '{identifier}' is claimed by "
            f"{len(qids)} Wikidata items that hold a '{body.label}' seat: "
            f"{labels}. The identifier should be unique, so either they are "
            "duplicates that need merging or one carries the wrong value. No "
            f"*sitting* member has that number, so this is a conflict between "
            "items about people who have left — invisible to the rest of this "
            "report, which is why it is raised here."
        ),
        links={
            "position": _item_url(body.position_qid),
            **{qid: _item_url(qid) for qid in qids},
        },
        payload={
            "duplicate_qids": qids,
            "parliament_id": identifier,
            "biography": config.biography_url_for(identifier),
        },
    )


def _departed_suggestion(
    body: Body,
    qid: str,
    person: WikidataPerson,
    statement: PositionStatement,
    config: Config,
    tenures: Dict[tuple, Tenure],
) -> Suggestion:
    """Wikidata still records this person as sitting; the source does not.

    Everything here is reached through Wikidata's own identifier value, because
    that is all this pass has: the person is outside the current-members set,
    so there is no :class:`~.models.Member` to read a number or a link off. It
    buys two things the report used to lack — the source's biography page, and
    the dates the source's historic table gives for the spell — and neither is
    guessed: an item with no identifier value gets no link and no dates.

    **Report-only, deliberately.** ``qid_source`` stays unset, so
    :func:`quickstatements.is_mechanical` refuses these; the payload also
    carries no ``position``, which refuses them a second time. Both are load
    bearing. The identifier here comes from Wikidata rather than from a
    resolved member, and the dates from a historic table no probe has yet
    measured for departed members — the two things a P582 backfill across
    everyone Wikidata lists as sitting would have to be sure of. A human reads
    the dates, applies them, and that is the intended workflow until such a
    probe exists.
    """
    number = _person_number(person.parliament_id)
    tenure = tenures.get((number, body.council.upper())) if number is not None else None
    statements = person.statements_for(body.position_qid)

    detail = (
        f"Wikidata records an open '{body.label}' membership (no end date), "
        f"but {config.source_name} does not list this person as a sitting "
        "member. They have most likely left; "
    )
    payload: Dict[str, object] = {"statement_id": statement.statement_id}

    # Left and returned: several P39 statements for one seat, which property +
    # main value does not identify. Stamped here for the same reason
    # ``_statement_suggestions`` stamps it for sitting members — and it is not
    # redundant with the report-only gates below. Those two say "this whole
    # class is unmeasured"; this says "this person is unaddressable however the
    # class is settled", which survives the day somebody removes them. Run 16
    # found 3 such people among 1,969.
    if len(statements) > 1:
        payload["ambiguous_statement"] = True

    if number is not None:
        payload["parliament_id"] = str(number)
        payload["biography"] = config.biography_url_for(number)

    if tenure is not None and tenure.end is not None:
        detail += (
            f"{config.source_name} records the seat as held from "
            f"{_date_str(tenure.start)} to {_date_str(tenure.end)}, so add an "
            f"end date (P582) of {_date_str(tenure.end)}"
        )
        payload["start"] = tenure.start
        payload["end"] = tenure.end
        if statement.start is None:
            detail += (
                f", and the statement has no start date (P580) either — "
                f"{_date_str(tenure.start)} per the same record"
            )
        elif tenure.start is not None and statement.start != tenure.start:
            detail += (
                f". Wikidata's start date (P580) is {_date_str(statement.start)} "
                f"against the source's {_date_str(tenure.start)}, so check that "
                "too"
            )
        detail += (
            ". These dates come from the source's historic record rather than "
            "from its current-members set, so confirm them on the biography "
            "page before applying."
        )
    elif tenure is not None:
        detail += (
            f"add an end date (P582). {config.source_name} records the seat as "
            f"held from {_date_str(tenure.start)} but gives no leaving date, so "
            "the end has to be looked up by hand on the biography page."
        )
        payload["start"] = tenure.start
    else:
        detail += (
            f"add an end date (P582). {config.source_name} gives no leaving "
            "date here because the person is outside the current-members set, "
            "so the date has to be looked up by hand."
        )

    return Suggestion(
        kind=KIND_ADD_END_DATE,
        body=body,
        member_label=person.label or qid,
        person_qid=qid,
        person_number=number,
        detail=detail,
        links={"item": _item_url(qid), "position": _item_url(body.position_qid)},
        payload=payload,
    )


def _member_suggestions(
    body: Body,
    member: Member,
    person: WikidataPerson,
    periods: Sequence[Period],
    config: Config,
    today: date,
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
                f"Add {config.identifier_property} "
                f"'{member.person_number}'. The item was found by name and "
                "birth date; recording the identifier makes every future "
                "comparison exact." + verify,
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
                    f"({body.position_qid}) for {exp.label}. "
                    f"{config.source_name} lists them as sitting since "
                    f"{_date_str(exp.start)}." + verify,
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
                config.source_name,
                today,
            )
        )

    out.extend(_party_suggestions(body, member, person, config, verify))
    out.extend(_person_data_suggestions(body, member, person, config, verify))
    return out


def source_values(member: Member, check: PersonDataCheck) -> List[str]:
    """The source's value(s) for one personal-data check, as strings. Pure.

    Empty whenever the source has nothing to say, which is what makes the whole
    class of check fail-safe: a column the source does not have, or does not
    fill in for this member, produces no suggestion rather than a claim that
    Wikidata is missing something.

    A ``number_of_children`` of **0** is deliberately treated as nothing. In a
    nullable integer column a zero and an unstated value are the same shape,
    and "this person has no children" is a statement worth making only when the
    source means it — which cannot be told apart from here.
    """
    value = getattr(member, check.attribute, None)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, bool):  # pragma: no cover - no boolean check today
        return []
    if isinstance(value, int):
        return [str(value)] if value > 0 else []
    text = str(value).strip()
    return [text] if text else []


def _person_data_suggestions(
    body: Body,
    member: Member,
    person: WikidataPerson,
    config: Config,
    verify: str,
) -> List[Suggestion]:
    """Facts the source publishes that the item records nowhere. Pure.

    The one check in this tool that compares **presence rather than value**,
    and the reason is in :data:`~.models.PERSON_DATA_CHECKS`: the source gives
    "Bern (BE)" or "Rechtsanwalt" where Wikidata wants an item, and resolving
    one to the other is the judgement the config's Q-ID maps exist to keep out
    of the code. So this never says a recorded value is wrong — only that a
    published fact is absent — and it hands the reader the source's own string.

    Three conditions, each of which alone would make a suggestion unsafe:

    - **the item must have been queried.** ``person_data_known`` is false for an
      item outside the personal-data query's population (a name-matched item
      with neither the identifier nor a seat), and an item nobody asked about
      is not an item that is missing anything.
    - **Wikidata must carry no statement for the property at all.** A different
      value is not a finding here; it is the noise ``REVIEW_PARTY`` exists to
      handle for the one property where the source can be compared.
    - **the source must actually have a value.**

    Report-only, and gated the way :func:`_departed_suggestion` is: the kind is
    not in ``quickstatements.MECHANICAL_KINDS``, and the payload carries no
    ``position``, so ``is_mechanical`` refuses these twice over. Removing one
    gate would not be enough to emit them, which is the point — a P19 written
    from an unresolved place name would be a wrong statement on a real person.
    """
    if not person.person_data_known:
        return []

    out: List[Suggestion] = []
    for check in config.person_data_checks:
        if person.has_property(check.property_id):
            continue
        # A mapped party is ``_party_suggestions``' business: it compares the
        # value and would raise REVIEW_PARTY for this same member, so checking
        # presence here as well would file one gap twice. Unmapped — which is
        # how both configs ship — that check returns nothing at all, and this
        # is the only thing that notices a member with no P102 whatsoever.
        if check.property_id == P_POLITICAL_PARTY and config.party_qid(
            member.party_abbreviation
        ):
            continue
        values = source_values(member, check)
        if not values:
            continue
        out.append(
            _base_suggestion(
                KIND_ADD_PERSON_DATA,
                body,
                member,
                f"No '{check.label}' ({check.property_id}) statement, but "
                f"{config.source_name} gives {', '.join(values)}. "
                + _person_data_advice(check)
                + verify,
                payload={
                    "property": check.property_id,
                    "values": values,
                    "biography": config.biography_url_for(member.person_number),
                },
            )
        )
    return out


def _person_data_advice(check: PersonDataCheck) -> str:
    """What a reader has to do with the source's value. Pure."""
    if check.needs_item:
        return (
            "The source publishes this as text, so it has to be matched to a "
            "Wikidata item by hand — which is why this is reported rather than "
            "applied."
        )
    if check.value_kind == "url":
        return (
            "Check that the address is the member's own site rather than their "
            "page on the source's website before adding it."
        )
    return (
        "Confirm the figure is current on the biography page before adding it; "
        "the source states it as of the member's last update."
    )


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
    source_name: str = "parlament.ch",
    today: Optional[date] = None,
) -> List[Suggestion]:
    """Checks against one existing P39 statement.

    ``ambiguous`` marks a member holding several P39 statements for this seat;
    it is stamped onto every payload below so the QuickStatements renderer can
    refuse commands that could not say which statement they mean.
    """
    today = today or date.today()
    out: List[Suggestion] = []

    # Closed on Wikidata, still sitting per parlament.ch. A future end date is
    # a planned/scheduled end (e.g. end of legislature), not a disagreement —
    # only flag once that date has actually passed.
    if (
        member.active
        and statement.end is not None
        and statement.end <= today
        and exp.end is None
    ):
        out.append(
            _base_suggestion(
                KIND_REVIEW_ENDED,
                body,
                member,
                f"Wikidata ends this membership on {_date_str(statement.end)}, "
                f"but {source_name} still lists the member as sitting. Either "
                "the end date is wrong, or they left and returned (which needs "
                "a separate statement)." + verify,
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
                f"Add an end date (P582) of {_date_str(exp.end)}; the "
                f"membership is open on Wikidata but {source_name} gives a "
                "leaving date." + verify,
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
                f"Wikidata's start date (P580) is "
                f"{_date_str(statement.start)}, but {source_name} gives "
                f"{_date_str(exp.start)}. Check which is right before changing "
                "it — a mid-term replacement often joins on a different day "
                "from the one a Wikipedia list records." + verify,
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
            f"{config.source_name} gives {member.party_abbreviation} "
            f"({member.party_name or '—'}) → {party_qid}."
        )
    else:
        detail = (
            f"Wikidata's open P102 value(s) {', '.join(person.parties)} do not "
            f"include {member.party_abbreviation} ({member.party_name or '—'}) → "
            f"{party_qid} as given by {config.source_name}. This is often a cantonal "
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
