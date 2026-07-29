"""Evaluate the OpenParlData backend as an alternative (or addition) to OData.

`swissparlpy` 1.0.0 ships a second backend, ``openparldata``, reading
https://api.openparldata.ch. Its ``persons`` records carry two fields this tool
currently has no source for — ``wikidata_id`` and
``party_harmonized_wikidata_id`` — and Wikidata has an *OpenParlData ID*
property, P14527, that could serve as a join key alongside P1307.

This probe **asserts nothing and changes nothing**: unlike ``verify_source.py``,
a "no" here is a finding about a design option, not a broken pipeline, so it
exits 0 whenever the API answered at all.

The data model, established by the 2026-07-29 run and corrected from what the
first version of this script assumed:

- a **body** is the *level* of parliament, not a chamber — the Federal Assembly
  is one body (``CHE`` / "Schweiz"), covering both councils;
- the **National Council and Council of States are groups**, and a person's
  seat is a row in ``memberships`` pointing at one of them;
- so the tenure this tool needs is reached by walking person → memberships →
  group, not by reading a chamber-shaped table.

What the probe therefore asks:

**A. Which groups are the two chambers?** Named, with their ids, so B and C can
use them.

**B. Do the chambers' memberships carry dates?** The decisive question. This
tool reconciles P39 "member of the National Council" with P580/P582 and P2937
term qualifiers, all of which come from a seat tenure with a start and an end.
The first run found a *cantonal* seat membership (``council_legislative``,
"Grosser Rat des Kantons Freiburg") whose ``date_start`` and ``date_end`` were
both null. Whether the federal ones are populated is what decides
replacement-versus-enrichment.

**C. Do the federal person records carry ``wikidata_id``?** A populated one is
the Q-ID itself. The first run measured 16.2% across all 26,574 people in the
API, but that is every Swiss parliament at every level; the federal subset is
the number that matters here.

**D. How does P14527 compare with P1307?** Measured 2026-07-29: P1307 on 3,719
items of which 3,043 are National Councillors, P14527 on 4,277 of which 3,025.
Near parity, so the useful number is not which is bigger but how many seat
holders carry P14527 and *not* P1307 — that is exactly what a second join path
would add.

A caution when reading the output: this backend logs unknown query parameters
as a warning and sends them anyway rather than rejecting them (and the warning
itself does not interpolate the table name — it prints a literal ``'{table}'``).
``limit`` is one of those: it is not honoured, which is why the first run pulled
all 26,574 person records. Scope queries by group instead of trying to cap them.

Run it locally::

    uv run python scripts/verify_openparldata.py

or dispatch the "Verify assumptions" workflow, which runs it alongside the
parlament.ch probes without letting its answer affect the job result.
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
from wd_parliament.wikidata import WikidataClient  # noqa: E402

# Shared with verify_source.py's vocabulary, so the two read alike.
CONFIRMED = "CONFIRMED"
CONTRADICTED = "CONTRADICTED"
INCONCLUSIVE = "INCONCLUSIVE"

# Wikidata's National Council seat, and the two identifier properties.
NATIONAL_COUNCIL = "Q18510612"
SWISS_PARLIAMENT_ID = "P1307"
OPENPARLDATA_ID = "P14527"

# How each chamber names itself, in the languages the API carries. Matched
# case-insensitively against every ``name*`` / ``title*`` field on a group, so
# a group is recognised whichever language happens to be populated.
CHAMBER_NAMES = {
    "NR": (
        "nationalrat",
        "conseil national",
        "consiglio nazionale",
        "national council",
    ),
    "SR": (
        "ständerat",
        "standerat",
        "conseil des états",
        "conseil des etats",
        "consiglio degli stati",
        "council of states",
    ),
}

# A cantonal legislature also calls itself a council, so the chamber names
# above are what distinguish the federal groups — not the membership type.
# Kept only to report what types exist alongside them.
SEAT_TYPES = ("council_legislative", "parliament", "legislature")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _normalise(value: Any) -> str:
    """Lowercased, whitespace-collapsed, for comparing a name to a name. Pure."""
    return " ".join(_text(value).lower().split())


def _name_values(row: Dict[str, Any]) -> List[str]:
    """Each name-ish field on a row, normalised, one per field. Pure.

    One string *per field* rather than one run-together haystack: matching has
    to be able to ask whether a name **equals** a chamber's, and concatenating
    the fields first makes that impossible.
    """
    return [
        _normalise(value)
        for field, value in row.items()
        if (field.startswith("name") or field.startswith("title")) and value
    ]


def chamber_of(row: Dict[str, Any]) -> Optional[str]:
    """"NR", "SR" or ``None`` for a group (or membership) row. Pure.

    Requires a name to **equal** the chamber's, not merely contain it. The
    2026-07-29 run showed why: a substring match picks up "Präsidium des
    Nationalrates" — a committee of eight — and reports it as the chamber, so
    section B measured a presidium and called the answer CONTRADICTED. "Büro
    NR", "Kommission für Verkehr des Nationalrates" and the parliamentary
    groups are all the same trap.

    The cost of being strict is a chamber named with a suffix would be missed,
    which is why :func:`chamber_candidates` reports the near misses rather than
    letting them pass silently.
    """
    names = _name_values(row)
    for council, spellings in CHAMBER_NAMES.items():
        if any(name in spellings for name in names):
            return council
    return None


def chamber_candidates(
    rows: Sequence[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Rows whose name *mentions* a chamber without being it. Pure.

    Reporting-only. These are the rows :func:`chamber_of` deliberately rejects,
    surfaced so that a chamber named "Nationalrat 52. Legislatur" would be
    visible as a near miss instead of vanishing.
    """
    out: Dict[str, List[Dict[str, Any]]] = {"NR": [], "SR": []}
    for row in rows:
        if chamber_of(row) is not None:
            continue
        names = _name_values(row)
        for council, spellings in CHAMBER_NAMES.items():
            if any(s in name for name in names for s in spellings):
                out[council].append(row)
                break
    return out


