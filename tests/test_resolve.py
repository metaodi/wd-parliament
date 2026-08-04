"""Tests for the P1307 join and the birth-date-corroborated name fallback."""

from datetime import date

from wd_parliament.models import (
    QID_FROM_IDENTIFIER,
    QID_FROM_NAME,
    Member,
    PersonMatch,
    WikidataPerson,
)
from wd_parliament.resolve import (
    apply_person_matches,
    index_by_identifier,
    match_by_identifier,
    match_members,
    select_person_match,
    unresolved_names,
)


def make_member(number, first="Anna", last="Muster", birth="1970-01-01"):
    return Member(
        person_number=number,
        first_name=first,
        last_name=last,
        date_of_birth=date.fromisoformat(birth) if birth else None,
    )


def make_person(qid, parliament_id=None, label="Anna Muster", birth=None):
    return WikidataPerson(
        qid=qid,
        label=label,
        parliament_id=parliament_id,
        birth_date=date.fromisoformat(birth) if birth else None,
    )


# --- the identifier join ----------------------------------------------------
def test_identifier_join_is_exact():
    members = [make_member(1108)]
    people = [make_person("Q121160", "1108")]
    matched = match_by_identifier(members, people)
    assert members[0].qid == "Q121160"
    assert members[0].qid_source == QID_FROM_IDENTIFIER
    assert matched[1108].qid == "Q121160"


def test_identifier_join_tolerates_leading_zeros_and_whitespace():
    members = [make_member(1108)]
    people = [make_person("Q121160", " 01108 ")]
    match_by_identifier(members, people)
    assert members[0].qid == "Q121160"


def test_identifier_join_ignores_unrelated_ids():
    members = [make_member(1108)]
    match_by_identifier(members, [make_person("Q1", "999")])
    assert members[0].qid is None


def test_a_contested_identifier_is_skipped_not_guessed():
    """Two items claiming one P1307 is a data problem, not a coin toss."""
    members = [make_member(1108)]
    people = [make_person("Q1", "1108"), make_person("Q2", "1108")]
    matched = match_by_identifier(members, people)
    assert members[0].qid is None
    assert matched == {}


def test_index_by_identifier_ignores_items_without_one():
    index = index_by_identifier([make_person("Q1"), make_person("Q2", "5")])
    assert list(index) == ["5"]


# --- name selection ---------------------------------------------------------
def test_birth_date_picks_between_namesakes():
    member = make_member(1, birth="1970-01-01")
    candidates = [
        PersonMatch(name="Anna Muster", qid="Q1", birth_date=date(1985, 5, 5)),
        PersonMatch(name="Anna Muster", qid="Q2", birth_date=date(1970, 1, 1)),
    ]
    assert select_person_match(candidates, member).qid == "Q2"


def test_a_contradicting_birth_date_rejects_the_candidate():
    member = make_member(1, birth="1970-01-01")
    candidates = [PersonMatch(name="Anna Muster", qid="Q1", birth_date=date(1985, 5, 5))]
    assert select_person_match(candidates, member) is None


def test_two_candidates_born_the_same_day_are_ambiguous():
    member = make_member(1, birth="1970-01-01")
    candidates = [
        PersonMatch(name="Anna Muster", qid="Q1", birth_date=date(1970, 1, 1)),
        PersonMatch(name="Anna Muster", qid="Q2", birth_date=date(1970, 1, 1)),
    ]
    assert select_person_match(candidates, member) is None


def test_single_undated_candidate_is_accepted():
    member = make_member(1, birth="1970-01-01")
    candidates = [PersonMatch(name="Anna Muster", qid="Q1")]
    assert select_person_match(candidates, member).qid == "Q1"


def test_two_undated_namesakes_are_refused():
    """wd-squads' rule survives where the birth date cannot settle it."""
    member = make_member(1, birth="1970-01-01")
    candidates = [
        PersonMatch(name="Anna Muster", qid="Q1"),
        PersonMatch(name="Anna Muster", qid="Q2"),
    ]
    assert select_person_match(candidates, member) is None


def test_holding_the_seat_breaks_a_tie_between_undated_candidates():
    member = make_member(1, birth=None)
    candidates = [
        PersonMatch(name="Anna Muster", qid="Q1"),
        PersonMatch(name="Anna Muster", qid="Q2", has_position=True),
    ]
    assert select_person_match(candidates, member).qid == "Q2"


def test_member_without_a_birth_date_falls_back_to_uniqueness():
    member = make_member(1, birth=None)
    candidates = [
        PersonMatch(name="Anna Muster", qid="Q1", birth_date=date(1985, 5, 5)),
        PersonMatch(name="Anna Muster", qid="Q2", birth_date=date(1970, 1, 1)),
    ]
    assert select_person_match(candidates, member) is None


def test_no_candidates():
    assert select_person_match([], make_member(1)) is None


# --- applying name matches --------------------------------------------------
def test_apply_person_matches_sets_the_name_provenance():
    members = [make_member(1, "Anna", "Muster", "1970-01-01")]
    matches = {
        "Anna Muster": [
            PersonMatch(name="Anna Muster", qid="Q7", birth_date=date(1970, 1, 1))
        ]
    }
    apply_person_matches(members, matches)
    assert members[0].qid == "Q7"
    assert members[0].qid_source == QID_FROM_NAME


