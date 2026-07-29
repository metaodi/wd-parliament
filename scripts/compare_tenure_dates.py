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

Reading the result
------------------
``CONFIRMED``
    The two agree. ``DateJoining`` is a tenure start and P580 may be emitted
    from it.
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
from wd_parliament.parliament import ParliamentClient, _as_date  # noqa: E402
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

    return (
        CONTRADICTED,
        f"{len(differ)} of {len(comparable)} members disagree, {len(year_starts)} "
        "of them with DateJoining on a 1 January. The sources do not tell the "
        "same story, so P580 from DateJoining cannot be trusted in bulk — but "
        "the disagreement is not purely the reporting-year shape, so read the "
        "rows above before deciding which source is right.",
        lines,
    )


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
    pairs: List[Pair] = []
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

    verdict, detail, lines = classify_start_dates(pairs, unjoined)
    for line in lines:
        print("  " + line if line else "")
    print()
    print(f"{verdict}: {detail}")
    print()
    print("=" * 70)
    print(f"Step 0c: {verdict}")
    print("=" * 70)
    # Reports rather than gates: a disagreement blocks mechanical P580, not the
    # generation of reports. Non-zero only when nothing could be compared.
    return 0 if verdict != INCONCLUSIVE else 1


if __name__ == "__main__":
    sys.exit(main())
