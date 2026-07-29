"""Tests for the pure decisions in ``scripts/verify_openparldata.py``.

The API calls are network code and are left alone, as in
``test_verify_source.py``. What is worth pinning down is what the probe
*concludes* from what comes back — above all ``classify_seat_memberships``,
which is the finding that decides whether the OpenParlData backend could
replace the OData read or only enrich it.
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
    classify_seat_memberships,
    compare_identifier_coverage,
    coverage_query,
    find_federal_bodies,
    summarise_wikidata_ids,
)


def body(id=1, key="ZH", name="Kantonsrat Zürich"):
    return {"id": id, "body_key": key, "name_de": name}


def membership(kind="committee", name="Büro NR", start=None, end=None):
    return {
        "type_harmonized": kind,
        "group_name_de": name,
        "role_name_de": "Mitglied",
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


# --- A. is the Federal Assembly there? --------------------------------------
def test_a_federal_body_is_found_by_name():
    rows = [body(), body(2, "CH", "Nationalrat")]
    federal, lines = find_federal_bodies(rows)
    assert [r["id"] for r in federal] == [2]
    assert any("1 of them federal" in ln for ln in lines)


def test_a_federal_body_is_found_by_key_alone():
    """The key may be the only federal marker if the name is in French."""
    federal, _ = find_federal_bodies([body(2, "CHE", "Assemblée")])
    assert len(federal) == 1


@pytest.mark.parametrize(
    "name",
    ["Ständerat", "Conseil national", "Bundesversammlung", "Federal Assembly"],
)
def test_the_chamber_names_are_recognised_in_several_languages(name):
    federal, _ = find_federal_bodies([body(2, "XX", name)])
    assert len(federal) == 1


def test_a_cantonal_only_api_is_called_out():
    """The answer that makes the rest of the evaluation moot."""
    federal, lines = find_federal_bodies([body(), body(2, "LU", "Kantonsrat Luzern")])
    assert federal == []
    assert any("cantonal only" in ln for ln in lines)


def test_no_bodies_at_all_is_not_reported_as_a_finding():
    federal, lines = find_federal_bodies([])
    assert federal == []
    assert any("could not be read" in ln for ln in lines)


# --- B. wikidata_id coverage ------------------------------------------------
def test_wikidata_id_coverage_is_counted():
    rows = [person(1, "Q1"), person(2), person(3, "Q3", "Q659461"), person(4)]
    count, lines = summarise_wikidata_ids(rows)
    assert count == 2
    blob = "\n".join(lines)
    assert "coverage:                      50.0%" in blob
    assert "carrying a party Q-ID:         1" in blob


def test_an_empty_wikidata_id_column_is_called_out():
    count, lines = summarise_wikidata_ids([person(1), person(2)])
    assert count == 0
    assert any("populated for nobody" in ln for ln in lines)


def test_blank_strings_do_not_count_as_populated():
    count, _ = summarise_wikidata_ids([person(1, "  "), person(2, "")])
    assert count == 0


# --- C. does memberships model the seat? ------------------------------------
def test_committee_only_memberships_rule_out_a_replacement():
    """What swissparlpy's own docs show: committees and interest groups only."""
    rows = [
        membership("interest_group", "Kultur"),
        membership("committee_ad_hoc", "Gruppe Parlaments-IT (PIT)"),
        membership("committee", "Büro NR"),
    ]
    verdict, detail, _ = classify_seat_memberships(rows)
    assert verdict == CONTRADICTED
    assert "cannot replace it as the source of P39" in detail


def test_a_dated_seat_membership_confirms():
    rows = [
        membership("committee", "Büro NR"),
        membership("parliament", "Nationalrat", start="2019-12-02", end=None),
    ]
    verdict, detail, _ = classify_seat_memberships(rows)
    assert verdict == CONFIRMED
    assert "can source P39" in detail


def test_a_seat_membership_without_a_start_is_still_no_good():
    """No start means no P580 and no period overlap, so it sources nothing."""
    rows = [membership("parliament", "Nationalrat", start=None)]
    verdict, detail, _ = classify_seat_memberships(rows)
    assert verdict == CONTRADICTED
    assert "date_start" in detail


def test_the_seat_is_recognised_by_name_when_the_type_is_generic():
    rows = [membership("membership", "Ständerat", start="2023-12-04")]
    assert classify_seat_memberships(rows)[0] == CONFIRMED


def test_no_memberships_is_inconclusive_not_a_refutation():
    verdict, detail, _ = classify_seat_memberships([])
    assert verdict == INCONCLUSIVE
    assert "nothing was tested" in detail


# --- D. which identifier to join on -----------------------------------------
def test_an_unused_property_cannot_be_a_join_key():
    verdict, detail = compare_identifier_coverage(3043, 0, 0)
    assert verdict == CONTRADICTED
    assert "does not exist" in detail


def test_a_property_nobody_seated_carries_joins_nothing_here():
    verdict, detail = compare_identifier_coverage(3043, 0, 500)
    assert verdict == CONTRADICTED
    assert "joins nothing" in detail


def test_lower_coverage_is_a_second_join_path_not_a_replacement():
    verdict, detail = compare_identifier_coverage(3043, 400, 900)
    assert verdict == CONTRADICTED
    assert "second" in detail
    assert "lower the hit rate" in detail


def test_equal_or_better_coverage_makes_switching_defensible():
    verdict, detail = compare_identifier_coverage(3043, 3043, 4000)
    assert verdict == CONFIRMED
    assert "running both is still better" in detail


# --- the census query itself ------------------------------------------------
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
