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
    KIND_DUPLICATE_IDENTIFIER,
    KIND_DUPLICATE_SOURCE_LINK,
    KIND_FIX_START_DATE,
    KIND_NO_WIKIDATA_ITEM,
    KIND_REVIEW_ENDED,
    KIND_REVIEW_PARTY,
    KIND_SOURCES_DISAGREE,
    MODEL_PERIOD,
    MODEL_TENURE,
    QID_FROM_IDENTIFIER,
    QID_FROM_NAME,
    Body,
    Member,
    Period,
    PositionStatement,
    Tenure,
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


def test_statement_end_date_in_the_future_is_not_a_disagreement(periods):
    """A future P582 (e.g. end of legislature) is a plan, not a contradiction."""
    member = make_member()
    statement = make_statement(
        start=date(2019, 12, 2), end=date(2027, 12, 5), districts=["Q11943"]
    )
    suggestions = compute_suggestions(
        BODY,
        [member],
        {"Q7": person([statement])},
        periods,
        make_config(MODEL_TENURE),
        today=date(2026, 8, 2),
    )
    assert KIND_REVIEW_ENDED not in kinds(suggestions)


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
def _ghost(qid="Q99", label="Ghost Member", end=None):
    return WikidataPerson(
        qid=qid,
        label=label,
        statements=[
            PositionStatement(
                person_qid=qid, statement_id="S9", position_qid=POSITION,
                start=date(2015, 11, 30), end=end,
            )
        ],
    )


def _sitting_member_and_person():
    """A satisfied member, so the reverse walk has a member list to work from."""
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    return member, person([statement])


def test_wikidata_still_lists_a_departed_member(periods):
    """Somebody Wikidata thinks is sitting, whom parlament.ch does not list."""
    member, seated = _sitting_member_and_person()
    people = {"Q7": seated, "Q99": _ghost()}
    suggestions = compute_suggestions(
        BODY, [member], people, periods, make_config(MODEL_TENURE)
    )
    assert kinds(suggestions) == [KIND_ADD_END_DATE]
    assert suggestions[0].person_qid == "Q99"
    # No leaving date is known for someone outside the current-members set, so
    # this one must not become a QuickStatement.
    assert "end" not in suggestions[0].payload


def test_the_departed_member_is_linked_to_the_source_by_their_identifier(periods):
    """The only bridge back to the source: Wikidata's own P1307 value.

    There is no Member to read a number off — that is what being outside the
    current-members set means — so the link and the dates come from the
    identifier Wikidata itself asserts, or not at all.
    """
    member, seated = _sitting_member_and_person()
    ghost = _ghost()
    ghost.parliament_id = "3432"
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": seated, "Q99": ghost}, periods, make_config(MODEL_TENURE)
    )
    assert suggestions[0].person_number == 3432
    assert (
        suggestions[0].payload["biography"]
        == "https://www.parlament.ch/de/biografie/wd/3432"
    )


def test_the_link_template_comes_from_the_config(periods):
    """A cantonal run must not send a reader to parlament.ch."""
    member, seated = _sitting_member_and_person()
    ghost = _ghost()
    ghost.parliament_id = "9532"
    config = make_config(MODEL_TENURE)
    config.biography_url = "https://example.org/{language}/member/{person_number}"
    config.language = "fr"
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": seated, "Q99": ghost}, periods, config
    )
    assert suggestions[0].payload["biography"] == "https://example.org/fr/member/9532"


def test_an_item_without_the_identifier_gets_no_link_rather_than_a_wrong_one(periods):
    member, seated = _sitting_member_and_person()
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": seated, "Q99": _ghost()}, periods,
        make_config(MODEL_TENURE),
    )
    assert suggestions[0].person_number is None
    assert "biography" not in suggestions[0].payload


def test_a_non_numeric_identifier_is_not_a_person_number(periods):
    """A malformed value must not become somebody else's biography URL."""
    member, seated = _sitting_member_and_person()
    ghost = _ghost()
    ghost.parliament_id = "P1307"
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": seated, "Q99": ghost}, periods, make_config(MODEL_TENURE)
    )
    assert suggestions[0].person_number is None
    assert "biography" not in suggestions[0].payload


