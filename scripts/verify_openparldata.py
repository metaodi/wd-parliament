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

The column names are resolved from the rows rather than assumed, and a column
that is **absent** is reported as INCONCLUSIVE rather than as empty. Those two
look identical through ``row.get()`` and mean opposite things: an earlier run
read ``date_start`` — which is what ``speeches`` calls it — and so reported all
5,618 seat memberships as undated when the field is ``begin_date``.

**C. Do the federal person records carry ``wikidata_id``?** A populated one is
the Q-ID itself. The first run measured 16.2% across all 26,574 people in the
API, but that is every Swiss parliament at every level; the federal subset is
the number that matters here.

**D. How does P14527 compare with P1307?** Measured 2026-07-29: P1307 on 3,719
items of which 3,043 are National Councillors, P14527 on 4,277 of which 3,025.
Near parity, so the useful number is not which is bigger but how many seat
holders carry P14527 and *not* P1307 — that is exactly what a second join path
would add.

Three things about querying it, from the API's own OpenAPI document:

- **the ``search`` parameter does not work through this backend.** Its
  ``search_mode`` defaults to ``partial`` (ILIKE substring, which is why a
  surname alone returned the wrong Andrey), and ``exact`` exists — but both
  returned **zero** rows for a German name or group, because swissparlpy
  hard-codes ``lang='en'`` with ``lang_format='flat'`` and the searchable
  columns are then the English ones. See :data:`EXACT`.
- **field filters are ordinary query parameters and do work**, so
  ``firstname=``, ``lastname=``, ``body_key=CHE`` and ``group_id=1663``
  narrow server-side. Prefer that to fetching a table and sieving it here —
  the first run pulled all 26,574 person records to measure a federal number.
- **``limit`` is the page size, not a cap on the result.** It is honoured, but
  swissparlpy's response iterator follows ``next_page`` to exhaustion, so
  iterating a query returns everything that matches regardless. ``len()`` on
  the response is ``meta.total_records`` off the first page, which
  :func:`count_only` uses to count without paging.

Note also that the backend logs *unrecognised* parameters as a warning and
sends them anyway rather than rejecting them, and the warning does not
interpolate the table name — it prints a literal ``'{table}'``. So a typo in a
filter is easy to miss; check the counts look plausible.

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


# What a membership might call its tenure bounds, most likely first. The live
# API uses ``begin_date`` / ``end_date``; ``date_start`` / ``date_end`` are what
# ``speeches`` uses, and assuming those here made the probe read a column that
# does not exist and report 5,618 seats as undated.
BEGIN_FIELDS = ("begin_date", "date_start", "start_date", "date_begin", "valid_from")
END_FIELDS = ("end_date", "date_end", "finish_date", "date_finish", "valid_to")


def _present(rows: Sequence[Dict[str, Any]], candidates: Sequence[str]) -> Optional[str]:
    """The first candidate that exists as a column on these rows. Pure."""
    keys = {key for row in rows for key in row}
    for candidate in candidates:
        if candidate in keys:
            return candidate
    return None


def date_columns(rows: Sequence[Dict[str, Any]]) -> List[str]:
    """Every date-ish column actually present, for reporting. Pure."""
    keys = {key for row in rows for key in row}
    return sorted(k for k in keys if "date" in k.lower() or "_at" in k.lower())


