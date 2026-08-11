"""Does the Staatsarchiv's KRRR export carry P13468, and what else is in it?

README step 11. **KRRR is "Kantonsrat und Regierungsrat" — the same two bodies
P13468 names**, exported straight from the application the canton's register
runs on:

    https://www.web.statistik.zh.ch:8443/KRRR/app?show_page=EXCEL&operation=EXCEL

**Run 25 (2026-08-10) answered it: ``id_person_new`` carries the P13468 value
for 583 of the 638 people compared.** After three sources failed, this one is
the register. Section D confirmed the property resolves through
``wahlen.zh.ch/krdaten_staatsarchiv/abfrage.php?id=$1`` — a claim this repo had
inherited and never measured.

The three failures are why it was measured rather than assumed, and they are
still the argument against assuming the next one:

- **OpenParlData** (run 20): 28 of 35 linked items carry P13468, **0 of 28**
  values are the person id, and the values appear in **no column**.
- **the canton's Gever** (run 23): 130 of 130 values appear in **no field**.
  Being the canton's own business system was not enough.
- **the openZH `krdb-lod` CSV export** (measured 2026-08-05, offline): the
  authoritative *Linked Open Data* dump of this same register — and its
  ``ID_PERSON_NEW`` is a third id space. Sonja Rueff-Frenkel is **17943**
  there against a P13468 of 21984; Ruth Genner 2408 against 22518; Ueli Maurer
  5399 against 22382. Searching every column of all eight files for those
  values returns nothing, and the other id column (``ID_PERSON``) is a slug
  (``Rueff-Frenkel_Sonja_1972_11_15``). Its ids also sit in the *same numeric
  band* as P13468's, which is the "two id spaces that overlap numerically"
  failure ``identifier_verified`` exists to catch: a join on it would match
  confidently and write 17943 where 21984 belongs.

So "this is the authoritative source" was true four times and sufficient once.
**Authoritative about the people is not the same as publishing the
identifier**, and only a value comparison can tell them apart.

**When the two disagree, the register wins.** That is the whole premise of this
tool — the source is authoritative, so a difference is a *suggested Wikidata
edit*, not evidence against the join. What a disagreement cannot be allowed to
hide is the other reading: two id spaces that overlap numerically match
exactly and match the wrong people. The two are told apart by degree and
nothing else, which is what ``AGREEMENT_THRESHOLD`` is for — and by the birth
date, which separates "the same human with two numbers" from "two humans".

Five sections:

**A. Reach and format.** The URL is an *application export*, not a file: what
comes back is unknown until it is measured. A Java application of this vintage
may serve OOXML, legacy BIFF, an HTML table under an Excel content-type, or a
delimited text file — so the payload is **sniffed** rather than trusted, and
the sniff is reported. Nothing here depends on a new package: OOXML is a zip of
XML and is read with :mod:`zipfile` and :mod:`xml.etree`, HTML with
:mod:`html.parser`, delimited text with :mod:`csv`. Legacy BIFF is the one
format this cannot read, and it says so rather than guessing.

**B. The real column list**, printed rather than inferred — run 19's rule.

**C. P13468.** Every item Wikidata gives the property to, matched by name, and
its value compared against **every column** of that person's rows. This is the
question the step exists for.

**D. P13468's own formatter URL**, read from Wikidata (P1630). This repo has
been asserting that the property resolves through
``wahlen.zh.ch/krdaten_staatsarchiv/`` since it was first written down, and
that claim was **inherited, never measured**. A probe that reports a register
should be able to say where the register is.

**E. What else the export carries** — the seat dates, party, district and
personal columns a config would read, counted **across every sheet**. The
workbook has several (Personen, Einsitze, …), and the first version of this
probe read only the first one and duly reported that the export "has no seat
columns" — a statement about the reader that read as a statement about the
source. That is this session's recurring failure, and section B printing every
sheet's columns is the guard.

**Nothing here gates anything.** No config names this service.

    uv run python scripts/verify_krrr.py
    uv run python scripts/verify_krrr.py --verbose
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
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wd_parliament.config import load_config  # noqa: E402
from wd_parliament.workbook import (  # noqa: E402
    FMT_BIFF,
    FMT_DELIMITED,
    FMT_HTML,
    FMT_OOXML,
    FMT_UNKNOWN,
    SINGLE_SHEET,
    Sheets,
    resolve_column,
    _grid_to_rows,
    find_column,
    parse_export,
    read_delimited,
    read_html_table,
    read_xlsx,
    sniff_format,
)
from wd_parliament.http_client import HttpClient  # noqa: E402
from wd_parliament.models import P_ZH_MEMBER_ID  # noqa: E402
from wd_parliament.wikidata import WikidataClient  # noqa: E402

# The identifier hunt is the same question ``verify_gever`` asks of a different
# service, and these are pure and already covered by tests. Reusing them keeps
# the two probes' answers comparable — a second copy that drifted would make
# "Gever says no, KRRR says yes" unreadable.
from verify_gever import (  # noqa: E402
    CONFIRMED,
    CONTRADICTED,
    INCONCLUSIVE,
    classify_identifier,
    column_report,
    find_identifier_columns,
    index_by_name,
    name_key,
    resolve_columns,
    wanted_by_name,
    zh_member_id_query,
)

KRRR_URL = (
    "https://www.web.statistik.zh.ch:8443/KRRR/app?show_page=EXCEL&operation=EXCEL"
)

# Columns a ZH config would want, by the property they would feed. Candidates
# only — the export's real names are unknown until section B prints them, and
# ``resolve_column`` finds a name that differs only in nesting or prefix.
FIELD_CANDIDATES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "P569": ("date of birth", ("geburtsdatum", "datum_geburt", "geburtstag")),
    "P570": ("date of death", ("todesdatum", "datum_tod")),
    "P106": ("occupation", ("beruf",)),
    "P102": ("political party", ("partei", "parteibezeichnung", "fraktion")),
    "P768": ("electoral district", ("wahlkreis",)),
    # The register splits every date into three columns, because a record
    # going back to 1803 routinely knows the year and not the day. So the
    # candidates name the *year* part: it is the one always present, and a
    # year-only date is a real value at reduced precision rather than a gap.
    # Run 28 reported "no such column" for `eintritt`/`datum_eintritt` — a
    # gap in this list, not in the export.
    "P580": ("start time", ("datum_eintritt_jahr", "eintritt_jahr")),
    "P582": ("end time", ("datum_austritt_jahr", "austritt_jahr")),
}

# Identifier columns the openZH CSV of this same register carries, which a
# richer export might too. They are *real* Wikidata properties, and the reason
# to look: an identifier this tool could actually verify beats a name match.
# (In the CSV they reach 8, 11 and 0 of the 180 sitting members, so they are a
# historic-notables join, not a chamber join — section E counts them here.)
# How much agreement establishes that a column and a property are the *same*
# id space rather than two that overlap numerically. Run 25 measured 583 of
# 638 (91.4%) before any corrections. The threshold exists because the two
# readings are told apart by degree and nothing else: a handful of matches
# among thousands of five-digit numbers is coincidence, and nine in ten is not.
AGREEMENT_THRESHOLD = 85.0

KNOWN_IDENTIFIER_COLUMNS = {
    "GND": "P227",
    "VIAF": "P214",
    "HLS": "P902",
}


# --- pure: what the export can feed -----------------------------------------
def identifier_columns(headers: Sequence[str]) -> Dict[str, str]:
    """Which known-identifier columns the export has → the property. Pure."""
    found: Dict[str, str] = {}
    for column, prop in KNOWN_IDENTIFIER_COLUMNS.items():
        resolved = resolve_column(headers, column.lower())
        if resolved:
            found[resolved] = prop
    return found


def filled(rows: Sequence[Dict[str, str]], column: str) -> int:
    """How many rows carry a value in this column. Pure."""
    return sum(1 for row in rows if (row.get(column) or "").strip())


def iso_date(value: Any) -> str:
    """``'1897-05-04 00:00:00.0'`` and ``'1897-05-04T00:00:00Z'`` → the day.

    Pure. Anything that is not a plain ``YYYY-MM-DD`` prefix is absence, for
    the reason ``verify_gever.plausible_year`` gives: a value that cannot be
    read is not evidence, and treating it as evidence takes the strongest
    conclusion from the weakest input.
    """
    text = str(value or "").strip().replace("T", " ")[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text[:4].isdigit() and text[5:7].isdigit() and text[8:10].isdigit():
            return text
    return ""


def diagnose_misses(
    index: Dict[str, List[Dict[str, str]]],
    wanted: Dict[str, str],
    id_column: str,
    limit: int = 12,
    wanted_birth: Optional[Dict[str, str]] = None,
    birth_column: str = "",
) -> Tuple[Dict[str, int], List[str]]:
    """Why did a name match but its identifier not? Pure.

    Run 25 found ``id_person_new`` carrying the P13468 value for 583 of 638
    people and nothing carrying it for the other 55, and
    :func:`classify_identifier` calls that a partial match — correctly, because
    from counts alone it cannot be told from a column that merely *collides*
    numerically. But a miss has two opposite causes and only one of them is
    about the data:

    - **the name reached the wrong person.** The index is keyed by name, and a
      register spanning 1803 to today is full of namesakes. Comparing
      Wikidata's value for one Hans Müller against the register's id for
      another says nothing about the column.
    - **the register really holds a different value** for a person the name
      identifies unambiguously. *That* is a disagreement, and the thing an
      identifier claim cannot survive.

    So each miss is sorted by whether the name is unique in the export. Same
    discipline as ``verify_gever.classify_row_key``'s namesake test, and the
    same refusal to arbitrate: an ambiguous name is reported as ambiguous
    rather than resolved in whichever direction flatters the column.
    """
    counts = {
        "ambiguous name": 0,
        "different person": 0,
        "value differs": 0,
        "undecided": 0,
        "no value in export": 0,
    }
    lines: List[str] = []
    wanted_birth = wanted_birth or {}
    for name, value in sorted(wanted.items()):
        rows = index.get(name)
        if not rows:
            continue
        held = {
            (row.get(id_column) or "").strip()
            for row in rows
            if (row.get(id_column) or "").strip()
        }
        if str(value).strip() in held:
            continue
        if len(held) > 1:
            kind = "ambiguous name"
            detail = f"the export has {len(held)} people so named: {sorted(held)}"
        elif not held:
            kind = "no value in export"
            detail = f"the export's {id_column} is empty for this person"
        else:
            got = held.pop()
            detail = f"export {id_column}={got!r} against Wikidata {value!r}"
            # A name matching the *wrong* register person looks exactly like a
            # register that holds a different id. The birth date is what tells
            # them apart, and without one neither reading is available.
            theirs = iso_date(wanted_birth.get(name))
            ours = iso_date(rows[0].get(birth_column)) if birth_column else ""
            if not theirs or not ours:
                kind = "undecided"
                detail += "  (no birth date on one side)"
            elif theirs != ours:
                kind = "different person"
                detail += f"  (born {ours} here, {theirs} on Wikidata)"
            else:
                kind = "value differs"
                detail += f"  (both born {ours})"
        counts[kind] += 1
        if len(lines) < limit:
            lines.append(f"{name}: {detail}  [{kind}]")
    return counts, lines


def classify_misses(counts: Dict[str, int], matched: int) -> Tuple[str, str]:
    """What the miss breakdown means for the join. Pure.

    Only ``value differs`` — same human by birth date, different id — can
    falsify the identifier. ``different person`` and ``ambiguous name`` are
    facts about the *name* match this probe uses to line two lists up, and
    ``undecided`` is the bucket that refuses to guess.
    """
    differs = counts.get("value differs", 0)
    wrong_person = counts.get("different person", 0)
    ambiguous = counts.get("ambiguous name", 0)
    undecided = counts.get("undecided", 0)
    empty = counts.get("no value in export", 0)
    artefacts = wrong_person + ambiguous + empty
    if not (differs or artefacts or undecided):
        return (
            CONFIRMED,
            f"Every one of the {matched} people compared carries the "
            "identifier, with nothing left to explain.",
        )
    if differs:
        agreed = matched - differs - artefacts - undecided
        share = (agreed / matched * 100) if matched else 0.0
        # **A disagreement is this tool's product, not its disqualification.**
        # The whole design rests on the source being authoritative: when the
        # register and Wikidata differ, the register wins and the difference
        # becomes a suggested edit. So the question a miss answers is not "may
        # the column be joined on" but "which items need fixing" — as long as
        # the agreeing majority is large enough to establish that the two are
        # the *same id space* at all. Below that, the numbers could be
        # coincidence, which is the failure identifier_verified guards.
        if share >= AGREEMENT_THRESHOLD:
            return (
                CONFIRMED,
                f"{agreed} of {matched} ({share:.1f}%) agree, which settles "
                "that the column and the property are one id space. The "
                f"{differs} person(s) with the same birth date and a different "
                "number are **Wikidata errors to fix, not evidence against the "
                "join** — they are exactly the suggestion this tool exists to "
                f"make. ({artefacts} further miss(es) are name-matching "
                f"artefacts and {undecided} could not be decided.)",
            )
        return (
            CONTRADICTED,
            f"Only {agreed} of {matched} ({share:.1f}%) agree, and {differs} "
            "person(s) with the same birth date on both sides carry a "
            "different value. Below "
            f"{AGREEMENT_THRESHOLD:.0f}% the agreement could be two id spaces "
            "overlapping numerically rather than one shared one, which is the "
            "failure identifier_verified guards against: a join would match "
            "exactly and match the wrong people. "
            f"({artefacts} artefact(s), {undecided} undecided.)",
        )
    if undecided:
        return (
            INCONCLUSIVE,
            f"No miss is a disagreement between two people the birth date "
            f"shows to be the same ({artefacts} are matching artefacts), but "
            f"{undecided} lack a birth date on one side and so cannot be told "
            "apart from one. The column looks like the identifier; what is "
            "missing is the evidence to say so about those people.",
        )
    return (
        CONFIRMED,
        f"All {artefacts} unexplained people are matching artefacts rather "
        f"than disagreements ({ambiguous} name(s) the export gives to several "
        f"people, {wrong_person} where the birth dates show the name reached a "
        f"different person, {empty} with no value there). No person the birth "
        "date confirms is the same disagrees with Wikidata, so the column is "
        "the identifier and the shortfall belongs to the *name* match this "
        "probe uses to line the two lists up.",
    )


# --- the SPARQL side ---------------------------------------------------------
def birth_dates_query(language: str = "de") -> str:
    """P13468 holders with their birth dates, to arbitrate the misses.

    Asked as a second bounded query rather than folded into
    ``zh_member_id_query``: that one is shared with ``verify_gever``, and
    widening a query two probes read to serve one of them is how they stop
    being comparable. P569 is ``OPTIONAL`` because an item without one is the
    *undecided* answer, not an absent row.
    """
    return f"""
