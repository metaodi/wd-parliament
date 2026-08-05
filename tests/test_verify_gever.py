"""Tests for the Gever probe's parsing and its decisions.

Same shape as the other probe tests: the network is never touched, the
*decisions* are. Two of them carry the weight here.

**"The value appears in no field" must be reachable.** That is the answer run
20 got from OpenParlData about P13468, and the answer this probe exists to ask
Gever for. A hunt that could only ever return "found" or "nothing compared"
would be unable to say the one thing that settles the question.

**A name several items claim is dropped, not arbitrated.** The comparison reads
a value off whichever Gever record a name reaches; pairing it with the wrong
item's identifier would report a disagreement that is really an ambiguity —
the same reason ``resolve`` skips a P1307 two items claim.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

from verify_gever import (  # noqa: E402
    CONFIRMED,
    CONTRADICTED,
    INCONCLUSIVE,
    FIELD_CANDIDATES,
    classify_identifier,
    classify_row_key,
    column_report,
    distinct_values,
    field_coverage,
    find_identifier_columns,
    flatten_element,
    index_by_name,
    name_key,
    parse_records,
    parse_search_fields,
    present_columns,
    record_values,
    strip_ns,
    wanted_by_name,
    zh_member_id_query,
)


@pytest.fixture
def mitglieder():
    return (FIXTURES / "gever_mitglieder.xml").read_bytes()


@pytest.fixture
def records(mitglieder):
    return parse_records(mitglieder)[0]


# --- parsing ----------------------------------------------------------------
def test_strip_ns_drops_the_namespace_and_folds_case():
    assert strip_ns("{http://www.cmiag.ch/cdws/Mitglieder}Mitglied") == "mitglied"
    assert strip_ns("Vorname") == "vorname"


def test_parse_records_reads_every_hit_and_the_total(mitglieder):
    records, total = parse_records(mitglieder)
    assert len(records) == 3
    assert total == 3


def test_the_record_element_is_taken_not_the_snippet(records):
    """``Snippet`` is a sibling of the record and carries no OBJ_GUID."""
    assert all("obj_guid" in record for record in records)
    assert not any(key.startswith("snippet") for record in records for key in record)


def test_attributes_are_fields_too(records):
    assert records[0]["obj_guid"] == "c180bdd8c294436ca373a46168449b38"
    assert records[0]["seq"] == "2244279"
    assert records[0]["idx"] == "Mitglieder"


def test_nesting_is_flattened_with_underscores(records):
    assert records[0]["dauer_start"] == "2019-05-06T00:00:00"
    assert records[0]["dauer_end"] == "9999-12-31T23:59:59"
    assert records[0]["parteizugehoerigkeit_kurzname"] == "SP"
    # an attribute on a nested element keeps its parent's path
    assert (
        records[0]["parteizugehoerigkeit_obj_guid"]
        == "2c7f8e1373594fb5bf6cf07bed46a154"
    )


def test_a_nil_element_is_an_empty_column_not_a_missing_one(records):
    """The run-19 distinction: no such column and a null column differ."""
    assert "sitz" in records[0]
    assert records[0]["sitz"] is None
    assert records[2]["beruf"] is None
    assert records[0]["beruf"] == "Juristin"


def test_a_repeated_element_becomes_a_list():
    xml = b"""<Root xmlns="urn:x"><Hit Guid="g"><Mitglied OBJ_GUID="g">
      <Position><Name>Mitglied</Name></Position>
      <Position><Name>Praesidentin</Name></Position>
    </Mitglied></Hit></Root>"""
    record = parse_records(xml)[0][0]
    assert record["position_name"] == ["Mitglied", "Praesidentin"]
    assert record_values(record)["position_name"] == ["Mitglied", "Praesidentin"]


def test_flatten_element_ignores_the_nil_attribute_itself():
    import xml.etree.ElementTree as ET

    elem = ET.fromstring(
        '<Mitglied xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'OBJ_GUID="g"><Beruf xsi:nil="true"/></Mitglied>'
    )
    record = flatten_element(elem)
    assert record == {"obj_guid": "g", "beruf": None}


def test_parse_search_fields_reads_the_query_side():
    schema = (FIXTURES / "gever_mitglieder_schema.xml").read_bytes()
    fields = parse_search_fields(schema)
    assert "Wahlkreis" in fields and "VorNachname" in fields
    assert len(fields) == len(set(fields))


# --- the column report ------------------------------------------------------
def test_column_report_counts_filled_records_and_keeps_empty_columns(records):
    report = dict((column, filled) for column, filled, _ in column_report(records))
    assert report["name"] == 3
    assert report["beruf"] == 2  # one member's is nil
    assert report["sitz"] == 0  # present in every record, filled in none
    assert "sitz" in report


def test_distinct_values_counts_the_gremien(records):
    assert distinct_values(records, "gremium") == [
        ("FRASP", 1),
        ("FRGRU", 1),
        ("KJS", 1),
    ]


# --- names ------------------------------------------------------------------
def test_name_key_is_order_free_and_punctuation_free():
    assert name_key("Rueff-Frenkel", "Sonja") == name_key("Sonja Rueff-Frenkel")


def test_name_key_folds_accents():
    assert name_key("Bäumli-Roth", "Rita") == name_key("Baumli Roth Rita")


def test_index_by_name_groups_a_persons_rows(records):
    index, unnamed = index_by_name(records)
    assert unnamed == 0
    assert len(index) == 2
    assert len(index[name_key("Muster", "Anna")]) == 2


def test_index_by_name_counts_rows_with_no_name():
    index, unnamed = index_by_name([{"obj_guid": "g"}])
    assert index == {} and unnamed == 1


# --- the identifier hunt ----------------------------------------------------
def person(**fields):
    return dict({"obj_guid": "g", "name": "Muster", "vorname": "Anna"}, **fields)


def test_a_field_carrying_the_value_is_found():
    index = {name_key("Muster", "Anna"): [person(krnummer="21984")]}
    counts, compared, missing, examples = find_identifier_columns(
        index, {name_key("Muster", "Anna"): "21984"}
    )
    assert counts == {"krnummer": 1}
    assert (compared, missing, examples) == (1, 0, [])


def test_a_leading_zero_is_not_a_disagreement():
    index = {name_key("Muster", "Anna"): [person(krnummer="021984")]}
    counts, _, _, _ = find_identifier_columns(
        index, {name_key("Muster", "Anna"): 21984}
    )
    assert counts == {"krnummer": 1}


def test_the_value_appearing_nowhere_is_the_finding():
    """Run 20's answer about OpenParlData, and the one this probe can give."""
    index = {name_key("Muster", "Anna"): [person(seq="2244279")]}
    counts, compared, missing, examples = find_identifier_columns(
        index, {name_key("Muster", "Anna"): "21984"}
    )
    assert counts == {}
    assert compared == 1 and missing == 0
    assert examples and "no field" in examples[0]


