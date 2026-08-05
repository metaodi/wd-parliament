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
    FIRST_NAME_FIELDS,
    NAME_FIELDS,
    PERSON_KEY_FIELDS,
    ROW_KEY_FIELDS,
    YEAR_ONLY_COLUMNS,
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
    plausible_year,
    present_columns,
    record_values,
    resolve_column,
    resolve_columns,
    strip_ns,
    wanted_by_name,
    zh_member_id_query,
)

# How the probe reads a person's name off a record, measured by run 22.
PERSON_NAME_COLUMNS = (NAME_FIELDS[0], FIRST_NAME_FIELDS[0])


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
    """The person is under Person/Kontakt, which run 22 measured."""
    assert records[0]["dauer_start"] == "2019-05-06T00:00:00.000"
    assert records[0]["person_kontakt_name"] == "Muster"
    assert (
        records[0][
            "person_kontakt_parteizugehoerigkeiten_parteizugehoerigkeit_kurzname"
        ]
        == "SP"
    )
    # an attribute on a nested element keeps its parent's path
    assert (
        records[0]["person_kontakt_obj_guid"] == "2c74bdac96f444719f08bddc6c596917"
    )


def test_a_record_carries_a_row_key_and_a_person_key(records):
    """Two GUIDs, and the whole join question turns on the difference."""
    assert records[0]["obj_guid"] != records[0]["person_kontakt_obj_guid"]
    assert records[0]["person_kontakt_obj_guid"] == records[1]["person_kontakt_obj_guid"]


def test_a_nil_element_is_an_empty_column_not_a_missing_one(records):
    """The run-19 distinction: no such column and a null column differ."""
    assert "sitz" in records[0]
    assert records[0]["sitz"] is None
    assert records[2]["person_kontakt_beruf"] is None
    assert records[0]["person_kontakt_beruf"] == "Juristin"


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
    assert report["person_kontakt_name"] == 3
    assert report["person_kontakt_beruf"] == 2  # one member's is nil
    assert report["sitz"] == 0  # present in every record, filled in none
    assert "sitz" in report


def test_distinct_values_counts_the_gremien(records):
    assert distinct_values(records, "gremium") == [("KR", 2), ("KJS", 1)]


# --- names ------------------------------------------------------------------
def test_name_key_is_order_free_and_punctuation_free():
    assert name_key("Rueff-Frenkel", "Sonja") == name_key("Sonja Rueff-Frenkel")


def test_name_key_folds_accents():
    assert name_key("Bäumli-Roth", "Rita") == name_key("Baumli Roth Rita")


def test_index_by_name_groups_a_persons_rows(records):
    index, unnamed = index_by_name(records, *PERSON_NAME_COLUMNS)
    assert unnamed == 0
    assert len(index) == 2
    assert len(index[name_key("Muster", "Anna")]) == 2


def test_index_by_name_on_the_wrong_columns_finds_nobody(records):
    """Run 22's defect, pinned: right field, wrong depth, and it reads as
    a fact about the source."""
    index, unnamed = index_by_name(records, "name", "vorname")
    assert index == {} and unnamed == 3


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


# --- is the record key a person, or only a row? ------------------------------
# The two questions one record answers differently, and asking only the second
# would report "no person-level key" about a source that has one.
def test_the_row_guid_varies_within_a_person(records):
    index, _ = index_by_name(records, *PERSON_NAME_COLUMNS)
    verdict, detail, lines = classify_row_key(index, ROW_KEY_FIELDS[0])
    assert verdict == CONTRADICTED
    assert "does not identify a person" in detail
    assert lines


def test_the_person_guid_does_not(records):
    index, _ = index_by_name(records, *PERSON_NAME_COLUMNS)
    verdict, detail, _ = classify_row_key(index, PERSON_KEY_FIELDS[0])
    assert verdict == CONFIRMED
    assert "person-level key" in detail


def test_one_row_each_tests_nothing():
    verdict, _, _ = classify_row_key(
        {name_key("Muster", "Anna"): [person()]}, "obj_guid"
    )
    assert verdict == INCONCLUSIVE


# Run 23's 14-of-748: a name is not a person, and two readings fit that shape.
def keyed(guid, year, **fields):
    return person(person_key=guid, birth_year=year, **fields)


def test_two_keys_with_different_birth_years_are_two_people():
    index = {
        name_key("Bachmann", "Oskar"): [
            keyed("a", "1921", gremium="KR"),
            keyed("b", "1968", gremium="KR"),
        ]
    }
    verdict, detail, lines = classify_row_key(index, "person_key", "birth_year")
    assert verdict == CONFIRMED
    assert "1 namesake(s), 0 split, 0 undecided" in detail
    assert "namesakes" in lines[0]


