"""The pure row-mapping half of ``gever.py``.

Fed from fixture rows, never by mocking the API — the rule every other adapter
in this repo is tested by. The field names below are the ones run 35 read off
the live service, which is the only kind of fixture that could have caught the
failure it found.

Four of these encode findings that would otherwise be paid for again:

* an open mandate carries ``9999-12-31``, not a null;
* the chamber is matched by **equality**, because the city's executive sits
  inside the council's own mandate list;
* the seat roles are an allowlist, because a presiding member has no separate
  ``Mitglied`` row;
* the district comes off the **mandate**, not the person.
"""

from datetime import date

import pytest

from wd_parliament.gever import (
    DEFAULT_SEAT_ROLES,
    OPEN_END_FROM,
    _as_date,
    body_of,
    end_date,
    has_seat_role,
    index_persons,
    is_seat_row,
    member_from_rows,
    members_from_rows,
    person_key,
    tenures_from_rows,
)
from wd_parliament.models import Body

TODAY = date(2026, 8, 18)
GR = Body(council="GR", label="Gemeinderat der Stadt Zürich",
          position_qid="Q111219780", group_name="Gemeinderat")
OPEN = "9999-12-31T23:59:59"


def mandate(guid="p1", gremium="Gemeinderat", funktion="Mitglied",
            start="2026-05-06", end=OPEN, **extra):
    row = {
        "kontaktguid": guid,
        "gremium": gremium,
        "funktion": funktion,
        "dauer_start": start,
        "dauer_end": end,
        "name": "Meier",
        "vorname": "Anna",
    }
    row.update(extra)
    return row


def person(guid="p1", **extra):
    row = {"obj_guid": guid, "name": "Meier", "vorname": "Anna",
           "namevorname": "Meier Anna"}
    row.update(extra)
    return row


# --- the open-end sentinel ---------------------------------------------------
def test_an_open_mandate_carries_9999_and_that_is_not_a_date():
    """``parliament.NULL_DATE`` at the other end of the axis.

    Unmapped it files a P582 of 9999-12-31 on every sitting member and reports
    the whole chamber as departed. Run 35 printed "870 ended, 0 open-ended" one
    line above three sitting members.
    """
    assert end_date(OPEN) is None
    assert end_date("9999-12-31") is None
    assert end_date(None) is None
    assert end_date("2026-04-30") == date(2026, 4, 30)


def test_the_sentinel_threshold_is_loose_rather_than_an_equality_test():
    """A service spelling its infinity 2999-12-31 means the same by it, and no
    parliament records a mandate ending in the 30th century."""
    assert end_date("2999-12-31") is None
    assert OPEN_END_FROM.year > date.today().year + 100
    assert end_date("2030-05-01") == date(2030, 5, 1)


def test_as_date_reads_what_the_service_and_the_client_both_send():
    """The backend hands back datetimes; the raw service writes 8-digit
    strings and ISO text. Reading only one shape reports a dated index as
    undated."""
    from datetime import datetime

    assert _as_date(datetime(2026, 5, 6, 0, 0)) == date(2026, 5, 6)
    assert _as_date("20260506") == date(2026, 5, 6)
    assert _as_date("2026-05-06T00:00:00") == date(2026, 5, 6)
    assert _as_date("") is None
    assert _as_date("nonsense") is None


def test_as_date_does_not_map_the_sentinel_so_a_caller_can_still_see_it():
    """`_as_date` says what the field literally holds; `end_date` says whether
    it has ended. Collapsing them would take away the ability to report the
    sentinel as a sentinel."""
    assert _as_date(OPEN) == date(9999, 12, 31)


# --- the chamber, by equality ------------------------------------------------
def test_the_chamber_is_matched_by_its_gremium():
    assert body_of(mandate(), [GR]) is GR


@pytest.mark.parametrize(
    "gremium",
    [
        # The city's nine-member EXECUTIVE has its own Gremium…
        "Stadtrat",
        # …organs of the chamber are not the chamber…
        "Büro des Gemeinderats",
        "Geschäftsleitung",
        "GPK (Geschäftsprüfungskommission)",
        # …and this service models the PARTIES as Gremien too.
        "SP",
        "FDP",
        "Grüne",
    ],
)
def test_nothing_that_merely_contains_the_chamber_is_the_chamber(gremium):
    assert body_of(mandate(gremium=gremium), [GR]) is None


def test_the_chamber_name_tolerates_the_services_spacing_but_not_its_neighbours():
    assert body_of(mandate(gremium="  Gemeinderat  "), [GR]) is GR
    assert body_of(mandate(gremium="Gemeinderat Zürich"), [GR]) is None


# --- the seat roles ----------------------------------------------------------
@pytest.mark.parametrize("role", DEFAULT_SEAT_ROLES)
def test_every_derived_seat_role_holds_a_seat(role):
    assert has_seat_role(mandate(funktion=role)) is True


@pytest.mark.parametrize(
    "role",
    [
        # The trap peculiar to a city: the executive attends the council and
        # holds no seat in it, and its rows are in the council's own list.
        "Mitglied Stadtrat",
        "Präsidium Stadtrat",
        "Stimmenzählende",
        "Ratssekretariat",
    ],
)
def test_the_executive_the_tellers_and_the_secretariat_hold_no_seat(role):
    assert has_seat_role(mandate(funktion=role)) is False


