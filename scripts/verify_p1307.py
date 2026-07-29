"""Probe whether Wikidata's P1307 really holds ``MemberCouncil.PersonNumber``.

This is verification step 1 from the README — the last assumption the join
strategy rests on that has not been checked against live data. Wikidata says
Guy Parmelin ([[Q121160]]) has P1307 = 1108, and his parlament.ch biography
lives at ``/biografie/guy-parmelin/1108``; what nobody has done is fetch his
``MemberCouncil`` row and read ``PersonNumber`` back off it.

Run it locally::

    uv run python scripts/verify_p1307.py

or dispatch the "Verify assumptions" workflow, which runs exactly this.

Both ``MemberCouncil`` and ``MemberCouncilHistory`` are searched. Parmelin left
the National Council in 2015, and the two tables share a shape, so which one
holds a former member is itself something this probe establishes.
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

    print(f"Looking up '{args.last_name}' on parlament.ch")
    print(f"Wikidata's P1307 for them is {args.expect}\n")

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
    return 0 if verdict == CONFIRMED else 1


if __name__ == "__main__":
    sys.exit(main())