def _departed(tenures=None, start=date(2015, 11, 30), wikidata_start=date(2015, 11, 30)):
    """The reverse walk over one departed member, with source dates supplied."""
    member, seated = _sitting_member_and_person()
    ghost = _ghost()
    ghost.parliament_id = "3432"
    ghost.statements[0].start = wikidata_start
    return compute_suggestions(
        BODY,
        [member],
        {"Q7": seated, "Q99": ghost},
        [],
        make_config(MODEL_TENURE),
        tenures=tenures,
    )[0]


def test_the_source_supplies_the_start_and_end_date_to_add(periods):
    """The report's whole point here: name the dates, do not say 'by hand'."""
    tenures = {
        (3432, "N"): Tenure(
            person_number=3432, council="N",
            start=date(2011, 12, 5), end=date(2019, 12, 1),
        )
    }
    suggestion = _departed(tenures)
    assert suggestion.payload["start"] == date(2011, 12, 5)
    assert suggestion.payload["end"] == date(2019, 12, 1)
    assert "2011-12-05 to 2019-12-01" in suggestion.detail
    assert "looked up by hand" not in suggestion.detail


def test_a_start_date_that_disagrees_is_pointed_out_too(periods):
    tenures = {
        (3432, "N"): Tenure(
            person_number=3432, council="N",
            start=date(2011, 12, 5), end=date(2019, 12, 1),
        )
    }
    suggestion = _departed(tenures, wikidata_start=date(2015, 11, 30))
    assert "start date (P580) is 2015-11-30" in suggestion.detail


def test_a_missing_start_date_is_offered_from_the_source(periods):
    tenures = {
        (3432, "N"): Tenure(
            person_number=3432, council="N",
            start=date(2011, 12, 5), end=date(2019, 12, 1),
        )
    }
    suggestion = _departed(tenures, wikidata_start=None)
    assert "no start date (P580) either" in suggestion.detail


def test_an_open_tenure_gives_no_end_date_rather_than_a_guess(periods):
    """The source knows them but has not closed the spell: still by hand."""
    tenures = {
        (3432, "N"): Tenure(person_number=3432, council="N", start=date(2011, 12, 5))
    }
    suggestion = _departed(tenures)
    assert "end" not in suggestion.payload
    assert suggestion.payload["start"] == date(2011, 12, 5)
    assert "no leaving date" in suggestion.detail


def test_another_chambers_tenure_is_never_reported_for_this_seat(periods):
    """A person is not a seat: an NR spell says nothing about an SR statement."""
    tenures = {
        (3432, "S"): Tenure(
            person_number=3432, council="S",
            start=date(2011, 12, 5), end=date(2019, 12, 1),
        )
    }
    suggestion = _departed(tenures)
    assert "end" not in suggestion.payload
    assert "looked up by hand" in suggestion.detail


def test_the_departed_member_is_never_mechanical(periods):
    """Report-only, and gated twice: no qid_source, and no position in payload.

    The identifier is Wikidata's rather than a resolved member's, and the dates
    come from a historic table no probe has measured for departed members. A
    P582 backfill across everyone Wikidata lists as sitting would need both.
    """
    from wd_parliament.quickstatements import is_mechanical

    tenures = {
        (3432, "N"): Tenure(
            person_number=3432, council="N",
            start=date(2011, 12, 5), end=date(2019, 12, 1),
        )
    }
    suggestion = _departed(tenures)
    assert suggestion.qid_source is None
    assert "position" not in suggestion.payload
    assert is_mechanical(suggestion, MODEL_TENURE) is False


