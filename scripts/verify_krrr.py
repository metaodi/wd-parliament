"""Does the Staatsarchiv's KRRR export carry P13468, and what else is in it?

README step 11. **KRRR is "Kantonsrat und Regierungsrat" — the same two bodies
P13468 names**, exported straight from the application the canton's register
runs on:

    https://www.web.statistik.zh.ch:8443/KRRR/app?show_page=EXCEL&operation=EXCEL

That makes it the strongest candidate yet for the property that three sources
have now failed to supply, which is exactly why it gets measured rather than
assumed. The three failures are the argument:

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

So "this is the authoritative source" has now been true three times and
sufficient none of them. **Authoritative about the people is not the same as
publishing the identifier**, and only a value comparison can tell them apart.

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
personal columns a config would read, counted.

**Nothing here gates anything.** No config names this service.

    uv run python scripts/verify_krrr.py
    uv run python scripts/verify_krrr.py --verbose
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
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
    resolve_column,
    resolve_columns,
    wanted_by_name,
    zh_member_id_query,
)

KRRR_URL = (
    "https://www.web.statistik.zh.ch:8443/KRRR/app?show_page=EXCEL&operation=EXCEL"
)

# What the export might be. The names are the ones printed in section A, and
# ``biff`` exists so that "a format this cannot read" stays sayable — the same
# reason ``resolve_column`` returns None rather than guessing.
FMT_OOXML = "xlsx (OOXML zip)"
FMT_BIFF = "xls (legacy BIFF/OLE2)"
FMT_HTML = "html table"
FMT_DELIMITED = "delimited text"
FMT_UNKNOWN = "unrecognised"

OOXML_MAGIC = b"PK\x03\x04"
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# OOXML namespaces, fixed by the standard.
NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Columns a ZH config would want, by the property they would feed. Candidates
# only — the export's real names are unknown until section B prints them, and
# ``resolve_column`` finds a name that differs only in nesting or prefix.
FIELD_CANDIDATES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "P569": ("date of birth", ("geburtsdatum", "datum_geburt", "geburtstag")),
    "P570": ("date of death", ("todesdatum", "datum_tod")),
    "P106": ("occupation", ("beruf",)),
    "P102": ("political party", ("partei", "parteibezeichnung", "fraktion")),
    "P768": ("electoral district", ("wahlkreis",)),
    "P580": ("start time", ("eintritt", "datum_eintritt", "von")),
    "P582": ("end time", ("austritt", "datum_austritt", "bis")),
}

# Identifier columns the openZH CSV of this same register carries, which a
# richer export might too. They are *real* Wikidata properties, and the reason
# to look: an identifier this tool could actually verify beats a name match.
# (In the CSV they reach 8, 11 and 0 of the 180 sitting members, so they are a
# historic-notables join, not a chamber join — section E counts them here.)
KNOWN_IDENTIFIER_COLUMNS = {
    "GND": "P227",
    "VIAF": "P214",
    "HLS": "P902",
}


# --- pure: sniffing and parsing the export ----------------------------------
def sniff_format(content: bytes, content_type: str = "") -> str:
    """What did the application actually send? Pure.

    Magic bytes first, because the content-type of an application export is a
    statement of intent rather than of fact: a Java app serving an HTML table
    as ``application/vnd.ms-excel`` is ordinary, and believing the header is
    how a "spreadsheet" turns out to be markup.
    """
    head = content[:2048]
    if content.startswith(OOXML_MAGIC):
        return FMT_OOXML
    if content.startswith(OLE2_MAGIC):
        return FMT_BIFF
    text = head.decode("utf-8", "replace").lstrip().lower()
    if text.startswith("<!doctype html") or "<table" in text or text.startswith("<html"):
        return FMT_HTML
    if not head:
        return FMT_UNKNOWN
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if any(sep in first_line for sep in ("\t", ";", ",")):
        return FMT_DELIMITED
    return FMT_UNKNOWN


def _cell_text(value: Optional[str]) -> str:
    return (value or "").strip()


def read_xlsx(content: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read an OOXML workbook with the standard library only. Pure.

    ``.xlsx`` is a zip of XML, so this needs no new dependency — which matters
    because this project's ``pyproject.toml`` dependencies are the *pipeline's*
    dependencies, and a probe must not widen them.

    Only the first worksheet is read, and only its values: an export has one
    sheet, and formulas and formatting are not what is being measured.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{NS_MAIN}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS_MAIN}t")))

        sheets = sorted(
            n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")
        )
        if not sheets:
            return [], []
        root = ET.fromstring(zf.read(sheets[0]))

    grid: List[List[str]] = []
    for row in root.iter(f"{NS_MAIN}row"):
        values: List[str] = []
        for cell in row.findall(f"{NS_MAIN}c"):
            kind = cell.get("t")
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in cell.iter(f"{NS_MAIN}t"))
            else:
                node = cell.find(f"{NS_MAIN}v")
                text = node.text if node is not None and node.text else ""
                if kind == "s" and text.isdigit() and int(text) < len(shared):
                    text = shared[int(text)]
            values.append(_cell_text(text))
        grid.append(values)
    return _grid_to_rows(grid)


class _TableParser(HTMLParser):
    """The first ``<table>`` of a page, as a grid of cell texts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.grid: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_cell_text("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.grid.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def read_html_table(content: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read the first HTML table of the payload. Pure."""
    parser = _TableParser()
    parser.feed(content.decode("utf-8", "replace"))
    return _grid_to_rows(parser.grid)


def read_delimited(content: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read delimited text, with the delimiter sniffed from the header. Pure."""
    text = content.decode("utf-8-sig", "replace")
    sample = text.splitlines()[0] if text.splitlines() else ""
    delimiter = max((";", "\t", ","), key=sample.count)
    grid = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    return _grid_to_rows([[_cell_text(c) for c in row] for row in grid])


def _grid_to_rows(
    grid: Sequence[Sequence[str]],
) -> Tuple[List[str], List[Dict[str, str]]]:
    """A grid → ``(headers, rows)``. Pure.

    The header is the first row with more than one non-empty cell: an export
    often opens with a title line, and taking row 0 unconditionally would name
    every column after it.
    """
    header_index = None
    for i, row in enumerate(grid):
        if sum(1 for c in row if c.strip()) > 1:
            header_index = i
            break
    if header_index is None:
        return [], []

    headers: List[str] = []
    for j, cell in enumerate(grid[header_index]):
        name = re.sub(r"\s+", "_", cell.strip().lower()) or f"column_{j + 1}"
        while name in headers:
            name = f"{name}_{j + 1}"
        headers.append(name)

    rows: List[Dict[str, str]] = []
    for row in grid[header_index + 1 :]:
        if not any(c.strip() for c in row):
            continue
        rows.append(
            {headers[j]: row[j] for j in range(min(len(headers), len(row)))}
        )
    return headers, rows


def parse_export(
    content: bytes, content_type: str = ""
) -> Tuple[str, List[str], List[Dict[str, str]], Optional[str]]:
    """``(format, headers, rows, error)``. Pure.

    An unreadable payload returns its format and an explanation rather than
    raising: "the export is legacy BIFF and this probe cannot read it" is a
    finding an operator can act on, and a traceback is not.
    """
    fmt = sniff_format(content, content_type)
    try:
        if fmt == FMT_OOXML:
            headers, rows = read_xlsx(content)
        elif fmt == FMT_HTML:
            headers, rows = read_html_table(content)
        elif fmt == FMT_DELIMITED:
            headers, rows = read_delimited(content)
        elif fmt == FMT_BIFF:
            return (
                fmt,
                [],
                [],
                "Legacy BIFF (.xls) needs a reader this project does not "
                "depend on. Re-run with 'uv run --with xlrd', or ask the "
                "application for its OOXML/CSV export if it has one.",
            )
        else:
            return (fmt, [], [], "The payload matched no format this probe reads.")
    except Exception as exc:  # a malformed export is a finding, not a crash
        return (fmt, [], [], f"{type(exc).__name__}: {exc}")
    if not rows:
        return (fmt, headers, rows, "Parsed, but no data rows were found.")
    return (fmt, headers, rows, None)


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


def diagnose_misses(
    index: Dict[str, List[Dict[str, str]]],
    wanted: Dict[str, str],
    id_column: str,
    limit: int = 12,
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
    counts = {"ambiguous name": 0, "value differs": 0, "no value in export": 0}
    lines: List[str] = []
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
            kind = "value differs"
            detail = f"export {id_column}={held.pop()!r} against Wikidata {value!r}"
        counts[kind] += 1
        if len(lines) < limit:
            lines.append(f"{name}: {detail}  [{kind}]")
    return counts, lines


def classify_misses(counts: Dict[str, int], matched: int) -> Tuple[str, str]:
    """What the miss breakdown means for the join. Pure."""
    differs = counts.get("value differs", 0)
    ambiguous = counts.get("ambiguous name", 0)
    empty = counts.get("no value in export", 0)
    if not (differs or ambiguous or empty):
        return (
            CONFIRMED,
            f"Every one of the {matched} people compared carries the "
            "identifier, with nothing left to explain.",
        )
    if differs:
        return (
            CONTRADICTED,
            f"{differs} person(s) whose name the export identifies "
            "unambiguously carry a different value there than Wikidata holds. "
            "That is a real disagreement about the identifier, not a matching "
            "artefact, and it has to be understood before the column can be "
            "joined on — one wrong value writes an edit onto somebody else.",
        )
    return (
        CONFIRMED,
        f"All {ambiguous + empty} unexplained people are matching artefacts "
        f"rather than disagreements ({ambiguous} name(s) the export gives to "
        f"several people, {empty} with no value there). No person the export "
        "identifies unambiguously disagrees with Wikidata, so the column is "
        "the identifier and the shortfall belongs to the *name* match this "
        "probe uses to line the two lists up.",
    )


# --- the SPARQL side ---------------------------------------------------------
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
    print("A. Does the KRRR export answer, and what format is it?")
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
    fmt, headers, rows, error = parse_export(content, content_type)
    print(f"sniffed format: {fmt}")
    if error:
        print(f"  ! {error}")
        print(f"  first bytes: {content[:80]!r}")
        if not rows:
            print("\nNothing below could be measured.")
            return 1
    print(f"rows: {len(rows)}; columns: {len(headers)}")

    # --- B. the real column list -------------------------------------------
    print()
    print("=" * 70)
    print("B. Which columns does the export actually have?")
    print("=" * 70)
    report = column_report(rows)
    shown = report if args.verbose else report[:60]
    width = max((len(c) for c, _, _ in shown), default=30)
    for column, count, sample in shown:
        text = "" if sample is None else f"  e.g. {sample[:40]!r}"
        print(f"  {column:<{width}} {count:>6}/{len(rows)}{text}")
    if len(shown) < len(report):
        print(f"  ... {len(report) - len(shown)} more (--verbose for all)")

    # --- C. P13468 ----------------------------------------------------------
    print()
    print("=" * 70)
    print(f"C. Does the KRRR export carry {P_ZH_MEMBER_ID}?")
    print("=" * 70)
    wikidata = WikidataClient(http)
    verdict, detail = INCONCLUSIVE, "Wikidata could not be read."
    try:
        bindings = wikidata.run_query(zh_member_id_query(config.language))
    except Exception as exc:
        print(f"  ! could not read Wikidata: {exc}")
        bindings = []

    seen = [column for column, _, _ in report]
    name_field = resolve_column(seen, "name") or resolve_column(seen, "nachname")
    first_field = resolve_column(seen, "vorname")
    print(f"name read from: {name_field!r} + {first_field!r}")
    index: Dict[str, List[Dict[str, Any]]] = {}
    if name_field:
        index, unnamed = index_by_name(
            rows, name_field=name_field, first_name_field=first_field or "vorname"
        )
        print(f"distinct people by name: {len(index)}; rows with no name: {unnamed}")
    else:
        print("  ! no name column resolved; candidates:")
        for column in seen:
            if "name" in column:
                print(f"    {column}")

    if bindings and index:
        wanted, read, dropped = wanted_by_name(bindings)
        print(f"\nitems carrying {P_ZH_MEMBER_ID}: {read}")
        print(f"  usable names: {len(wanted)}; dropped as ambiguous: {dropped}")
        counts, compared, missing, examples = find_identifier_columns(index, wanted)
        print(f"  matched to a KRRR person: {compared}; not in the export: {missing}")
        if counts:
            print("  columns carrying the value:")
            for column, hits in list(counts.items())[:10]:
                print(f"    {column:<34} {hits}/{compared}")
        else:
            print("  columns carrying the value: none")
        for line in examples:
            print(f"    {line}")
        verdict, detail = classify_identifier(counts, compared, compared)
        _verdict(f"Verdict ({P_ZH_MEMBER_ID})", verdict, detail)

        # A partial match has two opposite causes, and counts alone cannot
        # tell them apart. This is the part that can.
        if counts:
            best = next(iter(counts))
            print(f"\nwhy did the rest miss? (column {best!r})")
            miss_counts, miss_lines = diagnose_misses(index, wanted, best)
            for kind, n in miss_counts.items():
                print(f"  {kind:<22} {n}")
            for line in miss_lines:
                print(f"    {line}")
            miss_verdict, miss_detail = classify_misses(miss_counts, compared)
            _verdict("Verdict (are the misses disagreements?)", miss_verdict, miss_detail)
    else:
        _verdict(f"Verdict ({P_ZH_MEMBER_ID})", verdict, detail)

    # --- D. where does P13468 actually resolve? -----------------------------
    print()
    print("=" * 70)
    print(f"D. {P_ZH_MEMBER_ID}'s own formatter URL, from Wikidata")
    print("=" * 70)
    print("  (this repo has been asserting one it never measured)")
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
        columns = resolve_columns(seen, candidates)
        if not columns:
            print(f"  {prop} {label:<18} no such column: {', '.join(candidates)}")
            continue
        best = max(columns, key=lambda c: filled(rows, c))
        print(
            f"  {prop} {label:<18} {filled(rows, best):>6}/{len(rows)} "
            f"via {', '.join(columns)}"
        )

    print("\n  identifier columns (a join this tool could actually verify):")
    found = identifier_columns(seen)
    if not found:
        print("    none of " + ", ".join(KNOWN_IDENTIFIER_COLUMNS))
    for column, prop in found.items():
        print(f"    {column:<34} {filled(rows, column):>6}/{len(rows)}  -> {prop}")

    print()
    print("=" * 70)
    print(f"{P_ZH_MEMBER_ID}: {verdict}")
    print("Nothing here gates the pipeline: no config reads this service.")
    print("=" * 70)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
