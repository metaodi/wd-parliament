"""Tests for the personal-data presence checks (README step 9).

These are the one class of check that compares **presence, not value**, and
almost everything worth pinning down here is a refusal:

- an item nobody asked Wikidata about must produce nothing, or every
  name-matched member becomes a false positive;
- a property Wikidata already carries must produce nothing, whatever the
  source says, because this check never claims a recorded value is wrong;
- a source column that does not exist must produce nothing, which is what
  makes the unmeasured candidate column names in ``parliament`` and
  ``openparldata`` safe to guess at;
- and none of it may ever reach QuickStatements: the source's value is free
  text and the property wants an item.

The mapping tests use the committed fixtures, so they also pin down the two
splitting rules — a comma separates two Bürgerorte but must not tear a single
place name in half.
"""

from datetime import date

import pytest

from wd_parliament.config import Config, load_config
from wd_parliament.diff import compute_suggestions, source_values
from wd_parliament.models import (
    KIND_ADD_PERSON_DATA,
    KIND_REVIEW_PARTY,
    PERSON_DATA_BY_PROPERTY,
    PERSON_DATA_PROPERTIES,
    MODEL_TENURE,
    P_NUMBER_OF_CHILDREN,
    P_OCCUPATION,
    P_OFFICIAL_WEBSITE,
    P_PLACE_OF_BIRTH,
    P_PLACE_OF_ORIGIN,
    P_POLITICAL_PARTY,
    QID_FROM_IDENTIFIER,
    Body,
    Member,
    WikidataPerson,
)
from wd_parliament.openparldata import member_from_rows as opd_member_from_rows
from wd_parliament.parliament import (
    member_from_row,
    members_from_rows,
    occupations,
    place_of_birth,
    places_of_origin,
)
from wd_parliament.quickstatements import is_mechanical, render

from conftest import load_fixture

POSITION = "Q18510612"
BODY = Body(council="NR", label="Swiss National Council", position_qid=POSITION)


def make_config(person_data=None, parties=None):
    return Config(
        statement_model=MODEL_TENURE,
        bodies=[BODY],
        constituencies={"ZH": "Q11943"},
        parties=parties or {},
        person_data=(
            list(PERSON_DATA_PROPERTIES) if person_data is None else list(person_data)
        ),
    )


def make_member(**kwargs):
    defaults = dict(
        person_number=1101,
        first_name="Anna",
        last_name="Muster",
        active=True,
        council="NR",
        constituency_abbreviation="ZH",
        party_abbreviation="SVP",
        party_name="Schweizerische Volkspartei",
        date_joining=date(2023, 12, 4),
        qid="Q7",
        qid_source=QID_FROM_IDENTIFIER,
        place_of_birth="Bern (BE)",
        places_of_origin=["Chur (GR)"],
        occupations=["Rechtsanwalt"],
        website="https://example.ch/anna",
        number_of_children=2,
    )
    defaults.update(kwargs)
    return Member(**defaults)


def make_person(properties=(), known=True, **kwargs):
    """A Wikidata item that *was* asked about the personal-data properties."""
    return WikidataPerson(
        qid="Q7",
        label="Anna Muster",
        parliament_id="1101",
        properties=list(properties),
        person_data_known=known,
        **kwargs,
    )


@pytest.fixture
def zh_person_rows():
    return load_fixture("zh_persons.json")


def person_data(suggestions):
    return [s for s in suggestions if s.kind == KIND_ADD_PERSON_DATA]


def run(member, person, config):
    return compute_suggestions(
        BODY, [member], {person.qid: person}, [], config, today=date(2026, 8, 4)
    )


# --- the source mapping: parlament.ch ---------------------------------------
def test_place_of_birth_carries_the_canton_that_disambiguates_it():
    assert (
        place_of_birth({"BirthPlace_City": "Bern", "BirthPlace_Canton": "BE"})
        == "Bern (BE)"
    )
    assert place_of_birth({"BirthPlace_City": "Bern"}) == "Bern"


def test_a_canton_without_a_city_is_not_a_place_of_birth():
    # P19 wants the municipality; "BE" alone would be a wrong statement, and
    # this check never guesses at a value it does not have.
    assert place_of_birth({"BirthPlace_Canton": "BE"}) is None
    assert place_of_birth({}) is None


