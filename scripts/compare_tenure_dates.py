"""Answer README step 0c: is ``MemberCouncil.DateJoining`` a tenure start?

The question, and why it matters
--------------------------------
``diff`` emits P580 from ``MemberCouncil.DateJoining``, and both kinds that do
so — ``ADD_MEMBERSHIP`` and ``ADD_START_DATE`` — are **mechanical**, so the
value reaches Wikidata through QuickStatements without a human reading it. If
``DateJoining`` is not when the member took office, that is a wrong date written
at scale.

There is real doubt. Guy Parmelin's sitting row gives ``DateJoining =
2026-01-01`` — the current year, not his 2016 start — and his
``MemberCouncilHistory`` rows are broken up by year. ``verify_source.py``
section **A2** flags the shape of that (every start a 1 January) but cannot say
what the right date *is*, because it has only the one source.

This script has two. OpenParlData carries the same seats as per-term
``memberships`` rows with a ``begin_date``, measured in
``verify_openparldata.py`` section B: 4,398 for the National Council and 1,220
for the Council of States, every one dated, running back to 1853 and lining up
with legislature boundaries. Comparing the two answers 0c directly — and says
which source to trust if they disagree.

How the join works
------------------
There is no shared key between parlament.ch and OpenParlData, so the join goes
through Wikidata, using what is already confirmed:

    MemberCouncil.PersonNumber ──P1307──▶ Q-ID ◀──wikidata_id── OpenParlData

Both ends are established: P1307 == ``PersonNumber`` (README step 1) and 3,685
of 3,686 federal members carry a ``wikidata_id`` (step 6). A member who cannot
be joined is **counted and skipped**, never guessed at.

What it prints
--------------
Two comparisons off the same join, because they answer different questions:

1. **the raw field** — ``MemberCouncil.DateJoining`` against OpenParlData's
   latest term. This is step 0c as originally posed, and its answer (11 of 244
   sitting members disagree, all with ``DateJoining`` *later*) is what moved
   P580 off that field. Kept as the regression check for the finding.
2. **what ships** — :attr:`models.Member.start_date`, the start of the current
   continuous run of ``MemberCouncilHistory`` segments, against the same
   chaining applied to OpenParlData's per-term rows. This is the verdict to
   read before applying QuickStatements: it measures the date the tool would
   actually write, which the first comparison does not.

Reading the result
------------------
``CONFIRMED``
    The two agree. For comparison 2 that means P580 may be applied in bulk.
``CONTRADICTED``
    They disagree. The detail says whether ``DateJoining`` looks like a
    reporting-year start (a 1 January where OpenParlData has a real date),
    which is the specific failure 0c is about. Do not let
    ``ADD_MEMBERSHIP`` / ``ADD_START_DATE`` reach QuickStatements.
``INCONCLUSIVE``
    Too few members could be joined to conclude anything. Not a finding about
    the dates.

Run it locally::

    uv run python scripts/compare_tenure_dates.py

or dispatch the "Verify assumptions" workflow, which runs it alongside the other
probes. Like the OpenParlData evaluation it reports without gating the job: a
disagreement here blocks *mechanical P580*, not the generation of reports.
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
    MAX_SEGMENT_GAP_DAYS,
    ParliamentClient,
    _as_date,
    apply_tenure_starts,
)
from wd_parliament.wikidata import WikidataClient  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_openparldata import (  # noqa: E402
    BEGIN_FIELDS,
    CHAMBER_NAMES,
    CONFIRMED,
    CONTRADICTED,
    END_FIELDS,
    INCONCLUSIVE,
    _present,
    chamber_of,
    fetch,
)

# Below this many joined members the comparison says nothing either way.
MIN_JOINED = 10


@dataclass
class Pair:
    """One member seen from both sources."""

    person_number: int
    name: str
    qid: str
    council: str
    odata_start: Optional[date]
    opd_start: Optional[date]

    @property
    def agrees(self) -> bool:
        return (
            self.odata_start is not None
            and self.opd_start is not None
            and self.odata_start == self.opd_start
        )

    @property
    def odata_is_year_start(self) -> bool:
        """Does the OData date look like the start of a reporting year?"""
        return (
            self.odata_start is not None
            and self.odata_start.month == 1
            and self.odata_start.day == 1
        )


def classify_start_dates(
    pairs: Sequence[Pair], unjoined: int = 0
) -> Tuple[str, str, List[str]]:
    """Do the two sources agree on when sitting members took office? Pure.

    The whole of step 0c. ``unjoined`` is reported but does not weigh on the
    verdict: a member who could not be matched says nothing about the dates of
    the ones who could.
    """
    lines: List[str] = []
    comparable = [p for p in pairs if p.odata_start and p.opd_start]

    lines.append(f"members joined through Wikidata:  {len(pairs)}")
    lines.append(f"  of which both sources dated:    {len(comparable)}")
    lines.append(f"  not joined (skipped, not guessed): {unjoined}")

    if len(comparable) < MIN_JOINED:
        return (
            INCONCLUSIVE,
            f"Only {len(comparable)} member(s) could be compared, fewer than the "
            f"{MIN_JOINED} this needs. That is a finding about the join, not "
            "about the dates — check the P1307 and wikidata_id coverage first.",
            lines,
        )

    agree = [p for p in comparable if p.agrees]
    differ = [p for p in comparable if not p.agrees]
    year_starts = [p for p in differ if p.odata_is_year_start]

    share = 100.0 * len(agree) / len(comparable)
    lines.append("")
    lines.append(f"agree exactly:                    {len(agree)} ({share:.1f}%)")
    lines.append(f"disagree:                         {len(differ)}")
    lines.append(f"  of those, OData is a 1 January: {len(year_starts)}")
    lines.append("")
    for pair in differ[:12]:
        marker = "  <- 1 Jan" if pair.odata_is_year_start else ""
        lines.append(
            f"  {pair.person_number} {pair.name} ({pair.council}): "
            f"OData {pair.odata_start}, OpenParlData {pair.opd_start}{marker}"
        )

    if not differ:
        return (
            CONFIRMED,
            f"All {len(comparable)} comparable members agree. DateJoining is a "
            "tenure start, so P580 emitted from it is right and "
            "ADD_MEMBERSHIP / ADD_START_DATE are safe to apply. Step 0c is "
            "answered.",
            lines,
        )

    if len(year_starts) == len(differ):
        return (
            CONTRADICTED,
            f"All {len(differ)} disagreements have DateJoining on a 1 January "
            "against a real date in OpenParlData. That is exactly the failure "
            "step 0c predicted: DateJoining is a reporting-year start, not when "
            "the member took office. Do not let ADD_MEMBERSHIP or "
            "ADD_START_DATE reach QuickStatements; take P580 from "
            "OpenParlData's begin_date instead.",
            lines,
        )

    later = [p for p in differ if p.odata_start > p.opd_start]  # type: ignore[operator]
    if not year_starts and len(later) == len(differ):
        shared = {p.opd_start for p in differ}
        shared_note = (
            f" All of them sit against the same OpenParlData date, {shared.pop()}, "
            "which is a legislature start."
            if len(shared) == 1
            else ""
        )
        return (
            CONTRADICTED,
            f"{len(differ)} of {len(comparable)} members disagree, and in **every** "
            "case DateJoining is *later* than OpenParlData's start — none is a 1 "
            f"January.{shared_note} So this is not the reporting-year failure step "
            "0c was about. Two readings remain, and they call for opposite "
            "actions: either DateJoining is the member's own swearing-in (more "
            "precise than a term boundary, and the better P580), or it is the "
            "start of a later mandate *segment* for someone who has sat since "
            "before it (wrong for P580). Settle it by reading one of these "
            "members' MemberCouncilHistory rows — dispatch 'Verify assumptions' "
            "with last_name set to one of the surnames above and read section B. "
            "Do not bulk-apply until then.",
            lines,
        )

    return (
        CONTRADICTED,
        f"{len(differ)} of {len(comparable)} members disagree, {len(year_starts)} "
        "of them with DateJoining on a 1 January and "
        f"{len(later)} with DateJoining later than OpenParlData's. The sources do "
        "not tell the same story and the disagreement has no single shape, so "
        "P580 from DateJoining cannot be trusted in bulk — read the rows above "
        "before deciding which source is right.",
        lines,
    )


def classify_tenure_starts(
    pairs: Sequence[Pair], unjoined: int = 0
) -> Tuple[str, str, List[str]]:
    """Does the *shipped* P580 agree with OpenParlData? Pure.

    The second comparison, and the one that describes what the tool actually
    writes. :func:`classify_start_dates` measures the raw ``DateJoining``, which
    is how step 0c was posed; since that step was answered, ``diff`` takes P580
    from :attr:`models.Member.start_date` — the start of the current continuous
    run of ``MemberCouncilHistory`` segments — and nothing measured *that*.

    Both sides are chained by the same rule (:func:`chained_start` here,
    :func:`parliament.tenure_start` there), so the comparison is like for like:
    a member re-elected without a break should come out as one span from both
    sources. A disagreement is what blocks a bulk apply of ADD_MEMBERSHIP /
    ADD_START_DATE — this verdict, not the 0c one, is the one to read before
    running QuickStatements.
    """
    lines: List[str] = []
    comparable = [p for p in pairs if p.odata_start and p.opd_start]

    lines.append(f"members joined through Wikidata:  {len(pairs)}")
    lines.append(f"  of which both sources dated:    {len(comparable)}")
    lines.append(f"  not joined (skipped, not guessed): {unjoined}")

    if len(comparable) < MIN_JOINED:
        return (
            INCONCLUSIVE,
            f"Only {len(comparable)} member(s) could be compared, fewer than the "
            f"{MIN_JOINED} this needs. That is a finding about the join, not "
            "about the dates.",
            lines,
        )

    agree = [p for p in comparable if p.agrees]
    differ = [p for p in comparable if not p.agrees]
    share = 100.0 * len(agree) / len(comparable)
    lines.append("")
    lines.append(f"agree exactly:                    {len(agree)} ({share:.1f}%)")
    lines.append(f"disagree:                         {len(differ)}")
    lines.append("")
    for pair in differ[:12]:
        lines.append(
            f"  {pair.person_number} {pair.name} ({pair.council}): "
            f"tenure start {pair.odata_start}, OpenParlData {pair.opd_start}"
        )

    if not differ:
        return (
            CONFIRMED,
            f"All {len(comparable)} comparable members agree. The tenure start "
            "derived from MemberCouncilHistory is the same date OpenParlData "
            "gives for the run the member is currently serving, so P580 may be "
            "applied in bulk.",
            lines,
        )

    earlier = [p for p in differ if p.odata_start < p.opd_start]  # type: ignore[operator]
    later = [p for p in differ if p.odata_start > p.opd_start]  # type: ignore[operator]
    return (
        CONTRADICTED,
        f"{len(differ)} of {len(comparable)} members disagree — {len(earlier)} "
        f"with an earlier tenure start than OpenParlData and {len(later)} with a "
        "later one. Either the segment chaining is joining spans it should not, "
        "or the two sources genuinely disagree about a break in service. Read "
        "the rows above before letting ADD_MEMBERSHIP or ADD_START_DATE reach "
        "QuickStatements.",
        lines,
    )


def chained_start(rows: Sequence[Dict[str, Any]]) -> Optional[date]:
    """OpenParlData's start of the **current continuous run**. Pure.

    The counterpart to :func:`current_start`, which returns the start of the
    latest *term*. OpenParlData rows are per legislature, so a member who has
    sat since 2015 has three of them; the tool's P580 spans all three, and
    comparing it against the newest row alone would report every long-serving
    member as a disagreement.

    Chains adjacent rows by the same rule
    :data:`parliament.MAX_SEGMENT_GAP_DAYS` applies to
    ``MemberCouncilHistory`` — a legislature boundary is a one-day join — and
    stops at a real break, so someone who left and returned gets the return.
    Column names are resolved from the rows for the reason recorded in
    ``verify_openparldata.classify_seat_memberships``.
    """
    if not rows:
        return None
    begin_field = _present(rows, BEGIN_FIELDS)
    end_field = _present(rows, END_FIELDS)
    if begin_field is None:
        return None

    spans: List[Tuple[date, Optional[date]]] = []
    for row in rows:
        start = _as_date(row.get(begin_field))
        if start is None:
            continue
        end = _as_date(row.get(end_field)) if end_field else None
        spans.append((start, end))
    if not spans:
        return None

    spans.sort(key=lambda s: s[0])
    start = spans[-1][0]
    for (earlier_start, earlier_end), (later_start, _) in zip(
        reversed(spans[:-1]), reversed(spans[1:])
    ):
        if earlier_end is None:
            # An open earlier row cannot be chained: no end means no gap to
            # measure, and overlapping rows are not a continuous run.
            break
        gap = (later_start - earlier_end).days
        if gap > MAX_SEGMENT_GAP_DAYS or gap < 0:
            break
        start = earlier_start
    return start


def current_start(rows: Sequence[Dict[str, Any]]) -> Optional[date]:
    """The start of the tenure a member is *currently* serving. Pure.

    Prefers an open-ended row — no ``end_date`` means the seat is still held —
    and falls back to the latest start when every row is closed. Column names
    are resolved from the rows rather than assumed, for the reason recorded in
    ``verify_openparldata.classify_seat_memberships``.
    """
    if not rows:
        return None
    begin_field = _present(rows, BEGIN_FIELDS)
    end_field = _present(rows, END_FIELDS)
    if begin_field is None:
        return None

    starts: List[date] = []
    open_starts: List[date] = []
    for row in rows:
        start = _as_date(row.get(begin_field))
        if start is None:
            continue
        starts.append(start)
        if end_field is None or not str(row.get(end_field) or "").strip():
            open_starts.append(start)
    if open_starts:
        return max(open_starts)
    return max(starts) if starts else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config/parliament.yaml")
    parser.add_argument(
        "--body-key", default="CHE", help="OpenParlData body for the Federal Assembly."
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)

    print("=" * 70)
    print("Step 0c: MemberCouncil.DateJoining vs OpenParlData begin_date")
    print("=" * 70)

    # --- 1. sitting members from parlament.ch -------------------------------
    parliament = ParliamentClient(session=http.session, language=config.language)
    try:
        members = parliament.get_members(councils=config.councils)
    except Exception as exc:
        print(f"  ! parlament.ch: {exc}")
        return 1
    print(f"parlament.ch: {len(members)} sitting member(s)")
    if not members:
        print("  -> no members, so nothing to compare. Fix the source read first.")
        return 1

    # The tenure start the pipeline actually emits as P580, which is not
    # DateJoining — see README step 0c. Fetched here so section 2 can measure
    # what ships rather than what the raw field says. A failure leaves
    # ``start_date`` falling back to ``date_joining``, exactly as ``app.process``
    # does, and section 2 then repeats section 1 rather than misreporting.
    corrected = 0
    try:
        segments = parliament.get_member_segments(councils=config.councils)
        corrected = apply_tenure_starts(members, segments)
        print(
            f"parlament.ch: tenure start read from MemberCouncilHistory for "
            f"{sum(1 for m in members if m.tenure_start)} member(s); "
            f"{corrected} had a later segment start in MemberCouncil"
        )
    except Exception as exc:
        print(f"  ! MemberCouncilHistory: {exc}")
        print("  -> section 2 falls back to DateJoining, as the pipeline does.")

    # --- 2. the P1307 index, to turn PersonNumber into a Q-ID ---------------
    wikidata = WikidataClient(http)
    try:
        index = wikidata.get_identifier_index(config.language.lower())
    except Exception as exc:
        print(f"  ! WDQS: {exc}")
        return 1
    qid_by_parliament_id: Dict[str, str] = {}
    for qid, person in index.items():
        if person.parliament_id:
            qid_by_parliament_id.setdefault(person.parliament_id, qid)
    print(f"Wikidata: {len(qid_by_parliament_id)} P1307 value(s) mapped to a Q-ID")

    # --- 3. the seats from OpenParlData, keyed by Q-ID ----------------------
    import swissparlpy as spp

    opd = spp.SwissParlClient(session=http.session, backend="openparldata")
    people, _ = fetch(opd, "persons", body_key=args.body_key)
    qid_by_person: Dict[Any, str] = {
        r.get("id"): str(r["wikidata_id"]).strip()
        for r in people
        if str(r.get("wikidata_id") or "").strip()
    }
    print(f"OpenParlData: {len(qid_by_person)} federal person(s) carry a wikidata_id")

    groups, _ = fetch(opd, "groups", body_key=args.body_key)
    chambers = {}
    for row in groups:
        council = chamber_of(row)
        if council and council not in chambers:
            chambers[council] = row
    print(f"OpenParlData: chambers {', '.join(sorted(chambers)) or 'NONE'}")
    if not chambers:
        print("  -> no chamber group found, so there is nothing to compare against.")
        return 1

    seats_by_qid: Dict[str, List[Dict[str, Any]]] = {}
    for council, group in chambers.items():
        rows, _ = fetch(opd, "memberships", group_id=group.get("id"))
        for row in rows:
            qid = qid_by_person.get(row.get("person_id"))
            if qid:
                seats_by_qid.setdefault(qid, []).append(row)
    print(f"OpenParlData: seat rows for {len(seats_by_qid)} Q-ID(s)")
    print()

    # --- 4. compare -------------------------------------------------------
    # Two comparisons off the same join, because they answer different
    # questions. The first is step 0c as posed: is the *raw* DateJoining a
    # tenure start? The second is the one to read before applying anything: does
    # the date the pipeline actually emits agree with the other source?
    pairs: List[Pair] = []
    tenure_pairs: List[Pair] = []
    unjoined = 0
    for member in members:
        qid = qid_by_parliament_id.get(str(member.person_number))
        rows = seats_by_qid.get(qid or "")
        if not qid or not rows:
            unjoined += 1
            continue
        pairs.append(
            Pair(
                person_number=member.person_number,
                name=member.sort_name,
                qid=qid,
                council=member.council,
                odata_start=member.date_joining,
                opd_start=current_start(rows),
            )
        )
        tenure_pairs.append(
            Pair(
                person_number=member.person_number,
                name=member.sort_name,
                qid=qid,
                council=member.council,
                odata_start=member.start_date,
                opd_start=chained_start(rows),
            )
        )

    print("-" * 70)
    print("1. The raw field: MemberCouncil.DateJoining vs the latest term")
    print("-" * 70)
    verdict, detail, lines = classify_start_dates(pairs, unjoined)
    for line in lines:
        print("  " + line if line else "")
    print()
    print(f"{verdict}: {detail}")

    print()
    print("-" * 70)
    print("2. What ships: the tenure start vs OpenParlData's continuous run")
    print("-" * 70)
    tenure_verdict, tenure_detail, tenure_lines = classify_tenure_starts(
        tenure_pairs, unjoined
    )
    for line in tenure_lines:
        print("  " + line if line else "")
    print()
    print(f"{tenure_verdict}: {tenure_detail}")

    print()
    print("=" * 70)
    print(f"Step 0c (raw DateJoining) : {verdict}")
    print(f"P580 as emitted (tenure)  : {tenure_verdict}")
    print("=" * 70)
    print(
        "The second verdict is the one that says whether ADD_MEMBERSHIP and "
        "ADD_START_DATE may be applied in bulk. The first records why P580 is "
        "not taken from DateJoining any more."
    )
    # Reports rather than gates: a disagreement blocks mechanical P580, not the
    # generation of reports. Non-zero only when nothing could be compared.
    return 0 if INCONCLUSIVE not in (verdict, tenure_verdict) else 1


if __name__ == "__main__":
    sys.exit(main())
