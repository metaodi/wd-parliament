"""Read a spreadsheet an application exports, without adding a dependency.

Split out of ``scripts/verify_krrr.py`` when :mod:`krdaten` needed the same
reader: the probe measures the KRRR export and the adapter consumes it, and two
copies of a parser would let the thing that was *measured* drift from the thing
that is *read*.

Nothing here needs a package. ``.xlsx`` is a zip of XML and is read with
:mod:`zipfile` and :mod:`xml.etree`, an HTML table with :mod:`html.parser`,
delimited text with :mod:`csv`. That matters because this project's
``pyproject.toml`` dependencies are the *pipeline's* dependencies, and a
spreadsheet reader is a large thing to add for one source. Legacy BIFF (.xls)
is the one format not read, and :func:`parse_export` says so rather than
guessing.

Two rules paid for by measurement, both worth keeping:

- **the payload is sniffed, not trusted.** An application export's
  content-type is a statement of intent: a Java app of this vintage serving an
  HTML table as ``application/vnd.ms-excel`` is ordinary, and believing the
  header is how a "spreadsheet" turns out to be markup.
- **every sheet is read, by its name.** The first version of the probe opened
  only the first worksheet and reported that the KRRR export "has no seat
  columns" — a statement about the reader that read as one about the source.
  The names live in ``xl/workbook.xml`` and the paths behind relationship ids,
  so the two have to be joined; globbing ``worksheets/sheet*.xml`` gets both
  the names and the order wrong (``sheet10`` sorts before ``sheet2``).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# What an export might be. ``FMT_BIFF`` exists so that "a format this cannot
# read" stays sayable rather than being guessed at.
FMT_OOXML = "xlsx (OOXML zip)"
FMT_BIFF = "xls (legacy BIFF/OLE2)"
FMT_HTML = "html table"
FMT_DELIMITED = "delimited text"
FMT_UNKNOWN = "unrecognised"

OOXML_MAGIC = b"PK\x03\x04"
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# OOXML namespace, fixed by the standard.
NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# The name a single-table format gets, so every format presents one shape.
SINGLE_SHEET = "(single table)"

Sheets = Dict[str, Tuple[List[str], List[Dict[str, str]]]]


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

    Searching *across* sheets is the point: an export splits people from their
    mandates, so ``wahlkreis`` and ``nachname`` live in different sheets and a
    per-sheet answer would report each as missing from the other.
    """
    found: List[Tuple[str, str]] = []
    for sheet, (headers, _) in sheets.items():
        for candidate in candidates:
            column = resolve_column(headers, candidate)
            if column and (sheet, column) not in found:
                found.append((sheet, column))
    return found


def resolve_column(seen: Sequence[str], candidate: str) -> Optional[str]:
    """A candidate column name → the one the source actually has. Pure.

    Exact match first, then a column with the same **leaf segment** —
    ``beruf`` finds ``person_kontakt_beruf`` and vice versa. The *shallowest*
    match wins. A leaf nobody publishes resolves to ``None``, so "no such
    column" stays sayable; widening this to substring matching would take that
    away.
    """
    columns = list(seen)
    if candidate in columns:
        return candidate
    leaf = candidate.rsplit("_", 1)[-1]
    matches = [c for c in columns if c.rsplit("_", 1)[-1] == leaf]
    if not matches:
        return None
    return min(matches, key=lambda c: (c.count("_"), c))
