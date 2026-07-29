"""Evaluate the OpenParlData backend as an alternative (or addition) to OData.

`swissparlpy` 1.0.0 ships a second backend, ``openparldata``, reading
https://api.openparldata.ch. Its ``persons`` records carry two fields this tool
currently has no source for — ``wikidata_id`` and
``party_harmonized_wikidata_id`` — and Wikidata is said to have an *OpenParlData
ID* property (P14527) that could serve as a join key alongside P1307.

Whether that is an improvement turns on questions nobody has answered against
the live API. This probe answers them in one dispatch. It **asserts nothing and
changes nothing**: unlike ``verify_source.py``, a "no" here is a finding about a
design option, not a broken pipeline, so the script exits 0 whenever it managed
to read anything at all.

**A. Is the Federal Assembly in there?** The backend's own documentation
examples are cantonal (a Luzern ``body_key``), but two federal people appear in
it (Keller-Sutter, and Gerhard Andrey with a ``Büro NR`` membership), so the
coverage is presumed but unmeasured. This lists ``bodies`` and names the
federal one.

**B. Do the person records carry ``wikidata_id``?** A populated one would beat
any identifier join, since it *is* the Q-ID. The question is coverage.

**C. Does ``memberships`` model the seat itself?** The decisive question. This
tool reconciles P39 "member of the National Council" with P580/P582 and P2937
term qualifiers, so it needs a membership row *for the council seat* carrying a
date range. OData's ``MemberCouncil`` is exactly that. Every membership shown
in swissparlpy's documentation is a sub-body — an interest group, an ad-hoc
committee, ``Büro NR`` — and if that is all there is, this backend cannot
replace the OData read however good its other fields are.

**D. How does P14527's coverage compare with P1307's?** A join is worth only
what the Wikidata side carries. P1307 was censused at 3,043 items holding both
it and a National Council P39; a newer property is unlikely to match that. This
counts both, the same way, and says which to join on.

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

# How a federal body announces itself. Matched case-insensitively against the
# body's key and its names; deliberately several spellings, because which one
# the API uses is part of what this probe is establishing.
FEDERAL_MARKERS = (
    "nationalrat",
    "ständerat",
    "standerat",
    "bundesversammlung",
    "conseil national",
    "conseil des états",
    "conseil des etats",
    "assemblée fédérale",
    "assemblee federale",
    "federal assembly",
)

# Body keys that would denote Switzerland as a whole rather than a canton.
FEDERAL_KEYS = ("ch", "che", "bund")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _looks_federal(row: Dict[str, Any]) -> bool:
    """Does this ``bodies`` row describe the Federal Assembly? Pure."""
    key = _text(row.get("body_key")).strip().lower()
    if key in FEDERAL_KEYS:
        return True
    haystack = " ".join(
        _text(row.get(field)).lower()
        for field in row
        if field.startswith("name") or field.startswith("title") or field == "key"
    )
    return any(marker in haystack for marker in FEDERAL_MARKERS)


def find_federal_bodies(
    rows: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The federal entries in ``bodies``, plus what to print. Pure.

    Returns every row that names a federal chamber. An empty list is a real
    answer — it means the API is cantonal only and the rest of this evaluation
    is moot.
    """
    lines: List[str] = []
    if not rows:
        return [], ["The 'bodies' table is empty or could not be read."]

    federal = [r for r in rows if _looks_federal(r)]
    lines.append(f"{len(rows)} body/bodies in total, {len(federal)} of them federal")
    lines.append("")
    for row in federal:
        lines.append(f"  {_describe_body(row)}")
    if not federal:
        lines.append(
            "  -> No body names a federal chamber. Sample of what is there:"
        )
        for row in rows[:8]:
            lines.append(f"     {_describe_body(row)}")
        lines.append("")
        lines.append(
            "  -> CONTRADICTED: this API appears to be cantonal only, so it "
            "cannot source the National Council or Council of States. Nothing "
            "below will be meaningful."
        )
    return federal, lines


def _describe_body(row: Dict[str, Any]) -> str:
    """One body as a single line, tolerant of unknown field names."""
    parts = [f"id={row.get('id')}", f"key={row.get('body_key')!r}"]
    for field in ("name_de", "name", "name_fr", "title_de", "title"):
        if row.get(field):
            parts.append(f"{field}={row[field]!r}")
            break
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
    lines.append(f"person records inspected:      {len(rows)}")
    lines.append(f"carrying a wikidata_id:        {len(with_qid)}")
    lines.append(f"coverage:                      {100.0 * len(with_qid) / len(rows):.1f}%")

    parties = [r for r in rows if _text(r.get("party_harmonized_wikidata_id")).strip()]
    lines.append(f"carrying a party Q-ID:         {len(parties)}")
    lines.append("")
    for row in with_qid[:5]:
        lines.append(
            f"  {row.get('id')} {row.get('fullname')!r} -> "
            f"{row.get('wikidata_id')} (party {row.get('party_harmonized_wikidata_id')})"
        )
    if not with_qid:
        lines.append(
            "  -> wikidata_id is present in the schema but populated for nobody "
            "here, so it cannot drive matching. The party Q-IDs may still be "
            "worth having: config/parliament.yaml ships 'parties' empty because "
            "a wrong qualifier Q-ID is worse than none."
        )
    return len(with_qid), lines


