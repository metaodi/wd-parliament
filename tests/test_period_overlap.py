"""Exhaustive tests for the tenure x legislative-period join.

``period_overlap`` decides a P2937 qualifier on every statement the tool
emits, so it gets the most thorough treatment of any module here.
"""

from datetime import date

import pytest

from wd_parliament.models import Member, Period
from wd_parliament.period_overlap import (
    assign_periods,
    clip_to_period,
    coverage_report,
    current_period,
    intervals_overlap,
    period_covers,
)


def make_member(joining=None, leaving=None, person_number=1):
    return Member(
        person_number=person_number,
        first_name="Test",
        last_name="Member",
        date_joining=date.fromisoformat(joining) if joining else None,
        date_leaving=date.fromisoformat(leaving) if leaving else None,
    )


def make_period(number, start, end=None):
    return Period(
        number=number,
        name=f"{number}. Legislaturperiode",
        start=date.fromisoformat(start) if start else None,
        end=date.fromisoformat(end) if end else None,
        id=5000 + number,
    )


@pytest.fixture
def three_periods():
    return [
        make_period(50, "2015-11-30", "2019-12-01"),
        make_period(51, "2019-12-02", "2023-12-03"),
        make_period(52, "2023-12-04"),
    ]


# --- intervals_overlap ------------------------------------------------------
@pytest.mark.parametrize(
    "a_start,a_end,b_start,b_end,expected",
    [
        # Disjoint, a before b / b before a.
        ("2010-01-01", "2010-12-31", "2011-01-01", "2011-12-31", False),
        ("2011-01-01", "2011-12-31", "2010-01-01", "2010-12-31", False),
        # Touching at a single day: closed intervals, so they overlap.
        ("2010-01-01", "2011-01-01", "2011-01-01", "2011-12-31", True),
        # Fully contained.
        ("2020-06-01", "2020-06-30", "2020-01-01", "2020-12-31", True),
        # Identical.
        ("2020-01-01", "2020-12-31", "2020-01-01", "2020-12-31", True),
        # a open ended, starting inside b.
        ("2020-06-01", None, "2020-01-01", "2020-12-31", True),
        # a open ended, starting after b ends.
        ("2021-06-01", None, "2020-01-01", "2020-12-31", False),
        # b open ended.
        ("2020-01-01", "2020-12-31", "2020-06-01", None, True),
        # Both open ended.
        ("1990-01-01", None, "2020-01-01", None, True),
    ],
)
def test_intervals_overlap(a_start, a_end, b_start, b_end, expected):
    parse = lambda d: date.fromisoformat(d) if d else None  # noqa: E731
    assert (
        intervals_overlap(parse(a_start), parse(a_end), parse(b_start), parse(b_end))
        is expected
    )


def test_reversed_interval_covers_nothing():
    """Bad data (end before start) must not be treated as covering everything."""
    assert not intervals_overlap(
        date(2020, 12, 31), date(2020, 1, 1), date(2020, 6, 1), date(2020, 6, 30)
    )


# --- assign_periods ---------------------------------------------------------
def test_joined_mid_period(three_periods):
    """A replacement sworn in during the 51st covers the 51st and the 52nd."""
    member = make_member("2021-03-01")
    assert [p.number for p in assign_periods(member, three_periods)] == [51, 52]


def test_left_mid_period(three_periods):
    member = make_member("2021-03-01", "2022-09-30")
    assert [p.number for p in assign_periods(member, three_periods)] == [51]


def test_spans_three_periods(three_periods):
    member = make_member("2015-11-30")
    assert [p.number for p in assign_periods(member, three_periods)] == [50, 51, 52]


def test_single_day_tenure(three_periods):
    member = make_member("2022-06-13", "2022-06-13")
    assert [p.number for p in assign_periods(member, three_periods)] == [51]


def test_single_day_tenure_on_a_period_boundary(three_periods):
    """One day, landing exactly on the first day of the 52nd."""
    member = make_member("2023-12-04", "2023-12-04")
    assert [p.number for p in assign_periods(member, three_periods)] == [52]


def test_open_ended_tenure_reaches_the_running_period(three_periods):
    member = make_member("2019-12-02")
    assert [p.number for p in assign_periods(member, three_periods)] == [51, 52]