def test_two_burgerorte_split_on_the_comma():
    assert places_of_origin("Zürich (ZH), Chur (GR)") == ["Zürich (ZH)", "Chur (GR)"]


def test_a_place_name_containing_a_comma_is_left_whole():
    # The rule is "split only when every part still looks like City (XX)".
    # Without it this would be reported as two municipalities that do not exist.
    assert places_of_origin("Sils im Engadin, Segl (GR)") == [
        "Sils im Engadin, Segl (GR)"
    ]


def test_semicolons_always_separate_places_and_occupations():
    assert places_of_origin("Bern (BE); Chur (GR)") == ["Bern (BE)", "Chur (GR)"]
    assert occupations("Rechtsanwalt; Landwirt") == ["Rechtsanwalt", "Landwirt"]


def test_an_occupation_containing_a_comma_stays_one_occupation():
    assert occupations("Rechtsanwalt, selbständig") == ["Rechtsanwalt, selbständig"]


def test_nothing_from_an_empty_source_field():
    assert places_of_origin(None) == []
    assert places_of_origin("") == []
    assert occupations(None) == []


def test_the_fixture_rows_map_the_personal_columns(member_rows):
    members = {m.person_number: m for m in members_from_rows(member_rows, active_only=False)}

    bachmann = members[1101]
    assert bachmann.place_of_birth == "Musterhausen (ZH)"
    assert bachmann.places_of_origin == ["Musterhausen (ZH)"]
    assert bachmann.number_of_children == 2

    # Two places of origin on one row.
    assert members[1104].places_of_origin == [
        "Musterhausen (SG)",
        "Musterdorf (GR)",
    ]
    # And a member the source has no birthplace for at all.
    assert members[1105].place_of_birth is None


def test_membercouncil_has_no_occupation_or_website_column(member_rows):
    """The state of the world, pinned so a probe finding it can update it.

    ``OCCUPATION_FIELDS`` and ``WEBSITE_FIELDS`` are candidate names rather
    than measured ones, and this is what "a guess that costs nothing" means:
    the columns are absent, so the fields come back empty and the checks make
    no suggestion at all.
    """
    member = member_from_row(member_rows[0])
    assert member.occupations == []
    assert member.website is None


# --- the source mapping: OpenParlData ---------------------------------------
def test_openparldata_reads_whichever_column_the_source_uses():
    member = opd_member_from_rows(
        {"person_id": 9532, "begin_date": "2023-05-08", "role_name_de": "Mitglied"},
        {
            "id": 9532,
            "firstname": "Ruth",
            "lastname": "Genner",
            "birthplace": "Zürich",
            "occupation_de": "Architektin; Politikerin",
            "website": "https://example.ch/ruth",
        },
        BODY,
        today=date(2026, 8, 4),
    )
    assert member.place_of_birth == "Zürich"
    assert member.occupations == ["Architektin", "Politikerin"]
    assert member.website == "https://example.ch/ruth"


def test_openparldata_reads_the_columns_run_19_measured(zh_person_rows):
    """What the ZH ``persons`` table really carries, and what it does not.

    Measured 2026-08-04: an ``occupation_de`` and a ``website_personal``, but
    no birthplace, no Bürgerort and no children — so those three checks make no
    suggestion for this source however they are configured.
    """
    member = opd_member_from_rows(
        {"person_id": 9532, "begin_date": "2023-05-08", "role_name_de": "Mitglied"},
        zh_person_rows[0],
        BODY,
        today=date(2026, 8, 4),
    )
    assert member.occupations == ["Architektin"]
    assert member.website == "https://example.ch/genner"
    assert member.place_of_birth is None
    assert member.places_of_origin == []
    assert member.number_of_children is None


def test_the_parliaments_own_page_is_never_read_as_an_official_website(zh_person_rows):
    """``website_parliament_url_de`` is the member's page on the chamber's site.

    Every ZH person record carries one, and P856 is the person's *own* site —
    filing one as the other would put a wrong statement on 180 people.
    """
    row = dict(zh_person_rows[1])
    assert row["website_parliament_url_de"]  # the fixture carries it, as the API does
    assert "website_personal" not in row
    member = opd_member_from_rows(
        {"person_id": row["id"], "begin_date": "2023-05-08", "role_name_de": "Mitglied"},
        row,
        BODY,
        today=date(2026, 8, 4),
    )
    assert member.website is None


