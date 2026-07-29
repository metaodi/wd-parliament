"""Tests for the comparison logic, in both statement models."""

from datetime import date

import pytest

from wd_parliament.config import Config
from wd_parliament.diff import compute_suggestions, expected_statements, match_statement
from wd_parliament.models import (
    KIND_ADD_END_DATE,
    KIND_ADD_IDENTIFIER,
    KIND_ADD_MEMBERSHIP,
    KIND_ADD_QUALIFIER,
    KIND_ADD_START_DATE,
    KIND_ADD_TERM,
    KIND_FIX_START_DATE,
    KIND_NO_WIKIDATA_ITEM,
    KIND_REVIEW_ENDED,
    KIND_REVIEW_PARTY,
    MODEL_PERIOD,
    MODEL_TENURE,
    QID_FROM_IDENTIFIER,
    QID_FROM_NAME,
    Body,
    Member,
    Period,
    PositionStatement,
    WikidataPerson,
)

POSITION = "Q18510612"
BODY = Body(council="N", label="Swiss National Council", position_qid=POSITION)


@pytest.fixture
def periods():
    return [
        Period(number=50, name="50.", start=date(2015, 11, 30), end=date(2019, 12, 1)),
        Period(number=51, name="51.", start=date(2019, 12, 2), end=date(2023, 12, 3)),
        Period(number=52, name="52.", start=date(2023, 12, 4), end=None),
    ]


def make_config(model=MODEL_PERIOD, terms=None, cantons=None, parties=None, groups=None):
    return Config(
        statement_model=model,
        bodies=[BODY],
        cantons=cantons if cantons is not None else {"ZH": "Q11943"},
        parties=parties or {},
        parl_groups=groups or {},
        terms=terms or {},
    )


def make_member(**kwargs):
    defaults = dict(
        person_number=1101,
        first_name="Anna",
        last_name="Muster",
        active=True,
        council="N",
        canton_abbreviation="ZH",
        parl_group_abbreviation="V",
        party_abbreviation="SVP",
        date_joining=date(2019, 12, 2),
        qid="Q7",
        qid_source=QID_FROM_IDENTIFIER,
    )
    defaults.update(kwargs)
    return Member(**defaults)


def make_statement(start=None, end=None, terms=(), districts=(), groups=(), sid="S1"):
    return PositionStatement(
        person_qid="Q7",
        statement_id=sid,
        position_qid=POSITION,
        person_label="Anna Muster",
        start=start,
        end=end,
        terms=list(terms),
        districts=list(districts),
        groups=list(groups),
    )


def person(statements=(), parliament_id="1101", parties=()):
    return WikidataPerson(
        qid="Q7",
        label="Anna Muster",
        parliament_id=parliament_id,
        statements=list(statements),
        parties=list(parties),
    )


def kinds(suggestions):
    return [s.kind for s in suggestions]


# --- expected_statements ----------------------------------------------------
def test_tenure_model_expects_one_statement_with_every_term(periods):
    member = make_member(date_joining=date(2015, 11, 30))
    expected = expected_statements(member, periods, MODEL_TENURE)
    assert len(expected) == 1
    assert expected[0].start == date(2015, 11, 30)
    assert expected[0].end is None
    assert [p.number for p in expected[0].periods] == [50, 51, 52]


def test_period_model_expects_one_statement_per_period(periods):
    member = make_member(date_joining=date(2015, 11, 30))
    expected = expected_statements(member, periods, MODEL_PERIOD)
    assert [e.period.number for e in expected] == [50, 51, 52]
    assert expected[0].start == date(2015, 11, 30)
    assert expected[0].end == date(2019, 12, 1)  # clipped to the period
    assert expected[1].start == date(2019, 12, 2)
    assert expected[2].end is None  # the running period, still sitting


def test_a_member_with_no_joining_date_expects_nothing_in_period_model(periods):
    member = make_member(date_joining=None)
    assert expected_statements(member, periods, MODEL_PERIOD) == []