# Types and names that would mark a membership as the *council seat* rather
# than a committee, interest group or party affiliation within it.
_SEAT_TYPES = ("parliament", "council", "seat", "mandate", "legislature")
_SEAT_NAMES = FEDERAL_MARKERS


def _looks_like_a_seat(row: Dict[str, Any]) -> bool:
    """Is this membership the council seat itself? Pure.

    Deliberately generous: a false positive here is visible in the printed
    rows, whereas a false negative would wrongly rule the backend out.
    """
    kind = _text(row.get("type_harmonized")).lower()
    if any(marker in kind for marker in _SEAT_TYPES):
        return True
    names = " ".join(
        _text(value).lower()
        for field, value in row.items()
        if field.startswith("group_name") or field.startswith("body_name")
    )
    # "Büro NR" is a committee of the council, not the seat — require the
    # chamber's own name, which those committee rows do not carry.
    return any(marker in names for marker in _SEAT_NAMES)


def classify_seat_memberships(
    rows: Sequence[Dict[str, Any]],
) -> Tuple[str, str, List[str]]:
    """Does ``memberships`` carry the council seat, with dates? Pure.

    This is the question the whole evaluation turns on. The tool emits P39
    "member of the National Council" with P580/P582 and P2937 qualifiers, all
    of which come from a seat tenure with a start and an end. If memberships
    only model sub-bodies, the backend cannot replace ``MemberCouncil``.
    """
    lines: List[str] = []
    if not rows:
        return (
            INCONCLUSIVE,
            "No memberships came back for the sampled person, so nothing was "
            "tested. Try another member before concluding anything.",
            lines,
        )

    kinds = sorted({_text(r.get("type_harmonized")) or "(none)" for r in rows})
    lines.append(f"{len(rows)} membership(s), type_harmonized values: {', '.join(kinds)}")
    lines.append("")

    seats = [r for r in rows if _looks_like_a_seat(r)]
    for row in (seats or rows)[:10]:
        lines.append(f"  {_describe_membership(row)}")

    if not seats:
        return (
            CONTRADICTED,
            "None of these memberships is the council seat — they are "
            "committees, interest groups and the like. OData's MemberCouncil "
            "gives the seat with DateJoining/DateLeaving directly, so the "
            "OpenParlData backend cannot replace it as the source of P39. It "
            "may still be worth adding for wikidata_id and the party Q-IDs.",
            lines,
        )

    dated = [r for r in seats if _text(r.get("date_start")).strip()]
    if not dated:
        return (
            CONTRADICTED,
            f"{len(seats)} membership(s) look like the seat, but none carries a "
            "date_start. Without a start there is no P580 and no period "
            "overlap, so this cannot source P39 either.",
            lines,
        )

    return (
        CONFIRMED,
        f"{len(dated)} seat membership(s) with a date_start. This backend can "
        "source P39 — compare the dates against MemberCouncil.DateJoining "
        "before switching, since that is the open question in README step 0c.",
        lines,
    )


def _describe_membership(row: Dict[str, Any]) -> str:
    parts = [f"type={row.get('type_harmonized')!r}"]
    for field in ("group_name_de", "body_name_de", "group_name", "role_name_de"):
        if row.get(field):
            parts.append(f"{field}={row[field]!r}")
    parts.append(f"start={row.get('date_start')!r}")
    parts.append(f"end={row.get('date_end')!r}")
    return ", ".join(parts)


