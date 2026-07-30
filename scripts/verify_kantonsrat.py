"""Measure whether this tool can be pointed at the **Kantonsrat Zürich**.

README step 7. A probe, not a pipeline: it asserts nothing about the federal
run, writes nothing, and its answer is a finding about a design option rather
than a fault. Exit 0 whenever the APIs answered at all.

Why a probe first
-----------------
The federal pipeline is the reason this file exists rather than a half-written
cantonal adapter. The run of 2026-07-29 fetched **zero** sitting members and
published 2,234 wrong "this member has left" suggestions, because the config
filtered on ``CouncilAbbreviation`` values ("N" / "S") that the live service
does not use. Every assumption below is the cantonal equivalent of that one,
and each is cheaper to measure than to debug after a bad run.

What the extension needs, and what each section answers
-------------------------------------------------------
The reconciliation logic is already parliament-agnostic — ``period_overlap``,
``diff`` and ``quickstatements`` name no federal table and no federal property.
What is federal is the *source adapter* and the *identifier join*, so those are
what this measures:

**A. Where does the Kantonsrat live in OpenParlData?** The API covers the
federation, all 26 cantons and 50+ cities. A **body** is the level of
parliament (``CHE`` is the Federal Assembly), so Zurich should be its own body
— reportedly ``ZH`` — and the chamber itself a **group** under it, exactly as
the National Council is group 1663 under ``CHE``.

**B. Do its seat memberships carry dates, and are there 180 of them?** The
decisive structural question, and it has a built-in check the federal probe
also relied on: the National Council's open-ended memberships numbered exactly
200 and the Council of States' exactly 46, which is what turned "the column is
populated" into "the data is current *and* correct". The Kantonsrat has **180**
seats, so the same test applies. Without a start date there is no P580 and no
period overlap, and the seat cannot source P39 however well it is identified.

**C. Are these people reachable on Wikidata, and by what?** The one that
decides whether the cantonal tool may emit QuickStatements at all.
``quickstatements.is_mechanical`` gates on ``QID_FROM_IDENTIFIER``, which means
*Wikidata itself* asserted the identifier that established the match. Federally
that is P1307 against ``PersonNumber``. **The Kantonsrat has no P1307**, so
there are three candidates and they are not equivalent:

- **P14527 (OpenParlData ID)**, which *is* Wikidata-asserted and so slots into
  the existing gate unchanged. ``verify_openparldata.py`` found it adds nobody
  federally — 0 National Councillors carry it without P1307 — but that is a
  finding about the *federal* overlap. Cantonally it may be the only
  Wikidata-asserted identifier that exists, so the property correctly dismissed
  there could be the whole join here. That inversion is why this section exists.
- **OpenParlData's own ``wikidata_id`` field**, which is a *third party
  asserting a Q-ID about Wikidata* — a different class of claim wearing the same
  provenance flag. It would need its own ``QID_FROM_*`` constant and its own
  decision in ``is_mechanical``, not the existing gate by default.
- **name matching only**, which ``is_mechanical`` already refuses. That is a
  perfectly good first release: report-only, ``quickstatements: false``, exactly
  how the federal side began.

**D. Is the position Q-ID the right item?** ``config/parliament.yaml``'s
``position`` is the single most damaging value to get wrong: it is the main
value of every emitted P39. The candidate here (:data:`KANTONSRAT_POSITION`) was
**not** verified against a live store when this file was written, so the probe
checks it the way B checks the memberships — by counting. An item held by
roughly 180 people with an open statement is the seat; an item held by nobody,
or by thousands, is not.

**E. What would supply P768?** Federally the electoral district is the canton
and ``cantons:`` has 26 entries. Zurich elects its 180 members from **18
Wahlkreise**, so that map becomes a per-body district map with different keys —
and something in the source has to carry the key. This reports which field
could, rather than assuming one.

What it deliberately does not do
--------------------------------
No adapter, no config, no Q-ID maps. Those are worth writing once the answers
are in and not before: B decides whether an adapter is possible at all, C
decides whether it may write, and D decides what it would write *to*.

The API-querying discipline, and the two mistakes that paid for it, are
inherited wholesale from ``verify_openparldata.py`` — which is also where the
column-resolution helpers below are imported from rather than copied:

- **column names are resolved from the rows, never assumed.** Reading a field
  that does not exist returns ``None`` for every row, which is indistinguishable
  from a populated column full of nulls and means the opposite thing. That is
  why a missing column is INCONCLUSIVE here and never CONTRADICTED.
- **a name must *equal* the chamber's, not contain it.** A substring match made
  "Präsidium des Nationalrates" — a committee of eight — read as the National
  Council. Zurich has the same trap twice over: "Büro des Kantonsrates" is not
  the Kantonsrat, and the **Regierungsrat** is not a legislature at all.

Run it locally::

    uv run python scripts/verify_kantonsrat.py

or dispatch the "Verify assumptions" workflow, which runs it alongside the
federal probes without letting its answer affect the job result.
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

# ...and this directory, so the shared probe helpers import when the script is
# run from elsewhere (``python scripts/verify_kantonsrat.py`` adds it, an
# import from the test suite does not).
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from wd_parliament.config import load_config  # noqa: E402
from wd_parliament.http_client import HttpClient  # noqa: E402
from wd_parliament.wikidata import WikidataClient  # noqa: E402

# Imported rather than copied. These encode findings that cost a wrong answer
# each — which columns the API actually uses, and that an absent column is not
# an empty one — and there must be exactly one copy of that knowledge.
from verify_openparldata import (  # noqa: E402
    BEGIN_FIELDS,
    CONFIRMED,
    CONTRADICTED,
    END_FIELDS,
    INCONCLUSIVE,
    OPENPARLDATA_ID,
    SWISS_PARLIAMENT_ID,
    _describe,
    _describe_membership,
    _name_values,
    _present,
    _text,
    classify_seat_memberships,
    count_only,
    date_columns,
    fetch,
    summarise_wikidata_ids,
)

# The body the Kantonsrat should live under. A body is the *level* of
# parliament, so the canton is one body covering its legislature — the same
# shape as ``CHE`` covering both federal chambers. Reported by the API's users
# as ``ZH``; if it is wrong, section A says so instead of measuring nothing,
# because the bodies table is listed in full first.
DEFAULT_BODY_KEY = "ZH"

# Seats in the Kantonsrat. The check this enables is the cantonal version of
# the federal probe's strongest signal: the National Council's open-ended seat
# memberships came to exactly 200 and the Council of States' to exactly 46,
# which is what distinguishes "this column is populated" from "this data is
# current". 180 is the number to expect here.
DEFAULT_SEATS = 180

# **Unverified.** Search points at this item for "member of the Cantonal
# Council of Zürich" (with Q2110002 as the council itself), but it was not
# checked against a live store when this file was written, and the phrasing
# that produced it was ambiguous between the *position* item and a *category*
# of members. Section D exists to settle it, and ``--position`` overrides it.
#
# Getting this wrong is not a small error: ``position`` is the main value of
# every P39 this tool would emit, so a category item here would write hundreds
# of statements saying somebody holds a Wikimedia category.
KANTONSRAT_POSITION = "Q19479543"

# How the chamber names itself, normalised. Matched by **equality** against
# each name field on a group, never by substring — see the module docstring.
#
# Zurich makes that discipline earn its keep twice. "Büro des Kantonsrates" and
# "Geschäftsleitung des Kantonsrates" are organs *of* the chamber, and the
# **Regierungsrat** is the cantonal executive — a seven-member government that
# shares five letters with the legislature and would be a catastrophic match.
# It is not in this set, and equality is what keeps it out.
KANTONSRAT_NAMES = (
    "kantonsrat",
    "kantonsrat zürich",
    "kantonsrat zurich",
    "kantonsrat des kantons zürich",
    "kantonsrat des kantons zurich",
    "zürcher kantonsrat",
    "zurcher kantonsrat",
    "grand conseil de zurich",
    "cantonal council of zürich",
    "cantonal council of zurich",
)

# What a row might call the electoral district. Reported, never assumed — the
# same rule as BEGIN_FIELDS, for the same reason.
DISTRICT_HINTS = ("district", "wahlkreis", "constituency", "electoral", "circle")


def is_kantonsrat(row: Dict[str, Any]) -> bool:
    """Is this group the Kantonsrat itself? Pure.

    Requires a name field to **equal** one of :data:`KANTONSRAT_NAMES`. The
    cost of being strict is that a chamber named with a suffix
    ("Kantonsrat, 2023-2027") would be missed, which is why
    :func:`kantonsrat_candidates` surfaces the near misses rather than letting
    them pass silently.
    """
    return any(name in KANTONSRAT_NAMES for name in _name_values(row))


def kantonsrat_candidates(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Groups whose name *mentions* the Kantonsrat without being it. Pure.

    Reporting only. These are precisely the rows :func:`is_kantonsrat` rejects,
    made visible so that "not found" can be told apart from "found under a name
    this function did not expect" — the distinction the federal probe got wrong
    before it printed its near misses.
    """
    out: List[Dict[str, Any]] = []
    for row in rows:
        if is_kantonsrat(row):
            continue
        if any("kantonsrat" in name for name in _name_values(row)):
            out.append(row)
    return out