SELECT ?person ?personLabel ?birth WHERE {{
  ?person wdt:{P_ZH_MEMBER_ID} ?value .
  OPTIONAL {{ ?person wdt:P569 ?birth . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},en". }}
}}
"""


def births_by_name(bindings: Sequence[dict]) -> Dict[str, str]:
    """WDQS rows → ``name key -> birth date``. Pure.

    A name several items claim with *different* birth dates is dropped, the
    same refusal :func:`verify_gever.wanted_by_name` applies to the values: a
    birth date read off the wrong item would arbitrate the miss in a direction
    nobody measured.
    """
    seen: Dict[str, set] = {}
    for row in bindings:
        label = (row.get("personLabel") or {}).get("value", "")
        birth = iso_date((row.get("birth") or {}).get("value", ""))
        key = name_key(label)
        if not key or not birth:
            continue
        seen.setdefault(key, set()).add(birth)
    return {k: v.pop() for k, v in seen.items() if len(v) == 1}


# The canton of Zürich, kept so a reader of section F can tell at a glance
# whether a candidate district is even in the right canton. Not used to bound
# a query any more: run 29's class-first attempt timed out on WDQS, and the
# search API answers 18 cheap questions where SPARQL could not answer one.
CANTON_ZURICH = "Q11943"


def search_entities(
    http: HttpClient, term: str, language: str = "de", limit: int = 5
) -> List[Dict[str, str]]:
    """Ask Wikidata's search for items matching a name. Bounded, not SPARQL.

    Run 29's candidate query timed out, and deservedly: it asked for every
    item with any ``P31`` whose type label mentions a constituency and only
    then narrowed to the canton, which is a scan of Wikidata before the
    selective clause runs. The search API asks 18 cheap questions instead of
    one impossible one.

    **This is a search, and run 13's rule about searches still holds**: the
    Q-ID it returns is a *candidate*. The probe prints it with its description
    and fills nothing in — a name that looks right is how ``Q19479543``, a
    Wikimedia category held by nobody, got as far as being a default.
    """
    data = http.get_json(
        "https://www.wikidata.org/w/api.php",
        params={
            "action": "wbsearchentities",
            "search": term,
            "language": language,
            "uselang": language,
            "type": "item",
            "limit": limit,
            "format": "json",
        },
    )
    out = []
    for hit in data.get("search", []):
        out.append(
            {
                "qid": hit.get("id", ""),
                "label": hit.get("label", ""),
                "description": hit.get("description", ""),
            }
        )
    return out


def district_detail_query(qids: Sequence[str], language: str = "de") -> str:
    """What *are* these candidates? Bounded by the Q-IDs already in hand.

    A label alone cannot tell a Wahlkreis from a district of the same name, a
    Wikimedia category or a municipality. Type (P31) and container (P131) can,
    and asking about a handful of known items is cheap where asking about a
    class was not.
    """
    values = " ".join(f"wd:{q}" for q in qids)
    return f"""
SELECT ?item ?itemLabel ?typeLabel ?inLabel WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{ ?item wdt:P31 ?type . }}
  OPTIONAL {{ ?item wdt:P131 ?in . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},en". }}
}}
"""


def search_terms(district: str) -> List[str]:
    """The register's spelling → what to actually search for. Pure.

    ``'9. Wahlkreis (Horgen)'`` is not a name anybody else uses. The part in
    brackets is the district's real name and the prefix is the register's
    numbering, so both are tried: the bracketed name first, because
    ``'Wahlkreis Horgen'`` is the form a Wikidata label would take.
    """
    text = " ".join(str(district or "").split())
    terms: List[str] = []
    if "(" in text and ")" in text:
        inner = text[text.index("(") + 1 : text.rindex(")")].strip()
        if inner:
            terms.append(f"Wahlkreis {inner}")
            terms.append(inner)
    if text and text not in terms:
        terms.append(text)
    return terms


def district_usage_query(position_qid: str) -> str:
    """How many P39 statements for the seat already carry a P768, and which.

    Run 15 found 3 of 270, and all three naming *city of Zürich quarters*
    rather than Wahlkreise — the wrong kind of thing, which is why the config's
    map ships empty. Re-asked here because "there is no convention yet" is a
    claim with a date on it, and because this tool only ever *adds*: a member
    already carrying a wrong P768 would end up with two values, not a fix.
    """
    return f"""