def test_a_departed_member_with_two_statements_is_marked_ambiguous(periods):
    """Left and returned: property + main value names neither statement.

    Run 16 found 3 such people among 1,969. The stamp is not redundant with the
    report-only gates: those say the whole class is unmeasured, this says the
    person is unaddressable however the class is settled.
    """
    from wd_parliament.quickstatements import is_mechanical

    member, seated = _sitting_member_and_person()
    ghost = _ghost()
    ghost.parliament_id = "3432"
    ghost.statements.append(
        PositionStatement(
            person_qid="Q99", statement_id="S8", position_qid=POSITION,
            start=date(2003, 12, 1), end=date(2007, 12, 2),
        )
    )
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": seated, "Q99": ghost}, periods,
        make_config(MODEL_TENURE),
        tenures={
            (3432, "N"): Tenure(
                person_number=3432, council="N",
                start=date(2015, 11, 30), end=date(2019, 12, 1),
            )
        },
    )
    departed = next(s for s in suggestions if s.person_qid == "Q99")
    assert departed.payload["ambiguous_statement"] is True
    assert is_mechanical(departed, MODEL_TENURE) is False


def test_a_single_statement_is_not_marked_ambiguous(periods):
    member, seated = _sitting_member_and_person()
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": seated, "Q99": _ghost()}, periods,
        make_config(MODEL_TENURE),
    )
    assert "ambiguous_statement" not in suggestions[0].payload


def test_a_closed_statement_is_not_flagged_in_the_reverse_walk(periods):
    member, seated = _sitting_member_and_person()
    people = {"Q7": seated, "Q99": _ghost(end=date(2019, 12, 1))}
    assert compute_suggestions(
        BODY, [member], people, periods, make_config(MODEL_TENURE)
    ) == []


def test_an_empty_member_list_suppresses_the_reverse_walk(periods):
    """The 2026-07-29 failure: a broken source read flagged 2,234 people.

    'parlament.ch does not list this person' is only a claim we can make once
    parlament.ch has told us who it does list.
    """
    people = {f"Q{n}": _ghost(qid=f"Q{n}") for n in range(90, 99)}
    assert compute_suggestions(
        BODY, [], people, periods, make_config(MODEL_TENURE)
    ) == []


def test_a_sitting_member_is_not_flagged_in_the_reverse_walk(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    )
    assert KIND_ADD_END_DATE not in kinds(suggestions)


def test_statements_for_another_position_are_ignored(periods):
    """An open Council of States seat must not surface in the National Council."""
    member, seated = _sitting_member_and_person()
    other = WikidataPerson(
        qid="Q99",
        statements=[
            PositionStatement(
                person_qid="Q99", statement_id="S9", position_qid="Q18510613",
                start=date(2015, 11, 30),
            )
        ],
    )
    assert compute_suggestions(
        BODY, [member], {"Q7": seated, "Q99": other}, periods, make_config(MODEL_TENURE)
    ) == []


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


def test_several_same_seat_statements_are_flagged_as_ambiguous(periods):
    """A member who left and returned, under the tenure model."""
    member = make_member(date_joining=date(2019, 12, 2))
    first = make_statement(start=date(2015, 11, 30), end=date(2019, 12, 1), sid="A")
    second = make_statement(start=date(2019, 12, 2), sid="B")
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([first, second])}, periods, make_config(MODEL_TENURE)
    )
    assert suggestions
    assert all(s.payload.get("ambiguous_statement") for s in suggestions)


def test_a_single_statement_is_not_flagged_as_ambiguous(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2))
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods, make_config(MODEL_TENURE)
    )
    assert suggestions
    assert not any(s.payload.get("ambiguous_statement") for s in suggestions)


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


# --- one identifier, several items ------------------------------------------
# A conflict rather than a gap, and the one finding this tool makes that is an
# outright contradiction in Wikidata's own data. Before it was reported, the
# join skipped the member silently and they drew "no item was found, they may
# need a new item" — advice that would have created a third duplicate.
def _twins(identifier="1101", qids=("Q11", "Q12"), start=date(2019, 12, 2)):
    return {
        qid: WikidataPerson(
            qid=qid,
            label="Anna Muster",
            parliament_id=identifier,
            statements=[
                PositionStatement(
                    person_qid=qid, statement_id=f"S{qid}",
                    position_qid=POSITION, start=start,
                )
            ],
        )
        for qid in qids
    }


def _resolved(members, people):
    from wd_parliament.resolve import match_by_identifier

    match_by_identifier(members, people.values())
    return members


