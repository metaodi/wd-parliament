"""Tests for the KRRR probe's sniffing, parsing and decisions.

The network is never touched. What is exercised is the part that decides what
the payload *is*, because the URL is an application export rather than a file:
the content-type is a statement of intent, and a Java app serving an HTML table
as ``application/vnd.ms-excel`` is ordinary. Believing the header is how a
"spreadsheet" turns out to be markup.

The other thing pinned here is that an unreadable payload comes back as a
finding — format plus explanation — and never as a traceback. "The export is
legacy BIFF and this probe cannot read it" is something an operator can act on.
"""

import io
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_krrr import (  # noqa: E402
    FMT_BIFF,
    FMT_DELIMITED,
    FMT_HTML,
    FMT_OOXML,
    FMT_UNKNOWN,
    KNOWN_IDENTIFIER_COLUMNS,
    FIELD_CANDIDATES,
    _grid_to_rows,
    filled,
    formatter_url_query,
    identifier_columns,
    parse_export,
    read_delimited,
    read_html_table,
    read_xlsx,
    sniff_format,
)


def xlsx_bytes(grid):
    """A minimal OOXML workbook with inline strings."""
    rows = []
    for r, row in enumerate(grid, start=1):
        cells = "".join(
            f'<c r="A{r}" t="inlineStr"><is><t>{v}</t></is></c>' for v in row
        )
        rows.append(f'<row r="{r}">{cells}</row>')
    sheet = (
        '<?xml version="1.0"?><worksheet '
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(rows)}</sheetData></worksheet>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


# --- sniffing ---------------------------------------------------------------
def test_ooxml_is_recognised_by_its_magic_not_its_content_type():
    payload = xlsx_bytes([["Nachname"], ["Muster"]])
    assert sniff_format(payload, "text/html") == FMT_OOXML


def test_an_html_table_served_as_excel_is_still_html():
    """The failure this sniff exists for."""
    payload = b"<html><body><table><tr><td>x</td></tr></table></body></html>"
    assert sniff_format(payload, "application/vnd.ms-excel") == FMT_HTML


def test_legacy_biff_is_recognised_and_refused():
    payload = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    assert sniff_format(payload) == FMT_BIFF
    fmt, headers, rows, error = parse_export(payload)
    assert fmt == FMT_BIFF and not rows
    assert "xlrd" in error


def test_delimited_text_is_recognised():
    assert sniff_format(b"a;b;c\n1;2;3\n") == FMT_DELIMITED


def test_an_empty_body_is_unrecognised_not_delimited():
    assert sniff_format(b"") == FMT_UNKNOWN


# --- parsing ----------------------------------------------------------------
def test_read_xlsx_uses_only_the_standard_library():
    headers, rows = read_xlsx(
        xlsx_bytes([["Nachname", "Vorname"], ["Rueff-Frenkel", "Sonja"]])
    )
    assert headers == ["nachname", "vorname"]
    assert rows == [{"nachname": "Rueff-Frenkel", "vorname": "Sonja"}]


def test_read_html_table():
    payload = (
        b"<table><tr><th>Nachname</th><th>Vorname</th></tr>"
        b"<tr><td>Muster</td><td>Anna</td></tr></table>"
    )
    headers, rows = read_html_table(payload)
    assert headers == ["nachname", "vorname"]
    assert rows[0]["vorname"] == "Anna"


def test_read_delimited_sniffs_the_separator():
    headers, rows = read_delimited(b"Nachname;Vorname\nMuster;Anna\n")
    assert headers == ["nachname", "vorname"]
    assert rows[0]["nachname"] == "Muster"


def test_a_title_line_is_not_the_header():
    """An export that opens with a title would name every column after it."""
    headers, rows = _grid_to_rows(
        [["Mitglieder des Kantonsrates", ""], ["Nachname", "Vorname"], ["Muster", "Anna"]]
    )
    assert headers == ["nachname", "vorname"]
    assert len(rows) == 1


def test_blank_rows_are_dropped():
    headers, rows = _grid_to_rows([["a", "b"], ["", ""], ["1", "2"]])
    assert len(rows) == 1


def test_duplicate_headers_do_not_collide():
    headers, _ = _grid_to_rows([["Datum", "Datum"], ["1", "2"]])
    assert len(set(headers)) == 2


def test_a_blank_cell_inside_the_header_row_still_gets_a_name():
    headers, _ = _grid_to_rows(
        [["Nachname", "", "Vorname"], ["Muster", "x", "Anna"]]
    )
    assert headers[0] == "nachname" and headers[2] == "vorname"
    assert headers[1].startswith("column_")


def test_a_one_cell_row_reads_as_a_title_not_a_header():
    """The rule that skips a title line, stated as its own test.

    It cannot also recognise a two-column export whose second header is
    blank — those two are the same shape — and skipping the title is the
    case that actually occurs in application exports.
    """
    headers, rows = _grid_to_rows([["Kantonsrat"], ["Nachname", "Vorname"]])
    assert headers == ["nachname", "vorname"] and rows == []


def test_a_malformed_payload_is_a_finding_not_a_traceback():
    fmt, _, rows, error = parse_export(b"PK\x03\x04not-a-zip")
    assert fmt == FMT_OOXML and not rows and error


def test_a_parsed_export_with_no_data_rows_says_so():
    fmt, headers, rows, error = parse_export(b"Nachname;Vorname\n")
    assert headers == ["nachname", "vorname"] and not rows
    assert "no data rows" in error


# --- what it can feed -------------------------------------------------------
def test_identifier_columns_are_mapped_to_their_properties():
    assert identifier_columns(["gnd", "viaf", "nachname"]) == {
        "gnd": "P227",
        "viaf": "P214",
    }


def test_identifier_columns_absent_is_an_empty_answer():
    assert identifier_columns(["nachname", "vorname"]) == {}


def test_every_known_identifier_column_names_a_property():
    assert all(p.startswith("P") for p in KNOWN_IDENTIFIER_COLUMNS.values())


def test_filled_counts_non_blank_values():
    rows = [{"gnd": "1015915353"}, {"gnd": " "}, {"gnd": ""}, {}]
    assert filled(rows, "gnd") == 1


def test_every_candidate_names_a_property_and_columns():
    for prop, (label, candidates) in FIELD_CANDIDATES.items():
        assert prop.startswith("P") and label and candidates


# --- the formatter-URL question ---------------------------------------------
def test_the_formatter_query_asks_for_p1630_optionally():
    """A property with no formatter URL is an answer, not an empty result."""
    sparql = formatter_url_query()
    assert "wdt:P1630" in sparql and "wd:P13468" in sparql
    assert sparql.count("OPTIONAL") == 2