def find_chamber_groups(
    rows: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """The National Council and Council of States among ``groups``. Pure.

    Returns ``{"NR": row, "SR": row}`` for whichever were found by an exact
    name match. Missing either is a real answer — but a loud one: the near
    misses are printed so that "not found" can be told apart from "found under
    a name this function did not expect".
    """
    lines: List[str] = []
    if not rows:
        return {}, [
            "The 'groups' table came back empty. Without it the chambers "
            "cannot be located, so B and C below say nothing."
        ]

    found: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        council = chamber_of(row)
        if council and council not in found:
            found[council] = row

    near = chamber_candidates(rows)
    lines.append(f"{len(rows)} group(s) read, {len(found)} chamber(s) identified")
    lines.append("")
    for council in ("NR", "SR"):
        if council in found:
            lines.append(f"  {council}: {_describe(found[council])}")
        else:
            lines.append(
                f"  {council}: NOT FOUND by exact name "
                f"({len(near[council])} group(s) mention it)"
            )
        for row in near[council][:5]:
            lines.append(f"        near miss: {_describe(row)}")
    return found, lines


def _describe(row: Dict[str, Any]) -> str:
    """One row as a single line, tolerant of unknown field names."""
    parts = [f"id={row.get('id')}"]
    if row.get("body_key") is not None:
        parts.append(f"body_key={row.get('body_key')!r}")
    for field in ("name_de", "name", "name_fr", "title_de", "title"):
        if row.get(field):
            parts.append(f"{field}={row[field]!r}")
            break
    return ", ".join(parts)


def classify_seat_memberships(
    rows: Sequence[Dict[str, Any]],
) -> Tuple[str, str, List[str]]:
    """Do these seat memberships carry usable dates? Pure.

    The question the evaluation turns on. A membership without a ``date_start``
    yields no P580 and no period overlap, so it cannot source P39 however
    correctly it identifies the seat.
    """
    lines: List[str] = []
    if not rows:
        return (
            INCONCLUSIVE,
            "No memberships came back, so nothing was tested. Check the group "
            "id before concluding anything.",
            lines,
        )

    kinds = sorted({_text(r.get("type_harmonized")) or "(none)" for r in rows})
    dated = [r for r in rows if _text(r.get("date_start")).strip()]
    ended = [r for r in rows if _text(r.get("date_end")).strip()]

    lines.append(f"memberships:            {len(rows)}")
    lines.append(f"with a date_start:      {len(dated)}")
    lines.append(f"with a date_end:        {len(ended)}")
    lines.append(f"type_harmonized values: {', '.join(kinds)}")
    lines.append("")
    for row in rows[:8]:
        lines.append(f"  {_describe_membership(row)}")

    if not dated:
        return (
            CONTRADICTED,
            "No membership carries a date_start. Without a start there is no "
            "P580 and no period overlap, so this cannot source P39 — "
            "MemberCouncil's DateJoining/DateLeaving remain the only source of "
            "a tenure. The wikidata_id and party Q-IDs may still be worth "
            "having as enrichment.",
            lines,
        )

    share = 100.0 * len(dated) / len(rows)
    if len(dated) < len(rows):
        return (
            CONFIRMED,
            f"{len(dated)} of {len(rows)} memberships ({share:.1f}%) carry a "
            "date_start, so the seat tenure is here but incomplete. Usable as "
            "a cross-check on MemberCouncil.DateJoining; risky as the only "
            "source, since the members with no date would silently lose P580 "
            "and every P2937 term.",
            lines,
        )
    return (
        CONFIRMED,
        f"All {len(rows)} memberships carry a date_start. This backend can "
        "source P39. Compare the dates against MemberCouncil.DateJoining "
        "before switching — that disagreement is README step 0c.",
        lines,
    )


def _describe_membership(row: Dict[str, Any]) -> str:
    parts = [f"type={row.get('type_harmonized')!r}"]
    for field in ("group_name_de", "person_name", "fullname", "role_name_de"):
        if row.get(field):
            parts.append(f"{field}={row[field]!r}")
    parts.append(f"start={row.get('date_start')!r}")
    parts.append(f"end={row.get('date_end')!r}")
    return ", ".join(parts)


def summarise_wikidata_ids(rows: Sequence[Dict[str, Any]]) -> Tuple[int, List[str]]:
    """How many person records carry a ``wikidata_id``. Pure.

    A populated one is better than any identifier join — it is the Q-ID itself.
    But note the provenance question before using it: ``is_mechanical`` gates on
    ``QID_FROM_IDENTIFIER``, meaning *Wikidata* asserted the identifier that
    established the match. A Q-ID that a third party asserts about Wikidata is
    a different class of claim and needs its own decision, not this gate by
    default.
    """
    lines: List[str] = []
    if not rows:
        return 0, ["No person records to inspect."]

    with_qid = [r for r in rows if _text(r.get("wikidata_id")).strip()]
    parties = [r for r in rows if _text(r.get("party_harmonized_wikidata_id")).strip()]
    lines.append(f"person records inspected:  {len(rows)}")
    lines.append(f"carrying a wikidata_id:    {len(with_qid)} "
                 f"({100.0 * len(with_qid) / len(rows):.1f}%)")
    lines.append(f"carrying a party Q-ID:     {len(parties)} "
                 f"({100.0 * len(parties) / len(rows):.1f}%)")
    lines.append("")
    for row in with_qid[:5]:
        lines.append(
            f"  {row.get('id')} {row.get('fullname')!r} -> "
            f"{row.get('wikidata_id')} (party {row.get('party_harmonized_wikidata_id')})"
        )
    if not with_qid:
        lines.append(
            "  -> wikidata_id is in the schema but populated for nobody here, "
            "so it cannot drive matching. The party Q-IDs may still be worth "
            "having: config/parliament.yaml ships 'parties' empty because a "
            "wrong qualifier Q-ID is worse than none."
        )
    return len(with_qid), lines


def compare_identifier_coverage(
    p1307_seat: int, p14527_seat: int, only_new: int
) -> Tuple[str, str]:
    """Which identifier to join on. Pure.

    ``*_seat`` counts items holding the property *and* a National Council P39 —
    the population this tool reconciles. ``only_new`` is the count holding
    P14527 but **not** P1307, which is the only number that says what a second
    join path would actually add: two properties of identical size that sit on
    the same people are worth no more than one.
    """
    if p14527_seat == 0:
        return (
            CONTRADICTED,
            f"No National Councillor carries {OPENPARLDATA_ID}, so it joins "
            f"nothing in this tool's population. Keep {SWISS_PARLIAMENT_ID}.",
        )
    if only_new == 0:
        return (
            CONTRADICTED,
            f"{OPENPARLDATA_ID} reaches {p14527_seat} seat holders against "
            f"{SWISS_PARLIAMENT_ID}'s {p1307_seat}, but every one of them "
            f"already carries {SWISS_PARLIAMENT_ID}. A second join path would "
            "match nobody new — not worth the code.",
        )
    verdict = CONFIRMED if p14527_seat >= p1307_seat else CONTRADICTED
    replacement = (
        "Switching outright is defensible on size"
        if p14527_seat >= p1307_seat
        else f"Switching would lose {p1307_seat - p14527_seat} seat holder(s)"
    )
    return (
        verdict,
        f"{OPENPARLDATA_ID} reaches {p14527_seat} seat holders against "
        f"{SWISS_PARLIAMENT_ID}'s {p1307_seat}, and {only_new} of them carry "
        f"no {SWISS_PARLIAMENT_ID} at all. {replacement}, but running both is "
        f"strictly better: it is the {only_new} that a second pass adds, at "
        "the cost of one more SPARQL query.",
    )


# --- the SPARQL side --------------------------------------------------------
def coverage_query(prop: str, position_qid: str = NATIONAL_COUNCIL) -> str:
    """Items holding ``prop``, and how many of those hold the seat.

    One query rather than two so the numbers cannot come from different
    snapshots of the store.
    """
    return f"""
SELECT
  (COUNT(DISTINCT ?person) AS ?total)
  (COUNT(DISTINCT ?seated) AS ?seated)
WHERE {{
  ?person wdt:{prop} ?identifier .
  OPTIONAL {{
    ?person p:P39 ?statement .
    ?statement ps:P39 wd:{position_qid} .
    BIND(?person AS ?seated)
  }}
}}
"""


def marginal_gain_query(position_qid: str = NATIONAL_COUNCIL) -> str:
    """Seat holders carrying P14527 but not P1307 — what a second pass adds."""
    return f"""
SELECT
  (COUNT(DISTINCT ?person) AS ?either)
  (COUNT(DISTINCT ?only_new) AS ?only_new)
WHERE {{
  ?person p:P39 ?statement .
  ?statement ps:P39 wd:{position_qid} .
  {{ ?person wdt:{SWISS_PARLIAMENT_ID} ?old }}
  UNION
  {{ ?person wdt:{OPENPARLDATA_ID} ?new }}
  OPTIONAL {{
    ?person wdt:{OPENPARLDATA_ID} ?n .
    FILTER NOT EXISTS {{ ?person wdt:{SWISS_PARLIAMENT_ID} ?o }}
    BIND(?person AS ?only_new)
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


def fetch(
    client: Any, table: str, **params: Any
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Rows from one OpenParlData table, plus the error if it could not be read."""
    try:
        return [dict(r) for r in client.get_data(table, **params)], None
    except Exception as exc:  # an unreadable table is a finding, not a crash
        print(f"  ! {table}: {exc}")
        return [], f"{table}: {exc}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config/parliament.yaml")
    parser.add_argument(
        "--first-name",
        default="Gerhard",
        help="Given name of a sitting National Councillor, for the person walk.",
    )
    parser.add_argument(
        "--last-name",
        default="Andrey",
        help=(
            "Surname of that member. Matched together with --first-name: the "
            "backend searches partially, so a surname alone found the wrong "
            "Andrey on 2026-07-29 (a Fribourg cantonal member)."
        ),
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)

    import swissparlpy as spp

    client = spp.SwissParlClient(session=http.session, backend="openparldata")

    # --- A. which groups are the chambers? ----------------------------------
    print("=" * 70)
    print("A. Groups: which are the National Council and Council of States?")
    print("=" * 70)
    try:
        print(f"tables: {', '.join(sorted(client.get_tables()))}")
    except Exception as exc:
        print(f"  ! could not list tables: {exc}")
        print("\nThe API could not be read at all — connectivity, not a finding.")
        return 1

    # The body is the *level*, not a chamber; reported for orientation only.
    bodies, _ = fetch(client, "bodies")
    print(f"bodies: {len(bodies)} row(s)")
    for row in bodies[:5]:
        print(f"  {_describe(row)}")
    print()

    groups, _ = fetch(client, "groups")
    chambers, group_lines = find_chamber_groups(groups)
    for line in group_lines:
        print("  " + line if line else "")

    # --- B. do the chambers' memberships carry dates? -----------------------
    print()
    print("=" * 70)
    print("B. Do the chambers' memberships carry dates?")
    print("=" * 70)
    seat_verdict, seat_detail = INCONCLUSIVE, "No chamber group was found."
    members: List[Dict[str, Any]] = []
    for council, group in chambers.items():
        print(f"--- {council} (group {group.get('id')}) ---")
        rows, _ = fetch(client, "memberships", group_id=group.get("id"))
        members.extend(rows)
        verdict, detail, lines = classify_seat_memberships(rows)
        for line in lines:
            print("  " + line if line else "")
        print(f"  => {verdict}: {detail}")
        print()
        # The National Council is the population the census in D measures.
        if council == "NR":
            seat_verdict, seat_detail = verdict, detail
    if not chambers:
        print("  (skipped: no chamber group to query)")

    # --- C. the person walk, and wikidata_id coverage -----------------------
    print("=" * 70)
    print("C. Person walk and wikidata_id coverage")
    print("=" * 70)
    print(f"Sampling '{args.first_name} {args.last_name}'\n")
    sample, _ = fetch(
        client, "persons", firstname=args.first_name, lastname=args.last_name
    )
    for row in sample[:3]:
        print(f"  {row.get('id')} {row.get('fullname')!r} "
              f"body_key={row.get('body_key')!r} wikidata_id={row.get('wikidata_id')!r}")
    if sample:
        walked, _ = fetch(client, "memberships", person_id=sample[0].get("id"))
        seats = [r for r in walked if chamber_of(r)]
        dated = [r for r in walked if _text(r.get("date_start")).strip()]
        print(f"  {len(walked)} membership(s), {len(dated)} with a date_start, "
              f"{len(seats)} naming a chamber outright")
        # The chamber rows first: they are the ones that would source P39.
        for row in (seats + [r for r in walked if r not in seats])[:12]:
            marker = f"  <- {chamber_of(row)}" if chamber_of(row) else ""
            print(f"    {_describe_membership(row)}{marker}")
        if not seats:
            print(
                "  -> None of this person's memberships is the chamber itself. "
                "Either the seat is not modelled as a membership, or it is "
                "named differently — check the near misses in A."
            )
    else:
        print(f"  ! nobody matched '{args.first_name} {args.last_name}'")
    print()

    # Scope the coverage count to the people who actually hold a seat, rather
    # than to every person in the API at every level of government.
    seat_person_ids = {
        r.get("person_id") for r in members if r.get("person_id") is not None
    }
    people: List[Dict[str, Any]] = []
    if seat_person_ids:
        everyone, _ = fetch(client, "persons")
        people = [r for r in everyone if r.get("id") in seat_person_ids]
        print(f"  scoped to the {len(people)} federal member(s) found in B")
    else:
        print("  (no seat memberships in B, so there is nobody to scope to)")
    _, people_lines = summarise_wikidata_ids(people)
    for line in people_lines:
        print("  " + line if line else "")

    # --- D. how does P14527 compare with P1307? -----------------------------
    print()
    print("=" * 70)
    print(f"D. {OPENPARLDATA_ID} coverage against {SWISS_PARLIAMENT_ID}")
    print("=" * 70)
    wikidata = WikidataClient(http)
    id_verdict, id_detail = INCONCLUSIVE, "The coverage queries could not be run."
    try:
        old = wikidata.run_query(coverage_query(SWISS_PARLIAMENT_ID))
        new = wikidata.run_query(coverage_query(OPENPARLDATA_ID))
        overlap = wikidata.run_query(marginal_gain_query())
        p1307_seat = _count(old, "seated")
        p14527_seat = _count(new, "seated")
        only_new = _count(overlap, "only_new")
        print(f"  {SWISS_PARLIAMENT_ID}:  {_count(old, 'total')} item(s), "
              f"{p1307_seat} of them National Councillors")
        print(f"  {OPENPARLDATA_ID}: {_count(new, 'total')} item(s), "
              f"{p14527_seat} of them National Councillors")
        print(f"  reachable by either:  {_count(overlap, 'either')}")
        print(f"  {OPENPARLDATA_ID} only:      {only_new}")
        id_verdict, id_detail = compare_identifier_coverage(
            p1307_seat, p14527_seat, only_new
        )
    except Exception as exc:
        print(f"  ! WDQS: {exc}")
    print()
    print(f"{id_verdict}: {id_detail}")

    # --- what it all means --------------------------------------------------
    print()
    print("=" * 70)
    print(f"A. Chambers located as groups : {', '.join(sorted(chambers)) or 'NONE'}")
    print(f"B. Seat tenure with dates     : {seat_verdict}")
    print(f"C. wikidata_id on members     : "
          f"{sum(1 for r in people if _text(r.get('wikidata_id')).strip())}"
          f"/{len(people)}")
    print(f"D. {OPENPARLDATA_ID} as a join key   : {id_verdict}")
    print("=" * 70)
    print(
        "This probe evaluates an option; it does not gate anything. B is the "
        "one that decides whether OpenParlData can replace the OData read, "
        "rather than merely enrich it."
    )
    # Exit 0 whenever the API answered: a "no" here is an answer, not a fault.
    return 0


if __name__ == "__main__":
    sys.exit(main())
