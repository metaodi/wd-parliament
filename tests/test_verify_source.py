"""Tests for the pure decisions in ``scripts/verify_source.py``.

The fetching is network code and is left alone. ``diagnose_member_fetch``,
``describe_tenure_dates`` and ``classify`` are what decide *what the probe
concluded*, so they are the parts worth pinning down — especially the first,
which attributed the zero-member read of 2026-07-29 to the council filter.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_source import (  # noqa: E402
    CONFIRMED,
    CONTRADICTED,
    INCONCLUSIVE,
    classify,
    describe_tenure_dates,
    diagnose_member_fetch,
)


def row(person_number=None, id_code=None):
    return {"PersonNumber": person_number, "PersonIdCode": id_code, "LastName": "Parmelin"}


def member_row(
    person_number=1101,
    active=True,
    abbr="NR",
    joining="2023-12-04T00:00:00",
    leaving="1753-01-01T00:00:00",
):
    """One MemberCouncil row as the live service actually shapes it.

    "NR"/"SR", and a leaving date of 1753-01-01 — the null-date sentinel —
    for anyone still sitting.
    """
    return {
        "PersonNumber": person_number,
        "Active": active,
        "CouncilAbbreviation": abbr,
        "CouncilName": "Nationalrat" if abbr == "NR" else "Ständerat",
        "FirstName": "Anna",
        "LastName": "Muster",
        "DateJoining": joining,
        "DateLeaving": leaving,
    }


# --- diagnosing the zero-member read ----------------------------------------
def test_a_healthy_fetch_passes():
    ok, lines = diagnose_member_fetch([member_row(), member_row(1102)], ["NR", "SR"])
    assert ok is True
    assert any("2 sitting members would reach the diff" in ln for ln in lines)


def test_an_empty_odata_response_blames_the_odata_filters():
    ok, lines = diagnose_member_fetch([], ["NR", "SR"])
    assert ok is False
    assert any("OData itself returned nothing" in ln for ln in lines)


def test_a_council_abbreviation_mismatch_is_named_exactly():
    """The failure that emptied the first run's list, confirmed 2026-07-29."""
    raw = [member_row(abbr="NR"), member_row(1102, abbr="SR")]
    ok, lines = diagnose_member_fetch(raw, ["N", "S"])
    assert ok is False
    blob = "\n".join(lines)
    assert "COUNCIL FILTER" in blob
    assert "'NR'" in blob and "'SR'" in blob


def test_rows_without_a_person_number_are_called_out():
    raw = [member_row(person_number=None), member_row(person_number=None)]
    ok, lines = diagnose_member_fetch(raw, ["NR", "SR"])
    assert ok is False
    assert any("PersonNumber" in ln for ln in lines)


def test_no_active_rows_is_distinguished_from_a_bad_council_filter():
    raw = [member_row(active=False), member_row(1102, active=False)]
    ok, lines = diagnose_member_fetch(raw, ["NR", "SR"])
    assert ok is False
    assert any("Active" in ln for ln in lines)


# --- are the dates believable? ----------------------------------------------
def test_tenure_dates_are_reported_per_chamber():
    raw = [member_row(), member_row(1102, abbr="SR")]
    blob = "\n".join(describe_tenure_dates(raw, ["NR", "SR"]))
    assert "NR: 1 sitting member(s)" in blob
    assert "SR: 1 sitting member(s)" in blob
    assert "OK: no sitting member carries a leaving date" in blob


def test_starts_that_are_all_1_january_are_flagged():
    """A per-year mandate segment, not a tenure: P580 from it would be wrong."""
    raw = [
        member_row(joining="2026-01-01T00:00:00"),
        member_row(1102, joining="2026-01-01T00:00:00"),
    ]
    blob = "\n".join(describe_tenure_dates(raw, ["NR", "SR"]))
    assert "per-year mandate segments" in blob


def test_ordinary_starts_are_not_flagged():
    raw = [member_row(joining="2023-12-04T00:00:00")]
    blob = "\n".join(describe_tenure_dates(raw, ["NR", "SR"]))
    assert "per-year mandate segments" not in blob


def test_a_leaking_sentinel_is_caught():
    """If parliament._as_date stops handling it, a sitting member "leaves"."""
    import wd_parliament.parliament as parliament

    original = parliament.NULL_DATE
    parliament.NULL_DATE = date(1, 1, 1)  # disable the sentinel handling
    try:
        blob = "\n".join(describe_tenure_dates([member_row()], ["NR", "SR"]))
    finally:
        parliament.NULL_DATE = original
    assert "sentinel is leaking" in blob


def test_members_without_a_start_date_are_counted():
    raw = [member_row(joining="1753-01-01T00:00:00")]
    blob = "\n".join(describe_tenure_dates(raw, ["NR", "SR"]))
    assert "1 sitting member(s) have no DateJoining" in blob


def test_nothing_to_say_when_the_filter_emptied_the_list():
    blob = "\n".join(describe_tenure_dates([member_row()], ["N", "S"]))
    assert "no members survived the filter" in blob


def test_person_number_matching_confirms_the_assumption():
    verdict, detail = classify([row(person_number=1108, id_code=900001)], 1108)
    assert verdict == CONFIRMED
    assert "PersonNumber" in detail


def test_person_id_code_matching_instead_names_the_fix():
    """The most useful failure: it says which field to switch to."""
    verdict, detail = classify([row(person_number=4321, id_code=1108)], 1108)
    assert verdict == CONTRADICTED
    assert "PersonIdCode" in detail


def test_neither_field_matching_contradicts_the_design():
    verdict, detail = classify([row(person_number=4321, id_code=900001)], 1108)
    assert verdict == CONTRADICTED
    assert "name + birth date" in detail


def test_no_rows_is_inconclusive_not_a_failure():
    """Not finding the person tests nothing; it must not read as a refutation."""
    verdict, detail = classify([], 1108)
    assert verdict == INCONCLUSIVE
    assert "no such person" in detail


def test_an_unreachable_service_is_not_reported_as_person_not_found():
    """Otherwise a red job sends someone chasing the wrong problem."""
    verdict, detail = classify([], 1108, errors=["MemberCouncil: connection refused"])
    assert verdict == INCONCLUSIVE
    assert "connectivity" in detail
    assert "connection refused" in detail


def test_rows_win_over_a_partial_read_failure():
    """One table unreadable, the other answered — that is still a real test."""
    verdict, _ = classify(
        [row(person_number=1108)], 1108, errors=["MemberCouncilHistory: boom"]
    )
    assert verdict == CONFIRMED


def test_a_match_in_any_row_confirms():
    """Several rows come back — one per council, or per language."""
    rows = [row(person_number=4321), row(person_number=1108)]
    assert classify(rows, 1108)[0] == CONFIRMED


@pytest.mark.parametrize("verdict_rows,expected", [
    ([row(person_number=1108)], CONFIRMED),
    ([row(id_code=1108)], CONTRADICTED),
    ([], INCONCLUSIVE),
])
def test_only_confirmation_is_a_green_result(verdict_rows, expected):
    """The script exits 0 only on CONFIRMED; every other state is visible."""
    verdict, _ = classify(verdict_rows, 1108)
    assert verdict == expected
    assert (verdict == CONFIRMED) == (expected == CONFIRMED)
