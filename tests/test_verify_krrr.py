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
    AGREEMENT_THRESHOLD,
    CANTON_ZURICH,
    CONFIRMED,
    CONTRADICTED,
    FMT_BIFF,
    FMT_DELIMITED,
    FMT_HTML,
    FMT_OOXML,
    FMT_UNKNOWN,
    KNOWN_IDENTIFIER_COLUMNS,
    FIELD_CANDIDATES,
    INCONCLUSIVE,
    _grid_to_rows,
    birth_dates_query,
    births_by_name,
    classify_misses,
    diagnose_misses,
    align_districts,
    district_class_query,
    district_detail_query,
    district_key,
    district_numbers,
    render_cantons_yaml,
    district_usage_query,
    search_terms,
    filled,
    formatter_url_query,
    SINGLE_SHEET,
    find_column,
    identifier_columns,
    iso_date,
    parse_export,
    read_delimited,
    read_html_table,
    read_xlsx,
    sniff_format,
)


def xlsx_bytes(sheets):
    """A minimal OOXML workbook, built the way the standard says.

    Named sheets and a relationship file, because that is what `read_xlsx`
    has to join to get a sheet's *name* — globbing sheet*.xml gets both the
    names and the order wrong.
    """
    if isinstance(sheets, list):
        sheets = {"Sheet1": sheets}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        entries, rels = [], []
        for i, (name, grid) in enumerate(sheets.items(), start=1):
            rows = []
            for r, row in enumerate(grid, start=1):
                cells = "".join(
                    f'<c r="A{r}" t="inlineStr"><is><t>{v}</t></is></c>' for v in row
                )
                rows.append(f'<row r="{r}">{cells}</row>')
            zf.writestr(
                f"xl/worksheets/sheet{i}.xml",
                '<?xml version="1.0"?><worksheet '
                'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(rows)}</sheetData></worksheet>',
            )
            entries.append(
                f'<sheet name="{name}" sheetId="{i}" '
                f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                f'r:id="rId{i}"/>'
            )
            rels.append(
                f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/worksheet"/>'
            )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheets>{"".join(entries)}</sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{"".join(rels)}</Relationships>',
        )
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
    fmt, sheets, error = parse_export(payload)
    assert fmt == FMT_BIFF and not sheets
    assert "xlrd" in error


def test_delimited_text_is_recognised():
    assert sniff_format(b"a;b;c\n1;2;3\n") == FMT_DELIMITED


def test_an_empty_body_is_unrecognised_not_delimited():
    assert sniff_format(b"") == FMT_UNKNOWN


# --- parsing ----------------------------------------------------------------
def test_read_xlsx_uses_only_the_standard_library():
    sheets = read_xlsx(
        xlsx_bytes({"Personen": [["Nachname", "Vorname"], ["Rueff-Frenkel", "Sonja"]]})
    )
    headers, rows = sheets["Personen"]
    assert headers == ["nachname", "vorname"]
    assert rows == [{"nachname": "Rueff-Frenkel", "vorname": "Sonja"}]


def test_every_sheet_is_read_and_keeps_its_name():
    """The bug this fixes reported "the export has no seat columns", which
    was a fact about the reader: it only ever opened sheet1."""
    sheets = read_xlsx(
        xlsx_bytes(
            {
                "Personen": [["Nachname"], ["Muster"]],
                "Einsitze": [["Wahlkreis", "Eintritt"], ["I Zürich", "2019-05-06"]],
            }
        )
    )
    assert set(sheets) == {"Personen", "Einsitze"}
    assert sheets["Einsitze"][0] == ["wahlkreis", "eintritt"]


def test_find_column_searches_across_sheets():
    """People and mandates live in different sheets, so a per-sheet answer
    would report each column as missing from the other."""
    sheets = {
        "Personen": (["nachname"], [{"nachname": "Muster"}]),
        "Einsitze": (["wahlkreis"], [{"wahlkreis": "I Zürich"}]),
    }
    assert find_column(sheets, ("wahlkreis",)) == [("Einsitze", "wahlkreis")]
    assert find_column(sheets, ("nachname",)) == [("Personen", "nachname")]
    assert find_column(sheets, ("beruf",)) == []


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
    fmt, sheets, error = parse_export(b"PK\x03\x04not-a-zip")
    assert fmt == FMT_OOXML and not sheets and error


def test_a_parsed_export_with_no_data_rows_says_so():
    fmt, sheets, error = parse_export(b"Nachname;Vorname\n")
    headers, rows = sheets[SINGLE_SHEET]
    assert headers == ["nachname", "vorname"] and not rows
    assert "no sheet had any data rows" in error


