"""Tests for README step 4: the roll-call cross-check of the period join.

The check compares two things that are only comparable in one direction. The
overlap assigns periods to the **246 people sitting today**; a roll-call vote
records everyone who sat *then*. So a voter missing from the member list has
left office — which says nothing about the interval logic — while a voter who
is still sitting and was *not* assigned the period they demonstrably voted in
is exactly the failure this step exists to catch.

Getting that backwards is what would make the check useless: it would report
a mismatch for every member who has retired since the vote, and the number
would grow with the age of the roll-call.
"""

import pytest

from wd_parliament.app import validate_periods
from wd_parliament.config import Config
from wd_parliament.models import Body
from wd_parliament.parliament import (
    members_from_rows,
    periods_from_rows,
    segments_from_rows,
    vote_id_from_row,
)
from wd_parliament.__main__ import parse_vote_ids

NATIONAL = Body(council="NR", label="Swiss National Council", position_qid="Q18510612")
STATES = Body(council="SR", label="Swiss Council of States", position_qid="Q18510613")


class FakeParliament:
    """Fixture rows plus a scripted attendance list, instead of parlament.ch."""

    def __init__(self, member_rows, period_rows, attendance, votes=None):
        self._member_rows = member_rows
        self._period_rows = period_rows
        self._attendance = attendance
        self._votes = votes or {}
        self.attendance_calls = []
        self.find_votes_calls = []

    def get_members(self, councils=None, active_only=True, table=None):
        return members_from_rows(
            self._member_rows, councils=councils, active_only=active_only
        )

    def get_periods(self):
        return periods_from_rows(self._period_rows)

    def get_member_segments(self, councils=None):
        return segments_from_rows([], councils=councils)

    def find_votes(self, periods):
        self.find_votes_calls.append([p.number for p in periods])
        return {p.number: self._votes[p.number] for p in periods if p.number in self._votes}

    def get_period_attendance(self, vote_ids):
        self.attendance_calls.append(list(vote_ids))
        out = {}
        for vote_id in vote_ids:
            for period_id, voters in self._attendance.get(vote_id, {}).items():
                out.setdefault(period_id, set()).update(voters)
        return out


def run(member_rows, period_rows, attendance, votes=None, vote_ids=None, **kwargs):
    parliament = FakeParliament(member_rows, period_rows, attendance, votes)
    report = validate_periods(parliament, Config(bodies=[NATIONAL, STATES]),
                              vote_ids, **kwargs)
    return report, parliament


# Period 52 is 2023-12-04 onwards; 1101, 1102, 1104, 1105 and 1108 all sit in
# it, 1109 has no DateJoining and so is assigned nothing, and 1106/1107 left.
PERIOD_52 = 5052


def test_a_voter_who_has_left_is_not_counted_as_a_mismatch(member_rows, period_rows):
    """1106 sat in the 52nd but is Active=False, so is not in the member list."""
    attendance = {900: {PERIOD_52: {1101, 1102, 1106}}}
    report, _ = run(member_rows, period_rows, attendance, vote_ids=[900])

    assert report[52]["voted"] == 3
    assert report[52]["voted_and_still_sitting"] == 2
    assert report[52]["voted_but_no_longer_sitting"] == 1
    assert report[52]["voted_but_not_assigned"] == []


def test_a_sitting_member_who_voted_but_was_not_assigned_is_the_finding(
    member_rows, period_rows
):
    """1110 joined in 2024, so an overlap bug is the only way this happens."""
    attendance = {900: {5049: {1105, 1110}}}
    report, _ = run(member_rows, period_rows, attendance, vote_ids=[900])

    # 1105 joined in 2011 and belongs in the 49th; 1110 does not.
    assert report[49]["voted_but_not_assigned"] == [1110]