def test_a_duplicated_identifier_is_reported_rather_than_swallowed(periods):
    member = make_member(qid=None, qid_source=None)
    people = _twins()
    _resolved([member], people)
    suggestions = compute_suggestions(
        BODY, [member], people, periods, make_config(MODEL_TENURE)
    )
    conflict = next(s for s in suggestions if s.kind == KIND_DUPLICATE_IDENTIFIER)
    assert conflict.payload["duplicate_qids"] == ["Q11", "Q12"]
    assert "claimed by 2 Wikidata items" in conflict.detail
    assert conflict.priority == 1  # ahead of everything else about this member


def test_a_conflicted_member_is_not_told_to_create_a_new_item(periods):
    """The advice that would have made a third duplicate."""
    member = make_member(qid=None, qid_source=None)
    people = _twins()
    _resolved([member], people)
    kinds_seen = kinds(
        compute_suggestions(BODY, [member], people, periods, make_config(MODEL_TENURE))
    )
    assert KIND_DUPLICATE_IDENTIFIER in kinds_seen
    assert KIND_NO_WIKIDATA_ITEM not in kinds_seen


def test_neither_claimant_is_reported_as_having_left(periods):
    """Both items are open, and the member is sitting. The reverse walk keys on
    the Q-ID, which the skipped join never set — so without the identifier
    guard each claimant draws a confident, wrong 'they have left'."""
    member = make_member(qid=None, qid_source=None)
    people = _twins()
    _resolved([member], people)
    suggestions = compute_suggestions(
        BODY, [member], people, periods, make_config(MODEL_TENURE)
    )
    assert KIND_ADD_END_DATE not in kinds(suggestions)


def test_a_collision_between_two_departed_items_is_still_raised(periods):
    """No sitting member carries the number, so pass 1 cannot see it at all."""
    member = make_member()  # a different, cleanly matched member
    people = {"Q7": person([make_statement(start=date(2019, 12, 2),
                                           districts=["Q11943"])])}
    people.update(_twins(identifier="999", qids=("Q90", "Q91")))
    suggestions = compute_suggestions(
        BODY, [member], people, periods, make_config(MODEL_TENURE)
    )
    conflict = next(s for s in suggestions if s.kind == KIND_DUPLICATE_IDENTIFIER)
    assert conflict.payload["duplicate_qids"] == ["Q90", "Q91"]
    assert conflict.links["Q90"] == "https://www.wikidata.org/wiki/Q90"


def test_a_collision_is_raised_once_not_once_per_item(periods):
    member = make_member()
    people = {"Q7": person([make_statement(start=date(2019, 12, 2),
                                           districts=["Q11943"])])}
    people.update(_twins(identifier="999", qids=("Q90", "Q91", "Q92")))
    suggestions = compute_suggestions(
        BODY, [member], people, periods, make_config(MODEL_TENURE)
    )
    conflicts = [s for s in suggestions if s.kind == KIND_DUPLICATE_IDENTIFIER]
    assert len(conflicts) == 1
    assert conflicts[0].payload["duplicate_qids"] == ["Q90", "Q91", "Q92"]


def test_a_sitting_members_conflict_is_not_reported_twice(periods):
    """Pass 1 raises it for the member; pass 2 must not raise it again."""
    member = make_member(qid=None, qid_source=None)
    people = _twins()
    _resolved([member], people)
    suggestions = compute_suggestions(
        BODY, [member], people, periods, make_config(MODEL_TENURE)
    )
    assert len([s for s in suggestions if s.kind == KIND_DUPLICATE_IDENTIFIER]) == 1


def test_items_claiming_one_id_for_another_seat_are_not_this_chambers_problem(periods):
    member = make_member()
    people = {"Q7": person([make_statement(start=date(2019, 12, 2),
                                           districts=["Q11943"])])}
    for qid in ("Q90", "Q91"):
        people[qid] = WikidataPerson(
            qid=qid, label="Elsewhere", parliament_id="999",
            statements=[PositionStatement(
                person_qid=qid, statement_id=f"S{qid}",
                position_qid="Q18510613", start=date(1950, 1, 1),
            )],
        )
    suggestions = compute_suggestions(
        BODY, [member], people, periods, make_config(MODEL_TENURE)
    )
    assert KIND_DUPLICATE_IDENTIFIER not in kinds(suggestions)


