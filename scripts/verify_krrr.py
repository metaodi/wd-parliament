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


def _sheet_targets(zf: zipfile.ZipFile) -> List[Tuple[str, str]]:
    """``[(sheet name, zip path)]`` in workbook order. Pure-ish (reads the zip).

    The names live in ``xl/workbook.xml`` and the paths behind relationship
    ids in ``xl/_rels/workbook.xml.rels``, so the two have to be joined. The
    obvious shortcut — globbing ``xl/worksheets/sheet*.xml`` — gets both the
    names and the *order* wrong (``sheet10`` sorts before ``sheet2``), and a
    sheet's name is exactly what a reader of this report needs to see.
    """
    names = zf.namelist()
    if "xl/workbook.xml" not in names:
        return [
            (Path(n).stem, n) for n in sorted(n for n in names if "worksheets/sheet" in n)
        ]
    rels: Dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in names:
        root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        for rel in root:
            rid, target = rel.get("Id"), rel.get("Target", "")
            if rid and target:
                target = target.lstrip("/")
                rels[rid] = target if target.startswith("xl/") else f"xl/{target}"

    out: List[Tuple[str, str]] = []
    root = ET.fromstring(zf.read("xl/workbook.xml"))
    for sheet in root.iter(f"{NS_MAIN}sheet"):
        name = sheet.get("name") or f"sheet{len(out) + 1}"
        rid = next(
            (v for k, v in sheet.attrib.items() if k.endswith("}id") or k == "id"), ""
        )
        path = rels.get(rid, "")
        if path in names:
            out.append((name, path))
    return out


def read_xlsx(content: bytes) -> Dict[str, Tuple[List[str], List[Dict[str, str]]]]:
    """Read **every** worksheet of an OOXML workbook, by name. Pure.

    ``.xlsx`` is a zip of XML, so this needs no new dependency — which matters
    because this project's ``pyproject.toml`` dependencies are the *pipeline's*
    dependencies, and a probe must not widen them.

    Every sheet, because the first version of this read only ``sheet1`` and
    reported that the KRRR export "has no seat columns" — a statement about
    the reader that read as a statement about the source, which is this
    session's recurring failure and the one the column report exists to
    prevent. The workbook carries an *Einsitze* sheet; nothing was wrong with
    the export.
    """
    sheets: Dict[str, Tuple[List[str], List[Dict[str, str]]]] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{NS_MAIN}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS_MAIN}t")))

        for name, path in _sheet_targets(zf):
            root = ET.fromstring(zf.read(path))
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
            sheets[name] = _grid_to_rows(grid)
    return sheets


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


# The name a single-table format gets, so every format presents the same shape.
SINGLE_SHEET = "(single table)"

Sheets = Dict[str, Tuple[List[str], List[Dict[str, str]]]]


def parse_export(
    content: bytes, content_type: str = ""
) -> Tuple[str, Sheets, Optional[str]]:
    """``(format, {sheet name: (headers, rows)}, error)``. Pure.

    A workbook of several sheets and a single HTML table present the same
    shape, so nothing downstream has to know which format it came from.

    An unreadable payload returns its format and an explanation rather than
    raising: "the export is legacy BIFF and this probe cannot read it" is a
    finding an operator can act on, and a traceback is not.
    """
    fmt = sniff_format(content, content_type)
    try:
        if fmt == FMT_OOXML:
            sheets = read_xlsx(content)
        elif fmt == FMT_HTML:
            sheets = {SINGLE_SHEET: read_html_table(content)}
        elif fmt == FMT_DELIMITED:
            sheets = {SINGLE_SHEET: read_delimited(content)}
        elif fmt == FMT_BIFF:
            return (
                fmt,
                {},
                "Legacy BIFF (.xls) needs a reader this project does not "
                "depend on. Re-run with 'uv run --with xlrd', or ask the "
                "application for its OOXML/CSV export if it has one.",
            )
        else:
            return (fmt, {}, "The payload matched no format this probe reads.")
    except Exception as exc:  # a malformed export is a finding, not a crash
        return (fmt, {}, f"{type(exc).__name__}: {exc}")
    if not any(rows for _, rows in sheets.values()):
        return (fmt, sheets, "Parsed, but no sheet had any data rows.")
    return (fmt, sheets, None)


def find_column(sheets: Sheets, candidates: Sequence[str]) -> List[Tuple[str, str]]:
    """``[(sheet, column)]`` for every sheet that has one of these. Pure.

    Searching *across* sheets is the point: the export splits people from
    their mandates, so ``wahlkreis`` and ``nachname`` live in different
    sheets and a per-sheet answer would report each as missing from the other.
    """
    found: List[Tuple[str, str]] = []
    for sheet, (headers, _) in sheets.items():
        for candidate in candidates:
            column = resolve_column(headers, candidate)
            if column and (sheet, column) not in found:
                found.append((sheet, column))
    return found


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

    print()
    print("=" * 70)
    print(f"{P_ZH_MEMBER_ID}: {verdict}")
    print("Nothing here gates the pipeline: no config reads this service.")
    print("=" * 70)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
