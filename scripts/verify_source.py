"""Check that the parlament.ch source can actually be read as the tool assumes.

Three probes, all needing live network:

**A. Can we fetch the sitting members at all?** The first real run
(2026-07-29) read **zero** members and, because every Wikidata seat holder then
fell through the diff's second pass, published 2,234 confident "this member has
left" suggestions. Nothing reached QuickStatements — the P1307 safety rule held
— but the report was wrong and said so with total confidence. This probe walks
the same filters the pipeline uses, one at a time, and prints how many rows
survive each, so a silent zero can be attributed to the filter that caused it.
Run on 2026-07-29 it named the council filter: the config asked for "N"/"S",
the service says "NR"/"SR".

**A2. Are the dates on those rows believable?** Arriving is not the same as
being right. This one reports the ``DateJoining`` spread per chamber and
flags the two shapes that would poison mechanical edits — starts that are all
1 January (per-year segments rather than tenures) and any sitting member
carrying a leaving date (the null-date sentinel leaking through).

**B. Does P1307 hold ``MemberCouncil.PersonNumber``?** Verification step 1 from
the README, **CONFIRMED** on 2026-07-29: Guy Parmelin's row reads
``PersonNumber=1108, PersonIdCode=2621`` against Wikidata's P1307 = 1108, so it
is ``PersonNumber`` and not ``PersonIdCode``. Kept as a regression probe. Both
``MemberCouncil`` and ``MemberCouncilHistory`` are searched — the run showed a
former member appears in both.

Run it locally::

    uv run python scripts/verify_source.py

or dispatch the "Verify assumptions" workflow, which runs exactly this.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Make the ``src`` layout importable without installing the package.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wd_parliament.config import load_config  # noqa: E402
from wd_parliament.http_client import HttpClient  # noqa: E402
from wd_parliament.parliament import (  # noqa: E402
    HISTORIC_MEMBER_TABLE,
    MEMBER_TABLE,
    ParliamentClient,
    members_from_rows,
)

# The three things this probe can conclude.
CONFIRMED = "CONFIRMED"
CONTRADICTED = "CONTRADICTED"
INCONCLUSIVE = "INCONCLUSIVE"

# Fields worth printing for every row found.
_SHOWN = (
    "PersonNumber",
    "PersonIdCode",
    "FirstName",
    "LastName",
    "Active",
    "CouncilAbbreviation",
    "DateJoining",
    "DateLeaving",
)


def classify(
    rows: Sequence[Dict[str, Any]],
    expected: int,
    errors: Sequence[str] = (),
) -> Tuple[str, str]:
    """What do these rows say about the P1307 assumption? Pure.

    Kept separate from the fetching so the decision can be unit-tested, in
    keeping with the rest of the project. ``errors`` carries any table that
    could not be read at all, so an unreachable service is not reported as
    "person not found" — those need entirely different follow-up.
    """
    if not rows:
        if errors:
            return (
                INCONCLUSIVE,
                "No table could be read: "
                + "; ".join(errors)
                + ". Nothing was tested — this is a connectivity or service "
                "problem, not a finding about P1307.",
            )
        return (
            INCONCLUSIVE,
            "The tables answered but hold no such person, so nothing was "
            "tested. Check the surname, or try a currently sitting member.",
        )

    person_numbers = {r.get("PersonNumber") for r in rows}
    id_codes = {r.get("PersonIdCode") for r in rows}

    if expected in person_numbers:
        return (
            CONFIRMED,
            f"PersonNumber == {expected} == P1307. The identifier join is "
            "sound; resolve.match_by_identifier is comparing the right fields.",
        )
    if expected in id_codes:
        return (
            CONTRADICTED,
            f"PersonIdCode == {expected}, but PersonNumber is "
            f"{sorted(n for n in person_numbers if n is not None)}. P1307 maps "
            "to PersonIdCode, NOT PersonNumber — parliament.member_from_row "
            "and models.Member.person_number must be switched to it.",
        )
    return (
        CONTRADICTED,
        f"Neither PersonNumber {sorted(n for n in person_numbers if n is not None)} "
        f"nor PersonIdCode {sorted(c for c in id_codes if c is not None)} equals "
        f"{expected}. The identifier join does not work as designed; fall back "
        "to name + birth date and revisit the design before emitting anything.",
    )


def diagnose_member_fetch(
    raw: Sequence[Dict[str, Any]],
    councils: Sequence[str],
) -> Tuple[bool, List[str]]:
    """Attribute a member count to the filter stage that produced it. Pure.

    Walks the same narrowing the pipeline does — every row, then ``Active``,
    then the configured ``CouncilAbbreviation`` values — and reports the count
    after each, plus the distinct values actually present. When the final count
    is zero, the stage where it fell to zero *is* the diagnosis.
    """
    lines: List[str] = []
    lines.append(f"rows returned by OData:            {len(raw)}")

    if not raw:
        lines.append("")
        lines.append(
            "  -> OData itself returned nothing. The Language or Active filter "
            "is being rejected or matching no rows; try dropping Active and "
            "re-running to see whether the boolean is the problem."
        )
        return False, lines

    actives = sorted({repr(r.get("Active")) for r in raw})
    abbrs = sorted({repr(r.get("CouncilAbbreviation")) for r in raw})
    names = sorted({repr(r.get("CouncilName")) for r in raw})[:4]

    active_rows = [r for r in raw if r.get("Active") in (True, "true", "True", 1)]
    mapped = members_from_rows(raw, councils=None, active_only=True)
    filtered = members_from_rows(raw, councils=councils, active_only=True)

    lines.append(f"rows with Active true:            {len(active_rows)}")
    lines.append(f"mapped to Member (any council):   {len(mapped)}")
    lines.append(f"after the council filter {list(councils)}:  {len(filtered)}")
    lines.append("")
    lines.append(f"distinct Active values:               {', '.join(actives)}")
    lines.append(f"distinct CouncilAbbreviation values:  {', '.join(abbrs)}")
    lines.append(f"sample CouncilName values:            {', '.join(names)}")

    if filtered:
        lines.append("")
        lines.append(f"  -> OK: {len(filtered)} sitting members would reach the diff.")
        return True, lines

    lines.append("")
    if mapped and not filtered:
        lines.append(
            "  -> The COUNCIL FILTER is what empties the list. The config's "
            f"'council:' keys {list(councils)} do not match the "
            "CouncilAbbreviation values above — set them to the real values in "
            "config/parliament.yaml."
        )
    elif active_rows and not mapped:
        lines.append(
            "  -> Rows exist but none survive mapping: they are missing "
            "PersonNumber, which parliament.member_from_row requires."
        )
    else:
        lines.append(
            "  -> No row carries a true Active flag. Either the OData filter is "
            "not being applied and this is historic data, or Active is encoded "
            "differently than the mapping expects."
        )
    return False, lines


def describe_tenure_dates(
    raw: Sequence[Dict[str, Any]],
    councils: Sequence[str],
    sample: int = 3,
) -> List[str]:
    """What the surviving rows say about ``DateJoining``. Pure.

    Probe A establishes that members *arrive*; this says whether their dates
    can be believed. Two things it is looking for:

    **A shared 1 January start.** Guy Parmelin's sitting row gives
    ``DateJoining = 2026-01-01`` — the current year, not his 2016 start — which
    is what a per-year mandate segment looks like rather than a tenure start.
    If that holds for the National Council too, then P580 is the start of the
    reporting year for everyone, and ``ADD_MEMBERSHIP`` / ``ADD_START_DATE``
    (both mechanical) would write it to Wikidata as the date they took office.

    **Members with no start at all.** ``period_overlap`` deliberately assigns
    such a member no periods, so a large count here silently costs the P2937
    qualifiers for those people.
    """
    lines: List[str] = []
    members = members_from_rows(raw, councils=councils, active_only=True)
    if not members:
        return ["(no members survived the filter — nothing to say about dates)"]

    by_council: Dict[str, List[Any]] = {}
    for member in members:
        by_council.setdefault(member.council, []).append(member)

    undated = 0
    for council in sorted(by_council):
        group = by_council[council]
        starts = [m.date_joining for m in group if m.date_joining]
        undated += len(group) - len(starts)
        lines.append(f"{council}: {len(group)} sitting member(s)")
        for member in group[:sample]:
            lines.append(
                f"    {member.person_number} {member.sort_name}: "
                f"joined {member.date_joining}, left {member.date_leaving}"
            )
        distinct = sorted({d for d in starts})
        lines.append(f"    distinct DateJoining values: {len(distinct)}")
        if distinct:
            lines.append(
                f"    earliest {distinct[0]}, latest {distinct[-1]}"
            )
        # The signal that these are reporting-year segments, not tenures.
        if distinct and all(d.month == 1 and d.day == 1 for d in distinct):
            lines.append(
                "    -> WARNING: every start is a 1 January. These look like "
                "per-year mandate segments, not tenure starts. P580 emitted "
                "from them would be wrong; check MemberCouncilHistory for the "
                "real start before trusting ADD_MEMBERSHIP or ADD_START_DATE."
            )

    if undated:
        lines.append("")
        lines.append(
            f"-> {undated} sitting member(s) have no DateJoining, so they are "
            "assigned no legislative periods and get no P2937 qualifiers."
        )

    # The sentinel must never survive as a date; see parliament.NULL_DATE.
    left = [m for m in members if m.date_leaving is not None]
    lines.append("")
    if left:
        lines.append(
            f"-> WARNING: {len(left)} member(s) are Active yet carry a leaving "
            f"date (e.g. {left[0].person_number} left {left[0].date_leaving}). "
            "If that date is 1753-01-01 the null-date sentinel is leaking "
            "through parliament._as_date again."
        )
    else:
        lines.append("-> OK: no sitting member carries a leaving date.")
    return lines


def fetch(
    client: ParliamentClient, table: str, last_name: str
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Rows for ``last_name`` in ``table``, plus the error if it could not be read."""
    try:
        return [dict(r) for r in client.client.get_data(table, LastName=last_name)], None
    except Exception as exc:  # an unreadable table is a finding, not a crash
        print(f"  ! {table}: {exc}")
        return [], f"{table}: {exc}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config/parliament.yaml")
    parser.add_argument(
        "--last-name", default="Parmelin", help="Surname to look up on parlament.ch."
    )
    parser.add_argument(
        "--expect",
        type=int,
        default=1108,
        help="The P1307 value Wikidata holds for that person.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)
    client = ParliamentClient(session=http.session, language=config.language)

    # --- A. can we read the sitting members at all? -------------------------
    print("=" * 70)
    print("A. Fetching sitting members")
    print("=" * 70)
    raw: List[Dict[str, Any]] = []
    fetch_error: Optional[str] = None
    try:
        raw = [
            dict(r)
            for r in client.client.get_data(
                MEMBER_TABLE, Language=config.language.upper(), Active=True
            )
        ]
    except Exception as exc:
        fetch_error = str(exc)
        print(f"  ! {MEMBER_TABLE}: {exc}")

    if fetch_error:
        members_ok = False
        print("\n  -> Could not read the table at all; this is connectivity or "
              "a rejected filter, not a data finding.")
    else:
        members_ok, lines = diagnose_member_fetch(raw, config.councils)
        for line in lines:
            print("  " + line if line else "")

        if members_ok:
            print()
            print("-" * 70)
            print("A2. Are the tenure dates believable?")
            print("-" * 70)
            for line in describe_tenure_dates(raw, config.councils):
                print("  " + line if line else "")

    # --- B. does P1307 hold PersonNumber? -----------------------------------
    print()
    print("=" * 70)
    print("B. P1307 == MemberCouncil.PersonNumber")
    print("=" * 70)
    print(f"Looking up '{args.last_name}'; Wikidata's P1307 for them is {args.expect}\n")

    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for table in (MEMBER_TABLE, HISTORIC_MEMBER_TABLE):
        found, error = fetch(client, table, args.last_name)
        print(f"{table}: {len(found)} row(s)")
        for row in found:
            print("  " + ", ".join(f"{k}={row.get(k)!r}" for k in _SHOWN))
        rows.extend(found)
        if error:
            errors.append(error)
    print()

    verdict, explanation = classify(rows, args.expect, errors)
    print(f"{verdict}: {explanation}")

    print()
    print("=" * 70)
    print(f"A. sitting members readable : {'PASS' if members_ok else 'FAIL'}")
    print(f"B. P1307 == PersonNumber   : {verdict}")
    print("=" * 70)
    return 0 if (members_ok and verdict == CONFIRMED) else 1


if __name__ == "__main__":
    sys.exit(main())