def test_tenure_ending_exactly_on_a_period_end_date(three_periods):
    """Closed intervals: the last day in office is still inside the period.

    And, just as importantly, it does *not* leak into the next period, whose
    start is the following day.
    """
    member = make_member("2019-12-02", "2023-12-03")
    assert [p.number for p in assign_periods(member, three_periods)] == [51]


def test_tenure_starting_exactly_on_a_period_start_date(three_periods):
    member = make_member("2023-12-04")
    assert [p.number for p in assign_periods(member, three_periods)] == [52]


def test_tenure_ending_one_day_before_a_period_starts(three_periods):
    member = make_member("2019-12-02", "2023-12-03")
    numbers = [p.number for p in assign_periods(member, three_periods)]
    assert 52 not in numbers


def test_no_joining_date_assigns_nothing(three_periods):
    """The critical fail-safe: an unknown start must not mean 'all periods'."""
    member = make_member(None)
    assert assign_periods(member, three_periods) == []


def test_tenure_entirely_before_every_period(three_periods):
    member = make_member("1990-01-01", "1994-12-31")
    assert assign_periods(member, three_periods) == []


def test_period_without_a_start_date_is_skipped():
    member = make_member("2020-01-01")
    assert assign_periods(member, [Period(number=9, start=None, end=None)]) == []


def test_result_is_sorted_by_period_number(three_periods):
    member = make_member("2015-11-30")
    shuffled = [three_periods[2], three_periods[0], three_periods[1]]
    assert [p.number for p in assign_periods(member, shuffled)] == [50, 51, 52]


def test_period_covers_matches_assign_periods(three_periods):
    member = make_member("2021-03-01")
    covered = assign_periods(member, three_periods)
    for period in three_periods:
        assert period_covers(member, period) == (period in covered)


# --- clip_to_period ---------------------------------------------------------
def test_clip_uses_the_tenure_start_in_the_first_period(three_periods):
    member = make_member("2021-03-01")
    start, end = clip_to_period(member, three_periods[1])  # 51st
    assert start == date(2021, 3, 1)
    assert end == date(2023, 12, 3)  # the period's own end


def test_clip_uses_the_period_start_in_later_periods(three_periods):
    member = make_member("2021-03-01")
    start, end = clip_to_period(member, three_periods[2])  # 52nd, still running
    assert start == date(2023, 12, 4)
    assert end is None  # both the tenure and the period are open


def test_clip_uses_the_tenure_end_when_it_falls_inside(three_periods):
    member = make_member("2019-12-02", "2022-09-30")
    start, end = clip_to_period(member, three_periods[1])
    assert (start, end) == (date(2019, 12, 2), date(2022, 9, 30))


def test_clip_of_a_sitting_member_in_a_closed_period(three_periods):
    """Still sitting, but the 51st is over: that statement's end is the period's."""
    member = make_member("2019-12-02")
    _, end = clip_to_period(member, three_periods[1])
    assert end == date(2023, 12, 3)


# --- current_period / coverage_report ---------------------------------------
def test_current_period_picks_the_running_one(three_periods):
    assert current_period(three_periods, date(2026, 7, 29)).number == 52
    assert current_period(three_periods, date(2021, 1, 1)).number == 51


def test_current_period_of_an_empty_list():
    assert current_period([]) is None


def test_coverage_report_groups_person_numbers(three_periods):
    members = [
        make_member("2015-11-30", person_number=1),
        make_member("2023-12-04", person_number=2),
    ]
    coverage = coverage_report(members, three_periods)
    assert coverage[50] == {1}
    assert coverage[52] == {1, 2}


# --- against the committed fixtures -----------------------------------------
def test_fixture_members_against_fixture_periods(members, periods):
    by_number = {m.person_number: m for m in members}

    # Spans the 50th, 51st and 52nd.
    assert [p.number for p in assign_periods(by_number[1101], periods)] == [50, 51, 52]
    # Mid-period joiner in the 51st.
    assert [p.number for p in assign_periods(by_number[1103], periods)] == [51, 52]
    # Left on the last day of the 51st.
    assert [p.number for p in assign_periods(by_number[1106], periods)] == [51]
    # Single-day tenure.
    assert [p.number for p in assign_periods(by_number[1107], periods)] == [51]
    # No DateJoining at all.
    assert assign_periods(by_number[1109], periods) == []