def test_no_role_list_at_all_counts_every_role():
    """Only ever right for a parliament whose roles nobody has read yet."""
    assert has_seat_role(mandate(funktion="Gast"), seat_roles=[]) is True


# --- what counts as a seat held today ---------------------------------------
def test_a_current_mandate_in_a_seat_role_is_a_seat():
    assert is_seat_row(mandate(), today=TODAY) is True


def test_a_future_start_is_not_a_seat_yet():
    """Run 36 found future starts among the open rows; counting them would
    report a chamber larger than it is."""
    assert is_seat_row(mandate(start="2030-01-01"), today=TODAY) is False


def test_an_ended_mandate_is_not_a_seat():
    assert is_seat_row(mandate(end="2026-04-30"), today=TODAY) is False


def test_an_undated_mandate_is_not_a_seat():
    """The fail-safe direction: an unknown start must not mean 'always'."""
    assert is_seat_row(mandate(start=None), today=TODAY) is False


# --- mapping a member --------------------------------------------------------
def test_the_district_comes_off_the_mandate_not_the_person():
    """The whole reason this source beats OpenParlData here. There the district
    is on the person, so somebody who also sat cantonally carries THAT seat's
    district — which is why body 261 shows 17 districts for 9 seats."""
    m = member_from_rows(
        mandate(wahlkreis="4 und 5"),
        person(wahlkreis="III Zürich 4+5"),
        GR,
    )
    assert m.constituency_abbreviation == "4 und 5"


def test_the_person_supplies_what_the_mandate_does_not():
    m = member_from_rows(
        mandate(), person(beruf="Lehrerin", homepageprivat="https://example.ch"), GR
    )
    assert m.occupations == ["Lehrerin"]
    assert m.website == "https://example.ch"


def test_the_birth_year_is_never_read_as_a_birth_date():
    """`jahrgang` is '1936'. A 1 January is a precision this source never
    claimed, and it would make the name fallback confidently wrong about two
    namesakes rather than honestly uncertain."""
    m = member_from_rows(mandate(), person(jahrgang="1936"), GR)
    assert m.date_of_birth is None


def test_an_open_mandate_leaves_the_leaving_date_empty():
    assert member_from_rows(mandate(), person(), GR).date_leaving is None


def test_the_person_key_is_the_guid_the_mandate_points_at():
    assert person_key(mandate(guid="abc")) == "abc"
    assert index_persons([person(guid="abc")]) == {"abc": person(guid="abc")}


# --- assembling the chamber --------------------------------------------------
def test_a_member_with_two_rows_in_one_chamber_is_one_member():
    """A presiding member holds ONE seat and can carry two rows. Counting rows
    reported 143 where run 36's chamber has 125."""
    rows = [
        mandate(guid="p1", funktion="Mitglied", start="2022-05-01"),
        mandate(guid="p1", funktion="Präsidium", start="2026-05-06"),
    ]
    members = members_from_rows(rows, [person("p1")], [GR], today=TODAY)
    assert len(members) == 1
    # The earliest start wins: the tenure is the span they have held the seat,
    # and the presidium row starts partway through it.
    assert members[0].date_joining == date(2022, 5, 1)


def test_the_executives_rows_never_become_members():
    rows = [
        mandate(guid="p1", funktion="Mitglied"),
        mandate(guid="p2", funktion="Mitglied Stadtrat"),
        mandate(guid="p3", gremium="Stadtrat", funktion="Mitglied"),
    ]
    members = members_from_rows(rows, [], [GR], today=TODAY)
    assert [m.person_number for m in members] == ["p1"]


def test_a_row_with_no_person_guid_is_skipped_rather_than_keyed_on_nothing():
    members = members_from_rows(
        [mandate(guid="")], [], [GR], today=TODAY
    )
    assert members == []


# --- tenures -----------------------------------------------------------------
def test_tenures_include_the_people_who_have_left():
    """A different question from `get_members`, off the same rows: when did
    somebody hold this seat, asked about people the members table lacks."""
    rows = [mandate(guid="gone", start="1990-04-04", end="1998-02-26")]
    tenures = tenures_from_rows(rows, [GR])
    assert list(tenures) == [("gone", "GR")]
    assert tenures[("gone", "GR")].end == date(1998, 2, 26)


def test_a_tenure_is_keyed_by_person_AND_council():
    """A person is not a seat. Keyed on the person alone, somebody who moved
    between chambers has one spell chained onto the other."""
    other = Body(council="XX", label="Other", position_qid="Q1",
                 group_name="Anderer Rat")
    rows = [
        mandate(guid="p1", gremium="Gemeinderat", start="2010-01-01", end="2014-01-01"),
        mandate(guid="p1", gremium="Anderer Rat", start="2014-01-02", end=OPEN),
    ]
    tenures = tenures_from_rows(rows, [GR, other])
    assert set(tenures) == {("p1", "GR"), ("p1", "XX")}
    assert tenures[("p1", "XX")].end is None
