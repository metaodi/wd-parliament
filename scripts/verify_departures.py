"""Answer README step 8: may the departed members' P582 be applied in bulk?

The question, and why it matters
--------------------------------
The diff's second pass walks Wikidata's *open* memberships back to the source
and finds people the source does not list as sitting: they have left, and
Wikidata has never been told. Since the "Link and date the departed members"
change those suggestions carry a concrete leaving date, read from the source's
historic record — ``MemberCouncilHistory`` federally, the ended ``memberships``
rows in OpenParlData.

They are nevertheless **report-only**, gated twice in ``diff`` (no ``qid_source``
and no ``position`` in the payload), and this script is the probe that has to
come back clean before anyone considers removing those gates. Everything it
measures is what would go wrong if they were removed today:

- the population is defined by *Wikidata*, not by the source, so it is exactly
  the set nobody has measured. ``ADD_END_DATE`` is a **mechanical** kind, so
  ungating it writes P582 to every one of these items without a human reading
  a single date;
- the person number comes from the identifier value **Wikidata** carries, not
  from a member the source handed us. P1307 == ``PersonNumber`` was confirmed
  for a *sitting* member (Parmelin, README step 1). Nothing has checked it for
  somebody who left in 1987;
- the date comes from a table this pipeline has only ever read for people who
  are still in office. ``MemberCouncilHistory`` is where P580's tenure start
  comes from, and step 0c found the *other* field in the same table to be a
  segment start rather than a tenure start — so "it is in the history" has
  already been shown, once, not to mean what it looked like.

The four sections
-----------------
**A. Reach** — of the people the reverse walk reports, how many carry an
identifier value at all, how many of those resolve to a row in the source's
historic record, and how many of *those* have a closed tenure, i.e. an actual
date to emit. Reach is not correctness; it bounds how much the rest can say.

**B. Identity** — is the person the history returns the same human as the
Wikidata item? The departed twin of the Parmelin check, done the only way it
can be done without a second identifier: the surname on the history row against
the item's label. A disagreement here is the worst finding available, because
it means the number points at somebody else and the date is another person's.

**C. Agreement** — does the source's leaving date agree with an independent
one? Same join ``compare_tenure_dates.py`` uses, for the same reason::

    MemberCouncil.PersonNumber ──P1307──▶ Q-ID ◀──wikidata_id── OpenParlData

keyed by ``(Q-ID, council)`` and never by Q-ID alone: a member who moved
NR→SR has an NR row ending the day before their SR row begins, and pooling the
two reads a chamber change as a contradiction. This is the verdict to read.

**D. Which statement would it close?** QuickStatements matches a statement by
property and main value, so a P582 aimed at an item holding several P39
statements for the same seat cannot say which one it means. ``diff`` already
refuses those for sitting members via ``ambiguous_statement``; this section
sizes the same problem in the departed population, plus the subtler version of
it — an open statement whose P580 is not the source's tenure start is probably
about a *different* spell, and closing it with this tenure's end date would be
wrong even though both dates are real.

Reading the result
------------------
``CONFIRMED``
    The two sources agree on every comparable leaving date, the identities
    corroborate, and no statement is ambiguous. That is the evidence needed to
    consider letting ADD_END_DATE reach QuickStatements for departed members.
``CONTRADICTED``
    They disagree. Leave the gates in ``diff`` alone; the detail says how the
    disagreement is shaped.
``INCONCLUSIVE``
    Too few people could be joined to conclude anything — a finding about the
    reach, not about the dates. Note this is the *expected* answer on a run
    where Wikidata is already tidy: no open memberships for departed members
    means nothing to measure, which is good news about the data and no news
    about the question.

Run it locally::

    uv run python scripts/verify_departures.py

or dispatch the "Verify assumptions" workflow, which runs it alongside the
other probes. Like them it reports without gating: what it decides is whether a
*bulk apply* is safe, and today nothing it could falsify is emitted at all —
the suggestions it measures are report-only by construction.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Make the ``src`` layout importable without installing the package.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wd_parliament.config import load_config  # noqa: E402
from wd_parliament.http_client import HttpClient  # noqa: E402
from wd_parliament.parliament import (  # noqa: E402
    NULL_DATE,
    ParliamentClient,
    _as_date,
    tenures_from_segments,
)
from wd_parliament.resolve import match_by_identifier  # noqa: E402
from wd_parliament.wikidata import WikidataClient  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_openparldata import (  # noqa: E402
    BEGIN_FIELDS,
    CONFIRMED,
    CONTRADICTED,
    END_FIELDS,
    INCONCLUSIVE,
    _present,
    chamber_of,
    fetch,
)

# Below this many comparable people the verdict says nothing either way. Lower
# than ``compare_tenure_dates.MIN_JOINED`` on purpose: that script measures the
# whole sitting chamber and can demand a decent sample, while this population is
# whatever Wikidata happens to have left open — a tidy Wikidata makes it small,
# and demanding 10 would report a shortage of *errors* as a failed probe.
MIN_COMPARABLE = 5


@dataclass
class Departure:
    """One person the reverse walk reports, seen from every side at once."""

    qid: str
    label: str
    council: str
    # From Wikidata: the identifier value, and the open statement being judged.
    identifier: Optional[str] = None
    person_number: Optional[int] = None
    statement_start: Optional[date] = None
    statements_for_seat: int = 0
    open_statements: int = 0
    # From the source's historic record.
    history_name: Optional[str] = None
    source_start: Optional[date] = None
    source_end: Optional[date] = None
    # From OpenParlData, the independent second source.
    opd_end: Optional[date] = None
    opd_rows: int = 0

    @property
    def reachable(self) -> bool:
        """Did the identifier resolve to a row in the historic record?"""
        return self.person_number is not None and self.source_start is not None

    @property
    def has_date_to_emit(self) -> bool:
        """Is there a leaving date a P582 could actually be built from?"""
        return self.source_end is not None

    @property
    def comparable(self) -> bool:
        return self.source_end is not None and self.opd_end is not None

    @property
    def agrees(self) -> bool:
        return self.comparable and self.source_end == self.opd_end

    @property
    def ambiguous(self) -> bool:
        """Several P39 statements for this seat: property + value names none."""
        return self.statements_for_seat > 1

    @property
    def start_disagrees(self) -> bool:
        """Is the open statement plausibly about a *different* spell?

        A statement whose P580 is not this tenure's start is most likely the
        member's earlier service, and closing it with this tenure's end would
        put a wrong span on a real statement. An undated statement says nothing
        either way and is not counted here — it is counted in section D's own
        line, because "no P580" is a different problem from "the wrong P580".
        """
        return (
            self.statement_start is not None
            and self.source_start is not None
            and self.statement_start != self.source_start
        )


def surname_of(label: str) -> str:
    """The surname in a Wikidata label. Pure.

    Everything after the last space, lowercased. Crude, and deliberately so:
    this corroborates an identifier, it does not establish one, and a
    comparison that tried to be clever about "von Matt" or "Badran Jacqueline"
    would fail in ways that are harder to read than a plain mismatch.
    """
    return label.strip().rpartition(" ")[2].casefold()


def names_agree(label: Optional[str], last_name: Optional[str]) -> Optional[bool]:
    """Does a Wikidata label corroborate a history row's surname? Pure.

    ``None`` when either side is missing — unknown, which is neither
    agreement nor contradiction and must not be scored as either. An item whose
    label is a Q-ID (the fallback when no label exists in any queried language)
    is also unknown rather than a mismatch.
    """
    if not label or not last_name:
        return None
    label = label.strip()
    if label.startswith("Q") and label[1:].isdigit():
        return None
    wanted = last_name.strip().casefold()
    if not wanted:
        return None
    # Either the whole surname is the label's last word, or it appears in the
    # label at all — enough for "Jacqueline Badran" against "Badran", and for a
    # double-barrelled surname recorded differently on the two sides.
    return wanted == surname_of(label) or wanted in label.casefold()


def chained_end(rows: Sequence[Dict[str, Any]]) -> Optional[date]:
    """OpenParlData's end of the **current continuous run**. Pure.

    The mirror of ``compare_tenure_dates.chained_start``, and simpler than it
    for a reason worth stating: rows are per-term federally, so a member who
    sat 2011-2019 has two of them and the tenure the tool models spans both —
    but every row after the newest ends *inside* that span, so the tenure's end
    is the newest row's end and no chaining changes it. The start needs the
    walk; the end does not.

    ``None`` when the newest row is open. That means "still sitting per this
    source", which is an answer, and it must never fall back to an earlier
    row's end — that would manufacture a leaving date for somebody who has not
    left.
    """
    if not rows:
        return None
    begin_field = _present(rows, BEGIN_FIELDS)
    end_field = _present(rows, END_FIELDS)
    if begin_field is None or end_field is None:
        # "No such column" and "column full of nulls" are indistinguishable
        # through .get() and mean opposite things — see
        # verify_openparldata.classify_seat_memberships.
        return None

    spans: List[Tuple[date, Optional[date]]] = []
    for row in rows:
        start = _as_date(row.get(begin_field))
        if start is None:
            continue
        spans.append((start, _as_date(row.get(end_field))))
    if not spans:
        return None
    spans.sort(key=lambda s: s[0])
    return spans[-1][1]


def classify_reach(departures: Sequence[Departure]) -> Tuple[str, str, List[str]]:
    """Can the reverse walk's people be found in the source at all? Pure.

    Every count is derived from the population itself rather than passed in
    alongside it, so the "no identifier" line cannot drift out of step with the
    people it is describing.
    """
    lines: List[str] = []
    identified = [d for d in departures if d.person_number is not None]
    reachable = [d for d in departures if d.reachable]
    dated = [d for d in departures if d.has_date_to_emit]

    lines.append(f"people reported as departed:      {len(departures)}")
    lines.append(f"  carrying an identifier value:   {len(identified)}")
    lines.append(f"  no identifier value at all:     {len(departures) - len(identified)}")
    lines.append(f"  found in the historic record:   {len(reachable)}")
    lines.append(f"  with a closed tenure (a date):  {len(dated)}")

    if not departures:
        return (
            INCONCLUSIVE,
            "Wikidata has no open membership for anybody the source does not "
            "list as sitting, so this pass reports nobody and there is nothing "
            "to measure. Good news about the data, no news about the question.",
            lines,
        )
    if not reachable:
        return (
            CONTRADICTED,
            f"None of the {len(departures)} people could be found in the "
            "historic record. Either the identifier values are not person "
            "numbers or the history does not carry departed members — check "
            "section B's sample rows before believing any date this tool "
            "prints for them.",
            lines,
        )
    share = 100.0 * len(dated) / len(departures)
    return (
        CONFIRMED,
        f"{len(reachable)} of {len(departures)} resolve into the historic "
        f"record and {len(dated)} ({share:.1f}%) have a leaving date to "
        "suggest. Reach only — whether the dates are right is section C.",
        lines,
    )


def classify_identity(departures: Sequence[Departure]) -> Tuple[str, str, List[str]]:
    """Is the person the number reached the person the item is about? Pure.

    The departed twin of README step 1. It cannot be as strong: step 1 compared
    two identifiers, this compares a name against a label, so agreement is
    corroboration rather than proof. A *disagreement*, though, is conclusive in
    the direction that matters — the number is somebody else's.
    """
    lines: List[str] = []
    judged = [d for d in departures if d.reachable]
    verdicts = [(d, names_agree(d.label, d.history_name)) for d in judged]
    agree = [d for d, v in verdicts if v is True]
    differ = [d for d, v in verdicts if v is False]
    unknown = [d for d, v in verdicts if v is None]

    lines.append(f"identities checked:               {len(judged)}")
    lines.append(f"  surname corroborates the label: {len(agree)}")
    lines.append(f"  surname contradicts the label:  {len(differ)}")
    lines.append(f"  one side has no name:           {len(unknown)}")
    lines.append("")
    for d in differ[:12]:
        lines.append(
            f"  {d.qid} '{d.label}' ({d.council}) -> #{d.person_number} "
            f"'{d.history_name}'"
        )

    if not judged:
        return (
            INCONCLUSIVE,
            "Nobody resolved into the historic record, so there was no identity "
            "to check.",
            lines,
        )
    if differ:
        return (
            CONTRADICTED,
            f"{len(differ)} of {len(judged)} identifier values reach a person "
            "whose surname does not appear in the Wikidata label. Each one is a "
            "date that would be written to the wrong person's item. This alone "
            "blocks a bulk apply; read the rows above and check them by hand on "
            "the biography pages.",
            lines,
        )
    return (
        CONFIRMED,
        f"All {len(agree)} checkable identifier values reach a person whose "
        f"surname matches the item's label ({len(unknown)} unknown). "
        "Corroboration, not proof — it is a name check, not the two-identifier "
        "comparison README step 1 could do for sitting members.",
        lines,
    )


def classify_leaving_dates(
    departures: Sequence[Departure],
) -> Tuple[str, str, List[str]]:
    """Do the two sources agree on when these people left? Pure.

    **The verdict this script exists for.** ``ADD_END_DATE`` is mechanical, so
    a CONFIRMED here is what would justify letting the departed suggestions
    reach QuickStatements — and a wrong CONFIRMED writes a wrong P582 to every
    item in the population.
    """
    lines: List[str] = []
    dated = [d for d in departures if d.has_date_to_emit]
    comparable = [d for d in dated if d.comparable]
    sentinels = [d for d in dated if d.source_end and d.source_end <= NULL_DATE]
    reversed_spans = [
        d
        for d in dated
        if d.source_start and d.source_end and d.source_end < d.source_start
    ]

    # Two different reasons a date cannot be checked, and they mean opposite
    # things: OpenParlData not knowing the person is a gap in the join, while
    # OpenParlData knowing them and leaving the row open is a disagreement
    # about whether they left at all — the one case where "not comparable" is
    # itself the finding.
    unjoined = [d for d in dated if not d.opd_rows]
    still_open = [d for d in dated if d.opd_rows and d.opd_end is None]

    lines.append(f"leaving dates from the source:    {len(dated)}")
    lines.append(f"  also dated by OpenParlData:     {len(comparable)}")
    lines.append(f"  not in OpenParlData at all:     {len(unjoined)}")
    lines.append(f"  OpenParlData still shows open:  {len(still_open)}")
    lines.append(f"  null-date sentinel (1753):      {len(sentinels)}")
    lines.append(f"  end before start:               {len(reversed_spans)}")

    if sentinels:
        return (
            CONTRADICTED,
            f"{len(sentinels)} leaving date(s) are the 1753-01-01 sentinel, "
            "which means 'no date' and not 'left in 1753'. parliament.NULL_DATE "
            "should have mapped these to None at the boundary — this is the "
            "0b failure, and it must be fixed before anything else here is "
            "believed.",
            lines,
        )
    if reversed_spans:
        return (
            CONTRADICTED,
            f"{len(reversed_spans)} tenure(s) end before they start. The "
            "segment walk is returning ends and starts from different spells, "
            "so neither date can be trusted.",
            lines,
        )
    if len(comparable) < MIN_COMPARABLE:
        return (
            INCONCLUSIVE,
            f"Only {len(comparable)} leaving date(s) could be checked against "
            f"OpenParlData, fewer than the {MIN_COMPARABLE} this needs. That is "
            "a finding about the join and the population size, not about the "
            "dates — a tidy Wikidata leaves little for this pass to report, and "
            "a small sample cannot license a bulk apply.",
            lines,
        )

    agree = [d for d in comparable if d.agrees]
    differ = [d for d in comparable if not d.agrees]
    share = 100.0 * len(agree) / len(comparable)
    lines.append("")
    lines.append(f"agree exactly:                    {len(agree)} ({share:.1f}%)")
    lines.append(f"disagree:                         {len(differ)}")
    lines.append("")
    for d in differ[:12]:
        lines.append(
            f"  #{d.person_number} {d.label} ({d.council}): "
            f"source {d.source_end}, OpenParlData {d.opd_end}"
        )

    if not differ:
        return (
            CONFIRMED,
            f"All {len(comparable)} comparable leaving dates agree. The historic "
            "record gives the same end as an independent source for people the "
            "current-members table does not contain — which is the evidence "
            "the departed suggestions' report-only gates were waiting for. "
            "Read section D before removing them: agreement on the date is not "
            "agreement on which statement it closes.",
            lines,
        )

    earlier = [d for d in differ if d.source_end < d.opd_end]  # type: ignore[operator]
    return (
        CONTRADICTED,
        f"{len(differ)} of {len(comparable)} leaving dates disagree — "
        f"{len(earlier)} with the source earlier than OpenParlData and "
        f"{len(differ) - len(earlier)} later. Leave the gates in diff alone: a "
        "P582 is not correctable by QuickStatements once written, so a date "
        "that two sources dispute must be read by a human, which is what the "
        "report already asks for.",
        lines,
    )


def classify_statement_ambiguity(
    departures: Sequence[Departure],
) -> Tuple[str, str, List[str]]:
    """Could a P582 name the statement it means to close? Pure.

    QuickStatements matches on property + main value, so an item with two P39
    statements for one seat cannot be targeted by a qualifier-only command.
    ``diff`` already refuses that for sitting members; nothing has sized it for
    the departed. The second count is the subtler one: an open statement whose
    P580 is not this tenure's start is most likely about an earlier spell, and
    closing it with this tenure's end would put a wrong span on a real
    statement using two dates that are individually correct.
    """
    lines: List[str] = []
    dated = [d for d in departures if d.has_date_to_emit]
    ambiguous = [d for d in dated if d.ambiguous]
    mismatched = [d for d in dated if d.start_disagrees]
    undated = [d for d in dated if d.statement_start is None]

    lines.append(f"people with a date to emit:       {len(dated)}")
    lines.append(f"  several P39 for the same seat:  {len(ambiguous)}")
    lines.append(f"  open statement's P580 differs:  {len(mismatched)}")
    lines.append(f"  open statement has no P580:     {len(undated)}")
    lines.append("")
    for d in mismatched[:12]:
        lines.append(
            f"  {d.qid} {d.label} ({d.council}): statement starts "
            f"{d.statement_start}, source tenure starts {d.source_start}"
        )

    if not dated:
        return (
            INCONCLUSIVE,
            "No leaving dates to emit, so no statement to aim one at.",
            lines,
        )
    if not ambiguous and not mismatched:
        return (
            CONFIRMED,
            f"Every one of the {len(dated)} people holds a single P39 for the "
            "seat and its start agrees with the tenure the date came from, so a "
            "P582 would land on the statement it is meant for.",
            lines,
        )
    return (
        CONTRADICTED,
        f"{len(ambiguous)} item(s) hold several P39 statements for this seat "
        f"and {len(mismatched)} have an open statement whose P580 is not this "
        "tenure's start. A qualifier-only command cannot say which statement it "
        "means in the first case, and means the wrong one in the second. Those "
        "people would need excluding by the same ambiguous_statement rule the "
        "sitting members already have before any bulk apply — the report is "
        "right for them either way.",
        lines,
    )


def overall(verdicts: Sequence[str]) -> str:
    """The weakest of the section verdicts. Pure.

    CONTRADICTED beats INCONCLUSIVE beats CONFIRMED: a bulk apply needs *every*
    section to come back clean, so the answer to "may it be applied" is the
    worst news, never the average and never the best.
    """
    if CONTRADICTED in verdicts:
        return CONTRADICTED
    if INCONCLUSIVE in verdicts:
        return INCONCLUSIVE
    return CONFIRMED


def _section(title: str, result: Tuple[str, str, List[str]]) -> str:
    verdict, detail, lines = result
    print("-" * 70)
    print(title)
    print("-" * 70)
    for line in lines:
        print("  " + line if line else "")
    print()
    print(f"{verdict}: {detail}")
    print()
    return verdict


def collect(
    config: Any,
    parliament: ParliamentClient,
    wikidata: WikidataClient,
    opd_client: Any = None,
    body_key: str = "CHE",
) -> List[Departure]:
    """Build the population, from all three sources. Network.

    A person Wikidata says nothing identifying about is kept, not dropped:
    "no identifier value" is a reach finding that section A counts, and
    dropping them would quietly shrink the population the verdict is about.
    """
    members = parliament.get_members(councils=config.councils)
    print(f"parlament.ch: {len(members)} sitting member(s)")
    if not members:
        raise RuntimeError(
            "The source returned no sitting members, so every seat holder on "
            "Wikidata would look departed. Run scripts/verify_source.py first."
        )

    people = wikidata.get_position_holders(
        config.position_qids, config.language, config.identifier_property
    )
    print(f"Wikidata: {len(people)} item(s) hold one of the configured seats")

    # The same exact join the pipeline's first pass makes. Members matched only
    # by *name* are not excluded here, and cannot be: their items carry no
    # identifier value, so they land in the "no identifier" bucket below, which
    # no verdict is drawn from.
    match_by_identifier(members, people.values())
    sitting_qids = {m.qid for m in members if m.qid}

    # One read of MemberCouncilHistory answers both questions this needs: the
    # tenure dates, and the name that corroborates the identifier reached the
    # right person.
    segments = parliament.get_member_segments(councils=config.councils)
    tenures = tenures_from_segments(segments)
    print(f"parlament.ch: tenure dates for {len(tenures)} (person, council) pair(s)")

    seats_by_seat = _openparldata_seats(opd_client, body_key) if opd_client else {}

    departures: List[Departure] = []
    for body in config.bodies:
        for qid, person in sorted(people.items()):
            if qid in sitting_qids:
                continue
            statements = person.statements_for(body.position_qid)
            open_statements = [s for s in statements if s.is_open]
            if not open_statements:
                continue

            identifier = (person.parliament_id or "").strip() or None
            number = int(identifier) if identifier and identifier.isdigit() else None
            tenure = tenures.get((number, body.council.upper())) if number else None
            rows = seats_by_seat.get((qid, body.council.upper()), [])
            departures.append(
                Departure(
                    qid=qid,
                    label=person.label or qid,
                    council=body.council,
                    identifier=identifier,
                    person_number=number,
                    statement_start=open_statements[0].start,
                    statements_for_seat=len(statements),
                    open_statements=len(open_statements),
                    history_name=surname_in_history(segments, number, body.council),
                    source_start=tenure.start if tenure else None,
                    source_end=tenure.end if tenure else None,
                    opd_end=chained_end(rows),
                    opd_rows=len(rows),
                )
            )
    return departures


def surname_in_history(
    segments: Dict[tuple, List[Any]], number: Optional[int], council: str
) -> Optional[str]:
    """The surname the historic record carries for this person number. Pure.

    Read off the segments already fetched for the dates, so it costs no extra
    request. ``None`` when the number reaches nothing — section A counts that,
    and section B then has no identity to judge rather than a failed one.
    """
    if number is None:
        return None
    rows = segments.get((number, council.upper()), [])
    return (rows[0].last_name or None) if rows else None


def _openparldata_seats(client: Any, body_key: str) -> Dict[tuple, List[Dict[str, Any]]]:
    """``(Q-ID, council)`` → the seat rows OpenParlData has for it.

    Keyed by seat and never by person, for the reason recorded in
    ``compare_tenure_dates``: a member who moved NR→SR chains one chamber's
    years onto the other's statement if the two are pooled, and every one of
    run 11's 22 "disagreements" was that.
    """
    people, _ = fetch(client, "persons", body_key=body_key)
    qid_by_person = {
        r.get("id"): str(r["wikidata_id"]).strip()
        for r in people
        if str(r.get("wikidata_id") or "").strip()
    }
    print(f"OpenParlData: {len(qid_by_person)} person(s) carry a wikidata_id")

    groups, _ = fetch(client, "groups", body_key=body_key)
    chambers: Dict[str, Dict[str, Any]] = {}
    for row in groups:
        council = chamber_of(row)
        if council and council not in chambers:
            chambers[council] = row
    print(f"OpenParlData: chambers {', '.join(sorted(chambers)) or 'NONE'}")

    seats: Dict[tuple, List[Dict[str, Any]]] = {}
    for council, group in chambers.items():
        rows, _ = fetch(client, "memberships", group_id=group.get("id"))
        for row in rows:
            qid = qid_by_person.get(row.get("person_id"))
            if qid:
                seats.setdefault((qid, council), []).append(row)
    print(f"OpenParlData: seat rows for {len(seats)} (Q-ID, council) pair(s)")
    return seats


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config/parliament.yaml")
    parser.add_argument(
        "--body-key", default="CHE", help="OpenParlData body for the Federal Assembly."
    )
    parser.add_argument(
        "--no-openparldata",
        action="store_true",
        help="Skip the independent source. Section C is then INCONCLUSIVE by "
        "construction — a single source cannot corroborate itself.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)

    print("=" * 70)
    print("Step 8: may the departed members' P582 be applied in bulk?")
    print("=" * 70)

    parliament = ParliamentClient(session=http.session, language=config.language)
    wikidata = WikidataClient(http)

    opd_client = None
    if not args.no_openparldata:
        try:
            import swissparlpy as spp

            opd_client = spp.SwissParlClient(
                session=http.session, backend="openparldata"
            )
        except Exception as exc:  # a missing second source is a finding
            print(f"  ! OpenParlData: {exc}")
            print("  -> section C cannot compare and will say so.")

    try:
        departures = collect(config, parliament, wikidata, opd_client, args.body_key)
    except Exception as exc:
        print(f"  ! {exc}")
        return 1
    print()

    verdicts = [
        _section("A. Reach: can these people be found in the source?",
                 classify_reach(departures)),
        _section("B. Identity: is it the same person?",
                 classify_identity(departures)),
        _section("C. Agreement: do two sources give the same leaving date?",
                 classify_leaving_dates(departures)),
        _section("D. Which statement would the P582 close?",
                 classify_statement_ambiguity(departures)),
    ]

    result = overall(verdicts)
    print("=" * 70)
    print(f"Reach                 : {verdicts[0]}")
    print(f"Identity              : {verdicts[1]}")
    print(f"Leaving dates         : {verdicts[2]}")
    print(f"Statement ambiguity   : {verdicts[3]}")
    print(f"May P582 be bulk-applied for departed members? {result}")
    print("=" * 70)
    if result != CONFIRMED:
        print(
            "Leave the two gates in diff._departed_suggestion alone (no "
            "qid_source, no 'position' in the payload). The suggestions stay in "
            "the report with their dates, for a human to apply."
        )
    else:
        print(
            "Every section is clean. That is the evidence for removing those "
            "gates — do it deliberately, and paste one line into "
            "QuickStatements by hand first (README step 5)."
        )
    # Reports rather than gates, like the other three: what it decides is a bulk
    # apply, and the suggestions it measures are report-only today. Non-zero
    # only when nothing could be measured at all.
    return 0 if result != INCONCLUSIVE else 1


if __name__ == "__main__":
    sys.exit(main())