# --- the individual checks --------------------------------------------------
def test_sitting_member_with_no_statement(periods):
    member = make_member()
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person()}, periods, make_config(MODEL_TENURE)
    )
    assert KIND_ADD_MEMBERSHIP in kinds(suggestions)


def test_a_correct_statement_produces_nothing(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    )
    assert suggestions == []


def test_open_statement_without_a_start_date(periods):
    member = make_member()
    statement = make_statement(districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    )
    assert kinds(suggestions) == [KIND_ADD_START_DATE]
    assert suggestions[0].payload["start"] == date(2019, 12, 2)


def test_start_date_disagreeing_with_the_source(periods):
    """The check wd-squads cannot make: not absent, but *wrong*."""
    member = make_member()
    statement = make_statement(start=date(2019, 12, 9), districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    )
    assert kinds(suggestions) == [KIND_FIX_START_DATE]
    assert suggestions[0].payload["wikidata_start"] == date(2019, 12, 9)
    assert suggestions[0].payload["start"] == date(2019, 12, 2)


def test_statement_closed_but_member_still_sitting(periods):
    member = make_member()
    statement = make_statement(
        start=date(2019, 12, 2), end=date(2023, 12, 3), districts=["Q11943"]
    )
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    )
    assert KIND_REVIEW_ENDED in kinds(suggestions)


def test_open_statement_but_member_has_left(periods):
    member = make_member(active=False, date_leaving=date(2023, 12, 3))
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    )
    assert KIND_ADD_END_DATE in kinds(suggestions)
    end = next(s for s in suggestions if s.kind == KIND_ADD_END_DATE)
    assert end.payload["end"] == date(2023, 12, 3)


def test_missing_electoral_district(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2))
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    )
    assert kinds(suggestions) == [KIND_ADD_QUALIFIER]
    assert suggestions[0].payload["district"] == "Q11943"


def test_an_unmapped_canton_produces_no_qualifier_suggestion(periods):
    """Skip unknown values rather than guessing at them."""
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2))
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE, cantons={})
    )
    assert suggestions == []


def test_missing_parliamentary_group(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    config = make_config(MODEL_TENURE, groups={"V": "Q123"})
    suggestions = compute_suggestions(BODY, [member], {"Q7": person([statement])}, periods, config)
    assert kinds(suggestions) == [KIND_ADD_QUALIFIER]
    assert suggestions[0].payload["group"] == "Q123"


def test_missing_parliamentary_term(periods):
    member = make_member(date_joining=date(2019, 12, 2))
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    config = make_config(MODEL_TENURE, terms={51: "Q51", 52: "Q52"})
    suggestions = compute_suggestions(BODY, [member], {"Q7": person([statement])}, periods, config)
    assert kinds(suggestions) == [KIND_ADD_TERM]
    assert suggestions[0].payload["terms"] == ["Q51", "Q52"]


def test_terms_already_present_produce_nothing(periods):
    member = make_member(date_joining=date(2019, 12, 2))
    statement = make_statement(
        start=date(2019, 12, 2), districts=["Q11943"], terms=["Q51", "Q52"]
    )
    config = make_config(MODEL_TENURE, terms={51: "Q51", 52: "Q52"})
    assert compute_suggestions(BODY, [member], {"Q7": person([statement])}, periods, config) == []


def test_unmapped_terms_produce_no_term_suggestion(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    assert compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    ) == []


def test_no_wikidata_item(periods):
    member = make_member(qid=None, qid_source=None)
    suggestions = compute_suggestions(BODY, [member], {}, periods, make_config(MODEL_TENURE))
    assert kinds(suggestions) == [KIND_NO_WIKIDATA_ITEM]
    assert suggestions[0].person_qid is None


def test_name_matched_item_without_the_identifier(periods):
    member = make_member(qid_source=QID_FROM_NAME)
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY,
        [member],
        {"Q7": person([statement], parliament_id=None)},
        periods,
        make_config(MODEL_TENURE),
    )
    assert kinds(suggestions) == [KIND_ADD_IDENTIFIER]
    assert suggestions[0].payload["parliament_id"] == "1101"