def test_openparldata_reads_the_party_and_birthday_columns(zh_person_rows):
    """The two names run 19 corrected: ``party_de`` and ``birthday``.

    Both were wrong before it, and neither failure was visible from inside the
    pipeline — an unmapped party makes no suggestion, and a missing birth date
    quietly weakens the name fallback instead of breaking it.
    """
    member = opd_member_from_rows(
        {"person_id": 9532, "begin_date": "2023-05-08", "role_name_de": "Mitglied"},
        zh_person_rows[0],
        BODY,
        today=date(2026, 8, 4),
    )
    assert member.party_name == "Grüne"
    assert member.date_of_birth == date(1956, 8, 30)


# --- what counts as a value -------------------------------------------------
def test_zero_children_is_not_a_value():
    """A nullable integer cannot tell 0 from "not stated", so it says nothing."""
    check = PERSON_DATA_BY_PROPERTY[P_NUMBER_OF_CHILDREN]
    assert source_values(make_member(number_of_children=0), check) == []
    assert source_values(make_member(number_of_children=3), check) == ["3"]


def test_blank_strings_are_not_values():
    check = PERSON_DATA_BY_PROPERTY[P_PLACE_OF_BIRTH]
    assert source_values(make_member(place_of_birth="   "), check) == []


# --- the diff ---------------------------------------------------------------
def test_a_missing_property_the_source_knows_is_reported():
    suggestions = person_data(
        run(make_member(), make_person(properties=[]), make_config([P_PLACE_OF_BIRTH]))
    )
    assert len(suggestions) == 1
    assert suggestions[0].payload["property"] == "P19"
    assert suggestions[0].payload["values"] == ["Bern (BE)"]
    assert "Bern (BE)" in suggestions[0].detail
    assert "parlament.ch" in suggestions[0].detail


def test_a_property_wikidata_already_carries_is_not_reported():
    suggestions = person_data(
        run(
            make_member(),
            make_person(properties=["P19"]),
            make_config([P_PLACE_OF_BIRTH]),
        )
    )
    assert suggestions == []


def test_a_property_the_source_cannot_answer_is_not_reported():
    suggestions = person_data(
        run(
            make_member(occupations=[]),
            make_person(properties=[]),
            make_config([P_OCCUPATION]),
        )
    )
    assert suggestions == []


def test_an_item_nobody_queried_produces_nothing():
    """The gate that keeps a name-matched item from becoming a false positive.

    ``person_data_known`` is false for an item outside the personal-data
    query's population — it carries neither the identifier nor a seat — and an
    item nobody asked about is not an item that is missing anything.
    """
    suggestions = person_data(
        run(make_member(), make_person(properties=[], known=False), make_config())
    )
    assert suggestions == []


def test_one_suggestion_per_missing_property():
    suggestions = person_data(run(make_member(), make_person(), make_config()))
    assert {s.payload["property"] for s in suggestions} == {
        "P19",
        "P1321",
        "P106",
        "P102",
        "P856",
        "P1971",
    }


def test_only_the_configured_properties_are_checked():
    config = make_config([P_OFFICIAL_WEBSITE])
    suggestions = person_data(run(make_member(), make_person(), config))
    assert [s.payload["property"] for s in suggestions] == ["P856"]


def test_an_empty_list_turns_the_whole_class_off():
    assert person_data(run(make_member(), make_person(), make_config([]))) == []


def test_an_unmapped_party_is_the_only_thing_that_notices_a_missing_p102():
    """Both configs ship ``parties: {}``, so REVIEW_PARTY never fires.

    That is the gap this check closes: without it a member with no P102
    statement at all goes unmentioned for as long as the map stays empty.
    """
    suggestions = run(make_member(), make_person(), make_config([P_POLITICAL_PARTY]))
    assert [s.kind for s in suggestions if s.kind == KIND_REVIEW_PARTY] == []
    assert [s.payload["property"] for s in person_data(suggestions)] == ["P102"]