def classify_seat_memberships(
    rows: Sequence[Dict[str, Any]],
) -> Tuple[str, str, List[str]]:
    """Do these seat memberships carry usable dates? Pure.

    The question the evaluation turns on: a membership without a start yields
    no P580 and no period overlap, so it cannot source P39 however correctly it
    identifies the seat.

    The column names are **resolved from the rows**, never assumed. Reading a
    field that does not exist returns ``None`` for every row, which is
    indistinguishable from a populated column full of nulls unless the two are
    told apart deliberately — and they mean opposite things. That mistake made
    an earlier run report all 5,618 seat memberships as undated because it
    looked for ``date_start`` when the API calls it ``begin_date``. A missing
    column is therefore INCONCLUSIVE, never CONTRADICTED.
    """
    lines: List[str] = []
    if not rows:
        return (
            INCONCLUSIVE,
            "No memberships came back, so nothing was tested. Check the group "
            "id before concluding anything.",
            lines,
        )

    begin_field = _present(rows, BEGIN_FIELDS)
    end_field = _present(rows, END_FIELDS)
    kinds = sorted({_text(r.get("type_harmonized")) or "(none)" for r in rows})

    lines.append(f"memberships:            {len(rows)}")
    lines.append(f"type_harmonized values: {', '.join(kinds)}")
    lines.append(f"date-ish columns:       {', '.join(date_columns(rows)) or '(none)'}")
    lines.append(f"read as start / end:    {begin_field} / {end_field}")

    if begin_field is None:
        lines.append("")
        lines.append(f"  all columns: {', '.join(sorted(rows[0]))}")
        return (
            INCONCLUSIVE,
            "None of the expected start columns exists on these rows, so "
            "nothing was tested — this says nothing about whether the seat is "
            f"dated. Add the real column to BEGIN_FIELDS (candidates tried: "
            f"{', '.join(BEGIN_FIELDS)}) and re-run.",
            lines,
        )

    dated = [r for r in rows if _text(r.get(begin_field)).strip()]
    ended = [r for r in rows if end_field and _text(r.get(end_field)).strip()]
    lines.append(f"with a start:           {len(dated)}")
    lines.append(f"with an end:            {len(ended)}")
    lines.append("")
    for row in rows[:8]:
        lines.append(f"  {_describe_membership(row, begin_field, end_field)}")

    if not dated:
        return (
            CONTRADICTED,
            f"The column {begin_field!r} exists but is empty on all "
            f"{len(rows)} memberships. Without a start there is no P580 and no "
            "period overlap, so this cannot source P39 — MemberCouncil's "
            "DateJoining/DateLeaving remain the only source of a tenure.",
            lines,
        )

    share = 100.0 * len(dated) / len(rows)
    if len(dated) < len(rows):
        return (
            CONFIRMED,
            f"{len(dated)} of {len(rows)} memberships ({share:.1f}%) carry a "
            f"{begin_field}, so the seat tenure is here but incomplete. Usable "
            "as a cross-check on MemberCouncil.DateJoining; risky as the only "
            "source, since the members with no date would silently lose P580 "
            "and every P2937 term.",
            lines,
        )
    return (
        CONFIRMED,
        f"All {len(rows)} memberships carry a {begin_field}. This backend can "
        "source P39. Compare the dates against MemberCouncil.DateJoining "
        "before switching — that disagreement is README step 0c.",
        lines,
    )


def _describe_membership(
    row: Dict[str, Any],
    begin_field: Optional[str] = None,
    end_field: Optional[str] = None,
) -> str:
    parts = [f"type={row.get('type_harmonized')!r}"]
    for field in ("group_name_de", "person_name", "fullname", "role_name_de"):
        if row.get(field):
            parts.append(f"{field}={row[field]!r}")
    begin_field = begin_field or _present([row], BEGIN_FIELDS)
    end_field = end_field or _present([row], END_FIELDS)
    parts.append(f"{begin_field or 'start'}={row.get(begin_field)!r}")
    parts.append(f"{end_field or 'end'}={row.get(end_field)!r}")
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


def compare_counts(total: int, non_null: int, field: str) -> Tuple[str, str]:
    """Server-side confirmation that ``field`` is populated. Pure.

    ``exclude_null`` makes the API do the filtering, so this does not depend on
    the probe having picked the right column name in Python — the mistake that
    made an earlier run report every seat membership as undated. Only called
    once ``field`` is known to exist on the rows: handing ``exclude_null`` a
    column that is not there would leave the total unchanged and read as "all
    populated", which is the same false-confirmation in a new costume.
    """
    if total == 0:
        return INCONCLUSIVE, "No rows at all, so there is nothing to confirm."
    if non_null == total:
        return (
            CONFIRMED,
            f"The API reports {non_null} of {total} rows with a non-null "
            f"{field}, agreeing with the client-side count.",
        )
    if non_null == 0:
        return (
            CONTRADICTED,
            f"The API reports 0 of {total} rows with a non-null {field}.",
        )
    return (
        CONTRADICTED,
        f"The API reports {non_null} of {total} rows with a non-null {field}, "
        f"so {total - non_null} carry none.",
    )


# The API's own search modes (see its OpenAPI description). ``partial`` is the
# default and is ILIKE substring matching — which is why a surname alone found
# the wrong Andrey — and ``exact`` is a case-insensitive whole-value match.
#
# **But the ``search`` parameter is not usable through this backend.** Tried on
# 2026-07-29: ``search='Gerhard Andrey'`` and ``search='nationalrat'`` with
# ``search_mode=exact`` both returned **zero** rows, where the same lookups by
# *field filter* return the right ones. swissparlpy hard-codes ``lang='en'``
# with ``lang_format='flat'``, so the flattened searchable columns are the
# English ones and a German name matches nothing in them.
#
# Kept as a named constant so the finding is recorded rather than rediscovered.
# Use field filters — ``firstname=``, ``lastname=``, ``body_key=``,
# ``group_id=`` — which are ordinary query parameters and do work.
EXACT = {"search_mode": "exact", "search_scope": "metadata"}