def test_identifier_matched_item_never_gets_add_identifier(periods):
    member = make_member(qid_source=QID_FROM_IDENTIFIER)
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    )
    assert KIND_ADD_IDENTIFIER not in kinds(suggestions)


def test_name_matched_suggestions_carry_a_verify_note(periods):
    member = make_member(qid_source=QID_FROM_NAME)
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person(parliament_id=None)}, periods, make_config(MODEL_TENURE)
    )
    membership = next(s for s in suggestions if s.kind == KIND_ADD_MEMBERSHIP)
    assert "matched by name" in membership.detail


# --- party ------------------------------------------------------------------
def test_missing_party(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    config = make_config(MODEL_TENURE, parties={"SVP": "Q35591"})
    suggestions = compute_suggestions(BODY, [member], {"Q7": person([statement])}, periods, config)
    assert kinds(suggestions) == [KIND_REVIEW_PARTY]
    assert suggestions[0].payload["party"] == "Q35591"


def test_matching_party_produces_nothing(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    config = make_config(MODEL_TENURE, parties={"SVP": "Q35591"})
    people = {"Q7": person([statement], parties=["Q35591"])}
    assert compute_suggestions(BODY, [member], people, periods, config) == []


def test_an_unmapped_party_is_skipped(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    assert compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    ) == []


# --- the reverse walk -------------------------------------------------------
def test_wikidata_still_lists_a_departed_member(periods):
    """Somebody Wikidata thinks is sitting, whom parlament.ch does not list."""
    ghost = WikidataPerson(
        qid="Q99",
        label="Ghost Member",
        statements=[
            PositionStatement(
                person_qid="Q99", statement_id="S9", position_qid=POSITION,
                start=date(2015, 11, 30),
            )
        ],
    )
    suggestions = compute_suggestions(BODY, [], {"Q99": ghost}, periods, make_config(MODEL_TENURE))
    assert kinds(suggestions) == [KIND_ADD_END_DATE]
    assert suggestions[0].person_qid == "Q99"
    # No leaving date is known for someone outside the current-members set, so
    # this one must not become a QuickStatement.
    assert "end" not in suggestions[0].payload


def test_a_closed_statement_is_not_flagged_in_the_reverse_walk(periods):
    departed = WikidataPerson(
        qid="Q99",
        statements=[
            PositionStatement(
                person_qid="Q99", statement_id="S9", position_qid=POSITION,
                start=date(2015, 11, 30), end=date(2019, 12, 1),
            )
        ],
    )
    assert compute_suggestions(BODY, [], {"Q99": departed}, periods, make_config(MODEL_TENURE)) == []


def test_a_sitting_member_is_not_flagged_in_the_reverse_walk(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    )
    assert KIND_ADD_END_DATE not in kinds(suggestions)


def test_statements_for_another_position_are_ignored(periods):
    other = WikidataPerson(
        qid="Q99",
        statements=[
            PositionStatement(
                person_qid="Q99", statement_id="S9", position_qid="Q18510613",
                start=date(2015, 11, 30),
            )
        ],
    )
    assert compute_suggestions(BODY, [], {"Q99": other}, periods, make_config(MODEL_TENURE)) == []


# --- the period statement model ---------------------------------------------
def test_period_model_asks_for_one_statement_per_period(periods):
    member = make_member(date_joining=date(2015, 11, 30))
    config = make_config(MODEL_PERIOD, terms={50: "Q50", 51: "Q51", 52: "Q52"})
    suggestions = compute_suggestions(BODY, [member], {"Q7": person()}, periods, config)
    memberships = [s for s in suggestions if s.kind == KIND_ADD_MEMBERSHIP]
    assert len(memberships) == 3
    assert [m.payload["terms"] for m in memberships] == [["Q50"], ["Q51"], ["Q52"]]
    assert memberships[0].payload["end"] == date(2019, 12, 1)


def test_period_model_matches_an_existing_statement_by_its_term(periods):
    member = make_member(date_joining=date(2019, 12, 2))
    config = make_config(MODEL_PERIOD, terms={51: "Q51", 52: "Q52"})
    existing = make_statement(
        start=date(2019, 12, 2), end=date(2023, 12, 3),
        terms=["Q51"], districts=["Q11943"], sid="S51",
    )
    suggestions = compute_suggestions(BODY, [member], {"Q7": person([existing])}, periods, config)
    # The 51st is satisfied; only the 52nd is still missing.
    memberships = [s for s in suggestions if s.kind == KIND_ADD_MEMBERSHIP]
    assert len(memberships) == 1
    assert memberships[0].payload["terms"] == ["Q52"]


def test_period_model_falls_back_to_date_overlap_without_terms(periods):
    member = make_member(date_joining=date(2019, 12, 2))
    config = make_config(MODEL_PERIOD)
    existing = make_statement(
        start=date(2019, 12, 2), end=date(2023, 12, 3), districts=["Q11943"], sid="S51"
    )
    suggestions = compute_suggestions(BODY, [member], {"Q7": person([existing])}, periods, config)
    memberships = [s for s in suggestions if s.kind == KIND_ADD_MEMBERSHIP]
    assert len(memberships) == 1  # the 52nd


def test_one_statement_is_never_matched_to_two_periods(periods):
    member = make_member(date_joining=date(2019, 12, 2))
    config = make_config(MODEL_PERIOD, terms={51: "Q51", 52: "Q52"})
    existing = make_statement(
        start=date(2019, 12, 2), terms=["Q51", "Q52"], districts=["Q11943"], sid="S1"
    )
    suggestions = compute_suggestions(BODY, [member], {"Q7": person([existing])}, periods, config)
    memberships = [s for s in suggestions if s.kind == KIND_ADD_MEMBERSHIP]
    assert len(memberships) == 1


# --- match_statement --------------------------------------------------------
def test_match_statement_prefers_the_open_one_in_tenure_model(periods):
    from wd_parliament.diff import ExpectedStatement

    closed = make_statement(start=date(2015, 11, 30), end=date(2019, 12, 1), sid="A")
    open_ = make_statement(start=date(2019, 12, 2), sid="B")
    expected = ExpectedStatement(start=date(2015, 11, 30), period=None, periods=periods)
    assert match_statement(expected, [closed, open_], make_config(MODEL_TENURE)).statement_id == "B"


def test_match_statement_returns_none_when_nothing_fits(periods):
    from wd_parliament.diff import ExpectedStatement

    expected = ExpectedStatement(
        start=date(2023, 12, 4), period=periods[2], periods=[periods[2]]
    )
    far_off = make_statement(start=date(1995, 1, 1), end=date(1999, 1, 1), sid="A")
    assert match_statement(expected, [far_off], make_config(MODEL_PERIOD)) is None


# --- ordering ---------------------------------------------------------------
def test_suggestions_are_sorted_by_priority_then_name(periods):
    a = make_member(person_number=1, first_name="Zoe", last_name="Aebi", qid="Q1",
                    qid_source=QID_FROM_NAME)
    b = make_member(person_number=2, first_name="Alex", last_name="Bosshard", qid="Q2")
    people = {
        "Q1": WikidataPerson(qid="Q1", parliament_id=None),
        "Q2": WikidataPerson(qid="Q2", parliament_id="2"),
    }
    suggestions = compute_suggestions(BODY, [a, b], people, periods, make_config(MODEL_TENURE))
    keys = [(s.priority, s.member_label.casefold()) for s in suggestions]
    assert keys == sorted(keys)


def test_suggestions_carry_grouping_keys(periods):
    member = make_member()
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person()}, periods, make_config(MODEL_TENURE)
    )
    assert suggestions[0].canton == "ZH"
    assert suggestions[0].parl_group == "V"