def test_a_name_the_source_does_not_have_is_counted_not_compared():
    counts, compared, missing, _ = find_identifier_columns(
        {}, {name_key("Muster", "Anna"): "21984"}
    )
    assert counts == {} and compared == 0 and missing == 1


def test_every_row_of_a_person_is_searched():
    """A person's fields are spread over their Gremium rows."""
    index = {
        name_key("Muster", "Anna"): [
            person(gremium="FRASP"),
            person(gremium="KJS", krnummer="21984"),
        ]
    }
    counts, _, _, _ = find_identifier_columns(
        index, {name_key("Muster", "Anna"): "21984"}
    )
    assert counts == {"krnummer": 1}


# --- the verdicts -----------------------------------------------------------
def test_no_match_at_all_is_inconclusive():
    verdict, detail = classify_identifier({}, 0, 0)
    assert verdict == INCONCLUSIVE
    assert "overlap" in detail


def test_nothing_carries_the_value_is_contradicted():
    verdict, detail = classify_identifier({}, 12, 12)
    assert verdict == CONTRADICTED
    assert "Staatsarchiv" in detail


def test_a_field_carrying_every_value_is_confirmed():
    verdict, detail = classify_identifier({"krnummer": 12}, 12, 12)
    assert verdict == CONFIRMED
    assert "krnummer" in detail


def test_a_partial_match_is_not_an_identifier():
    verdict, detail = classify_identifier({"seq": 3}, 12, 12)
    assert verdict == CONTRADICTED
    assert "collides" in detail


# --- is the record key a person? --------------------------------------------
def test_two_rows_with_two_guids_mean_the_key_is_a_row(records):
    index, _ = index_by_name(records)
    verdict, detail, lines = classify_row_key(index)
    assert verdict == CONTRADICTED
    assert "membership row" in detail
    assert lines


def test_two_rows_with_one_guid_mean_the_key_is_a_person():
    index = {
        name_key("Muster", "Anna"): [
            person(gremium="FRASP"),
            person(gremium="KJS"),
        ]
    }
    verdict, _, _ = classify_row_key(index)
    assert verdict == CONFIRMED


def test_one_row_each_tests_nothing():
    verdict, _, _ = classify_row_key({name_key("Muster", "Anna"): [person()]})
    assert verdict == INCONCLUSIVE


# --- what else --------------------------------------------------------------
def test_present_columns_reports_only_columns_the_service_returns(records):
    seen = [column for column, _, _ in column_report(records)]
    assert present_columns(seen, ("beruf", "occupation_de")) == ["beruf"]
    assert present_columns(seen, ("occupation_de",)) == []


def test_field_coverage_counts_records_with_a_value(records):
    assert field_coverage(records, ["beruf"]) == 2
    assert field_coverage(records, ["sitz"]) == 0
    assert field_coverage(records, ["beruf", "wahlkreis"]) == 3


def test_every_candidate_names_a_property_and_at_least_one_column():
    for prop, (label, candidates) in FIELD_CANDIDATES.items():
        assert prop.startswith("P") and label and candidates


# --- the SPARQL side --------------------------------------------------------
def test_the_query_is_global_rather_than_bounded_by_a_position():
    sparql = zh_member_id_query("de")
    assert "wdt:P13468" in sparql
    assert "P39" not in sparql


def binding(qid, label, value):
    return {
        "person": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "personLabel": {"value": label},
        "value": {"value": value},
    }


def test_wanted_by_name_keys_items_by_their_label():
    wanted, read, dropped = wanted_by_name([binding("Q1", "Sonja Rueff-Frenkel", "21984")])
    assert wanted == {name_key("Rueff-Frenkel", "Sonja"): "21984"}
    assert (read, dropped) == (1, 0)


def test_two_items_sharing_a_name_are_dropped_not_arbitrated():
    wanted, read, dropped = wanted_by_name(
        [binding("Q1", "Anna Muster", "21984"), binding("Q2", "Anna Muster", "22518")]
    )
    assert wanted == {}
    assert (read, dropped) == (2, 1)


def test_one_person_with_two_statements_of_the_same_value_survives():
    wanted, _, dropped = wanted_by_name(
        [binding("Q1", "Anna Muster", "21984"), binding("Q1", "Anna Muster", "21984")]
    )
    assert wanted == {name_key("Anna", "Muster"): "21984"}
    assert dropped == 0


def test_an_item_with_no_label_has_no_name_to_match_on():
    wanted, read, _ = wanted_by_name([binding("Q1", "Q1", "21984")])
    assert wanted == {} and read == 1