def test_a_conflict_is_never_mechanical(periods):
    """Every repair is destructive in a way QuickStatements cannot express."""
    from wd_parliament.quickstatements import is_mechanical

    member = make_member(qid=None, qid_source=None)
    people = _twins()
    _resolved([member], people)
    conflict = next(
        s for s in compute_suggestions(
            BODY, [member], people, periods, make_config(MODEL_TENURE)
        )
        if s.kind == KIND_DUPLICATE_IDENTIFIER
    )
    assert is_mechanical(conflict, MODEL_TENURE) is False


def test_a_name_match_onto_one_claimant_still_raises_the_conflict(periods):
    """The fallback can land on one of them; that is a guess, not a resolution."""
    member = make_member(qid="Q11", qid_source=QID_FROM_NAME)
    member.duplicate_identifier_qids = ["Q11", "Q12"]
    suggestions = compute_suggestions(
        BODY, [member], _twins(), periods, make_config(MODEL_TENURE)
    )
    conflict = next(s for s in suggestions if s.kind == KIND_DUPLICATE_IDENTIFIER)
    assert "uses Q11" in conflict.detail
    assert "not an answer" in conflict.detail


# --- one Wikidata item, several source records -------------------------------
# The mirror image of DUPLICATE_IDENTIFIER, and the only finding in this report
# that is not repaired on Wikidata. Raised anyway: the report is the only place
# anybody looks, and such a link silently corrupts any join made through it.
def test_a_duplicated_source_link_is_reported(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods,
        make_config(MODEL_TENURE),
        link_conflicts={("N", "Q117716"): [9532, 99999]},
    )
    conflict = next(s for s in suggestions if s.kind == KIND_DUPLICATE_SOURCE_LINK)
    assert conflict.payload["wikidata_qid"] == "Q117716"
    assert conflict.payload["source_person_ids"] == [9532, 99999]
    assert "#9532, #99999" in conflict.detail
    assert conflict.links["item"] == "https://www.wikidata.org/wiki/Q117716"


def test_the_source_link_conflict_says_where_it_is_fixed(periods):
    """Nothing here is a Wikidata edit, and the report must not imply one."""
    suggestions = compute_suggestions(
        BODY, [make_member()], {"Q7": person()}, periods, make_config(MODEL_TENURE),
        link_conflicts={("N", "Q1"): [1, 2]},
    )
    conflict = next(s for s in suggestions if s.kind == KIND_DUPLICATE_SOURCE_LINK)
    assert "not on Wikidata" in conflict.detail


def test_a_source_link_conflict_is_never_mechanical(periods):
    from wd_parliament.quickstatements import is_mechanical

    suggestions = compute_suggestions(
        BODY, [make_member()], {"Q7": person()}, periods, make_config(MODEL_TENURE),
        link_conflicts={("N", "Q1"): [1, 2]},
    )
    conflict = next(s for s in suggestions if s.kind == KIND_DUPLICATE_SOURCE_LINK)
    assert is_mechanical(conflict, MODEL_TENURE) is False


def test_another_chambers_link_conflict_is_not_reported_here(periods):
    suggestions = compute_suggestions(
        BODY, [make_member()], {"Q7": person()}, periods, make_config(MODEL_TENURE),
        link_conflicts={("S", "Q1"): [1, 2]},
    )
    assert KIND_DUPLICATE_SOURCE_LINK not in kinds(suggestions)


def test_a_source_that_asserts_no_wikidata_link_raises_nothing(periods):
    """parlament.ch says nothing about Wikidata; that is not a degradation."""
    suggestions = compute_suggestions(
        BODY, [make_member()], {"Q7": person()}, periods, make_config(MODEL_TENURE)
    )
    assert KIND_DUPLICATE_SOURCE_LINK not in kinds(suggestions)


