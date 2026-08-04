"""Tests for the SPARQL builders and the binding-row parsing.

No HTTP is mocked: ``run_query`` is overridden with canned binding rows, which
is the same seam the real client uses.
"""

from datetime import date

import pytest

from wd_parliament.wikidata import (
    WikidataClient,
    date_value,
    qid_from_uri,
)

ENTITY = "http://www.wikidata.org/entity/"
POSITIONS = ["Q18510612", "Q18510613"]


def uri(qid):
    return {"value": ENTITY + qid}


def lit(value):
    return {"value": value}


class FakeClient(WikidataClient):
    """A client whose ``run_query`` replays canned rows per query."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.queries = []

    def run_query(self, sparql):
        self.queries.append(sparql)
        return self._responses.pop(0) if self._responses else []


# --- helpers ----------------------------------------------------------------
@pytest.mark.parametrize(
    "value,expected",
    [
        (ENTITY + "Q42", "Q42"),
        ("http://www.wikidata.org/entity/statement/Q42-abc", None),
        ("", None),
        ("nonsense", None),
    ],
)
def test_qid_from_uri(value, expected):
    assert qid_from_uri(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2021-07-01T00:00:00Z", date(2021, 7, 1)),
        ("+2021-07-01T00:00:00Z", date(2021, 7, 1)),
        ("2021-00-00T00:00:00Z", None),  # year precision: unusable as a date
        ("", None),
    ],
)
def test_date_value(value, expected):
    assert date_value({"value": value}) == expected


def test_date_value_of_nothing():
    assert date_value(None) is None


# --- query construction -----------------------------------------------------
def test_position_query_names_every_qualifier():
    q = WikidataClient.position_query(POSITIONS)
    for prop in ("pq:P580", "pq:P582", "pq:P768", "pq:P4100", "pq:P2937"):
        assert prop in q
    assert "wd:Q18510612" in q and "wd:Q18510613" in q
    assert "DeprecatedRank" in q


def test_identifier_query_uses_p1307():
    assert "wdt:P1307" in WikidataClient.identifier_query()


def test_party_query_excludes_closed_statements():
    q = WikidataClient.party_query(POSITIONS)
    assert "FILTER NOT EXISTS" in q and "pq:P582" in q


def test_person_data_query_asks_one_question_per_person_and_property():
    q = WikidataClient.person_data_query(POSITIONS, ["P19", "P106"])
    assert "VALUES ?prop { wdt:P19 wdt:P106 }" in q
    # The population is both halves of the diff's view of Wikidata: everyone
    # with the identifier, and everyone holding one of the seats.
    assert "wdt:P1307" in q and "wd:Q18510612" in q
    # And it must be OPTIONAL, or a person carrying none of the properties
    # would be indistinguishable from a person nobody asked about.
    assert "OPTIONAL" in q


def test_person_data_query_follows_the_configured_identifier():
    q = WikidataClient.person_data_query(POSITIONS, ["P19"], "P14527")
    assert "wdt:P14527" in q


def test_people_search_query_is_narrow():
    q = WikidataClient.people_search_query(["Anna Muster"], POSITIONS)
    assert "wdt:P31 wd:Q5" in q  # humans only
    assert "wd:Q82955" in q  # politicians
    assert "wdt:P569" in q  # the birth date disambiguator
    assert '"Anna Muster"@de' in q


def test_search_query_escapes_quotes():
    q = WikidataClient.people_search_query(['Anna "Ann" Muster'], POSITIONS)
    assert r'\"Ann\"' in q


def test_name_variant_query_asks_for_both_kinds_of_name():
    q = WikidataClient.name_variant_query(["Q7"])
    assert "skos:altLabel" in q          # "also known as"
    assert "p:P1307 ?idStatement" in q   # the qualifier hangs off the statement
    assert "pq:P1810" in q               # "subject named as"
    assert "UNION" in q                  # never a product of aliases × P1810
    assert '"de"' in q and '"mul"' in q  # aliases restricted to label languages


def test_name_variant_query_follows_the_configured_identifier():
    """P1307 federally, P14527 cantonally — the qualifier hangs off whichever."""
    q = WikidataClient.name_variant_query(["Q7"], "de", "P14527")
    assert "p:P14527 ?idStatement" in q
    assert "p:P1307" not in q


# --- parsing ----------------------------------------------------------------
def test_identifier_index():
    client = FakeClient([[
        {"person": uri("Q121160"), "personLabel": lit("Guy Parmelin"),
         "parliamentId": lit("1108"), "birth": lit("1959-11-09T00:00:00Z")},
    ]])
    people = client.get_identifier_index()
    assert people["Q121160"].parliament_id == "1108"
    assert people["Q121160"].birth_date == date(1959, 11, 9)


def test_position_holders_merges_repeated_qualifier_rows():
    """Repeated qualifiers arrive as extra rows and must be collected, not lost."""
    statement = "http://www.wikidata.org/entity/statement/Q7-abc"
    rows = [
        {"person": uri("Q7"), "personLabel": lit("Anna Muster"),
         "statement": lit(statement), "position": uri("Q18510612"),
         "start": lit("2019-12-02T00:00:00Z"), "term": uri("Q51")},
        {"person": uri("Q7"), "personLabel": lit("Anna Muster"),
         "statement": lit(statement), "position": uri("Q18510612"),
         "start": lit("2019-12-02T00:00:00Z"), "term": uri("Q52")},
        {"person": uri("Q7"), "personLabel": lit("Anna Muster"),
         "statement": lit(statement), "position": uri("Q18510612"),
         "start": lit("2019-12-02T00:00:00Z"), "district": uri("Q11943")},
    ]
    client = FakeClient([[], rows, []])
    people = client.get_position_holders(POSITIONS)
    statements = people["Q7"].statements
    assert len(statements) == 1
    assert statements[0].terms == ["Q51", "Q52"]
    assert statements[0].districts == ["Q11943"]
    assert statements[0].start == date(2019, 12, 2)
    assert statements[0].is_open is True


def test_position_holders_keeps_separate_statements_apart():
    rows = [
        {"person": uri("Q7"), "statement": lit("stmt/A"), "position": uri("Q18510612"),
         "start": lit("2019-12-02T00:00:00Z"), "end": lit("2023-12-03T00:00:00Z")},
        {"person": uri("Q7"), "statement": lit("stmt/B"), "position": uri("Q18510612"),
         "start": lit("2023-12-04T00:00:00Z")},
    ]
    client = FakeClient([[], rows, []])
    people = client.get_position_holders(POSITIONS)
    assert len(people["Q7"].statements) == 2
    assert sum(1 for s in people["Q7"].statements if s.is_open) == 1


def test_position_holders_merges_the_identifier_index():
    identifiers = [{"person": uri("Q7"), "personLabel": lit("Anna Muster"),
                    "parliamentId": lit("1101")}]
    positions = [{"person": uri("Q7"), "statement": lit("stmt/A"),
                  "position": uri("Q18510612")}]
    client = FakeClient([identifiers, positions, []])
    people = client.get_position_holders(POSITIONS)
    assert people["Q7"].parliament_id == "1101"
    assert len(people["Q7"].statements) == 1


def test_position_holders_collects_parties():
    parties = [{"person": uri("Q7"), "party": uri("Q35591")}]
    client = FakeClient([[], [], parties])
    people = client.get_position_holders(POSITIONS)
    assert people["Q7"].parties == ["Q35591"]


def test_person_data_records_both_what_is_there_and_that_it_was_asked():
    prop = "http://www.wikidata.org/prop/direct/"
    rows = [
        {"person": uri("Q7"), "prop": lit(prop + "P19")},
        {"person": uri("Q7"), "prop": lit(prop + "P106")},
        {"person": uri("Q8")},  # queried, carries none of them
    ]
    client = FakeClient([[], [], [], rows])
    people = client.get_position_holders(POSITIONS, person_data_properties=["P19", "P106"])

    assert people["Q7"].properties == ["P19", "P106"]
    assert people["Q7"].has_property("P19") is True
    # The OPTIONAL row: no property, but the item *was* looked at, which is the
    # difference between "carries none" (a finding) and "unknown" (silence).
    assert people["Q8"].properties == []
    assert people["Q8"].person_data_known is True


def test_no_person_data_query_runs_when_none_is_configured():
    client = FakeClient([[], [], []])
    people = client.get_position_holders(POSITIONS)
    assert len(client.queries) == 3
    assert all(not p.person_data_known for p in people.values())


def test_statements_for_filters_by_position():
    rows = [
        {"person": uri("Q7"), "statement": lit("A"), "position": uri("Q18510612")},
        {"person": uri("Q7"), "statement": lit("B"), "position": uri("Q18510613")},
    ]
    client = FakeClient([[], rows, []])
    people = client.get_position_holders(POSITIONS)
    assert len(people["Q7"].statements_for("Q18510612")) == 1


# --- search -----------------------------------------------------------------
def test_search_people_returns_birth_dates():
    rows = [
        {"name": lit("Anna Muster"), "person": uri("Q7"), "personLabel": lit("Anna Muster"),
         "birth": lit("1970-01-01T00:00:00Z"), "hasPosition": lit("true")},
    ]
    matches = FakeClient([rows]).search_people(["Anna Muster"], POSITIONS)
    match = matches["Anna Muster"][0]
    assert match.qid == "Q7"
    assert match.birth_date == date(1970, 1, 1)
    assert match.has_position is True


def test_search_people_skips_single_word_names():
    client = FakeClient([[]])
    assert client.search_people(["Muster"], POSITIONS) == {}
    assert client.queries == []  # nothing was even asked


def test_search_people_deduplicates_across_label_languages():
    rows = [
        {"name": lit("Anna Muster"), "person": uri("Q7")},
        {"name": lit("Anna Muster"), "person": uri("Q7")},
    ]
    matches = FakeClient([rows]).search_people(["Anna Muster"], POSITIONS)
    assert len(matches["Anna Muster"]) == 1


def test_name_variants_are_grouped_by_item_and_keep_their_origin():
    rows = [
        {"person": uri("Q7"), "name": {"value": "Johannes Zündt", "xml:lang": "de"},
         "origin": lit("alias")},
        {"person": uri("Q7"), "name": lit("Zündt Johannes"), "origin": lit("P1810")},
        {"person": uri("Q8"), "name": lit("Desplands"), "origin": lit("P1810")},
    ]
    variants = FakeClient([rows]).get_name_variants(["Q7", "Q8", "Q9"])
    assert [v.name for v in variants["Q7"]] == ["Johannes Zündt", "Zündt Johannes"]
    assert variants["Q7"][0].origin == "alias"
    assert variants["Q7"][0].language == "de"
    assert variants["Q7"][1].origin == "P1810"
    # An item with neither is absent, not empty: "nothing more to compare".
    assert "Q9" not in variants


def test_name_variants_deduplicate_and_ask_once_per_batch():
    rows = [
        {"person": uri("Q7"), "name": lit("Zündt"), "origin": lit("alias")},
        {"person": uri("Q7"), "name": lit("Zündt"), "origin": lit("alias")},
        {"person": uri("Q7"), "name": lit("Zündt"), "origin": lit("P1810")},
    ]
    client = FakeClient([rows])
    variants = client.get_name_variants(["Q7", "Q7"])
    assert len(client.queries) == 1  # the duplicate Q-ID is not asked twice
    # Same string, different assertion: both are kept, because which one settled
    # a row is the thing section B prints.
    assert [(v.name, v.origin) for v in variants["Q7"]] == [
        ("Zündt", "alias"),
        ("Zündt", "P1810"),
    ]


def test_describe_qids_reports_labels_and_instances():
    rows = [
        {"item": uri("Q11943"), "itemLabel": lit("Canton of Zürich"),
         "itemDescription": lit("canton of Switzerland"),
         "instanceLabel": lit("canton of Switzerland")},
    ]
    described = FakeClient([rows]).describe_qids(["Q11943", "Q999999999"])
    assert described["Q11943"]["label"] == "Canton of Zürich"
    # An item that came back with nothing is still reported, as unresolved.
    assert described["Q999999999"]["label"] is None