# Combinations worth trying against the ``search`` parameter, which returns
# nothing under swissparlpy's defaults. The backend always sends ``search=""``
# with ``lang="en"``, ``lang_format="flat"`` and ``search_scope="all"``, and any
# of those could be the reason.
#
# Two notes on the parameters themselves. The language one is
# ``search_language`` — ``search_lang`` is **not** a parameter, and this backend
# forwards unrecognised names with only a warning, so a near-miss spelling is
# silently dropped rather than rejected. And ``%`` is only meaningful in
# ``partial`` mode, where the match is ILIKE and ``%`` is the wildcard; under
# ``exact`` it is a literal per cent sign and matches nothing.
SEARCH_VARIANTS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("swissparlpy defaults", {}),
    ("search=%", {"search": "%"}),
    ("search_language=de", {"search_language": "de"}),
    ("search=% + search_language=de", {"search": "%", "search_language": "de"}),
    ("search=% + scope=metadata", {"search": "%", "search_scope": "metadata"}),
    ("lang=de", {"lang": "de"}),
    ("search=% + lang=de", {"search": "%", "lang": "de"}),
)

# The same, for looking a chamber up by name rather than listing a table.
NAMED_VARIANTS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("exact", {"search_mode": "exact"}),
    ("exact + search_language=de", {"search_mode": "exact", "search_language": "de"}),
    ("exact + lang=de", {"search_mode": "exact", "lang": "de"}),
    ("partial + search_language=de", {"search_mode": "partial", "search_language": "de"}),
    ("partial + lang=de", {"search_mode": "partial", "lang": "de"}),
)


def summarise_variants(
    results: Sequence[Tuple[str, Optional[int]]], expected_at_least: int = 1
) -> Tuple[List[str], Optional[str]]:
    """Which parameter combinations returned rows. Pure.

    Returns the lines to print and the label of the first combination that
    worked, or ``None`` when every one came back empty. ``None`` is a real
    answer — it says the ``search`` parameter is unusable here, not that the
    data is missing, and the field-filter path is unaffected either way.
    """
    lines: List[str] = []
    winner: Optional[str] = None
    for label, count in results:
        if count is None:
            lines.append(f"  {label:<32} ERROR")
            continue
        mark = "" if count >= expected_at_least else "  (empty)"
        lines.append(f"  {label:<32} {count} row(s){mark}")
        if winner is None and count >= expected_at_least:
            winner = label
    lines.append("")
    if winner is None:
        lines.append(
            "  -> Every combination came back empty. The 'search' parameter is "
            "not usable through this backend; narrow with field filters "
            "instead. Nothing below depends on it."
        )
    else:
        lines.append(f"  -> First combination that returns rows: {winner}")
    return lines, winner


