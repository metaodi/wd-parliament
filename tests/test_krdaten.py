"""Tests for the KR-Daten adapter's row mapping.

Pure functions only, from fixture rows — the same discipline the other two
adapters are tested by, and the reason none of them needs HTTP mocking.

The fixture's **column names and value shapes are real**: they are what the
KRRR export and the openZH CSV of the same register write (`ID_EINSITZ_NEW`
values like ``'13_EINSITZ_1'``, dates split into ``_TAG``/``_MONAT``/``_JAHR``,
``WAHLKREIS`` as ``'1. Wahlkreis (Zürich 1+2)'``). The **people are invented**.

Three of these tests exist because getting them wrong writes a false statement
onto a real person's item:

- a **partial date is no date**. The register knows the year and not the day
  for most of its older members, and 1 January is a precision it never claimed.
- an **exit year with no day still means somebody left**. Reading the composed
  date would make every incompletely-dated departure look like a sitting
  member — the shape of the failure that put 2,234 wrong suggestions into this
  repo's first live run.
- the **Regierungsrat is not the Kantonsrat**, and contains the word.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wd_parliament.krdaten import (  # noqa: E402
    KANTONSRAT,
    compose_date,
    find_sheet,
    index_rows,
    is_kantonsrat_row,
    is_open_seat,
    member_from_rows,
    members_from_sheets,
    parse_full_date,
    party_for_seat,
    rows_of,
    tenures_from_sheets,
)
from wd_parliament.models import Body  # noqa: E402

BODY = Body(council="KR", label="Kantonsrat Zürich", position_qid="Q21518678")
TODAY = date(2026, 8, 10)

PERSON_HEADERS = [
    "id_person_new", "nachname", "vorname", "geschlecht",
    "datum_geburt_tag", "datum_geburt_monat", "datum_geburt_jahr",
    "datum_geburt", "datum_tod", "beruf", "gnd", "viaf", "hls",
]
SEAT_HEADERS = [
    "id_einsitz_new", "id_person_new", "id_person", "rat",
    "datum_eintritt_tag", "datum_eintritt_monat", "datum_eintritt_jahr",
    "datum_austritt_tag", "datum_austritt_monat", "datum_austritt_jahr",
    "wahlkreis", "quellen", "bemerkungen",
]
PARTY_HEADERS = [
    "id_partei_new", "id_einsitz_new", "id_person_new",
    "parteibezeichnung", "fraktion",
    "datum_von_tag", "datum_von_monat", "datum_von_jahr",
]


def person(pid="17943", last="Muster", first="Anna", **kw):
    row = dict.fromkeys(PERSON_HEADERS, "")
    row.update(
        id_person_new=pid, nachname=last, vorname=first,
        datum_geburt="1972-11-15 00:00:00.0", beruf="Juristin",
    )
    row.update(kw)
    return row


def seat(sid="17943_EINSITZ_1", pid="17943", rat=KANTONSRAT, entry=("06", "05", "2019"),
         exit=("", "", ""), district="1. Wahlkreis  (Zürich 1+2)", **kw):
    row = dict.fromkeys(SEAT_HEADERS, "")
    row.update(
        id_einsitz_new=sid, id_person_new=pid, rat=rat, wahlkreis=district,
        datum_eintritt_tag=entry[0], datum_eintritt_monat=entry[1],
        datum_eintritt_jahr=entry[2],
        datum_austritt_tag=exit[0], datum_austritt_monat=exit[1],
        datum_austritt_jahr=exit[2],
    )
    row.update(kw)
    return row


def party(sid="17943_EINSITZ_1", pid="17943", name="FDP", fraktion="", **kw):
    row = dict.fromkeys(PARTY_HEADERS, "")
    row.update(
        id_partei_new=f"{sid}_PARTEI_1", id_einsitz_new=sid, id_person_new=pid,
        parteibezeichnung=name, fraktion=fraktion,
    )
    row.update(kw)
    return row


def workbook(persons=None, seats=None, parties=None):
    return {
        "Personen": (PERSON_HEADERS, persons if persons is not None else [person()]),
        "Einsitze": (SEAT_HEADERS, seats if seats is not None else [seat()]),
        "Parteien": (PARTY_HEADERS, parties if parties is not None else [party()]),
    }


# --- dates ------------------------------------------------------------------
def test_a_complete_date_composes():
    row = seat(entry=("06", "05", "2019"))
    assert compose_date(row, ("datum_eintritt_tag", "datum_eintritt_monat",
                              "datum_eintritt_jahr")) == date(2019, 5, 6)


@pytest.mark.parametrize("entry", [("", "", "1937"), ("", "05", "1937"), ("06", "", "")])
def test_a_partial_date_is_no_date(entry):
    """1 January is a precision the register never claimed, and these values
    become P569/P580/P582 — mechanical kinds, written without review."""
    row = seat(entry=entry)
    assert compose_date(row, ("datum_eintritt_tag", "datum_eintritt_monat",
                              "datum_eintritt_jahr")) is None


def test_an_impossible_date_is_skipped_not_raised():
    row = seat(entry=("31", "02", "1937"))
    assert compose_date(row, ("datum_eintritt_tag", "datum_eintritt_monat",
                              "datum_eintritt_jahr")) is None


def test_parse_full_date_reads_the_registers_timestamp():
    assert parse_full_date("1972-11-15 00:00:00.0") == date(1972, 11, 15)
    assert parse_full_date("") is None
    assert parse_full_date("1972") is None


# --- the chamber ------------------------------------------------------------
def test_the_kantonsrat_is_matched_by_equality():
    assert is_kantonsrat_row(seat(rat="Kantonsrat"), SEAT_HEADERS)


@pytest.mark.parametrize("rat", ["Regierungsrat", "Grosser Rat", "Kleiner Rat", ""])
def test_no_other_rat_is_the_kantonsrat(rat):
    """'Regierungsrat' contains 'rat' and is a seven-member executive."""
    assert not is_kantonsrat_row(seat(rat=rat), SEAT_HEADERS)


# --- who sits today ---------------------------------------------------------
def test_a_seat_with_no_exit_is_open():
    assert is_open_seat(seat(), SEAT_HEADERS, today=TODAY)


def test_an_exit_year_alone_still_closes_the_seat():
    """The failure this guards: reading the *composed* exit date would make
    every incompletely-dated departure look like a sitting member."""
    assert not is_open_seat(seat(exit=("", "", "1917")), SEAT_HEADERS, today=TODAY)


def test_a_future_start_is_not_a_seat_yet():
    assert not is_open_seat(
        seat(entry=("01", "01", "2030")), SEAT_HEADERS, today=TODAY
    )


# --- the party --------------------------------------------------------------
def test_the_last_affiliation_spell_wins():
    """One row per spell; only the current one is a claim about them now."""
    by_seat = index_rows(
        [party(name="SP"), party(name="Grüne", fraktion="GP")], "id_einsitz_new"
    )
    assert party_for_seat("17943_EINSITZ_1", by_seat, PARTY_HEADERS) == ("Grüne", "GP")


def test_an_empty_party_does_not_erase_the_earlier_one():
    by_seat = index_rows([party(name="SP"), party(name="")], "id_einsitz_new")
    assert party_for_seat("17943_EINSITZ_1", by_seat, PARTY_HEADERS)[0] == "SP"


def test_a_seat_with_no_party_row_has_none():
    assert party_for_seat("nope", {}, PARTY_HEADERS) == (None, None)


# --- the member -------------------------------------------------------------
def test_a_member_is_mapped_from_a_seat_and_a_person():
    member = member_from_rows(
        seat(), person(), BODY,
        seat_headers=SEAT_HEADERS, person_headers=PERSON_HEADERS,
        party="FDP", today=TODAY,
    )
    assert member.person_number == 17943  # == P13468
    assert (member.last_name, member.first_name) == ("Muster", "Anna")
    assert member.active is True
    assert member.date_joining == date(2019, 5, 6)
    assert member.date_of_birth == date(1972, 11, 15)
    assert member.occupations == ["Juristin"]
    assert member.party_name == "FDP"


def test_the_district_is_tidied_because_it_is_a_map_key():
    """'1. Wahlkreis  (Zürich 1+2)' is one district however it is spaced, and
    an untidied key misses the config's Q-ID map silently."""
    member = member_from_rows(
        seat(district="1.  Wahlkreis   (Zürich 1+2)"), person(), BODY,
        seat_headers=SEAT_HEADERS, person_headers=PERSON_HEADERS,
    )
    assert member.canton_abbreviation == "1. Wahlkreis (Zürich 1+2)"


