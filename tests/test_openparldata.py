"""Tests for the cantonal source adapter.

Same discipline as ``test_parliament.py``: the row-mapping functions are pure
and are fed fixture rows directly, never by mocking the API. The fixtures are
hand-built to the shape ``scripts/verify_kantonsrat.py`` measured on
2026-07-30 — the field names and values are exact, the people are real ones
OpenParlData links, and the seven membership rows reproduce every case that
made run 15's funnel go 186 → 182 → 180.

What is worth pinning down here is what the adapter *decides*, and that is
almost entirely one thing: **which membership rows are seats held today**.
``Member.active`` is computed rather than read, because the source carries no
usable flag, and getting it wrong is the failure that published 2,234 bad
suggestions federally.
"""

import json
from datetime import date

import pytest

from wd_parliament.models import Body
from wd_parliament.openparldata import (
    DEFAULT_SEAT_ROLES,
    OpenParlDataClient,
    is_seat_row,
    member_from_rows,
    members_from_rows,
    tenures_from_rows,
)

from conftest import FIXTURES

TODAY = date(2026, 7, 30)
ZH = Body(
    council="KR",
    label="Cantonal Council of Zürich",
    position_qid="Q21518678",
    group_id=5077,
    group_name="Kantonsrat Zürich",
)


@pytest.fixture
def memberships():
    return json.loads((FIXTURES / "zh_memberships.json").read_text(encoding="utf-8"))


@pytest.fixture
def persons():
    return json.loads((FIXTURES / "zh_persons.json").read_text(encoding="utf-8"))


def row(**overrides):
    base = {
        "id": 1,
        "person_id": 42,
        "role_name_de": "Mitglied",
        "begin_date": "2023-05-08",
        "end_date": None,
    }
    base.update(overrides)
    return base


# --- which rows are seats held today ----------------------------------------
def test_a_plain_member_holds_a_seat():
    assert is_seat_row(row(), today=TODAY, seat_roles=DEFAULT_SEAT_ROLES) is True


def test_a_closed_membership_is_not_a_seat():
    assert is_seat_row(
        row(end_date="2025-01-01"), today=TODAY, seat_roles=DEFAULT_SEAT_ROLES
    ) is False


def test_a_member_who_takes_office_later_is_not_sitting_yet():
    """Run 15: four open rows began 2026-08-17, eighteen days out.

    Real and correctly open, but not sitting — and counting them puts the
    chamber over its 180 seats.
    """
    assert is_seat_row(
        row(begin_date="2026-08-17"), today=TODAY, seat_roles=DEFAULT_SEAT_ROLES
    ) is False
    assert is_seat_row(
        row(begin_date="2026-08-17"), today=date(2026, 9, 1),
        seat_roles=DEFAULT_SEAT_ROLES,
    ) is True


@pytest.mark.parametrize("role", ["Präsidium", "1. Vizepräsidium", "2. Vizepräsidium"])
def test_a_presiding_officer_holds_a_seat_through_that_row(role):
    """They have no separate 'Mitglied' row — the presidium row IS the seat.

    Excluding them was run 14's mistake and cost three of the 180.
    """
    assert is_seat_row(row(role_name_de=role), today=TODAY,
                       seat_roles=DEFAULT_SEAT_ROLES) is True


@pytest.mark.parametrize("role", ["Gast", None, "Beobachter"])
def test_a_anything_outside_the_allowlist_is_not_a_seat(role):
    assert is_seat_row(row(role_name_de=role), today=TODAY,
                       seat_roles=DEFAULT_SEAT_ROLES) is False


def test_a_a_row_with_no_start_date_is_never_a_seat():
    """The same fail-safe period_overlap applies to an unknown start.

    One of the 913 rows carries no begin_date. Treating it as sitting would put
    a member in the chamber with no P580 and no interval at all.
    """
    assert is_seat_row(row(begin_date=None), today=TODAY,
                       seat_roles=DEFAULT_SEAT_ROLES) is False


# --- mapping a row to a Member ----------------------------------------------
def test_the_wahlkreis_becomes_the_district_key_with_tidy_whitespace(persons):
    """The source spells it 'I      Zürich 1+2' with six spaces.

    That string is the P768 lookup key and the report's grouping heading, so it
    is normalised at the mapping boundary rather than everywhere it is used.
    """
    member = member_from_rows(row(person_id=9532), persons[0], ZH, today=TODAY)
    assert member.canton_abbreviation == "I Zürich 1+2"


def test_the_person_id_becomes_the_join_key(persons):
    member = member_from_rows(row(person_id=9532), persons[0], ZH, today=TODAY)
    assert member.person_number == 9532
    assert member.council == "KR"
    assert member.full_name == "Ruth Genner"


def test_the_begin_date_is_the_tenure_start_not_a_segment_start(persons):
    """No MemberCouncilHistory equivalent is needed here.

    Federally DateJoining is a mandate *segment* start and P580 must come from
    a chained history. The ZH rows are one per tenure (913 across 834 people),
    so begin_date is already the date P580 wants.
    """
    member = member_from_rows(
        row(person_id=9532, begin_date="2015-05-18"), persons[0], ZH, today=TODAY
    )
    assert member.date_joining == date(2015, 5, 18)
    assert member.start_date == date(2015, 5, 18)


def test_a_membership_whose_person_is_missing_still_produces_a_member():
    """The seat is the fact being reconciled; dropping it shrinks the chamber."""
    member = member_from_rows(row(person_id=99999), {}, ZH, today=TODAY)
    assert member is not None and member.person_number == 99999
    assert member.full_name == ""


def test_a_row_without_a_person_id_is_skipped():
    """No join key, no reconciliation — the rule parliament.py applies too."""
    assert member_from_rows(row(person_id=None), {}, ZH, today=TODAY) is None


