"""Tests for the personal-data probe's decisions.

Same shape as the other probe tests: the network parts are not exercised, the
*decisions* are. The one that matters is the three-way split — a column that is
missing, a column that is empty, and a column listing that could not be read
mean three different things, and reporting the third as the first is exactly
the mistake ``openparldata.classify_seat_memberships`` was written to avoid.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_person_data import (  # noqa: E402
    CONFIRMED,
    CONTRADICTED,
    INCONCLUSIVE,
    SOURCE_COLUMNS,
    classify_source,
    count_by_property,
    present_columns,
    source_coverage,
    volume_note,
)

from wd_parliament.config import SOURCE_OPENPARLDATA, SOURCE_PARLAMENT  # noqa: E402
from wd_parliament.models import (  # noqa: E402
    KIND_ADD_MEMBERSHIP,
    KIND_ADD_PERSON_DATA,
    PERSON_DATA_BY_PROPERTY,
    PERSON_DATA_PROPERTIES,
    Body,
    Member,
    Suggestion,
)

BODY = Body(council="NR", label="NR", position_qid="Q18510612")
BIRTH = PERSON_DATA_BY_PROPERTY["P19"]


def member(**kwargs):
    return Member(person_number=kwargs.pop("n", 1), **kwargs)


def suggestion(kind, prop=None):
    return Suggestion(
        kind=kind,
        body=BODY,
        member_label="Anna Muster",
        detail="",
        payload={"property": prop} if prop else {},
    )


# --- every check must say where it would come from --------------------------
@pytest.mark.parametrize("source", [SOURCE_PARLAMENT, SOURCE_OPENPARLDATA])
def test_every_supported_property_names_its_source_columns(source):
    """Adding a check without a source column is the thing this catches."""
    assert set(SOURCE_COLUMNS[source]) == set(PERSON_DATA_PROPERTIES)
    assert all(SOURCE_COLUMNS[source][p] for p in PERSON_DATA_PROPERTIES)


# --- coverage ---------------------------------------------------------------
def test_source_coverage_counts_members_with_a_value():
    members = [
        member(n=1, place_of_birth="Bern (BE)"),
        member(n=2, place_of_birth=None),
        member(n=3, place_of_birth="Chur (GR)"),
    ]
    assert source_coverage(members, BIRTH) == 2


# --- the three-way split ----------------------------------------------------
def test_a_filled_column_is_confirmed():
    verdict, message = classify_source(BIRTH, 200, 246, ["BirthPlace_City"], ("BirthPlace_City",))
    assert verdict == CONFIRMED
    assert "200 of 246" in message


def test_a_missing_column_is_contradicted_and_says_what_to_do():
    verdict, message = classify_source(BIRTH, 0, 246, [], ("Occupation",))
    assert verdict == CONTRADICTED
    assert "person_data" in message  # the config line to change


def test_an_empty_column_is_inconclusive_not_contradicted():
    """A column filled in for nobody today is a fact about today's chamber."""
    verdict, _ = classify_source(BIRTH, 0, 246, ["BirthPlace_City"], ("BirthPlace_City",))
    assert verdict == INCONCLUSIVE


def test_an_unreadable_listing_is_inconclusive():
    verdict, message = classify_source(BIRTH, 0, 246, None, ("BirthPlace_City",))
    assert verdict == INCONCLUSIVE
    assert "opposite things" in message


def test_present_columns_passes_the_unknown_through():
    assert present_columns(["a", "b"], ("b", "c")) == ["b"]
    assert present_columns(None, ("b",)) is None


# --- the volume report ------------------------------------------------------
def test_counts_are_per_property_and_ignore_the_seat_findings():
    counts = count_by_property(
        [
            suggestion(KIND_ADD_PERSON_DATA, "P19"),
            suggestion(KIND_ADD_PERSON_DATA, "P19"),
            suggestion(KIND_ADD_PERSON_DATA, "P106"),
            suggestion(KIND_ADD_MEMBERSHIP),
        ]
    )
    assert counts == {"P19": 2, "P106": 1}


def test_volume_note_names_a_bulk_edit_as_one():
    assert "bulk" in volume_note(200, 246)
    assert "clean-up" in volume_note(10, 246)
    assert "nothing to do" in volume_note(0, 246)