def compare_identifier_coverage(
    p1307_seat: int, p14527_seat: int, p14527_total: int
) -> Tuple[str, str]:
    """Which identifier to join on, given how many items carry each. Pure.

    ``*_seat`` counts items holding the property *and* a National Council P39 —
    the population this tool actually reconciles. A property with more items
    overall but fewer among seat holders is worth less here.
    """
    if p14527_total == 0:
        return (
            CONTRADICTED,
            f"No item carries {OPENPARLDATA_ID}. Either the property does not "
            "exist or nothing uses it yet; either way it cannot be a join key. "
            f"Keep {SWISS_PARLIAMENT_ID}.",
        )
    if p14527_seat == 0:
        return (
            CONTRADICTED,
            f"{p14527_total} item(s) carry {OPENPARLDATA_ID}, but none of them "
            "also holds a National Council seat, so it joins nothing in this "
            f"tool's population. Keep {SWISS_PARLIAMENT_ID}.",
        )
    if p14527_seat < p1307_seat:
        return (
            CONTRADICTED,
            f"{OPENPARLDATA_ID} reaches {p14527_seat} seat holders against "
            f"{SWISS_PARLIAMENT_ID}'s {p1307_seat}. Switching would lower the "
            "hit rate. Worth adding as a *second* join path — it costs nothing "
            "and can only match people the first pass missed — but not as a "
            "replacement.",
        )
    return (
        CONFIRMED,
        f"{OPENPARLDATA_ID} reaches {p14527_seat} seat holders against "
        f"{SWISS_PARLIAMENT_ID}'s {p1307_seat}, so it is at least as good. "
        "Switching is defensible; running both is still better, since the two "
        "sets are unlikely to be identical.",
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


def _count(bindings: Sequence[dict], name: str) -> int:
    if not bindings:
        return 0
    try:
        return int(bindings[0][name]["value"])
    except (KeyError, TypeError, ValueError):
        return 0


def fetch(client: Any, table: str, **params: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
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
        "--last-name",
        default="Andrey",
        help="A sitting National Councillor, used to sample memberships.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="How many person records to sample for wikidata_id coverage.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)

    import swissparlpy as spp

    client = spp.SwissParlClient(session=http.session, backend="openparldata")

    # --- A. is the Federal Assembly in there? -------------------------------
    print("=" * 70)
    print("A. Bodies: is the Federal Assembly covered?")
    print("=" * 70)
    try:
        print(f"tables: {', '.join(sorted(client.get_tables()))}")
    except Exception as exc:
        print(f"  ! could not list tables: {exc}")
        print("\nThe API could not be read at all — connectivity, not a finding.")
        return 1
    print()

    bodies, _ = fetch(client, "bodies")
    federal, body_lines = find_federal_bodies(bodies)
    for line in body_lines:
        print("  " + line if line else "")

    # --- B. do person records carry a Q-ID? ---------------------------------
    print()
    print("=" * 70)
    print("B. Do person records carry wikidata_id?")
    print("=" * 70)
    people: List[Dict[str, Any]] = []
    if federal:
        for body in federal:
            rows, _ = fetch(client, "persons", body_id=body.get("id"), limit=args.limit)
            people.extend(rows)
    else:
        print("  (no federal body found; sampling persons unfiltered)")
        people, _ = fetch(client, "persons", limit=args.limit)
    _, people_lines = summarise_wikidata_ids(people)
    for line in people_lines:
        print("  " + line if line else "")

    # --- C. does memberships model the seat? --------------------------------
    print()
    print("=" * 70)
    print("C. Does 'memberships' carry the council seat, with dates?")
    print("=" * 70)
    print(f"Sampling memberships for '{args.last_name}'\n")
    sample, _ = fetch(client, "persons", lastname=args.last_name)
    memberships: List[Dict[str, Any]] = []
    if sample:
        person_id = sample[0].get("id")
        print(f"  {sample[0].get('fullname')!r} (id={person_id})")
        memberships, _ = fetch(client, "memberships", person_id=person_id)
    else:
        print(f"  ! no person matched '{args.last_name}'")
    seat_verdict, seat_detail, seat_lines = classify_seat_memberships(memberships)
    for line in seat_lines:
        print("  " + line if line else "")
    print()
    print(f"{seat_verdict}: {seat_detail}")

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
        p1307_total, p1307_seat = _count(old, "total"), _count(old, "seated")
        p14527_total, p14527_seat = _count(new, "total"), _count(new, "seated")
        print(f"  {SWISS_PARLIAMENT_ID}:  {p1307_total} item(s), "
              f"{p1307_seat} of them National Councillors")
        print(f"  {OPENPARLDATA_ID}: {p14527_total} item(s), "
              f"{p14527_seat} of them National Councillors")
        id_verdict, id_detail = compare_identifier_coverage(
            p1307_seat, p14527_seat, p14527_total
        )
    except Exception as exc:
        print(f"  ! WDQS: {exc}")
    print()
    print(f"{id_verdict}: {id_detail}")

    # --- what it all means --------------------------------------------------
    print()
    print("=" * 70)
    print(f"A. Federal Assembly covered  : {'YES' if federal else 'NO'}")
    print(f"B. wikidata_id on persons    : "
          f"{sum(1 for r in people if _text(r.get('wikidata_id')).strip())}"
          f"/{len(people)}")
    print(f"C. Seat membership with dates: {seat_verdict}")
    print(f"D. {OPENPARLDATA_ID} as a join key  : {id_verdict}")
    print("=" * 70)
    print(
        "This probe evaluates an option; it does not gate anything. Section C "
        "is the one that decides whether OpenParlData can replace the OData "
        "read, rather than merely enrich it."
    )
    # Exit 0 whenever the API answered: a "no" here is an answer, not a fault.
    return 0


if __name__ == "__main__":
    sys.exit(main())
