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
    chamber_of,
    classify_seat_memberships,
    compare_identifier_coverage,
    coverage_query,
    find_chamber_groups,
    marginal_gain_query,
    summarise_wikidata_ids,
)


def group(id=1, name="Grosser Rat des Kantons Freiburg"):
    return {"id": id, "body_key": "FR", "name_de": name}


def membership(kind="council_legislative", name="Nationalrat", start=None, end=None,
               person_id=1):
    return {
        "type_harmonized": kind,
        "group_name_de": name,
        "person_id": person_id,
        "date_start": start,
        "date_end": end,
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


def test_both_chambers_are_found_among_cantonal_groups():
    rows = [group(1), group(2, "Nationalrat"), group(3, "Ständerat"), group(4)]
    found, lines = find_chamber_groups(rows)
    assert {k: v["id"] for k, v in found.items()} == {"NR": 2, "SR": 3}
    assert any("2 chamber(s) identified" in ln for ln in lines)


def test_a_missing_chamber_is_named_rather_than_omitted():
    found, lines = find_chamber_groups([group(2, "Nationalrat")])
    assert set(found) == {"NR"}
    assert any("SR: NOT FOUND" in ln for ln in lines)


def test_no_chambers_at_all_shows_what_was_there_instead():
    found, lines = find_chamber_groups([group(1), group(2, "Kantonsrat Zug")])
    assert found == {}
    blob = "\n".join(lines)
    assert "Sample of what is there instead" in blob
    assert "Kantonsrat Zug" in blob


def test_an_empty_groups_table_is_not_reported_as_a_finding():
    found, lines = find_chamber_groups([])
    assert found == {}
    assert any("came back empty" in ln for ln in lines)


# --- B. do the seat memberships carry dates? --------------------------------
def test_undated_memberships_rule_out_a_replacement():
    """What the first run found for a cantonal seat: type right, dates null."""
    rows = [membership(start=None), membership(start=None, person_id=2)]
    verdict, detail, _ = classify_seat_memberships(rows)
    assert verdict == CONTRADICTED
    assert "cannot source P39" in detail


def test_fully_dated_memberships_confirm():
    rows = [
        membership(start="2019-12-02", person_id=1),
        membership(start="2023-12-04", end="2025-06-01", person_id=2),
    ]
    verdict, detail, _ = classify_seat_memberships(rows)
    assert verdict == CONFIRMED
    assert "All 2 memberships carry a date_start" in detail


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
    assert "with a date_start:      2" in blob
    assert "with a date_end:        0" in blob


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
