"""Tests for the OData row mapping.

``parliament.get_members`` is exercised by feeding the fixture rows to the pure
``members_from_rows`` it delegates to, rather than by mocking ``swissparlpy``'s
OData layer.
"""

from datetime import date

import pytest

from wd_parliament.parliament import (
    _as_bool,
    _as_date,
    member_from_row,
    members_from_rows,
    period_from_row,
    periods_from_rows,
)


# --- date coercion ----------------------------------------------------------
@pytest.mark.parametrize(
    "value,expected",
    [
        ("2023-12-04T00:00:00", date(2023, 12, 4)),
        ("2023-12-04 00:00:00", date(2023, 12, 4)),
        ("2023-12-04", date(2023, 12, 4)),
        (date(2023, 12, 4), date(2023, 12, 4)),
        (None, None),
        ("", None),
        ("not a date", None),
    ],
)
def test_as_date(value, expected):
    assert _as_date(value) == expected


def test_as_date_accepts_datetime():
    from datetime import datetime

    assert _as_date(datetime(2023, 12, 4, 9, 30)) == date(2023, 12, 4)


@pytest.mark.parametrize(
    "value,expected",
    [(True, True), (False, False), ("true", True), ("True", True), ("false", False),
     (1, True), (0, False), (None, False)],
)
def test_as_bool(value, expected):
    assert _as_bool(value) is expected


# --- member mapping ---------------------------------------------------------
def test_member_from_row_maps_every_field_we_use(member_rows):
    row = next(r for r in member_rows if r["PersonNumber"] == 1103)
    member = member_from_row(row)
    assert member.person_number == 1103
    assert member.full_name == "Carole Dubois"
    assert member.sort_name == "Dubois Carole"
    assert member.council == "N"
    assert member.council_number == 1
    assert member.canton_abbreviation == "VD"
    assert member.parl_group_abbreviation == "GL"
    assert member.party_abbreviation == "GLP"
    assert member.date_joining == date(2021, 3, 1)
    assert member.date_leaving is None
    assert member.date_of_birth == date(1980, 11, 25)
    assert member.active is True


def test_row_without_a_person_number_is_skipped(member_rows):
    row = next(r for r in member_rows if r["PersonNumber"] is None)
    assert member_from_row(row) is None


def test_members_from_rows_skips_the_broken_row(member_rows):
    numbers = {m.person_number for m in members_from_rows(member_rows, active_only=False)}
    assert None not in numbers
    assert len(numbers) == 10


def test_members_are_deduplicated_across_languages(member_rows):
    """The service returns one row per person per language."""
    members = members_from_rows(member_rows, active_only=False)
    assert [m.person_number for m in members].count(1101) == 1


def test_active_only_filters_departures(member_rows):
    active = members_from_rows(member_rows, active_only=True)
    numbers = {m.person_number for m in active}
    assert 1106 not in numbers  # left on 2023-12-03
    assert 1107 not in numbers  # single-day tenure, now inactive
    assert 1101 in numbers


def test_council_filter(member_rows):
    national = members_from_rows(member_rows, councils=["N"], active_only=False)
    assert {m.council for m in national} == {"N"}
    states = members_from_rows(member_rows, councils=["S"], active_only=False)
    assert {m.council for m in states} == {"S"}
    assert len(national) + len(states) == 10


def test_council_filter_is_case_insensitive(member_rows):
    assert members_from_rows(member_rows, councils=["n"], active_only=False)


def test_members_are_sorted_by_council_then_name(member_rows):
    members = members_from_rows(member_rows, active_only=False)
    keys = [(m.council, m.sort_name.casefold()) for m in members]
    assert keys == sorted(keys)


def test_member_with_no_birth_date_maps_cleanly(member_rows):
    row = next(r for r in member_rows if r["PersonNumber"] == 1108)
    assert member_from_row(row).date_of_birth is None


# --- period mapping ---------------------------------------------------------
def test_period_from_row(period_rows):
    row = next(r for r in period_rows if r["LegislativePeriodNumber"] == 51)
    period = period_from_row(row)
    assert period.number == 51
    assert period.start == date(2019, 12, 2)
    assert period.end == date(2023, 12, 3)
    assert period.id == 5051


def test_running_period_has_no_end_date(period_rows):
    row = next(
        r
        for r in period_rows
        if r["LegislativePeriodNumber"] == 52 and r["Language"] == "DE"
    )
    assert period_from_row(row).end is None


def test_periods_are_deduplicated_and_sorted(period_rows):
    periods = periods_from_rows(period_rows)
    numbers = [p.number for p in periods]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers)) == 9


def test_periods_from_rows_ignores_rows_without_a_number():
    assert periods_from_rows([{"LegislativePeriodNumber": None}]) == []