def test_a_mapped_party_is_left_to_review_party():
    """One gap, one suggestion — not the same finding filed twice."""
    config = make_config([P_POLITICAL_PARTY], parties={"SVP": "Q49892"})
    suggestions = run(make_member(), make_person(), config)
    assert person_data(suggestions) == []
    assert KIND_REVIEW_PARTY in [s.kind for s in suggestions]


def test_the_place_of_origin_values_reach_the_payload():
    member = make_member(places_of_origin=["Chur (GR)", "Sion (VS)"])
    suggestions = person_data(
        run(member, make_person(), make_config([P_PLACE_OF_ORIGIN]))
    )
    assert suggestions[0].payload["values"] == ["Chur (GR)", "Sion (VS)"]


def test_they_sort_below_every_seat_finding():
    """Priority 6: enrichment must not bury the findings about the mandate."""
    member = make_member()
    suggestions = run(member, make_person(), make_config())
    kinds = [s.kind for s in suggestions]
    assert kinds[-1] == KIND_ADD_PERSON_DATA
    assert KIND_ADD_PERSON_DATA not in kinds[: kinds.index(KIND_ADD_PERSON_DATA)]


# --- and never mechanical ---------------------------------------------------
def test_never_reaches_quickstatements_even_for_an_identifier_match():
    """Refused twice: the kind is not mechanical, and there is no ``position``.

    Both gates are load bearing. A P19 rendered from "Bern (BE)" is not a
    statement at all — QuickStatements would need the item — so this may never
    become emittable by relaxing one rule somewhere else.
    """
    config = make_config()
    suggestions = person_data(run(make_member(), make_person(), config))
    assert suggestions
    for suggestion in suggestions:
        assert suggestion.qid_source == QID_FROM_IDENTIFIER  # the strong provenance
        assert not is_mechanical(suggestion, config.statement_model)
        assert "position" not in suggestion.payload
    assert render(suggestions, date(2026, 8, 4), config.statement_model) == []


# --- config -----------------------------------------------------------------
def test_the_checks_are_on_by_default(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "user_agent: 'wd-parliament/0.1 (https://example.ch/wd; me@somewhere.ch)'\n"
        "bodies:\n  - council: NR\n    label: NR\n    position: Q18510612\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.person_data == list(PERSON_DATA_PROPERTIES)
    assert [c.property_id for c in config.person_data_checks] == list(
        PERSON_DATA_PROPERTIES
    )


def test_an_unknown_property_is_an_error_not_a_skip(tmp_path):
    """Unlike a Q-ID map, where a missing value is "not established yet".

    A property id here selects a code path, so a typo would silently drop a
    check the operator asked for.
    """
    path = tmp_path / "c.yaml"
    path.write_text(
        "user_agent: 'wd-parliament/0.1 (https://example.ch/wd; me@somewhere.ch)'\n"
        "person_data: [P19, P999999]\n"
        "bodies:\n  - council: NR\n    label: NR\n    position: Q18510612\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="P999999"):
        load_config(path)


def test_each_shipped_config_asks_only_what_its_source_answers():
    """Run 19 (2026-08-04) measured both, and they answer opposite halves.

    parlament.ch has a birthplace, a Bürgerort and a number of children but no
    occupation and no website column; OpenParlData's ZH records have exactly
    the reverse. A property listed against a source that cannot answer it would
    cost nothing — it simply never fires — but it would also assert a check
    that does not exist, which is what this pins down.
    """
    federal = load_config("config/parliament.yaml")
    assert federal.person_data == [
        P_PLACE_OF_BIRTH,
        P_PLACE_OF_ORIGIN,
        P_POLITICAL_PARTY,
        P_NUMBER_OF_CHILDREN,
    ]
    assert P_OCCUPATION not in federal.person_data
    assert P_OFFICIAL_WEBSITE not in federal.person_data

    cantonal = load_config("config/kantonsrat-zh.yaml")
    assert cantonal.person_data == [
        P_POLITICAL_PARTY,
        P_OCCUPATION,
        P_OFFICIAL_WEBSITE,
    ]
    assert P_PLACE_OF_BIRTH not in cantonal.person_data