def test_a_single_table_format_presents_the_same_shape_as_a_workbook():
    fmt, sheets, error = parse_export(b"Nachname;Vorname\nMuster;Anna\n")
    assert list(sheets) == [SINGLE_SHEET] and error is None


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


# --- why did a name match but its identifier not? ---------------------------
# Run 25's 583-of-638: a partial match has two opposite causes and only one of
# them is about the data.
def krrr_row(pid, last="Müller", first="Hans"):
    return {"id_person_new": pid, "nachname": last, "vorname": first}


def test_a_name_the_export_gives_to_several_people_is_an_artefact():
    index = {"hans muller": [krrr_row("111"), krrr_row("222")]}
    counts, lines = diagnose_misses(index, {"hans muller": "333"}, "id_person_new")
    assert counts["ambiguous name"] == 1 and counts["value differs"] == 0
    assert "2 people so named" in lines[0]
    verdict, detail = classify_misses(counts, 638)
    assert verdict == CONFIRMED
    assert "matching artefacts" in detail


def test_a_unique_name_with_another_value_needs_a_birth_date_to_be_a_disagreement():
    """Export-side uniqueness alone cannot say it: run 26 called 49 misses
    disagreements on that evidence, and a Wikidata item can still have
    reached the wrong register person."""
    index = {"hans muller": [krrr_row("111")]}
    counts, lines = diagnose_misses(index, {"hans muller": "333"}, "id_person_new")
    assert counts["undecided"] == 1 and counts["value differs"] == 0
    assert "against Wikidata" in lines[0]
    assert classify_misses(counts, 638)[0] == INCONCLUSIVE


def test_an_empty_id_column_is_neither():
    index = {"hans muller": [krrr_row("")]}
    counts, _ = diagnose_misses(index, {"hans muller": "333"}, "id_person_new")
    assert counts["no value in export"] == 1


def test_a_person_whose_value_matches_is_not_a_miss():
    index = {"hans muller": [krrr_row("333")]}
    counts, lines = diagnose_misses(index, {"hans muller": "333"}, "id_person_new")
    assert sum(counts.values()) == 0 and lines == []


def test_artefacts_do_not_count_against_the_agreement_share():
    """Artefacts are facts about the name match, so they neither prove nor
    disprove the id space — but they are not counted as agreement either."""
    counts = {"ambiguous name": 40, "value differs": 1, "no value in export": 5}
    verdict, detail = classify_misses(counts, 638)
    assert verdict == CONFIRMED
    assert "46 further miss(es)" not in detail  # 45 artefacts, 1 differs
    assert "45 further miss(es)" in detail


# --- the birth date arbitrates a miss ---------------------------------------
# Run 26 reported 49 "real disagreements" using export-side ambiguity alone.
# A Wikidata item can still reach the wrong register person, and only a birth
# date on both sides can tell that from a register holding a different id.
def test_iso_date_reads_both_shapes():
    assert iso_date("1897-05-04 00:00:00.0") == "1897-05-04"
    assert iso_date("1897-05-04T00:00:00Z") == "1897-05-04"


@pytest.mark.parametrize("value", ["", None, "1897", "04.05.1897", "not-a-date"])
def test_an_unreadable_date_is_absence(value):
    assert iso_date(value) == ""


def dated_row(pid, birth):
    return {
        "id_person_new": pid,
        "nachname": "Müller",
        "vorname": "Hans",
        "datum_geburt": birth,
    }


def test_agreeing_birth_dates_make_it_a_real_disagreement():
    index = {"hans muller": [dated_row("23384", "1952-03-01 00:00:00.0")]}
    counts, lines = diagnose_misses(
        index,
        {"hans muller": "21620"},
        "id_person_new",
        wanted_birth={"hans muller": "1952-03-01"},
        birth_column="datum_geburt",
    )
    assert counts["value differs"] == 1 and counts["different person"] == 0
    assert "both born 1952-03-01" in lines[0]
    # One differing value among a dominant majority is a Wikidata error to
    # fix — the suggestion this tool exists to make — not a disqualification.
    assert classify_misses(counts, 638)[0] == CONFIRMED
    assert classify_misses(counts, 2)[0] == CONTRADICTED


def test_disagreeing_birth_dates_make_it_the_wrong_person():
    index = {"hans muller": [dated_row("23384", "1952-03-01 00:00:00.0")]}
    counts, lines = diagnose_misses(
        index,
        {"hans muller": "21620"},
        "id_person_new",
        wanted_birth={"hans muller": "1901-09-14"},
        birth_column="datum_geburt",
    )
    assert counts["different person"] == 1 and counts["value differs"] == 0
    assert "born 1952-03-01 here" in lines[0]
    verdict, detail = classify_misses(counts, 638)
    assert verdict == CONFIRMED and "matching artefacts" in detail


