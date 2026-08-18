"""Can the city of Zürich's own Gever supply what OpenParlData does not?

README step 13. Two things make this probe necessary rather than optional, and
they are separate questions that happen to have the same answer to look for.

**OpenParlData has the city's people and not its seats.** Run 34 (2026-08-18)
read 807 person records under body ``261`` and **zero** membership rows under
the Gemeinderat's group (``id=8062``, matched by exact name, exactly the id
``config/gemeinderat-zuerich.yaml`` configures). A seat is a ``memberships``
row, so with none there are no members and no P39 to compare — the config
cannot produce a report at all. That is a gap in the source, and no
configuration fixes it.

**swissparlpy 2.1 can reach the city's Gever.** ``backends/gever_config.py``
carries a ``city_zurich`` instance beside the canton's: ``www.gemeinderat-zuerich.ch``
with ``/api/kontakt``, ``/api/behoerdenmandat``, ``/api/wahlkreis``,
``/api/partei`` and a dozen more. It is shaped differently from the canton's
single ``MITGLIEDER`` index — here the **person** and the **mandate** are
separate indexes — and a ``behoerdenmandat`` row is precisely the thing
OpenParlData is missing.

So this probe asks, in order:

**A. Reach.** Which of the configured indexes answer, and with how many
records. Absence is reported per index rather than aborting: "this endpoint is
404" and "this endpoint is empty" mean opposite things, and
``gever_config.py`` already documents one index (``geschlecht``) that is
currently 404.

**B. The real column list**, for the two indexes that would carry members. Two
lists, printed side by side: what the index's **schema** declares
(``get_variables``) and what the returned **records** actually carry. Run 22
is why they are printed separately — its field names came from a client's
normalised output while it read raw XML, and it reported "3,862 rows with no
name", a line that read as a fact about the source and was a fact about the
probe. Where the two lists disagree, the records win and the disagreement is
the finding.

**C. Is there a seat in here, and is it dated?** The mandates are grouped by
whatever names the body (``gremium``-ish column, resolved from the rows), so
the Gemeinderat's own rows are *discovered* rather than guessed at — run 13's
lesson, one level down. Then: how many mandates are open, does that number
look like 125, and do they carry start and end dates.

**D. Could it ever join to Wikidata?** Every identifier column the records
carry, against what Wikidata holds. The expected answer is **no**: run 23
established that no Wikidata property holds a Gever GUID for the canton, and
nothing suggests the city differs. A "no" here is not a failure — it means a
Gever-sourced or Gever-enriched config would be **name-matched throughout**,
which ``quickstatements.is_mechanical`` already refuses, and which run 24
showed needs duplicate handling of its own: the canton's person-level key was
*nearly* a person key and not one.

**Nothing here gates anything**, for the same reason steps 7, 10 and 11 do not:
no config in this repo names this service, so no verdict it returns can change
what a pipeline run does, let alone what reaches ``suggestions.qs``. What a
good answer would license is an adapter or an enricher, and that is a decision
somebody makes, not a run.

One thing it deliberately does **not** do: decide between *source* and
*enrichment*. Those differ in what they are allowed to do — an enricher
produces no members and can only withhold — and the finding above (OpenParlData
has no seats for this body) is what makes the question live rather than
settled. The probe measures; the choice is in the config.

    uv run python scripts/verify_gever_city.py
    uv run python scripts/verify_gever_city.py --limit 200 --verbose
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wd_parliament.config import load_config  # noqa: E402
from wd_parliament.http_client import HttpClient  # noqa: E402

CONFIRMED = "CONFIRMED"
CONTRADICTED = "CONTRADICTED"
INCONCLUSIVE = "INCONCLUSIVE"

# The instance name in ``swissparlpy.backends.gever_config.INSTANCES``. Named
# rather than a URL, because the instance carries the whole index map and a URL
# would carry one endpoint.
INSTANCE = "gever_city_zurich"

# The indexes this probe reads. The rest of the instance's map is business
# records — Geschäfte, Sitzungen, Wortmeldungen — which say nothing about who
# holds a seat.
PERSON_INDEX = "kontakt"
MANDATE_INDEX = "behoerdenmandat"
CONTEXT_INDEXES = ("wahlkreis", "partei", "gremiumdetail")

# Seats in the Gemeinderat. The check it enables is the one that told the
# federal and cantonal probes apart from wishful reading: 200 and 46 open rows
# for the two federal chambers, 180 for the Kantonsrat. A number that is not
# the chamber's size is never CONFIRMED, whatever else looks healthy.
DEFAULT_SEATS = 125

# Everything is a candidate list resolved against the rows, never an assumption.
# See ``openparldata.BEGIN_FIELDS``: reading a column that does not exist
# returns ``None`` for every row, which is indistinguishable from a populated
# column full of nulls and means the opposite thing.
BODY_HINTS = ("gremium", "behoerde", "behörde", "rat", "organ")
BEGIN_HINTS = ("start", "von", "beginn", "eintritt", "dauer_start")
END_HINTS = ("ende", "bis", "austritt", "dauer_end")
NAME_HINTS = ("name", "nachname", "vorname", "titel")
DISTRICT_HINTS = ("wahlkreis", "kreis", "district")
PARTY_HINTS = ("partei", "party", "fraktion")
ROLE_HINTS = ("rolle", "funktion", "role")
# What a Gever key looks like. GUIDs are the canton's shape; the city may
# differ, which is why the hunt is over every column rather than these names.
ID_HINTS = ("guid", "_id", "id_", "nummer", "seq", "obj")

# How the chamber names itself. Matched by EQUALITY against a row's body
# column, never by substring: the **Stadtrat** is the city's nine-member
# executive, and `Büro des Gemeinderats` is an organ of the chamber. Both are
# in this service and neither is the Gemeinderat.
CHAMBER_NAMES = (
    "gemeinderat",
    "gemeinderat der stadt zürich",
    "gemeinderat stadt zürich",
    "gemeinderat zürich",
)

COLUMN_HEAD = 200
SAMPLE_ROWS = 3


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _norm(value: Any) -> str:
    return " ".join(_text(value).split()).casefold()


def columns_of(rows: Sequence[Dict[str, Any]]) -> "Counter[str]":
    """Every key any record carries, with how many carry a non-empty value.

    The count is what separates "no such column" from "column full of nulls",
    and those mean opposite things for a config — the first forbids a check,
    the second only makes it fire on nobody.
    """
    counts: "Counter[str]" = Counter()
    for row in rows:
        for key, value in row.items():
            counts.setdefault(key, 0)
            if value not in (None, "", [], {}):
                counts[key] += 1
    return counts


def resolve_column(
    columns: Iterable[str], hints: Sequence[str]
) -> Optional[str]:
    """The first column whose name contains one of ``hints``, hints in order.

    Deliberately weaker than :func:`verify_gever.resolve_column`, which matches
    leaf segments of a nested path: the backend flattens for us, so what is
    left is a flat name that may or may not carry a prefix. Ordered by hint
    rather than by column so the caller's preference wins, and ``None`` stays
    sayable — "no such column" is an answer this probe has to be able to give.
    """
    names = list(columns)
    for hint in hints:
        for name in names:
            if hint in name.casefold():
                return name
    return None


def is_the_chamber(value: Any) -> bool:
    """Does this body column name the Gemeinderat itself? Pure.

    Equality against :data:`CHAMBER_NAMES`, for the reason
    ``verify_kantonsrat.is_kantonsrat`` gives and one more: the city's
    executive is the *Stadtrat*, which shares no prefix with the legislature
    but sits in the same index, and its `Büro` does share one.
    """
    return _norm(value) in CHAMBER_NAMES


def _as_date(value: Any) -> Optional[date]:
    """An ISO-ish date off a row value, or ``None``. Pure and tolerant.

    One malformed date must not cost the whole measurement — the rule
    ``parliament._as_date`` states and this probe has no reason to break.
    """
    text = _text(value).strip()
    if not text:
        return None
    for fmt in (text, text.replace(" ", "T").split("T")[0]):
        try:
            return date.fromisoformat(fmt)
        except ValueError:
            continue
    # CMI also writes plain 8-digit dates (20220501).
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:]))
        except ValueError:
            return None
    return None


def classify_dates(
    rows: Sequence[Dict[str, Any]], begin_field: Optional[str], end_field: Optional[str]
) -> Tuple[str, str, List[str]]:
    """Are the mandates dated? Pure.

    INCONCLUSIVE — never CONTRADICTED — when a column is *absent*, the rule
    ``verify_openparldata.classify_seat_memberships`` exists for. A column that
    is not there and a column full of nulls are the same shape through
    ``.get()`` and mean opposite things.
    """
    lines: List[str] = []
    if not rows:
        return INCONCLUSIVE, "No mandate rows came back, so nothing was tested.", lines
    if begin_field is None:
        return (
            INCONCLUSIVE,
            "No column of these rows looks like a start date. That is 'no such "
            "column', which is not the same finding as 'the column is empty' — "
            "read the column list in B before concluding the mandates are "
            "undated.",
            lines,
        )
    dated = sum(1 for r in rows if _as_date(r.get(begin_field)))
    lines.append(f"start column: {begin_field}  ({dated} of {len(rows)} parse)")
    if end_field is None:
        lines.append("end column:   (none found)")
        open_rows = None
    else:
        ended = sum(1 for r in rows if _as_date(r.get(end_field)))
        open_rows = len(rows) - ended
        lines.append(
            f"end column:   {end_field}  ({ended} ended, {open_rows} open-ended)"
        )
    if dated == 0:
        return (
            CONTRADICTED,
            f"The column {begin_field!r} exists and no row carries a parseable "
            "date in it, so P580 has no source here.",
            lines,
        )
    if dated == len(rows):
        return (
            CONFIRMED,
            f"Every one of the {len(rows)} mandate rows carries a parseable "
            f"start date in {begin_field!r}"
            + (f", and {open_rows} are open-ended." if open_rows is not None else "."),
            lines,
        )
    return (
        CONFIRMED,
        f"{dated} of {len(rows)} mandate rows are dated. Partial coverage is a "
        "real answer: an undated row makes no P580 suggestion rather than a "
        "wrong one.",
        lines,
    )


def classify_seat_count(
    open_rows: int, people: int, expected: int = DEFAULT_SEATS
) -> Tuple[str, str]:
    """Does the number of open mandates look like the chamber? Pure.

    Counting **people** and not rows, for the reason run 14 paid for: a
    presiding member holds one seat and can carry two rows, and a guest carries
    a row and holds none. The verdict is on the distinct-people figure; the row
    count is printed beside it so the gap is visible.
    """
    if not open_rows:
        return (
            INCONCLUSIVE,
            "No open mandate rows, so this says nothing about the chamber's "
            "size. Either the index is historic only, or the body filter above "
            "matched the wrong rows.",
        )
    if people == expected:
        return (
            CONFIRMED,
            f"{people} people hold an open mandate, which is exactly the "
            f"chamber's {expected} seats — the strongest single signal that "
            "this index is both the right one and current.",
        )
    return (
        CONTRADICTED,
        f"{people} people hold an open mandate against {expected} seats "
        f"({open_rows} rows). Read the roles and the body values above before "
        "trusting either number: the cantonal probe's 186 was four future "
        "starts, a Gast and a row with no role, and its 177 was three "
        "presiding members whose presidium row IS their seat.",
    )


def classify_join(id_columns: Sequence[str]) -> Tuple[str, str]:
    """Could anything here join to Wikidata? Pure.

    The expected answer is no, and saying so precisely is the point: an
    identifier needs a value on **both** sides, and run 23 searched every field
    of the canton's Gever for every P13468 value Wikidata holds and found them
    in none. A "no" does not close the door on this source — it says the join
    would be by name, which is a different set of rules and not a smaller
    version of the same ones.
    """
    if not id_columns:
        return (
            INCONCLUSIVE,
            "No column of these records looks like an identifier at all, so "
            "there is nothing to compare against Wikidata.",
        )
    return (
        CONTRADICTED,
        f"{len(id_columns)} identifier-shaped column(s) here — "
        f"{', '.join(id_columns[:6])} — and **no Wikidata property holds a "
        "Gever key**, which run 23 established for the canton and nothing "
        "suggests differs for the city. So a Gever-sourced or Gever-enriched "
        "config is name-matched throughout: is_mechanical refuses every "
        "suggestion made that way, and the duplicate handling cannot be "
        "inherited from resolve — run 24 found the canton's person-level key "
        "was nearly a person key and not one.",
    )


def _sample(rows: Sequence[Dict[str, Any]], columns: Sequence[str], n: int = SAMPLE_ROWS) -> List[str]:
    """A few whole rows, narrowed to the columns that were resolved."""
    out: List[str] = []
    for row in rows[:n]:
        bits = [f"{c}={row.get(c)!r}" for c in columns if c]
        out.append("    " + "  ".join(bits))
    return out


def _verdict(name: str, verdict: str, detail: str) -> None:
    print(f"\n{name}: {verdict}")
    print(f"  {detail}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config/gemeinderat-zuerich.yaml")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after this many records per index (0 = all).",
    )
    parser.add_argument(
        "--expect-seats",
        type=int,
        default=DEFAULT_SEATS,
        help="Seats in the chamber. 125 for the Gemeinderat of Zürich.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every column and every distinct body value, not the head.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)

    import swissparlpy as spp

    def read(table: str) -> Tuple[List[Dict[str, Any]], Optional[int], Optional[str]]:
        """Rows, the server's total, and the error — an unreadable index is a
        finding about that index and never the end of the probe."""
        try:
            response = client.get_data(table, limit=args.limit or None)
            total = len(response)
            rows = [dict(r) for r in response]
            return rows, total, None
        except Exception as exc:
            return [], None, f"{exc}"

    # --- A. reach -----------------------------------------------------------
    print("=" * 70)
    print(f"A. Does the city's Gever answer, via swissparlpy instance {INSTANCE!r}?")
    print("=" * 70)
    try:
        client = spp.SwissParlClient(session=http.session, backend=INSTANCE)
        tables = sorted(client.get_tables())
    except Exception as exc:
        print(f"  ! could not build the client at all: {exc}")
        print("\nConnectivity or a missing backend, not a finding about the")
        print("source. swissparlpy >= 2.1 is what ships backends/gever.py.")
        return 1
    print(f"indexes configured ({len(tables)}): {', '.join(tables)}")
    print()

    counts: Dict[str, Optional[int]] = {}
    for table in (PERSON_INDEX, MANDATE_INDEX, *CONTEXT_INDEXES):
        if table not in tables:
            print(f"  {table:<20} NOT in this instance's index map")
            counts[table] = None
            continue
        _, total, error = read(table)
        counts[table] = total
        if error:
            print(f"  {table:<20} ! {error}")
        else:
            print(f"  {table:<20} {total} record(s)")
    print()
    print("  ('not in the map', 'errored' and 'zero rows' are three different")
    print("   findings; gever_config.py already documents one index that 404s.)")

    reach_verdict, reach_detail = INCONCLUSIVE, "Nothing was read."
    if counts.get(MANDATE_INDEX):
        reach_verdict, reach_detail = (
            CONFIRMED,
            f"The mandate index {MANDATE_INDEX!r} holds "
            f"{counts[MANDATE_INDEX]} record(s) and the person index "
            f"{PERSON_INDEX!r} holds {counts.get(PERSON_INDEX)}. This is the "
            "table OpenParlData is missing for body 261 — see the header.",
        )
    elif counts.get(MANDATE_INDEX) == 0:
        reach_verdict, reach_detail = (
            CONTRADICTED,
            f"{MANDATE_INDEX!r} answers and is empty, so this service has no "
            "mandates either and cannot fill the gap OpenParlData leaves.",
        )
    _verdict("A. Reach", reach_verdict, reach_detail)

    # --- B. the real column list -------------------------------------------
    print()
    print("=" * 70)
    print("B. What do the person and mandate records actually carry?")
    print("=" * 70)
    records: Dict[str, List[Dict[str, Any]]] = {}
    column_counts: Dict[str, "Counter[str]"] = {}
    for table in (PERSON_INDEX, MANDATE_INDEX):
        print(f"--- {table} ---")
        if counts.get(table) in (None, 0):
            print("  (nothing to read)")
            records[table] = []
            column_counts[table] = Counter()
            continue
        rows, _, error = read(table)
        records[table] = rows
        if error:
            print(f"  ! {error}")
            column_counts[table] = Counter()
            continue
        try:
            declared = sorted(client.get_variables(table))
        except Exception as exc:
            declared = []
            print(f"  ! schema unreadable: {exc}")
        present = columns_of(rows)
        column_counts[table] = present
        print(f"  schema declares: {len(declared)} variable(s)")
        print(f"  records carry:   {len(present)} column(s), {len(rows)} row(s)")
        undeclared = sorted(set(present) - set(declared))
        unseen = sorted(set(declared) - set(present))
        if undeclared:
            print(f"  in records, not in schema ({len(undeclared)}): "
                  f"{', '.join(undeclared[:20])}")
        if unseen:
            print(f"  in schema, not in records ({len(unseen)}): "
                  f"{', '.join(unseen[:20])}")
        print("  -> where the two disagree the RECORDS win. Run 22 reported "
              "'3,862 rows with no name' off a field list borrowed from a "
              "client rather than read from the service.")
        head = present.most_common() if args.verbose else present.most_common(COLUMN_HEAD)
        for name, filled in head:
            print(f"    {name:<44} {filled}/{len(rows)}")
        print()

    # --- C. is there a seat in here, and is it dated? -----------------------
    print("=" * 70)
    print(f"C. Is the Gemeinderat's seat here, and dated? (expecting "
          f"{args.expect_seats})")
    print("=" * 70)
    mandates = records.get(MANDATE_INDEX, [])
    mandate_columns = list(column_counts.get(MANDATE_INDEX, {}))
    body_field = resolve_column(mandate_columns, BODY_HINTS)
    begin_field = resolve_column(mandate_columns, BEGIN_HINTS)
    end_field = resolve_column(mandate_columns, END_HINTS)
    role_field = resolve_column(mandate_columns, ROLE_HINTS)
    person_ref = resolve_column(mandate_columns, ("kontakt", "person"))
    print(f"  body column:   {body_field or '(none)'}")
    print(f"  start column:  {begin_field or '(none)'}")
    print(f"  end column:    {end_field or '(none)'}")
    print(f"  role column:   {role_field or '(none)'}")
    print(f"  person column: {person_ref or '(none)'}")

    chamber_rows: List[Dict[str, Any]] = []
    if body_field:
        bodies = Counter(_text(r.get(body_field)) for r in mandates)
        print()
        print(f"  distinct {body_field} values: {len(bodies)}")
        for value, n in (bodies.most_common() if args.verbose
                         else bodies.most_common(30)):
            mark = "  <- THE CHAMBER" if is_the_chamber(value) else ""
            print(f"    {value!r} x{n}{mark}")
        chamber_rows = [r for r in mandates if is_the_chamber(r.get(body_field))]
        print()
        print(f"  rows whose body EQUALS the chamber: {len(chamber_rows)}")
        print("  (equality, never substring: the Stadtrat is the city's "
              "executive and the Büro is an organ of the chamber.)")
    else:
        print()
        print("  ! no body column, so the chamber's own rows cannot be "
              "separated from every other Behördenmandat in the city.")

    date_verdict, date_detail, date_lines = classify_dates(
        chamber_rows or mandates, begin_field, end_field
    )
    print()
    for line in date_lines:
        print("  " + line)
    if chamber_rows:
        print()
        print("  sample rows:")
        for line in _sample(
            chamber_rows, [c for c in (person_ref, body_field, role_field,
                                       begin_field, end_field) if c]
        ):
            print(line)

    open_rows = 0
    people = 0
    if chamber_rows and begin_field:
        today = date.today()
        seen_people = set()
        for row in chamber_rows:
            start = _as_date(row.get(begin_field))
            end = _as_date(row.get(end_field)) if end_field else None
            if start is None or start > today:
                continue
            if end is not None and end < today:
                continue
            open_rows += 1
            if person_ref:
                seen_people.add(_text(row.get(person_ref)))
        people = len(seen_people) or open_rows
        print()
        print(f"  open, already-begun mandate rows: {open_rows}")
        print(f"  distinct people among them:       {people}")
    seat_verdict, seat_detail = classify_seat_count(
        open_rows, people, args.expect_seats
    )
    _verdict("C. Dates", date_verdict, date_detail)
    _verdict("C. Size ", seat_verdict, seat_detail)

    # --- D. could it ever join to Wikidata? --------------------------------
    print()
    print("=" * 70)
    print("D. Is there anything here a Wikidata property could join on?")
    print("=" * 70)
    id_columns: List[str] = []
    for table in (PERSON_INDEX, MANDATE_INDEX):
        found = [
            name
            for name in column_counts.get(table, {})
            if any(hint in name.casefold() for hint in ID_HINTS)
        ]
        id_columns.extend(found)
        print(f"  {table}: {', '.join(sorted(found)) or '(none)'}")
    district_field = resolve_column(
        list(column_counts.get(PERSON_INDEX, {}))
        + list(column_counts.get(MANDATE_INDEX, {})),
        DISTRICT_HINTS,
    )
    party_field = resolve_column(
        list(column_counts.get(PERSON_INDEX, {}))
        + list(column_counts.get(MANDATE_INDEX, {})),
        PARTY_HINTS,
    )
    name_field = resolve_column(list(column_counts.get(PERSON_INDEX, {})), NAME_HINTS)
    print()
    print("  What the pipeline would want, and whether a column exists for it:")
    print(f"    P768 electoral district : {district_field or '(none)'}")
    print(f"    P102 political party    : {party_field or '(none)'}")
    print(f"    name, for the fallback  : {name_field or '(none)'}")
    join_verdict, join_detail = classify_join(sorted(set(id_columns)))
    _verdict("D. Join ", join_verdict, join_detail)

    # --- what it all means --------------------------------------------------
    print()
    print("=" * 70)
    print(f"A. Reach                         : {reach_verdict}")
    print(f"C. Mandates dated                : {date_verdict}")
    print(f"C. Open mandates == {args.expect_seats:<3}          : {seat_verdict}")
    print(f"D. A Wikidata-joinable id        : {join_verdict}")
    print("=" * 70)
    print(
        "Nothing here gates anything: no config in this repo names this "
        "service. A is what says whether the service could fill the gap "
        "OpenParlData leaves for body 261 at all; C decides whether it could "
        "carry P39 with P580/P582; D decides how a member would be matched, "
        "and a 'no' there means name matching, which is_mechanical already "
        "refuses. Choosing between source and enrichment is a decision, not a "
        "verdict this probe returns."
    )
    # Exit 0 whenever the service answered: a "no" is an answer, not a fault.
    return 0


if __name__ == "__main__":
    sys.exit(main())