def fetch(
    client: Any, table: str, **params: Any
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Rows from one OpenParlData table, plus the error if it could not be read.

    Note this **iterates**, and the response iterator follows ``next_page`` to
    exhaustion — so ``limit`` bounds the page size, not the number of rows you
    get back. Use :func:`count_only` when a total is all that is wanted.
    """
    try:
        return [dict(r) for r in client.get_data(table, **params)], None
    except Exception as exc:  # an unreadable table is a finding, not a crash
        print(f"  ! {table}: {exc}")
        return [], f"{table}: {exc}"


def count_only(client: Any, table: str, **params: Any) -> Optional[int]:
    """``meta.total_records`` for a query, without paging through it.

    ``len()`` on the response is the server's total, read off the first page,
    so this costs one request however many rows match.
    """
    try:
        return len(client.get_data(table, **params))
    except Exception as exc:
        print(f"  ! {table} (count): {exc}")
        return None


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
            "Surname of that member. Looked up together with --first-name under "
            "search_mode=exact: the API's default 'partial' mode is ILIKE "
            "substring matching, so a surname alone found the wrong Andrey on "
            "2026-07-29 (a Fribourg cantonal member)."
        ),
    )
    parser.add_argument(
        "--body-key",
        default="CHE",
        help="The body whose members to measure. CHE is the Federal Assembly.",
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

    # --- E. which query parameters actually work? ----------------------------
    # An experiment, deliberately separate from the measurement: A-D narrow with
    # field filters, which work, so nothing here can regress them. Replacing a
    # working mechanism with an untested one is what made run 6 worse than 5.
    print()
    print("-" * 70)
    print("E. Does the 'search' parameter work with different parameters?")
    print("-" * 70)
    print("listing a table (bodies returns 0 under the defaults):")
    body_results = [
        (label, count_only(client, "bodies", **params))
        for label, params in SEARCH_VARIANTS
    ]
    body_lines, _ = summarise_variants(body_results)
    for line in body_lines:
        print(line)
    print()
    print("looking a chamber up by name (search='Nationalrat'):")
    named_results = [
        (label, count_only(client, "groups", search="Nationalrat", **params))
        for label, params in NAMED_VARIANTS
    ]
    named_lines, _ = summarise_variants(named_results)
    for line in named_lines:
        print(line)
    print()

    # The body is the *level*, not a chamber; reported for orientation only.
    # An earlier run read 0 rows here, which was blamed on the API. It is more
    # likely swissparlpy's defaults: it always sends search="" with
    # search_scope="all", so scope is pinned to metadata explicitly.
    bodies, _ = fetch(client, "bodies", indexed=True)
    print(f"bodies: {len(bodies)} row(s)")
    for row in bodies[:5]:
        seats = row.get("legislative_seats")
        suffix = f", legislative_seats={seats!r}" if seats is not None else ""
        print(f"  {_describe(row)}{suffix}")
    if not bodies:
        print("  (still empty; unexplained — the table is listed but returns "
              "nothing. Not load-bearing: the chambers are groups.)")
    print()

    # Narrow by body_key, which is a field filter and does work, then match the
    # chamber by name here. The `search` parameter returns nothing through this
    # backend (see EXACT), so the narrowing has to come from a filter.
    groups, _ = fetch(client, "groups", body_key=args.body_key)
    if not groups:
        print(f"  (no groups for body_key={args.body_key!r}; falling back to all)")
        groups, _ = fetch(client, "groups")
    print(f"groups searched: {len(groups)}")
    chambers, group_lines = find_chamber_groups(groups)
    for line in group_lines:
        print("  " + line if line else "")

    # --- B. do the chambers' memberships carry dates? -----------------------
    print()
    print("=" * 70)
    print("B. Do the chambers' memberships carry dates?")
    print("=" * 70)
    seat_verdict, seat_detail = INCONCLUSIVE, "No chamber group was found."
    seat_person_ids: set = set()
    for council, group in chambers.items():
        group_id = group.get("id")
        print(f"--- {council} (group {group_id}) ---")
        rows, _ = fetch(client, "memberships", group_id=group_id)
        seat_person_ids.update(
            r.get("person_id") for r in rows if r.get("person_id") is not None
        )
        verdict, detail, lines = classify_seat_memberships(rows)
        for line in lines:
            print("  " + line if line else "")

        # Independent confirmation: let the API do the null-filtering with
        # exclude_null, so the answer does not rest on this script having
        # guessed the column name. Only meaningful once the column is known to
        # exist, which classify_seat_memberships establishes.
        begin_field = _present(rows, BEGIN_FIELDS)
        if begin_field:
            total = count_only(client, "memberships", group_id=group_id)
            non_null = count_only(
                client, "memberships", group_id=group_id, exclude_null=begin_field
            )
            if total is not None and non_null is not None:
                server_verdict, server_detail = compare_counts(
                    total, non_null, begin_field
                )
                print(f"  server-side check: {server_verdict}: {server_detail}")

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
    full_name = f"{args.first_name} {args.last_name}"
    print(f"Sampling '{full_name}' by field filter\n")
    sample, _ = fetch(
        client,
        "persons",
        firstname=args.first_name,
        lastname=args.last_name,
        body_key=args.body_key,
    )
    for row in sample[:3]:
        print(f"  {row.get('id')} {row.get('fullname')!r} "
              f"body_key={row.get('body_key')!r} wikidata_id={row.get('wikidata_id')!r}")
    if sample:
        walked, _ = fetch(client, "memberships", person_id=sample[0].get("id"))
        seats = [r for r in walked if chamber_of(r)]
        begin_field = _present(walked, BEGIN_FIELDS)
        dated = [r for r in walked if begin_field and _text(r.get(begin_field)).strip()]
        print(f"  {len(walked)} membership(s), {len(dated)} with a "
              f"{begin_field or 'start'}, {len(seats)} naming a chamber outright")
        print(f"  date-ish columns: {', '.join(date_columns(walked)) or '(none)'}")
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
        print(f"  ! nobody matched firstname={args.first_name!r} "
              f"lastname={args.last_name!r}")
    print()

    # Narrow with body_key — one request instead of the 26,574 rows an early
    # run pulled — then keep the people who actually hold a seat. That, not
    # everyone attached to the Federal Assembly, is the population this tool
    # reconciles: body_key also carries Federal Councillors and staff.
    federal, _ = fetch(client, "persons", body_key=args.body_key)
    print(f"  persons with body_key={args.body_key!r}: {len(federal)}")
    sitting = [r for r in federal if r.get("active") in (True, "true", "True", 1)]
    if sitting:
        print(f"  of which active: {len(sitting)}")
    people = [r for r in federal if r.get("id") in seat_person_ids] or federal
    if seat_person_ids:
        print(f"  holding a seat membership from B: {len(people)}")
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
    print(f"C. wikidata_id on body {args.body_key:<6}: "
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