# --- the two sources disagreeing ---------------------------------------------
# The check the tool could not make while it read one source. It does not say
# which side is right; it says the value Wikidata would be given is disputed,
# which is exactly the condition for not writing it unreviewed.
def _span(start="2019-12-02", end=None):
    from wd_parliament.models import SourceSpan

    return SourceSpan(
        council="N",
        start=date.fromisoformat(start) if start else None,
        end=date.fromisoformat(end) if end else None,
        rows=1,
        person_ids=[42],
    )


def test_a_disagreement_is_reported(periods):
    member = make_member()  # parlament.ch: joined 2019-12-02
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods,
        make_config(MODEL_TENURE),
        enrichment={("Q7", "N"): _span(start="2015-11-30")},
    )
    disagreement = next(s for s in suggestions if s.kind == KIND_SOURCES_DISAGREE)
    assert disagreement.payload["start"] == date(2019, 12, 2)
    assert disagreement.payload["other_start"] == date(2015, 11, 30)
    assert disagreement.payload["other_person_ids"] == [42]
    assert "2019-12-02" in disagreement.detail and "2015-11-30" in disagreement.detail


def test_agreeing_sources_add_nothing(periods):
    member = make_member()
    statement = make_statement(start=date(2019, 12, 2), districts=["Q11943"])
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person([statement])}, periods,
        make_config(MODEL_TENURE),
        enrichment={("Q7", "N"): _span()},
    )
    assert suggestions == []


def test_a_disagreement_withholds_the_mechanical_edit(periods):
    """The point of the check. A disputed P580 must not be written unreviewed."""
    from wd_parliament.quickstatements import is_mechanical, render

    member = make_member()
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person()}, periods, make_config(MODEL_TENURE),
        enrichment={("Q7", "N"): _span(start="2015-11-30")},
    )
    add = next(s for s in suggestions if s.kind == KIND_ADD_MEMBERSHIP)
    assert add.payload["sources_disagree"] is True
    assert is_mechanical(add, MODEL_TENURE) is False
    assert render(suggestions, date(2026, 8, 4), MODEL_TENURE) == []


def test_without_a_disagreement_the_edit_is_still_emitted(periods):
    """The suppression must be caused by the disagreement, not by the lookup."""
    from wd_parliament.quickstatements import is_mechanical

    member = make_member()
    suggestions = compute_suggestions(
        BODY, [member], {"Q7": person()}, periods, make_config(MODEL_TENURE),
        enrichment={("Q7", "N"): _span()},
    )
    add = next(s for s in suggestions if s.kind == KIND_ADD_MEMBERSHIP)
    assert "sources_disagree" not in add.payload
    assert is_mechanical(add, MODEL_TENURE) is True


def test_the_disagreement_itself_is_never_mechanical(periods):
    from wd_parliament.quickstatements import is_mechanical

    suggestions = compute_suggestions(
        BODY, [make_member()], {"Q7": person()}, periods, make_config(MODEL_TENURE),
        enrichment={("Q7", "N"): _span(start="2015-11-30")},
    )
    disagreement = next(s for s in suggestions if s.kind == KIND_SOURCES_DISAGREE)
    assert is_mechanical(disagreement, MODEL_TENURE) is False


def test_another_chambers_span_is_not_compared(periods):
    """A person is not a seat: an SR span says nothing about an NR tenure."""
    suggestions = compute_suggestions(
        BODY, [make_member()], {"Q7": person()}, periods, make_config(MODEL_TENURE),
        enrichment={("Q7", "S"): _span(start="1990-01-01")},
    )
    assert KIND_SOURCES_DISAGREE not in kinds(suggestions)


def test_no_second_source_leaves_the_run_exactly_as_it_was(periods):
    from wd_parliament.quickstatements import is_mechanical

    suggestions = compute_suggestions(
        BODY, [make_member()], {"Q7": person()}, periods, make_config(MODEL_TENURE)
    )
    assert KIND_SOURCES_DISAGREE not in kinds(suggestions)
    add = next(s for s in suggestions if s.kind == KIND_ADD_MEMBERSHIP)
    assert is_mechanical(add, MODEL_TENURE) is True