def test_two_keys_with_one_birth_year_are_one_person_split():
    index = {
        name_key("Bachmann", "Oskar"): [
            keyed("a", "1921", gremium="KR"),
            keyed("b", "1921", gremium="FRASP"),
        ]
    }
    verdict, detail, _ = classify_row_key(index, "person_key", "birth_year")
    assert verdict == CONTRADICTED
    assert "does not identify a person" in detail


def test_two_keys_and_no_birth_year_is_undecided_not_a_failure():
    """Neither reading is available, so neither is asserted."""
    index = {
        name_key("Bachmann", "Oskar"): [
            keyed("a", None, gremium="KR"),
            keyed("b", "1921", gremium="FRASP"),
        ]
    }
    verdict, detail, _ = classify_row_key(index, "person_key", "birth_year")
    assert verdict == INCONCLUSIVE
    assert "0 split, 1 undecided" in detail


def test_a_placeholder_year_is_absence_not_a_disagreement():
    """Run 24 called two of its namesakes off a geburtsjahr of '1'.

    A placeholder that reads as data is the cantonal ``1753-01-01``. Reading
    it as a year turns "we cannot tell" into "definitely two people", which is
    the strongest reading from the weakest evidence — and in the direction
    that lets the key off.
    """
    index = {
        name_key("Brunner", "Roland"): [
            keyed("a", "1", gremium="KR"),
            keyed("b", "1952", gremium="FRASP"),
        ]
    }
    verdict, detail, lines = classify_row_key(index, "person_key", "birth_year")
    assert verdict == INCONCLUSIVE
    assert "0 namesake(s), 0 split, 1 undecided" in detail
    assert "undecided" in lines[0]


@pytest.mark.parametrize(
    "value,ok",
    [("1936", True), ("1", False), ("0001", False), ("19366", False),
     ("", False), (None, False), ("1800", False), ("neunzehn", False)],
)
def test_plausible_year(value, ok):
    assert plausible_year(value) is ok


def test_one_split_outweighs_any_number_of_namesakes():
    index = {
        name_key("Bachmann", "Oskar"): [
            keyed("a", "1921"),
            keyed("b", "1968"),
        ],
        name_key("Muster", "Anna"): [
            keyed("c", "1975"),
            keyed("d", "1975"),
        ],
    }
    verdict, detail, _ = classify_row_key(index, "person_key", "birth_year")
    assert verdict == CONTRADICTED
    assert "1 namesake(s), 1 split" in detail


# --- resolving a candidate to a real column ----------------------------------
# Run 22's one real defect in this probe, and the guard against its recurrence:
# the field names were right about the field and wrong about its depth.
@pytest.fixture
def seen(records):
    return [column for column, _, _ in column_report(records)]


def test_an_exact_column_wins(seen):
    assert resolve_column(seen, "person_kontakt_beruf") == "person_kontakt_beruf"


def test_a_flat_candidate_finds_the_nested_column(seen):
    assert resolve_column(seen, "beruf") == "person_kontakt_beruf"
    assert resolve_column(seen, "wahlkreis") == "person_kontakt_wahlkreis"


def test_a_nested_candidate_finds_a_flat_column(seen):
    assert resolve_column(seen, "person_kontakt_dauer_start") == "dauer_start"


def test_the_shallowest_match_wins(seen):
    """``…behoerdenmandat_name`` is the name of a Gremium, not of a person."""
    assert resolve_column(seen, "name") == "person_kontakt_name"


def test_a_leaf_nobody_publishes_stays_unresolved(seen):
    """"No such column" has to remain sayable, or section D says nothing."""
    assert resolve_column(seen, "person_kontakt_website") is None
    assert resolve_columns(seen, ("website", "buergerort")) == []


def test_every_configured_candidate_resolves_against_a_real_record(seen):
    """Every property in section D must reach a column of the live shape."""
    for prop, (_, candidates) in FIELD_CANDIDATES.items():
        assert resolve_columns(seen, candidates), prop


def test_the_birth_column_is_a_year_and_is_flagged(seen):
    columns = resolve_columns(seen, FIELD_CANDIDATES["P569"][1])
    assert columns == ["person_kontakt_geburtsjahr"]
    assert any(c in YEAR_ONLY_COLUMNS for c in columns)


# --- what else --------------------------------------------------------------
def test_present_columns_reports_only_columns_the_service_returns(seen):
    assert present_columns(seen, ("person_kontakt_beruf", "occupation_de")) == [
        "person_kontakt_beruf"
    ]
    assert present_columns(seen, ("beruf",)) == []


def test_field_coverage_counts_records_with_a_value(records):
    assert field_coverage(records, ["person_kontakt_beruf"]) == 2
    assert field_coverage(records, ["sitz"]) == 0
    assert field_coverage(records, ["person_kontakt_beruf", "gremium"]) == 3


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