def test_a_missing_birth_date_on_either_side_is_undecided():
    index = {"hans muller": [dated_row("23384", "")]}
    counts, _ = diagnose_misses(
        index,
        {"hans muller": "21620"},
        "id_person_new",
        wanted_birth={"hans muller": "1952-03-01"},
        birth_column="datum_geburt",
    )
    assert counts["undecided"] == 1
    assert classify_misses(counts, 638)[0] == INCONCLUSIVE


def test_without_birth_dates_at_all_every_miss_is_undecided():
    """The pre-run-26 behaviour must not silently return as 'value differs'."""
    index = {"hans muller": [dated_row("23384", "1952-03-01")]}
    counts, _ = diagnose_misses(index, {"hans muller": "21620"}, "id_person_new")
    assert counts["undecided"] == 1 and counts["value differs"] == 0


def test_births_by_name_drops_a_name_two_items_date_differently():
    def b(label, birth):
        return {
            "personLabel": {"value": label},
            "birth": {"value": birth},
        }

    assert births_by_name([b("Hans Müller", "1952-03-01T00:00:00Z")]) == {
        "hans muller": "1952-03-01"
    }
    assert births_by_name(
        [b("Hans Müller", "1952-03-01T00:00:00Z"), b("Hans Müller", "1901-09-14T00:00:00Z")]
    ) == {}


def test_the_birth_query_keeps_p569_optional():
    sparql = birth_dates_query("de")
    assert "wdt:P13468" in sparql and "OPTIONAL" in sparql and "wdt:P569" in sparql


# --- a disagreement is the product, not the disqualification ----------------
# The tool's whole design rests on the source being authoritative: where the
# register and Wikidata differ, the register wins and the difference becomes a
# suggested edit. What a miss decides is which items need fixing — provided
# the agreeing majority is large enough to establish one shared id space.
def test_a_dominant_majority_makes_disagreements_a_worklist():
    counts = {
        "ambiguous name": 5, "different person": 0, "value differs": 49,
        "undecided": 0, "no value in export": 1,
    }
    verdict, detail = classify_misses(counts, 638)
    assert verdict == CONFIRMED
    assert "Wikidata errors to fix" in detail
    assert "one id space" in detail


def test_a_weak_majority_is_still_two_id_spaces_overlapping():
    counts = {
        "ambiguous name": 0, "different person": 0, "value differs": 300,
        "undecided": 0, "no value in export": 0,
    }
    verdict, detail = classify_misses(counts, 638)
    assert verdict == CONTRADICTED
    assert "overlapping numerically" in detail


def test_the_threshold_is_named_not_inlined():
    assert 50.0 < AGREEMENT_THRESHOLD < 100.0


# --- can the districts be mapped? -------------------------------------------
def test_district_numbers_keys_on_the_only_alignable_token():
    """The register numbers its districts and Wikidata does not, so the number
    is the only thing two sides can be lined up on by machine."""
    assert district_numbers(["3. Wahlkreis (Zürich 4+5)"]) == {
        "3": "3. Wahlkreis (Zürich 4+5)"
    }


def test_an_unnumbered_district_is_left_out_rather_than_guessed():
    assert district_numbers(["Wahlkreis Zürich", ""]) == {}


def test_the_first_spelling_of_a_number_wins_and_none_is_lost():
    names = ["1. Wahlkreis (Zürich 1+2)", "1.  Wahlkreis  (Zürich 1+2)"]
    assert district_numbers(names) == {"1": names[0]}


def test_the_search_drops_the_registers_numbering():
    """'9. Wahlkreis (Horgen)' is not a name anybody else uses; the bracketed
    part is, and it is tried first."""
    assert search_terms("9. Wahlkreis (Horgen)")[0] == "Wahlkreis Horgen"
    assert "Horgen" in search_terms("9. Wahlkreis (Horgen)")


def test_a_district_with_no_brackets_is_searched_as_written():
    assert search_terms("Wahlkreis Zürich") == ["Wahlkreis Zürich"]


def test_search_terms_of_nothing_is_nothing():
    assert search_terms("") == [] and search_terms(None) == []


def test_the_detail_query_is_bounded_by_the_qids_in_hand():
    """Run 29's class-first query timed out; this one asks about a handful of
    known items, which is cheap."""
    sparql = district_detail_query(["Q1", "Q2"], "de")
    assert "VALUES ?item { wd:Q1 wd:Q2 }" in sparql
    assert "wdt:P31" in sparql and "wdt:P131" in sparql


def test_the_usage_query_counts_what_the_seat_already_carries():
    sparql = district_usage_query("Q21518678")
    assert "pq:P768" in sparql and "Q21518678" in sparql


