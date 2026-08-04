"""Tests for the OData row mapping.

``parliament.get_members`` is exercised by feeding the fixture rows to the pure
``members_from_rows`` it delegates to, rather than by mocking ``swissparlpy``'s
OData layer.
"""

from datetime import date, datetime

import pytest

from wd_parliament.parliament import (
    _as_bool,
    _as_date,
    apply_tenure_starts,
    latest_tenure,
    member_from_row,
    members_from_rows,
    segments_from_rows,
    tenure_start,
    tenures_from_segments,
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


# --- the null-date sentinel -------------------------------------------------
# The service says "no date" with SQL Server's datetime minimum rather than a
# null. Read literally it means a member left in 1753, which reverses their
# tenure interval and — because ADD_END_DATE is mechanical — would reach
# QuickStatements as a P582 backfill across the whole chamber.
@pytest.mark.parametrize(
    "value",
    [
        "1753-01-01T00:00:00",
        "1753-01-01",
        date(1753, 1, 1),
        datetime(1753, 1, 1),
        "1752-12-31",  # below the floor; cannot be a real date either
    ],
)
def test_the_null_date_sentinel_reads_as_no_date(value):
    assert _as_date(value) is None


def test_a_real_date_just_after_the_sentinel_survives():
    assert _as_date("1753-01-02") == date(1753, 1, 2)


def test_a_sitting_member_has_no_leaving_date(member_rows):
    """Every active row carries the sentinel; none of them has left."""
    for member in members_from_rows(member_rows, active_only=True):
        assert member.date_leaving is None


def test_a_sentinel_joining_date_leaves_the_member_undated(member_rows):
    """1109 joins on the sentinel — the fail-safe needs that to read as unknown."""
    row = next(r for r in member_rows if r["PersonNumber"] == 1109)
    assert member_from_row(row).date_joining is None


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
    assert member.council == "NR"
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


def test_duplicate_rows_for_one_person_collapse_to_the_latest(member_rows):
    """The service repeats a person within one language; keep the latest row."""
    national = members_from_rows(member_rows, councils=["NR"], active_only=False)
    bachmann = [m for m in national if m.person_number == 1101]
    assert len(bachmann) == 1
    assert bachmann[0].date_joining == date(2015, 11, 30)  # not the 2011 row


def test_french_rows_carry_french_abbreviations(member_rows):
    """Why the pipeline pushes Language=DE down rather than de-duplicating.

    The French row for 1101 is "CN", not "NR", so it is not the same key as
    the German one and the council filter is what discards it.
    """
    everything = members_from_rows(member_rows, active_only=False)
    assert {m.council for m in everything} == {"NR", "SR", "CN"}
    assert [m.person_number for m in everything].count(1101) == 2

    national = members_from_rows(member_rows, councils=["NR"], active_only=False)
    assert [m.person_number for m in national].count(1101) == 1


def test_active_only_filters_departures(member_rows):
    active = members_from_rows(member_rows, active_only=True)
    numbers = {m.person_number for m in active}
    assert 1106 not in numbers  # left on 2023-12-03
    assert 1107 not in numbers  # single-day tenure, now inactive
    assert 1101 in numbers


def test_council_filter(member_rows):
    national = members_from_rows(member_rows, councils=["NR"], active_only=False)
    assert {m.council for m in national} == {"NR"}
    states = members_from_rows(member_rows, councils=["SR"], active_only=False)
    assert {m.council for m in states} == {"SR"}
    assert len(national) + len(states) == 10


def test_the_old_council_codes_match_nothing(member_rows):
    """"N"/"S" came from the OData docs and cost the first run every member."""
    assert members_from_rows(member_rows, councils=["N", "S"], active_only=False) == []


def test_council_filter_is_case_insensitive(member_rows):
    assert members_from_rows(member_rows, councils=["nr"], active_only=False)


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


# --- mandate segments and the tenure start -----------------------------------
# README step 0c: MemberCouncil.DateJoining is the start of the *current
# segment*, not of the tenure. Bregy's row says 2025-09-16 while his history
# shows continuous service from 2019-03-04, and P580 is emitted by two
# mechanical kinds — so reading the raw field would write a wrong date unseen.
def history_row(joining, leaving, person=4230, abbr="NR", language="DE", active=False):
    return {
        "PersonNumber": person,
        "Language": language,
        "Active": active,
        "CouncilAbbreviation": abbr,
        "CouncilName": "Nationalrat",
        "FirstName": "Philipp Matthias",
        "LastName": "Bregy",
        "DateJoining": joining,
        "DateLeaving": leaving,
    }


BREGY = [
    history_row("2019-03-04T00:00:00", "2019-12-01T00:00:00"),
    history_row("2019-12-02T00:00:00", "2023-12-03T00:00:00"),
    history_row("2023-12-04T00:00:00", "1753-01-01T00:00:00", active=True),
]


def test_adjacent_segments_chain_into_one_tenure():
    """Bregy, exactly as the live service returns him."""
    segments = segments_from_rows(BREGY, councils=["NR"])
    assert tenure_start(segments[(4230, "NR")]) == date(2019, 3, 4)


def test_a_real_break_starts_a_new_tenure():
    """Left and returned years later: the current tenure begins at the return."""
    rows = [
        history_row("2011-12-05T00:00:00", "2015-11-29T00:00:00"),
        history_row("2023-12-04T00:00:00", "1753-01-01T00:00:00", active=True),
    ]
    segments = segments_from_rows(rows, councils=["NR"])
    assert tenure_start(segments[(4230, "NR")]) == date(2023, 12, 4)


def test_segments_are_not_deduplicated_away():
    """members_from_rows collapses (person, council); this must not."""
    segments = segments_from_rows(BREGY, councils=["NR"])
    assert len(segments[(4230, "NR")]) == 3


def test_language_duplicates_are_dropped_but_distinct_spans_survive():
    rows = BREGY + [
        history_row("2019-03-04T00:00:00", "2019-12-01T00:00:00", language="FR"),
    ]
    segments = segments_from_rows(rows, councils=["NR"])
    assert len(segments[(4230, "NR")]) == 3  # the FR copy of span 1 is dropped


def test_an_undated_segment_is_discarded_rather_than_chained():
    rows = BREGY + [history_row("1753-01-01T00:00:00", "1753-01-01T00:00:00")]
    segments = segments_from_rows(rows, councils=["NR"])
    assert len(segments[(4230, "NR")]) == 3
    assert tenure_start(segments[(4230, "NR")]) == date(2019, 3, 4)


def test_no_segments_gives_no_start_rather_than_a_guess():
    assert tenure_start([]) is None


def test_an_open_earlier_segment_stops_the_chain():
    """Overlapping open rows are not a continuous run; do not measure a gap."""
    rows = [
        history_row("2015-11-30T00:00:00", "1753-01-01T00:00:00", active=True),
        history_row("2023-12-04T00:00:00", "1753-01-01T00:00:00", active=True),
    ]
    segments = segments_from_rows(rows, councils=["NR"])
    assert tenure_start(segments[(4230, "NR")]) == date(2023, 12, 4)


def test_apply_tenure_starts_counts_only_the_corrections():
    """The count is what is worth reporting: rows whose raw value was a segment."""
    segmented = member_from_row(
        history_row("2025-09-16T00:00:00", "1753-01-01T00:00:00", active=True)
    )
    unchanged = member_from_row(
        history_row("2023-12-04T00:00:00", "1753-01-01T00:00:00", person=4053, active=True)
    )
    segments = segments_from_rows(
        BREGY + [history_row("2023-12-04T00:00:00", "1753-01-01T00:00:00", person=4053)],
        councils=["NR"],
    )
    corrected = apply_tenure_starts([segmented, unchanged], segments)
    assert corrected == 1
    assert segmented.tenure_start == date(2019, 3, 4)
    assert segmented.start_date == date(2019, 3, 4)
    assert unchanged.start_date == date(2023, 12, 4)


def test_a_member_with_no_history_keeps_the_raw_field():
    """Degrade to DateJoining rather than inventing a tenure start."""
    member = member_from_row(
        history_row("2025-09-16T00:00:00", "1753-01-01T00:00:00", person=9999, active=True)
    )
    assert apply_tenure_starts([member], {}) == 0
    assert member.tenure_start is None
    assert member.start_date == date(2025, 9, 16)


# --- tenure dates for people who have already left ---------------------------
# The same history rows, read for the other end of the span: MemberCouncil
# carries only today's ~246 members, so a member Wikidata still records as
# sitting has no leaving date anywhere else.
DEPARTED = [
    history_row("2011-12-05T00:00:00", "2015-11-29T00:00:00"),
    history_row("2015-11-30T00:00:00", "2019-12-01T00:00:00"),
]


def test_a_departed_member_gets_both_ends_of_their_tenure():
    segments = segments_from_rows(DEPARTED, councils=["NR"])
    tenure = latest_tenure(segments[(4230, "NR")])
    assert (tenure.start, tenure.end) == (date(2011, 12, 5), date(2019, 12, 1))
    assert tenure.key == (4230, "NR")


def test_a_sitting_members_tenure_has_no_end():
    """The sentinel leaving date is absence, not 1753 — so: no date to add."""
    segments = segments_from_rows(BREGY, councils=["NR"])
    tenure = latest_tenure(segments[(4230, "NR")])
    assert tenure.start == date(2019, 3, 4)
    assert tenure.end is None


def test_the_end_is_the_newest_segments_leaving_date_not_an_earlier_one():
    """A break means the current spell's end, never the first spell's."""
    rows = [
        history_row("2003-12-01T00:00:00", "2007-12-02T00:00:00"),
        history_row("2015-11-30T00:00:00", "2019-12-01T00:00:00"),
    ]
    tenure = latest_tenure(segments_from_rows(rows, councils=["NR"])[(4230, "NR")])
    assert (tenure.start, tenure.end) == (date(2015, 11, 30), date(2019, 12, 1))


def test_undated_segments_give_no_tenure_rather_than_a_guess():
    assert latest_tenure([]) is None


def test_tenures_are_keyed_by_person_and_council():
    """A person is not a seat: a chamber change is two tenures, not one."""
    rows = DEPARTED + [
        history_row("2019-12-02T00:00:00", "1753-01-01T00:00:00", abbr="SR", active=True)
    ]
    tenures = tenures_from_segments(segments_from_rows(rows))
    assert set(tenures) == {(4230, "NR"), (4230, "SR")}
    assert tenures[(4230, "NR")].end == date(2019, 12, 1)
    assert tenures[(4230, "SR")].end is None
