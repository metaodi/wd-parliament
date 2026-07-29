"""Tests for the pure decisions in ``scripts/verify_openparldata.py``.

The API calls are network code and are left alone, as in
``test_verify_source.py``. What is worth pinning down is what the probe
*concludes* — above all ``classify_seat_memberships``, which decides whether
the OpenParlData backend could replace the OData read or only enrich it.

The fixtures here follow the model the 2026-07-29 run established: the chambers
are **groups**, a seat is a membership pointing at one, and a cantonal
legislature carries the same ``council_legislative`` type as a federal seat
would — so the chamber's *name* is what distinguishes them.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_openparldata import (  # noqa: E402
    CONFIRMED,
    CONTRADICTED,
    INCONCLUSIVE,
    chamber_candidates,
    chamber_of,
    EXACT,
    classify_seat_memberships,
    compare_counts,
    compare_identifier_coverage,
    date_columns,
    coverage_query,
    find_chamber_groups,
    marginal_gain_query,
    summarise_wikidata_ids,
)


def group(id=1, name="Grosser Rat des Kantons Freiburg"):
    return {"id": id, "body_key": "FR", "name_de": name}


def membership(kind="council_legislative", name="Nationalrat", start=None, end=None,
               person_id=1):
    """One membership as the live API shapes it: begin_date / end_date."""
    return {
        "type_harmonized": kind,
        "group_name_de": name,
        "person_id": person_id,
        "begin_date": start,
        "end_date": end,
    }


def person(id=1, qid=None, party_qid=None):
    return {
        "id": id,
        "fullname": f"Person {id}",
        "wikidata_id": qid,
        "party_harmonized_wikidata_id": party_qid,
    }


# --- A. locating the chambers -----------------------------------------------
@pytest.mark.parametrize(
    "name,expected",
    [
        ("Nationalrat", "NR"),
        ("Conseil national", "NR"),
        ("National Council", "NR"),
        ("Ständerat", "SR"),
        ("Conseil des États", "SR"),
        ("Consiglio degli Stati", "SR"),
        ("Grosser Rat des Kantons Freiburg", None),
        ("Kantonsrat Zürich", None),
    ],
)
def test_a_chamber_is_recognised_by_name_in_any_language(name, expected):
    assert chamber_of({"name_de": name}) == expected


@pytest.mark.parametrize(
    "name",
    [
        "Präsidium des Nationalrates",
        "Büro NR",
        "Kommission für Verkehr des Nationalrates",
        "Präsidium des Ständerates",
        "Sekretariat Nationalrat",
    ],
)
def test_a_committee_of_the_chamber_is_not_the_chamber(name):
    """The 2026-07-29 trap: a substring match made a presidium of eight the
    National Council, so section B measured a committee and answered anyway."""
    assert chamber_of({"name_de": name}) is None


def test_names_are_compared_per_field_not_run_together():
    """Concatenating the name fields first would make an exact match impossible."""
    row = {"name_de": "Nationalrat", "name_fr": "Conseil national"}
    assert chamber_of(row) == "NR"


def test_a_chamber_name_is_matched_regardless_of_spacing_or_case():
    assert chamber_of({"name_de": "  NATIONALRAT  "}) == "NR"


def test_near_misses_are_reported_rather_than_matched():
    """So a chamber named unexpectedly is visible instead of silently missed."""
    rows = [group(1, "Präsidium des Nationalrates"), group(2, "Nationalrat"),
            group(3, "Kantonsrat Zug")]
    near = chamber_candidates(rows)
    assert [r["id"] for r in near["NR"]] == [1]  # 2 is an exact match, not a near miss
    assert near["SR"] == []


def test_a_missing_chamber_names_its_near_misses():
    found, lines = find_chamber_groups([group(1, "Präsidium des Nationalrates")])
    assert found == {}
    blob = "\n".join(lines)
    assert "NR: NOT FOUND by exact name (1 group(s) mention it)" in blob
    assert "near miss" in blob
    assert "Präsidium des Nationalrates" in blob


def test_both_chambers_are_found_among_cantonal_groups():
    rows = [group(1), group(2, "Nationalrat"), group(3, "Ständerat"), group(4)]
    found, lines = find_chamber_groups(rows)
    assert {k: v["id"] for k, v in found.items()} == {"NR": 2, "SR": 3}
    assert any("2 chamber(s) identified" in ln for ln in lines)


def test_a_missing_chamber_is_named_rather_than_omitted():
    found, lines = find_chamber_groups([group(2, "Nationalrat")])
    assert set(found) == {"NR"}
    assert any("SR: NOT FOUND" in ln for ln in lines)


def test_no_chambers_and_no_near_misses_says_so_plainly():
    found, lines = find_chamber_groups([group(1), group(2, "Kantonsrat Zug")])
    assert found == {}
    blob = "\n".join(lines)
    assert "NR: NOT FOUND by exact name (0 group(s) mention it)" in blob
    assert "SR: NOT FOUND by exact name (0 group(s) mention it)" in blob


def test_an_empty_groups_table_is_not_reported_as_a_finding():
    found, lines = find_chamber_groups([])
    assert found == {}
    assert any("came back empty" in ln for ln in lines)


# --- B. do the seat memberships carry dates? --------------------------------
def test_a_populated_column_that_is_empty_rules_out_a_replacement():
    rows = [membership(start=None), membership(start=None, person_id=2)]
    verdict, detail, _ = classify_seat_memberships(rows)
    assert verdict == CONTRADICTED
    assert "exists but is empty" in detail
    assert "cannot source P39" in detail


def test_an_absent_column_is_inconclusive_not_a_refutation():
    """The 2026-07-29 defect, and the worst of the three.

    Reading a column that does not exist gives None for every row, which is
    indistinguishable from a populated column full of nulls through .get() —
    and they mean opposite things. Looking for 'date_start' when the API calls
    it 'begin_date' reported all 5,618 seat memberships as undated.
    """
    rows = [
        {"type_harmonized": "council_legislative", "group_name_de": "Nationalrat"},
        {"type_harmonized": "council_legislative", "group_name_de": "Nationalrat"},
    ]
    verdict, detail, lines = classify_seat_memberships(rows)
    assert verdict == INCONCLUSIVE
    assert "says nothing about whether the seat is" in detail
    assert "begin_date" in detail  # names the candidates it tried
    assert any("all columns:" in ln for ln in lines)


def test_the_column_actually_read_is_reported():
    """So a silent misread cannot happen again without being visible."""
    _, _, lines = classify_seat_memberships([membership(start="2019-12-02")])
    assert any("read as start / end:    begin_date / end_date" in ln for ln in lines)


def test_a_legacy_date_start_column_is_still_understood():
    """The preference list falls back rather than failing on a variant."""
    rows = [{"type_harmonized": "x", "date_start": "2019-12-02", "date_end": None}]
    verdict, _, lines = classify_seat_memberships(rows)
    assert verdict == CONFIRMED
    assert any("read as start / end:    date_start / date_end" in ln for ln in lines)


def test_date_columns_lists_what_is_actually_there():
    assert date_columns([membership(start="2019-12-02")]) == ["begin_date", "end_date"]


def test_fully_dated_memberships_confirm():
    rows = [
        membership(start="2019-12-02", person_id=1),
        membership(start="2023-12-04", end="2025-06-01", person_id=2),
    ]
    verdict, detail, _ = classify_seat_memberships(rows)
    assert verdict == CONFIRMED
    assert "All 2 memberships carry a begin_date" in detail


def test_partly_dated_memberships_confirm_but_warn():
    """The dangerous middle: undated members would silently lose P580."""
    rows = [membership(start="2019-12-02"), membership(start=None, person_id=2)]
    verdict, detail, _ = classify_seat_memberships(rows)
    assert verdict == CONFIRMED
    assert "50.0%" in detail
    assert "risky as the only source" in detail


def test_the_counts_are_reported_separately_for_start_and_end():
    rows = [membership(start="2019-12-02", end=None), membership(start="2023-12-04")]
    _, _, lines = classify_seat_memberships(rows)
    blob = "\n".join(lines)
    assert "with a start:           2" in blob
    assert "with an end:            0" in blob


def test_no_memberships_is_inconclusive_not_a_refutation():
    verdict, detail, _ = classify_seat_memberships([])
    assert verdict == INCONCLUSIVE
    assert "nothing was tested" in detail


# --- C. wikidata_id coverage ------------------------------------------------
def test_wikidata_id_coverage_is_counted():
    rows = [person(1, "Q1"), person(2), person(3, "Q3", "Q659461"), person(4)]
    count, lines = summarise_wikidata_ids(rows)
    assert count == 2
    blob = "\n".join(lines)
    assert "carrying a wikidata_id:    2 (50.0%)" in blob
    assert "carrying a party Q-ID:     1 (25.0%)" in blob


def test_an_empty_wikidata_id_column_is_called_out():
    count, lines = summarise_wikidata_ids([person(1), person(2)])
    assert count == 0
    assert any("populated for nobody" in ln for ln in lines)


def test_blank_strings_do_not_count_as_populated():
    count, _ = summarise_wikidata_ids([person(1, "  "), person(2, "")])
    assert count == 0


# --- D. which identifier to join on -----------------------------------------
def test_a_property_nobody_seated_carries_joins_nothing_here():
    verdict, detail = compare_identifier_coverage(3043, 0, 0)
    assert verdict == CONTRADICTED
    assert "joins nothing" in detail


def test_a_perfectly_overlapping_property_adds_nothing():
    """Two identifiers on the same people are worth no more than one."""
    verdict, detail = compare_identifier_coverage(3043, 3043, 0)
    assert verdict == CONTRADICTED
    assert "match nobody new" in detail


def test_the_measured_near_parity_is_a_second_join_path():
    """The real numbers from 2026-07-29, with a plausible marginal gain."""
    verdict, detail = compare_identifier_coverage(3043, 3025, 40)
    assert verdict == CONTRADICTED  # smaller, so not a replacement...
    assert "would lose 18 seat holder(s)" in detail
    assert "running both is strictly better" in detail  # ...but worth adding
    assert "40" in detail


def test_a_larger_property_makes_switching_defensible():
    verdict, detail = compare_identifier_coverage(3043, 3100, 120)
    assert verdict == CONFIRMED
    assert "defensible on size" in detail


# --- the server-side cross-check --------------------------------------------
def test_the_server_agreeing_confirms():
    """exclude_null makes the API filter, so this does not rest on the probe
    having guessed the column name — the mistake that cost the last answer."""
    verdict, detail = compare_counts(4398, 4398, "begin_date")
    assert verdict == CONFIRMED
    assert "agreeing with the client-side count" in detail


def test_the_server_reporting_none_contradicts():
    verdict, detail = compare_counts(4398, 0, "begin_date")
    assert verdict == CONTRADICTED
    assert "0 of 4398" in detail


def test_a_partial_server_count_names_the_shortfall():
    verdict, detail = compare_counts(4398, 4000, "begin_date")
    assert verdict == CONTRADICTED
    assert "398 carry none" in detail


def test_no_rows_confirms_nothing():
    verdict, _ = compare_counts(0, 0, "begin_date")
    assert verdict == INCONCLUSIVE


def test_lookups_ask_the_api_for_an_exact_match():
    """The API's default is ILIKE substring, which found the wrong Andrey."""
    assert EXACT == {"search_mode": "exact", "search_scope": "metadata"}


# --- the census queries themselves ------------------------------------------
def test_the_coverage_query_counts_both_populations_in_one_pass():
    """Two queries could disagree if the store moved between them."""
    sparql = coverage_query("P1307")
    assert "wdt:P1307" in sparql
    assert "wd:Q18510612" in sparql
    assert sparql.count("SELECT") == 1
    assert "COUNT(DISTINCT ?person) AS ?total" in sparql
    assert "COUNT(DISTINCT ?seated) AS ?seated" in sparql


def test_the_coverage_query_takes_the_position_it_is_given():
    assert "wd:Q18510613" in coverage_query("P1307", "Q18510613")


def test_the_marginal_gain_query_isolates_the_new_property():
    """only_new is what a second join path adds; it must exclude P1307 holders."""
    sparql = marginal_gain_query()
    assert "FILTER NOT EXISTS" in sparql
    assert "wdt:P1307" in sparql
    assert "wdt:P14527" in sparql
    assert "?only_new" in sparql