def find_kantonsrat_group(
    rows: Sequence[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """The Kantonsrat among ``groups``, plus what to print. Pure.

    ``None`` is a real answer, but a loud one: the near misses are listed, and
    so is the count of groups searched, so an empty table and a chamber named
    unexpectedly cannot look alike.
    """
    lines: List[str] = []
    if not rows:
        return None, [
            "The 'groups' table came back empty for this body. Check the body "
            "key in the listing above before concluding anything — B, C and E "
            "below say nothing without it."
        ]

    found = next((row for row in rows if is_kantonsrat(row)), None)
    near = kantonsrat_candidates(rows)
    lines.append(f"{len(rows)} group(s) read for this body")
    lines.append("")
    if found is not None:
        lines.append(f"  Kantonsrat: {_describe(found)}")
    else:
        lines.append(
            f"  Kantonsrat: NOT FOUND by exact name "
            f"({len(near)} group(s) mention it)"
        )
    for row in near[:8]:
        lines.append(f"        near miss: {_describe(row)}")
    if found is None and not near:
        lines.append(
            "        -> nothing here even mentions a Kantonsrat, which points "
            "at the body key rather than at the name spellings."
        )
    return found, lines


def classify_seat_count(
    rows: Sequence[Dict[str, Any]], expected: int = DEFAULT_SEATS
) -> Tuple[str, str, List[str]]:
    """Do the open-ended seat memberships number ``expected``? Pure.

    The check that turns "this column is populated" into "this data is current
    and complete". A membership with no end date is a seat somebody holds
    *today*, so the open rows should come to the size of the chamber — the
    federal probe got exactly 200 and 46 and that is what made its answer
    trustworthy rather than merely non-empty.

    A count that is *close* is reported as CONTRADICTED rather than waved
    through, because the near misses are the interesting failures: 179 means a
    vacant seat or a member the source has not yet opened a row for, and 181
    means a row that should have been closed. Both change what the diff would
    claim, and both are things a human should look at before a run.
    """
    lines: List[str] = []
    if not rows:
        return (
            INCONCLUSIVE,
            "No memberships came back, so the seat count says nothing.",
            lines,
        )

    end_field = _present(rows, END_FIELDS)
    if end_field is None:
        return (
            INCONCLUSIVE,
            "None of the expected end-date columns exists on these rows, so "
            "open memberships cannot be told from closed ones. This says "
            "nothing about the chamber's size; add the real column to "
            f"END_FIELDS (tried: {', '.join(END_FIELDS)}) and re-run.",
            lines,
        )

    open_rows = [r for r in rows if not _text(r.get(end_field)).strip()]
    lines.append(f"read as end:            {end_field}")
    lines.append(f"memberships:            {len(rows)}")
    lines.append(f"open (no {end_field}):  {len(open_rows)}")
    lines.append(f"expected seats:         {expected}")

    if len(open_rows) == expected:
        return (
            CONFIRMED,
            f"{len(open_rows)} memberships are open, exactly the {expected} "
            "seats of the Kantonsrat. The data is current as well as dated, "
            "which is the same signal that made the federal read trustworthy "
            "(200 National Council and 46 Council of States seats).",
            lines,
        )
    if not open_rows:
        return (
            CONTRADICTED,
            f"Every one of the {len(rows)} memberships is closed, so this "
            "group holds nobody today. Either it is a historic record, or the "
            "group is not the sitting chamber — check what section A matched.",
            lines,
        )
    return (
        CONTRADICTED,
        f"{len(open_rows)} memberships are open against {expected} seats. "
        "Do not build the adapter on this until the difference is explained: a "
        "count near the chamber's size usually means vacancies or rows that "
        "were never closed, and a count far from it means the group is not the "
        "chamber. Either way the member list would be wrong, which is the "
        "failure that published 2,234 bad suggestions federally.",
        lines,
    )


def classify_wikidata_reach(
    holders: int,
    with_opd_id: int,
    with_federal_id: int,
    with_either: int,
    seats: int = DEFAULT_SEATS,
) -> Tuple[str, str]:
    """Can the cantonal tool ever emit QuickStatements? Pure.

    ``holders`` is everyone with a P39 for the seat — historic members
    included, so it should comfortably exceed ``seats``. The rest count how
    many of them carry an identifier **Wikidata itself asserts**, which is what
    ``is_mechanical`` requires.

    The verdict is deliberately about *provenance*, not about coverage being
    high. A low number is not a failure of the extension; it is the answer that
    the first release ships report-only, which is how the federal side began
    and is a perfectly good tool. What would be a failure is emitting edits off
    a join nothing on Wikidata vouches for.
    """
    if holders == 0:
        return (
            INCONCLUSIVE,
            "Nobody holds this position on Wikidata, so there is nothing to "
            "measure. That is almost certainly section D's problem — the wrong "
            "position item — rather than a fact about cantonal coverage.",
        )

    share = 100.0 * with_either / holders
    detail_counts = (
        f"{holders} item(s) hold the seat; {with_opd_id} carry "
        f"{OPENPARLDATA_ID}, {with_federal_id} carry {SWISS_PARLIAMENT_ID} "
        f"(federal service, not the cantonal seat), {with_either} carry either "
        f"({share:.1f}%)."
    )

    if with_either == 0:
        return (
            CONTRADICTED,
            detail_counts + " No Wikidata-asserted identifier reaches these "
            "people at all, so every match would rest on a name. Ship the "
            "cantonal config with 'quickstatements: false' and treat the "
            "reports as a worklist — chiefly for creating items, since a "
            "cantonal member is far likelier to have none. Revisit if "
            f"{OPENPARLDATA_ID} gets populated.",
        )
    if with_opd_id == 0:
        return (
            CONTRADICTED,
            detail_counts + f" The only identifier present is "
            f"{SWISS_PARLIAMENT_ID}, which is the *federal* service and says "
            "nothing about a cantonal seat — it reaches only the members who "
            "also sat in Bern. It must not be used as the cantonal join: the "
            "people it misses are precisely the ones who never went federal.",
        )
    if share >= 50.0:
        return (
            CONFIRMED,
            detail_counts + f" {OPENPARLDATA_ID} is Wikidata-asserted, so it "
            "slots into the existing 'is_mechanical' gate with no change to "
            "the safety rule — only 'identifier_property' in the config and "
            "the two SPARQL builders in wikidata.py need to become "
            "configurable. Note this inverts the federal finding, where "
            f"{OPENPARLDATA_ID} added nobody.",
        )
    return (
        CONFIRMED,
        detail_counts + f" {OPENPARLDATA_ID} works as a join and needs no "
        "change to the safety rule, but it reaches a minority, so most members "
        "would still fall through to the name fallback and stay report-only. "
        "That is a usable first release: the identifier-matched members get "
        "QuickStatements, everyone else gets a suggestion to add "
        f"{OPENPARLDATA_ID} — which is the highest-leverage edit there is, "
        "since it makes every later run exact.",
    )


def classify_position_item(
    holders: int, open_holders: int, seats: int = DEFAULT_SEATS
) -> Tuple[str, str]:
    """Is the configured Q-ID really the seat? Pure.

    Counted rather than read, for the same reason section B counts: a label can
    be plausible and the item still wrong. A position held by roughly ``seats``
    people *right now* is the chamber. Nobody at all is a category item or a
    typo; an open count wildly above the chamber's size means the item is
    something broader than one seat.

    The tolerance is deliberately loose — Wikidata's coverage of a cantonal
    parliament is incomplete, and missing members make the open count *low*
    without making the item wrong. It is the upper bound that falsifies.
    """
    if holders == 0:
        return (
            CONTRADICTED,
            "No item holds this position, so it is not the seat — most likely "
            "a category of members rather than the position itself, or simply "
            "the wrong Q-ID. Find the right one before writing any config: "
            "this value is the main value of every P39 the tool would emit.",
        )
    if open_holders > seats:
        return (
            CONTRADICTED,
            f"{open_holders} people hold this position with no end date, more "
            f"than the chamber's {seats} seats. An item cannot be currently "
            "held by more people than there are seats, so this is either a "
            "broader position than one chamber's, or Wikidata has open "
            "statements that should have been closed. Settle which before "
            "trusting the diff's second pass, which reports exactly those as "
            "'has left'.",
        )
    share = 100.0 * open_holders / seats if seats else 0.0
    if open_holders == 0:
        return (
            INCONCLUSIVE,
            f"{holders} item(s) hold this position but none currently. That is "
            "consistent with a real position whose statements all carry an end "
            "date, and also with the wrong item — the counts cannot tell them "
            "apart. Read the label and description printed above.",
        )
    return (
        CONFIRMED,
        f"{holders} item(s) hold this position, {open_holders} of them "
        f"currently — {share:.0f}% of the chamber's {seats} seats. Consistent "
        "with the seat itself. The shortfall from 100% is Wikidata's coverage "
        "gap, which is the tool's whole reason to exist; check the label above "
        "names the position and not a category.",
    )


def district_fields(rows: Sequence[Dict[str, Any]]) -> List[str]:
    """Columns that might carry the Wahlkreis. Pure, reporting only.

    Every candidate is returned rather than one being picked. Federally the
    electoral district is the canton and the key comes from
    ``CantonAbbreviation``; here it has to come from whatever the source calls
    the Wahlkreis, and guessing a single name is the mistake that made an
    earlier probe read a column that does not exist.
    """
    keys = {key for row in rows for key in row}
    return sorted(k for k in keys if any(h in k.lower() for h in DISTRICT_HINTS))


# --- the SPARQL side --------------------------------------------------------
def seat_reach_query(position_qid: str) -> str:
    """Seat holders and their identifier coverage, in one query.

    One query rather than four so the counts cannot come from different
    snapshots of the store — the same discipline as
    ``verify_openparldata.coverage_query``.

    ``?open`` re-walks the statement rather than reusing ``?statement``,
    because a member with a closed statement *and* an open one (they left and
    returned) must count as open — testing the single bound statement would
    make the answer depend on which one the store happened to bind.
    """
    return f"""
SELECT
  (COUNT(DISTINCT ?person) AS ?holders)
  (COUNT(DISTINCT ?open) AS ?open)
  (COUNT(DISTINCT ?withOpd) AS ?withOpd)
  (COUNT(DISTINCT ?withFederal) AS ?withFederal)
  (COUNT(DISTINCT ?either) AS ?either)
WHERE {{
  ?person p:P39 ?statement .
  ?statement ps:P39 wd:{position_qid} ;
             wikibase:rank ?rank .
  FILTER ( ?rank != wikibase:DeprecatedRank )
  OPTIONAL {{
    ?person p:P39 ?openStatement .
    ?openStatement ps:P39 wd:{position_qid} .
    FILTER NOT EXISTS {{ ?openStatement pq:P582 ?anyEnd . }}
    BIND(?person AS ?open)
  }}
  OPTIONAL {{ ?person wdt:{OPENPARLDATA_ID} ?opd . BIND(?person AS ?withOpd) }}
  OPTIONAL {{
    ?person wdt:{SWISS_PARLIAMENT_ID} ?fed . BIND(?person AS ?withFederal)
  }}
  OPTIONAL {{
    {{ ?person wdt:{OPENPARLDATA_ID} ?a }}
    UNION
    {{ ?person wdt:{SWISS_PARLIAMENT_ID} ?b }}
    BIND(?person AS ?either)
  }}
}}
"""


def _count(bindings: Sequence[dict], name: str) -> int:
    if not bindings:
        return 0
    try:
        return int(bindings[0][name]["value"])
    except (KeyError, TypeError, ValueError):
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config/parliament.yaml")
    parser.add_argument(
        "--body-key",
        default=DEFAULT_BODY_KEY,
        help=(
            "The OpenParlData body for the canton. A body is the *level* of "
            "parliament, so this is the canton, not the chamber."
        ),
    )
    parser.add_argument(
        "--position",
        default=KANTONSRAT_POSITION,
        help=(
            "Candidate Q-ID for 'member of the Cantonal Council of Zürich'. "
            "The default is UNVERIFIED; section D is what checks it."
        ),
    )
    parser.add_argument(
        "--expect-seats",
        type=int,
        default=DEFAULT_SEATS,
        help="Seats in the chamber. 180 for the Kantonsrat Zürich.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)

    import swissparlpy as spp

    # --- A. where does the Kantonsrat live? ---------------------------------
    print("=" * 70)
    print(f"A. Body {args.body_key!r}: is the Kantonsrat there, and as what?")
    print("=" * 70)
    # Constructing the client is itself a request — it reads the API's OpenAPI
    # document — so it belongs inside the same guard as the first call. Left
    # outside, an unreachable API ends the probe in a traceback that looks like
    # a bug in this script rather than the connectivity failure it is.
    try:
        client = spp.SwissParlClient(session=http.session, backend="openparldata")
        client.get_tables()
    except Exception as exc:
        print(f"  ! could not read the API at all: {exc}")
        print("\nConnectivity, not a finding. Nothing below was measured.")
        return 1

    # lang='de' explicitly: the names matched here are German, and pinning the
    # language is what keeps the run reproducible across swissparlpy versions.
    bodies, _ = fetch(client, "bodies", lang="de")
    match = [
        row
        for row in bodies
        if _text(row.get("key")).strip().upper() == args.body_key.upper()
    ]
    print(f"bodies: {len(bodies)} row(s); {len(match)} match key={args.body_key!r}")
    for row in match[:3]:
        seats = row.get("legislative_seats")
        suffix = f", legislative_seats={seats!r}" if seats is not None else ""
        print(f"  {_describe(row)}{suffix}")
    if not match:
        print(
            f"  (no body has key={args.body_key!r}. Zürich-ish rows follow; "
            "pass the right one with --body-key.)"
        )
        for row in bodies:
            if any("zürich" in n or "zurich" in n for n in _name_values(row)):
                print(f"    {_describe(row)}")
    print()

    groups, _ = fetch(client, "groups", body_key=args.body_key)
    chamber, group_lines = find_kantonsrat_group(groups)
    for line in group_lines:
        print("  " + line if line else "")

    # --- B. are the seats dated, and are there 180 of them? -----------------
    print()
    print("=" * 70)
    print("B. Do the Kantonsrat's memberships carry dates, and how many are open?")
    print("=" * 70)
    date_verdict, date_detail = INCONCLUSIVE, "No Kantonsrat group was found."
    seat_verdict, seat_detail = INCONCLUSIVE, "No Kantonsrat group was found."
    seat_rows: List[Dict[str, Any]] = []
    if chamber is not None:
        group_id = chamber.get("id")
        print(f"--- Kantonsrat (group {group_id}) ---")
        seat_rows, _ = fetch(client, "memberships", group_id=group_id)

        date_verdict, date_detail, date_lines = classify_seat_memberships(seat_rows)
        for line in date_lines:
            print("  " + line if line else "")
        print(f"  => dates: {date_verdict}: {date_detail}")
        print()

        seat_verdict, seat_detail, seat_lines = classify_seat_count(
            seat_rows, args.expect_seats
        )
        for line in seat_lines:
            print("  " + line if line else "")
        print(f"  => size:  {seat_verdict}: {seat_detail}")
    else:
        print("  (skipped: no chamber group to query)")

    # --- C. who are these people on Wikidata? -------------------------------
    print()
    print("=" * 70)
    print("C. Are the members reachable on Wikidata, and by which identifier?")
    print("=" * 70)
    wikidata = WikidataClient(http)
    reach_verdict, reach_detail = INCONCLUSIVE, "The reach query could not be run."
    pos_verdict, pos_detail = INCONCLUSIVE, "The reach query could not be run."
    try:
        rows = wikidata.run_query(seat_reach_query(args.position))
        holders = _count(rows, "holders")
        open_holders = _count(rows, "open")
        with_opd = _count(rows, "withOpd")
        with_fed = _count(rows, "withFederal")
        either = _count(rows, "either")
        print(f"  holding {args.position:<10}    : {holders}")
        print(f"  of them currently        : {open_holders}")
        print(f"  carrying {OPENPARLDATA_ID:<9}      : {with_opd}")
        print(f"  carrying {SWISS_PARLIAMENT_ID:<9}       : {with_fed} "
              "(federal service, not this seat)")
        print(f"  carrying either          : {either}")
        reach_verdict, reach_detail = classify_wikidata_reach(
            holders, with_opd, with_fed, either, args.expect_seats
        )
        pos_verdict, pos_detail = classify_position_item(
            holders, open_holders, args.expect_seats
        )
    except Exception as exc:
        print(f"  ! WDQS: {exc}")
    print()
    print(f"{reach_verdict}: {reach_detail}")

    # The source-asserted Q-ID, measured separately because it is a different
    # class of claim: see the module docstring and summarise_wikidata_ids.
    print()
    print("  OpenParlData's own wikidata_id, for comparison:")
    person_ids = {r.get("person_id") for r in seat_rows if r.get("person_id") is not None}
    people, _ = fetch(client, "persons", body_key=args.body_key)
    seated = [r for r in people if r.get("id") in person_ids] or people
    print(f"  persons with body_key={args.body_key!r}: {len(people)}"
          + (f", holding a seat from B: {len(seated)}" if person_ids else ""))
    _, people_lines = summarise_wikidata_ids(seated)
    for line in people_lines:
        print("  " + line if line else "")
    print(
        "  -> A Q-ID from here is asserted by a third party ABOUT Wikidata, "
        "not by Wikidata. It needs its own QID_FROM_* constant and its own "
        "decision in is_mechanical; it must not inherit the P1307 gate."
    )

    # --- D. is the position item right? -------------------------------------
    print()
    print("=" * 70)
    print(f"D. Is {args.position} really the seat?")
    print("=" * 70)
    try:
        described = wikidata.describe_qids([args.position], config.language)
        entry = described.get(args.position, {})
        print(f"  label:       {entry.get('label')!r}")
        print(f"  description: {entry.get('description')!r}")
        instances = entry.get("instance_of") or []
        print(f"  instance of: {', '.join(str(i) for i in instances) or '(none)'}")
        if any("category" in str(i).lower() for i in instances):
            print(
                "  ! this is a CATEGORY item, not a position. Emitting it as "
                "P39 would claim people hold a Wikimedia category."
            )
    except Exception as exc:
        print(f"  ! WDQS: {exc}")
    print()
    print(f"{pos_verdict}: {pos_detail}")

    # --- E. what would supply P768? -----------------------------------------
    print()
    print("=" * 70)
    print("E. What could supply the Wahlkreis (P768)?")
    print("=" * 70)
    print(f"  membership columns mentioning a district: "
          f"{', '.join(district_fields(seat_rows)) or '(none)'}")
    print(f"  person columns mentioning a district:     "
          f"{', '.join(district_fields(seated)) or '(none)'}")
    print(f"  date-ish membership columns:              "
          f"{', '.join(date_columns(seat_rows)) or '(none)'}")
    if seat_rows:
        print()
        print("  sample memberships:")
        for row in seat_rows[:5]:
            print(f"    {_describe_membership(row)}")
    print()
    print(
        "  Zürich elects its 180 members from 18 Wahlkreise, so the config's "
        "26-entry 'cantons' map becomes a per-body district map with different "
        "keys. If nothing above carries the Wahlkreis, P768 has to come from "
        "the canton's own service (opendata.swiss) or be left unmapped — which "
        "the tool already handles: an unmapped district makes no suggestion."
    )

    # --- what it all means --------------------------------------------------
    print()
    print("=" * 70)
    print(f"A. Kantonsrat located as a group : "
          f"{'YES, id=' + str(chamber.get('id')) if chamber else 'NO'}")
    print(f"B. Seat tenure dated             : {date_verdict}")
    print(f"B. Open seats == {args.expect_seats:<3}            : {seat_verdict}")
    print(f"C. Wikidata-asserted identifier  : {reach_verdict}")
    print(f"D. Position item {args.position:<12}   : {pos_verdict}")
    print("=" * 70)
    print(
        "This probe evaluates an option; it gates nothing. B decides whether "
        "an adapter is possible at all, C decides whether it may ever write, "
        "and D decides what it would write to. Do not start the adapter until "
        "all three have answers."
    )
    # Exit 0 whenever the APIs answered: a "no" here is an answer, not a fault.
    return 0


if __name__ == "__main__":
    sys.exit(main())