SELECT ?district ?districtLabel (COUNT(?person) AS ?count) WHERE {{
  ?person p:P39 ?st .
  ?st ps:P39 wd:{position_qid} .
  ?st pq:P768 ?district .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "de,en". }}
}}
GROUP BY ?district ?districtLabel
ORDER BY DESC(?count)
"""


def district_numbers(values: Sequence[str]) -> Dict[str, str]:
    """``'3. Wahlkreis (Zürich 4+5)'`` → ``{'3': the whole string}``. Pure.

    The register numbers its districts and Wikidata will not, so the number is
    the only token the two sides can be lined up on by machine. Everything
    else about the alignment is a reader's job.
    """
    out: Dict[str, str] = {}
    for value in values:
        head = str(value).strip().split(".", 1)[0].strip()
        if head.isdigit():
            out.setdefault(head, str(value).strip())
    return out


def formatter_url_query() -> str:
    """P13468's own formatter URL (P1630) and its label.

    Section D exists because this repo has been repeating a formatter URL it
    never measured. A property's URL template is what turns a printed number
    into a page a reader can open (``models.IDENTIFIER_PROPERTIES``), so a
    wrong one sends every reader of a cantonal report to a page that cannot
    resolve the number beside the member's name.
    """
    return f"""
SELECT ?formatter ?label WHERE {{
  OPTIONAL {{ wd:{P_ZH_MEMBER_ID} wdt:P1630 ?formatter . }}
  OPTIONAL {{
    wd:{P_ZH_MEMBER_ID} rdfs:label ?label .
    FILTER(LANG(?label) = "en")
  }}
}}
"""


def _get(http: HttpClient, url: str) -> Tuple[bytes, str, int]:
    """GET raw bytes through the shared client, which throttles and retries."""
    http._throttle()
    resp = http.session.get(url, timeout=http.timeout)
    return resp.content, resp.headers.get("Content-Type", ""), resp.status_code


def _verdict(name: str, verdict: str, detail: str) -> None:
    print(f"\n{name}: {verdict}")
    print(f"  {detail}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config/kantonsrat-zh.yaml")
    parser.add_argument("--url", default=KRRR_URL, help="The KRRR Excel export.")
    parser.add_argument(
        "--verbose", action="store_true", help="Print every column, not the head."
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)


    # --- A. reach and format ------------------------------------------------
    print("=" * 70)
    print("A. Does the KRRR export answer, and what is in it?")
    print("=" * 70)
    print(f"url: {args.url}")
    try:
        content, content_type, status = _get(http, args.url)
    except Exception as exc:
        print(f"  ! could not fetch it: {exc}")
        print(
            "\nConnectivity, not a finding. Note the port is 8443 and the "
            "origin reset the TLS handshake when this was tried from a "
            "sandbox — if it fails here too, it is the service, not the probe."
        )
        return 1

    print(f"HTTP {status}, {len(content)} bytes, Content-Type: {content_type!r}")
    fmt, sheets, error = parse_export(content, content_type)
    print(f"sniffed format: {fmt}")
    if error:
        print(f"  ! {error}")
        print(f"  first bytes: {content[:80]!r}")
        if not sheets:
            print("\nNothing below could be measured.")
            return 1
    print(f"sheets: {len(sheets)}")
    for sheet, (headers, rows) in sheets.items():
        print(f"  {sheet:<24} {len(rows):>6} rows, {len(headers):>3} columns")

    # --- B. the real column list -------------------------------------------
    print()
    print("=" * 70)
    print("B. Which columns does each sheet actually have?")
    print("=" * 70)
    reports: Dict[str, List[Tuple[str, int, Optional[str]]]] = {}
    for sheet, (headers, rows) in sheets.items():
        report = column_report(rows)
        reports[sheet] = report
        print(f"\n-- {sheet} ({len(rows)} rows)")
        shown = report if args.verbose else report[:40]
        width = max((len(c) for c, _, _ in shown), default=20)
        for column, count, sample in shown:
            text = "" if sample is None else f"  e.g. {sample[:36]!r}"
            print(f"  {column:<{width}} {count:>6}/{len(rows)}{text}")
        if len(shown) < len(report):
            print(f"  ... {len(report) - len(shown)} more (--verbose for all)")

    # --- C. P13468 ----------------------------------------------------------
    print()
    print("=" * 70)
    print(f"C. Does the KRRR export carry {P_ZH_MEMBER_ID}?")
    print("=" * 70)
    wikidata = WikidataClient(http)
    try:
        bindings = wikidata.run_query(zh_member_id_query(config.language))
    except Exception as exc:
        print(f"  ! could not read Wikidata: {exc}")
        bindings = []
    wanted, read, dropped = wanted_by_name(bindings) if bindings else ({}, 0, 0)
    if bindings:
        print(f"items carrying {P_ZH_MEMBER_ID}: {read}")
        print(f"  usable names: {len(wanted)}; dropped as ambiguous: {dropped}")

    births: Dict[str, str] = {}
    if bindings:
        try:
            births = births_by_name(
                wikidata.run_query(birth_dates_query(config.language))
            )
        except Exception as exc:
            print(f"  ! could not read birth dates: {exc}")

    # Every sheet that names people is asked, because which one is the person
    # table is a fact about the export, not something to assume from a name.
    verdict, detail = INCONCLUSIVE, "No sheet could be compared."
    for sheet, (headers, rows) in sheets.items():
        name_field = resolve_column(headers, "nachname") or resolve_column(
            headers, "name"
        )
        if not name_field or not wanted:
            continue
        first_field = resolve_column(headers, "vorname")
        index, unnamed = index_by_name(
            rows, name_field=name_field, first_name_field=first_field or "vorname"
        )
        if not index:
            continue
        print(f"\n-- {sheet}: names from {name_field!r} + {first_field!r}")
        print(f"   distinct people: {len(index)}; rows with no name: {unnamed}")
        counts, compared, missing, examples = find_identifier_columns(index, wanted)
        print(f"   matched: {compared}; not in this sheet: {missing}")
        if counts:
            for column, hits in list(counts.items())[:6]:
                print(f"     {column:<30} {hits}/{compared}")
        else:
            print("     no column carries the value")
        sheet_verdict, sheet_detail = classify_identifier(counts, compared, compared)
        print(f"   verdict: {sheet_verdict}")

        if counts:
            best = next(iter(counts))
            birth_column = resolve_column(headers, "datum_geburt") or resolve_column(
                headers, "geburtsdatum"
            )
            miss_counts, miss_lines = diagnose_misses(
                index, wanted, best,
                wanted_birth=births, birth_column=birth_column or "",
            )
            print(f"   misses, arbitrated by {birth_column!r} against P569:")
            for kind, n in miss_counts.items():
                print(f"     {kind:<22} {n}")
            for line in miss_lines[:6]:
                print(f"       {line}")
            miss_verdict, miss_detail = classify_misses(miss_counts, compared)
            if verdict != CONFIRMED:
                verdict, detail = miss_verdict, f"[{sheet}] {miss_detail}"
    _verdict(f"Verdict ({P_ZH_MEMBER_ID})", verdict, detail)

    # --- D. where does P13468 actually resolve? -----------------------------
    print()
    print("=" * 70)
    print(f"D. {P_ZH_MEMBER_ID}'s own formatter URL, from Wikidata")
    print("=" * 70)
    try:
        for row in wikidata.run_query(formatter_url_query()):
            label = (row.get("label") or {}).get("value", "")
            formatter = (row.get("formatter") or {}).get("value", "")
            print(f"  label:     {label or '(none)'}")
            print(f"  P1630:     {formatter or '(the property has no formatter URL)'}")
    except Exception as exc:
        print(f"  ! could not read Wikidata: {exc}")

    # --- E. what else it carries -------------------------------------------
    print()
    print("=" * 70)
    print("E. What else could this export feed?")
    print("=" * 70)
    for prop, (label, candidates) in FIELD_CANDIDATES.items():
        found = find_column(sheets, candidates)
        if not found:
            print(f"  {prop} {label:<18} no such column: {', '.join(candidates)}")
            continue
        parts = []
        for sheet, column in found:
            rows = sheets[sheet][1]
            parts.append(f"{sheet}.{column} {filled(rows, column)}/{len(rows)}")
        print(f"  {prop} {label:<18} " + " | ".join(parts))

    print("\n  identifier columns (a join this tool could actually verify):")
    any_found = False
    for column, prop in KNOWN_IDENTIFIER_COLUMNS.items():
        for sheet, resolved in find_column(sheets, (column.lower(),)):
            rows = sheets[sheet][1]
            print(
                f"    {sheet}.{resolved:<24} {filled(rows, resolved):>6}/{len(rows)}"
                f"  -> {prop}"
            )
            any_found = True
    if not any_found:
        print("    none of " + ", ".join(KNOWN_IDENTIFIER_COLUMNS))

    # --- F. can the districts be mapped? ------------------------------------
    print()
    print("=" * 70)
    print("F. Could P768 be filled in from the register's Wahlkreis?")
    print("=" * 70)
    district_cols = find_column(sheets, ("wahlkreis",))
    districts: List[str] = []
    for sheet, column in district_cols:
        rows = sheets[sheet][1]
        open_year = resolve_column(sheets[sheet][0], "datum_austritt_jahr")
        for row in rows:
            if open_year and (row.get(open_year) or "").strip():
                continue  # a seat somebody has left; its district may be historic
            value = " ".join((row.get(column) or "").split())
            if value and value not in districts:
                districts.append(value)
    print(f"districts among *open* seats: {len(districts)}")
    numbered = district_numbers(districts)
    for number, name in sorted(numbered.items(), key=lambda kv: int(kv[0])):
        print(f"  {number:>2}. {name}")
    unnumbered = [d for d in districts if d not in numbered.values()]
    if unnumbered:
        # Naming it rather than counting it: run 29 reported "1 not numbered"
        # and left a reader unable to tell a stray blank from a real district
        # the alignment would silently drop.
        print(f"  not numbered, so unalignable ({len(unnumbered)}):")
        for value in unnumbered:
            print(f"      {value!r}")

    print("\ndoes Wikidata have items for them? (candidates, not answers)")
    candidates: Dict[str, List[Dict[str, str]]] = {}
    for number, name in sorted(numbered.items(), key=lambda kv: int(kv[0])):
        hits: List[Dict[str, str]] = []
        for term in search_terms(name):
            try:
                hits = search_entities(http, term, config.language, limit=3)
            except Exception as exc:
                print(f"  ! search failed for {term!r}: {exc}")
                hits = []
            if hits:
                break
        candidates[number] = hits

    detail: Dict[str, Tuple[str, str]] = {}
    qids = [h["qid"] for hits in candidates.values() for h in hits if h.get("qid")]
    if qids:
        try:
            for row in wikidata.run_query(
                district_detail_query(sorted(set(qids)), config.language)
            ):
                qid = (row.get("item") or {}).get("value", "").rsplit("/", 1)[-1]
                detail[qid] = (
                    (row.get("typeLabel") or {}).get("value", ""),
                    (row.get("inLabel") or {}).get("value", ""),
                )
        except Exception as exc:
            print(f"  ! could not read the candidates' types: {exc}")

    for number, hits in sorted(candidates.items(), key=lambda kv: int(kv[0])):
        print(f"  {number:>2}. {numbered[number]}")
        if not hits:
            print("      (no candidate found)")
        for hit in hits:
            kind, where = detail.get(hit["qid"], ("", ""))
            note = f"{kind} · {where}".strip(" ·") or hit.get("description", "")
            print(f"      {hit['qid']:<11} {hit['label'][:34]:<34} {note[:34]}")
    print(
        "\n  Which of these is the register's '3. Wahlkreis' is a reader's\n"
        "  judgement, and this probe fills nothing in — run 13 spent a whole\n"
        "  run on a Q-ID that turned out to be a Wikimedia category."
    )

    print("\nwhat do the seat's statements already carry?")
    position = config.bodies[0].position_qid if config.bodies else ""
    try:
        used = wikidata.run_query(district_usage_query(position)) if position else []
    except Exception as exc:
        print(f"  ! could not read Wikidata: {exc}")
        used = []
    if not used:
        print("  no P39 statement for this seat carries a P768 at all.")
    for row in used[:10]:
        label = (row.get("districtLabel") or {}).get("value", "")
        qid = (row.get("district") or {}).get("value", "").rsplit("/", 1)[-1]
        count = (row.get("count") or {}).get("value", "?")
        print(f"    {count:>4}x  {qid:<12} {label}")
    print(
        "\n  Run 15 found 3 of 270, all naming *city of Zürich quarters* rather\n"
        "  than Wahlkreise. This tool only ever ADDS, so a member carrying a\n"
        "  wrong P768 ends up with two values rather than a correction."
    )

    print()
    print("=" * 70)
    print(f"{P_ZH_MEMBER_ID}: {verdict}")
    print("Nothing here gates the pipeline: no config reads this service.")
    print("=" * 70)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