def test_a_seat_without_a_person_id_is_skipped():
    """That id is P13468's value and the join key; a row lacking it cannot be
    reconciled with anything."""
    assert member_from_rows(
        seat(pid=""), {}, BODY, seat_headers=SEAT_HEADERS
    ) is None


def test_a_seat_whose_person_is_missing_still_produces_a_member():
    """The seat is the fact being reconciled; dropping it shrinks the chamber."""
    member = member_from_rows(
        seat(), {}, BODY, seat_headers=SEAT_HEADERS, person_headers=PERSON_HEADERS
    )
    assert member is not None and member.last_name == ""


# --- the whole workbook -----------------------------------------------------
def test_only_kantonsrat_seats_become_members():
    sheets = workbook(
        persons=[person(pid="1", last="Kantons"), person(pid="2", last="Regierungs")],
        seats=[seat(sid="a", pid="1"), seat(sid="b", pid="2", rat="Regierungsrat")],
        parties=[],
    )
    members = members_from_sheets(sheets, BODY, today=TODAY)
    assert [m.last_name for m in members] == ["Kantons"]


def test_active_only_filters_departed_members():
    sheets = workbook(
        persons=[person(pid="1", last="Sitting"), person(pid="2", last="Gone")],
        seats=[
            seat(sid="a", pid="1"),
            seat(sid="b", pid="2", exit=("19", "08", "1985")),
        ],
        parties=[],
    )
    assert [m.last_name for m in members_from_sheets(sheets, BODY, today=TODAY)] == [
        "Sitting"
    ]
    assert len(members_from_sheets(sheets, BODY, active_only=False, today=TODAY)) == 2