def test_an_exact_match_suppresses_the_leaf_fallback():
    """Run 29 asked for `datum_eintritt_jahr` and got nine columns: the leaf is
    `jahr`, which every date column in a register that splits its dates ends
    with. The fallback is for a name that is absent, not one that is present.
    """
    sheets = {
        "Einsitze": (["datum_eintritt_jahr", "datum_austritt_jahr"], []),
        "Personen": (["datum_geburt_jahr"], []),
    }
    assert find_column(sheets, ("datum_eintritt_jahr",)) == [
        ("Einsitze", "datum_eintritt_jahr")
    ]


def test_the_fallback_still_runs_when_nothing_matches_exactly():
    sheets = {"Personen": (["person_kontakt_beruf"], [])}
    assert find_column(sheets, ("beruf",)) == [("Personen", "person_kontakt_beruf")]


# --- aligning the register's districts with the new Wahlkreis items ---------
# Run 30 could not find the districts because no class existed; one does now,
# so the population is derived from it rather than searched for by name.
@pytest.mark.parametrize(
    "text,expected",
    [
        ("9. Wahlkreis (Horgen)", 9),
        ("Wahlkreis IX Horgen", 9),
        ("IX     Horgen", 9),
        ("18. Wahlkreis (Dielsdorf)", 18),
        ("Wahlkreis Horgen", None),
        ("", None),
    ],
)
def test_district_key_reads_arabic_and_roman(text, expected):
    """This register numbers in arabic, OpenParlData's ZH rows in roman, and
    the number is the only token both sides carry."""
    assert district_key(text) == expected


def test_a_number_outside_the_cantons_range_is_not_a_district_number():
    assert district_key("Wahlkreis 2024") is None


def item(qid, label, description=""):
    return {"qid": qid, "label": label, "description": description}


def test_districts_align_on_the_number():
    register = {"9": "9. Wahlkreis (Horgen)", "3": "3. Wahlkreis (Zürich 4+5)"}
    items = [item("Q1", "Wahlkreis IX Horgen"), item("Q2", "Wahlkreis III Zürich 4+5")]
    mapping, unmatched, spare = align_districts(register, items)
    assert mapping == {
        "9. Wahlkreis (Horgen)": ("Q1", "Wahlkreis IX Horgen"),
        "3. Wahlkreis (Zürich 4+5)": ("Q2", "Wahlkreis III Zürich 4+5"),
    }
    assert unmatched == [] and spare == []


def test_a_number_two_items_claim_is_left_unaligned():
    """This repo does not arbitrate a duplicate, and a wrong district would be
    written onto every member who sits in it."""
    register = {"9": "9. Wahlkreis (Horgen)"}
    items = [item("Q1", "Wahlkreis IX Horgen"), item("Q2", "Wahlkreis 9 Horgen")]
    mapping, unmatched, spare = align_districts(register, items)
    assert mapping == {} and unmatched == ["9. Wahlkreis (Horgen)"]
    assert {i["qid"] for i in spare} == {"Q1", "Q2"}


def test_both_leftovers_are_reported():
    """'Eighteen of eighteen' means nothing without what did not match, and an
    item nobody claims is as much a finding as a district nobody has."""
    register = {"9": "9. Wahlkreis (Horgen)", "7": "7. Wahlkreis (Dietikon)"}
    items = [item("Q1", "Wahlkreis IX Horgen"), item("Q9", "Wahlkreis XVII Bülach")]
    mapping, unmatched, spare = align_districts(register, items)
    assert list(mapping) == ["9. Wahlkreis (Horgen)"]
    assert unmatched == ["7. Wahlkreis (Dietikon)"]
    assert [i["qid"] for i in spare] == ["Q9"]


def test_an_unnumbered_item_falls_back_to_its_description():
    items = [item("Q1", "Wahlkreis Horgen", "9. Wahlkreis des Kantons Zürich")]
    mapping, _, _ = align_districts({"9": "9. Wahlkreis (Horgen)"}, items)
    assert mapping["9. Wahlkreis (Horgen)"][0] == "Q1"


def test_the_yaml_block_is_ordered_by_district_number():
    mapping = {
        "10. Wahlkreis (Meilen)": ("Q10", "Wahlkreis X"),
        "2. Wahlkreis (Zürich 3+9)": ("Q2", "Wahlkreis II"),
    }
    lines = render_cantons_yaml(mapping)
    assert lines[0] == "cantons:"
    assert lines[1].startswith('  "2. Wahlkreis') and "Q2" in lines[1]
    assert lines[2].startswith('  "10. Wahlkreis')


def test_the_class_query_is_bounded_by_the_class():
    sparql = district_class_query("Q141021240", "de")
    assert "wdt:P31 wd:Q141021240" in sparql
    assert "CONTAINS" not in sparql  # run 29's scan, not repeated