def test_name_matching_ignores_case_and_extra_whitespace():
    members = [make_member(1, "Anna", "Muster", "1970-01-01")]
    matches = {
        "  anna   muster ": [
            PersonMatch(name="anna muster", qid="Q7", birth_date=date(1970, 1, 1))
        ]
    }
    apply_person_matches(members, matches)
    assert members[0].qid == "Q7"


def test_a_qid_already_taken_is_never_reused():
    """Two sitting members resolving to one item means at least one is wrong."""
    first = make_member(1, "Anna", "Muster", "1970-01-01")
    first.qid = "Q7"
    first.qid_source = QID_FROM_IDENTIFIER
    second = make_member(2, "Anna", "Muster", "1970-01-01")
    matches = {
        "Anna Muster": [
            PersonMatch(name="Anna Muster", qid="Q7", birth_date=date(1970, 1, 1))
        ]
    }
    apply_person_matches([first, second], matches)
    assert second.qid is None


def test_already_resolved_members_are_left_alone():
    member = make_member(1)
    member.qid = "Q1"
    member.qid_source = QID_FROM_IDENTIFIER
    apply_person_matches([member], {"Anna Muster": [PersonMatch(name="Anna Muster", qid="Q9")]})
    assert member.qid == "Q1"
    assert member.qid_source == QID_FROM_IDENTIFIER


def test_unresolved_names_are_deduplicated():
    members = [make_member(1), make_member(2), make_member(3, "Beat", "Zoss")]
    members[0].qid = "Q1"
    assert unresolved_names(members) == ["Anna Muster", "Beat Zoss"]


# --- match_members ----------------------------------------------------------
def test_match_members_runs_both_passes():
    by_id = make_member(1108, "Guy", "Muster")
    by_name = make_member(2000, "Anna", "Muster", "1970-01-01")
    people = [
        make_person("Q121160", "1108", "Guy Muster"),
        make_person("Q7", None, "Anna Muster", "1970-01-01"),
    ]
    matches = {
        "Anna Muster": [
            PersonMatch(name="Anna Muster", qid="Q7", birth_date=date(1970, 1, 1))
        ]
    }
    matched = match_members([by_id, by_name], people, matches)
    assert by_id.qid_source == QID_FROM_IDENTIFIER
    assert by_name.qid_source == QID_FROM_NAME
    assert set(matched) == {1108, 2000}


def test_match_members_without_name_matches_only_joins_on_the_identifier():
    member = make_member(1108)
    matched = match_members([member], [make_person("Q1", "999")])
    assert member.qid is None
    assert matched == {}


# --- corroborating an unverified identifier ---------------------------------
# Turned on by ``identifier_verified: false``, which is what a config says when
# the property's *value* has not been shown to be the source's person id. The
# failure it guards against is the one an exact join can produce and a missing
# one cannot: two id spaces that overlap numerically match confidently, and
# match the wrong people.
def test_an_unverified_identifier_match_must_be_corroborated():
    member = make_member(9532, "Ruth", "Genner", birth=None)
    people = [make_person("Q999", "9532", label="Heinrich Müller")]
    matched = match_by_identifier([member], people, corroborate=True)
    assert member.qid is None
    assert member.identifier_mismatch_qids == ["Q999"]
    assert matched == {}


def test_the_same_match_is_taken_when_the_property_is_verified():
    """The default, and what the federal P1307 join keeps doing.

    P1307's value was measured against ``PersonNumber`` directly, so an item it
    points at is this member however the item is labelled — an unhelpful label
    is not evidence of anything.
    """
    member = make_member(9532, "Ruth", "Genner", birth=None)
    match_by_identifier([member], [make_person("Q999", "9532", label="Q999")])
    assert member.qid == "Q999"


def test_corroboration_accepts_an_agreeing_name():
    member = make_member(9532, "Ruth", "Genner", birth=None)
    people = [make_person("Q117716", "9532", label="Ruth Genner")]
    match_by_identifier([member], people, corroborate=True)
    assert member.qid == "Q117716"
    assert member.identifier_mismatch_qids == []


def test_corroboration_lets_the_birth_date_decide_over_the_label():
    """The strongest evidence available, and the same test the name fallback
    applies to a candidate: a namesake born on another day is another person."""
    member = make_member(9532, "Ruth", "Genner", birth="1956-08-30")
    agreeing = make_person("Q1", "9532", label="R. Genner-Muster", birth="1956-08-30")
    match_by_identifier([member], [agreeing], corroborate=True)
    assert member.qid == "Q1"

    other = make_member(9532, "Ruth", "Genner", birth="1956-08-30")
    namesake = make_person("Q2", "9532", label="Ruth Genner", birth="1980-01-01")
    match_by_identifier([other], [namesake], corroborate=True)
    assert other.qid is None


def test_an_item_nothing_corroborates_is_refused_not_assumed():
    """No label and no birth date corroborates nothing, so the fail-safe
    direction is to refuse: the member falls through to the name search, which
    corroborates whatever it finds."""
    member = make_member(9532, "Ruth", "Genner", birth=None)
    match_by_identifier([member], [make_person("Q3", "9532", label="Q3")],
                        corroborate=True)
    assert member.qid is None
    assert member.identifier_mismatch_qids == ["Q3"]