def test_absences_are_counted_but_are_not_a_mismatch(member_rows, period_rows):
    attendance = {900: {PERIOD_52: {1101}}}
    report, _ = run(member_rows, period_rows, attendance, vote_ids=[900])

    assert report[52]["assigned"] > 1
    assert report[52]["assigned_but_did_not_vote"] >= 1
    assert report[52]["voted_but_not_assigned"] == []


def test_the_period_row_id_is_translated_to_the_period_number(
    member_rows, period_rows
):
    """Voting keys by the LegislativePeriod row ID, the overlap by its number."""
    attendance = {900: {PERIOD_52: {1101}}}
    report, _ = run(member_rows, period_rows, attendance, vote_ids=[900])

    assert 52 in report and PERIOD_52 not in report
    assert report[52]["period_id"] == PERIOD_52


# --- discovering the votes --------------------------------------------------
def test_votes_are_discovered_when_none_are_given(member_rows, period_rows):
    """Step 4 stayed untouched because it needed IdVotes nobody had."""
    attendance = {777: {PERIOD_52: {1101}}}
    report, parliament = run(
        member_rows, period_rows, attendance, votes={52: 777}, vote_ids=None
    )

    assert parliament.attendance_calls == [[777]]
    assert report[52]["voted"] == 1


def test_discovery_asks_only_for_periods_with_sitting_members(
    member_rows, period_rows
):
    """A period nobody sitting today served in cannot test anything."""
    _, parliament = run(
        member_rows, period_rows, {777: {PERIOD_52: {1101}}},
        votes={52: 777}, vote_ids=None, max_periods=99,
    )

    asked = parliament.find_votes_calls[0]
    assert asked == sorted(asked, reverse=True)  # newest first
    assert 44 not in asked  # 1991-1995: nobody sitting today was there
    assert 52 in asked


def test_discovery_is_capped_at_the_most_recent_periods(member_rows, period_rows):
    _, parliament = run(
        member_rows, period_rows, {777: {PERIOD_52: {1101}}},
        votes={52: 777}, vote_ids=None, max_periods=2,
    )
    assert len(parliament.find_votes_calls[0]) == 2


def test_finding_no_votes_raises_rather_than_reporting_a_clean_run(
    member_rows, period_rows
):
    """An empty comparison must never read as 'everything agrees'."""
    with pytest.raises(RuntimeError, match="No roll-call votes"):
        run(member_rows, period_rows, {}, votes={}, vote_ids=None)


def test_explicit_vote_ids_skip_discovery(member_rows, period_rows):
    _, parliament = run(
        member_rows, period_rows, {900: {PERIOD_52: {1101}}}, votes={52: 777},
        vote_ids=[900],
    )
    assert parliament.find_votes_calls == []
    assert parliament.attendance_calls == [[900]]


# --- the Vote row's own identifier ------------------------------------------
def test_the_vote_id_is_read_from_whichever_column_carries_it():
    assert vote_id_from_row({"IdVote": 23458}) == 23458
    assert vote_id_from_row({"ID": 23458}) == 23458
    # IdVote wins when both are present: that is the column Voting joins on.
    assert vote_id_from_row({"IdVote": 1, "ID": 2}) == 1


def test_an_unidentifiable_vote_row_yields_nothing_rather_than_a_guess():
    assert vote_id_from_row({"VoteDate": "2026-03-01"}) is None
    assert vote_id_from_row({"IdVote": None}) is None


# --- the command line -------------------------------------------------------
def test_no_vote_ids_and_auto_both_mean_discover():
    assert parse_vote_ids([]) == []
    assert parse_vote_ids(None) == []
    assert parse_vote_ids(["auto"]) == []


def test_vote_ids_are_parsed_as_numbers():
    assert parse_vote_ids(["12345", "23456"]) == [12345, 23456]


def test_an_unusable_argument_raises_rather_than_being_dropped():
    with pytest.raises(ValueError, match="IdVote numbers or 'auto'"):
        parse_vote_ids(["12345", "letzte"])