def test_a_name_falls_back_to_splitting_fullname():
    member = member_from_rows(
        row(person_id=7), {"id": 7, "fullname": "Anna Beispiel"}, ZH, today=TODAY
    )
    assert (member.first_name, member.last_name) == ("Anna", "Beispiel")


# --- the whole fixture, which is run 15's funnel in miniature ----------------
def test_only_the_sitting_members_come_back(memberships, persons):
    """Seven rows in, three members out.

    One plain member, one presiding officer, and one member who left and
    returned. Excluded: a Gast, a member starting 2026-08-17, a row with no
    role, and the closed half of the returner's service.
    """
    members = members_from_rows(
        memberships, persons, ZH, today=TODAY, seat_roles=DEFAULT_SEAT_ROLES
    )
    assert [m.person_number for m in members] == [9532, 21905, 18759]
    assert all(m.active for m in members)


def test_someone_who_left_and_returned_gets_their_current_tenure(memberships, persons):
    """Two rows, and the later one is the tenure they are serving now."""
    members = members_from_rows(
        memberships, persons, ZH, today=TODAY, seat_roles=DEFAULT_SEAT_ROLES
    )
    noser = next(m for m in members if m.person_number == 18759)
    assert noser.date_joining == date(2023, 5, 8)
    assert noser.date_leaving is None


def test_members_are_sorted_by_surname(memberships, persons):
    members = members_from_rows(
        memberships, persons, ZH, today=TODAY, seat_roles=DEFAULT_SEAT_ROLES
    )
    assert [m.last_name for m in members] == ["Genner", "Mörgeli", "Noser"]


def test_active_only_false_keeps_everyone_with_a_join_key(memberships, persons):
    members = members_from_rows(
        memberships, persons, ZH, active_only=False, today=TODAY,
        seat_roles=DEFAULT_SEAT_ROLES,
    )
    assert len(members) == 6  # seven rows, two of them the same person


# --- the client's own decisions ---------------------------------------------
def test_a_configured_group_id_costs_no_request():
    """5077 is measured and stable; looking it up again would be a request."""
    client = OpenParlDataClient(body_key="ZH", bodies=[ZH], client=object())
    assert client.resolve_group_ids() == {"KR": 5077}


def test_a_group_is_matched_by_exact_name_never_as_a_substring():
    """The Regierungsrat is the cantonal executive, not the legislature.

    A substring match on 'Kantonsrat' would also take 'Büro des Kantonsrates';
    a looser one still could reach the seven-member government.
    """
    class FakeClient:
        def get_data(self, table, **params):
            return [
                {"id": 1, "name_de": "Regierungsrat"},
                {"id": 2, "name_de": "Büro des Kantonsrates"},
                {"id": 5077, "name_de": "Kantonsrat Zürich"},
            ]

    body = Body(
        council="KR",
        label="Cantonal Council of Zürich",
        position_qid="Q21518678",
        group_name="Kantonsrat Zürich",
    )
    client = OpenParlDataClient(body_key="ZH", bodies=[body], client=FakeClient())
    assert client.resolve_group_ids() == {"KR": 5077}


def test_a_group_that_matches_nothing_is_left_out_rather_than_guessed():
    class FakeClient:
        def get_data(self, table, **params):
            return [{"id": 1, "name_de": "Regierungsrat"}]

    body = Body(
        council="KR", label="Kantonsrat", position_qid="Q21518678",
        group_name="Kantonsrat Zürich",
    )
    client = OpenParlDataClient(body_key="ZH", bodies=[body], client=FakeClient())
    assert client.resolve_group_ids() == {}


def test_there_are_no_periods_and_that_is_deliberate():
    """No period table exists, so no P2937 is ever suggested.

    A fail-safe rather than a gap: with no periods assign_periods assigns none,
    and the empty `terms` map would produce nothing anyway.
    """
    assert OpenParlDataClient(client=object()).get_periods() == []


def test_there_are_no_segments_to_correct():
    """begin_date is already the tenure start — see the module docstring."""
    assert OpenParlDataClient(client=object()).get_member_segments() == {}


# --- tenure dates, ended rows included --------------------------------------
# What `members_from_rows` filters away is exactly what the report needs for
# somebody Wikidata still records as sitting: the row that says when they left.
def test_an_ended_membership_yields_both_of_its_dates():
    rows = [row(person_id=777, begin_date="2011-05-09", end_date="2019-05-05")]
    tenure = tenures_from_rows(rows, ZH, seat_roles=DEFAULT_SEAT_ROLES)[(777, "KR")]
    assert (tenure.start, tenure.end) == (date(2011, 5, 9), date(2019, 5, 5))


def test_the_latest_row_wins_for_someone_who_left_and_returned(memberships):
    """Person 18759 sat 2011-2019 and came back in 2023; the seat is open."""
    tenure = tenures_from_rows(memberships, ZH, seat_roles=DEFAULT_SEAT_ROLES)[
        (18759, "KR")
    ]
    assert tenure.start == date(2023, 5, 8)
    assert tenure.end is None


def test_a_guest_row_is_not_a_tenure(memberships):
    """It was never a seat, so its dates are not this seat's dates."""
    tenures = tenures_from_rows(memberships, ZH, seat_roles=DEFAULT_SEAT_ROLES)
    assert (17728, "KR") not in tenures  # Gast
    assert (24011, "KR") not in tenures  # no role at all


def test_a_future_start_is_still_a_tenure(memberships):
    """Not sitting *today*, which is `is_seat_row`'s question, not this one."""
    tenures = tenures_from_rows(memberships, ZH, seat_roles=DEFAULT_SEAT_ROLES)
    assert tenures[(17860, "KR")].start == date(2026, 8, 17)