def test_someone_who_left_and_returned_is_one_member_with_the_current_tenure():
    sheets = workbook(
        persons=[person(pid="1", last="Returner")],
        seats=[
            seat(sid="a", pid="1", entry=("01", "05", "1999"),
                 exit=("30", "04", "2003")),
            seat(sid="b", pid="1", entry=("06", "05", "2019")),
        ],
        parties=[],
    )
    members = members_from_sheets(sheets, BODY, active_only=False, today=TODAY)
    assert len(members) == 1
    assert members[0].date_joining == date(2019, 5, 6) and members[0].active


def test_the_open_seat_wins_even_when_the_closed_one_started_later():
    """Which tenure is theirs *now* is the question; a seat with no exit is the
    answer whatever the dates say."""
    sheets = workbook(
        persons=[person(pid="1")],
        seats=[
            seat(sid="a", pid="1", entry=("", "", "")),
            seat(sid="b", pid="1", entry=("06", "05", "2019"),
                 exit=("01", "01", "2023")),
        ],
        parties=[],
    )
    members = members_from_sheets(sheets, BODY, active_only=False, today=TODAY)
    assert len(members) == 1 and members[0].active


def test_the_party_reaches_the_member_through_the_seat_id():
    sheets = workbook(
        persons=[person(pid="1")],
        seats=[seat(sid="s1", pid="1")],
        parties=[party(sid="s1", pid="1", name="GLP")],
    )
    assert members_from_sheets(sheets, BODY, today=TODAY)[0].party_name == "GLP"


# --- tenures, for people the members table does not contain -----------------
def test_tenures_include_people_who_have_left():
    sheets = workbook(
        persons=[person(pid="2", last="Gone")],
        seats=[seat(sid="b", pid="2", entry=("01", "05", "1999"),
                    exit=("30", "04", "2003"))],
        parties=[],
    )
    tenures = tenures_from_sheets(sheets, "KR")
    assert tenures[(2, "KR")].end == date(2003, 4, 30)


def test_a_tenure_is_keyed_by_person_and_council():
    """A person is not a seat: somebody who sat in two chambers has two."""
    sheets = workbook(persons=[person(pid="2")], seats=[seat(sid="b", pid="2")],
                      parties=[])
    assert list(tenures_from_sheets(sheets, "KR")) == [(2, "KR")]


def test_the_latest_spell_is_the_one_reported():
    sheets = workbook(
        persons=[person(pid="1")],
        seats=[
            seat(sid="a", pid="1", entry=("01", "05", "1999"),
                 exit=("30", "04", "2003")),
            seat(sid="b", pid="1", entry=("06", "05", "2019"),
                 exit=("01", "01", "2023")),
        ],
        parties=[],
    )
    assert tenures_from_sheets(sheets, "KR")[(1, "KR")].start == date(2019, 5, 6)


# --- finding the sheets -----------------------------------------------------
def test_a_sheet_is_found_case_insensitively():
    assert find_sheet({"PERSONEN": ([], [])}, "Personen") == "PERSONEN"


def test_a_sheet_is_found_by_prefix():
    """Sheet names are typed by hand, so an umlaut or a suffix must not lose a
    whole table."""
    assert find_sheet({"Zivilstände": ([], [])}, "Zivilstaende") == "Zivilstände"
    assert find_sheet({"Einsitze (KR)": ([], [])}, "Einsitze") == "Einsitze (KR)"


def test_an_unrelated_sheet_is_not_matched():
    """The prefix is 6 characters, not a substring search: 'no such sheet' has
    to stay sayable, or a missing table reads as an empty one."""
    assert find_sheet({"Bindungen": ([], [])}, "Einsitze") is None


def test_an_absent_sheet_reads_as_empty_not_as_an_error():
    assert rows_of({}, "Einsitze") == ([], [])
    assert members_from_sheets({}, BODY, today=TODAY) == []
