"""The pure parts of ``scripts/verify_gever_city.py``.

Same discipline as ``test_verify_gever.py``: the network half is not tested,
the classification half is — because a probe that returns the wrong verdict is
worse than one that cannot run, and this one is being read to decide whether a
whole parliament gets a source.

Three of these encode findings the repo has already paid for elsewhere and
would otherwise pay for again one level down:

* an **absent** column is INCONCLUSIVE, never CONTRADICTED;
* the chamber is matched by **equality**, because the city's executive sits in
  the same index;
* the seat count is over **people**, not rows.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_gever_city import (  # noqa: E402
    CONFIRMED,
    CONTRADICTED,
    INCONCLUSIVE,
    OPEN_END_FROM,
    _as_date,
    classify_dates,
    classify_join,
    classify_seat_count,
    columns_of,
    end_date,
    is_the_chamber,
    resolve_column,
)


# --- A/B. reading the records rather than assuming them ----------------------
def test_b_a_column_is_counted_by_the_values_that_are_actually_there():
    """"No such column" and "column full of nulls" have to be tellable apart."""
    rows = [
        {"name": "Meier", "beruf": None},
        {"name": "Huber", "beruf": ""},
        {"name": "Frei", "beruf": "Lehrerin"},
    ]
    counts = columns_of(rows)
    assert counts["name"] == 3
    assert counts["beruf"] == 1
    assert "wahlkreis" not in counts


def test_b_a_column_only_some_records_carry_is_still_a_column():
    counts = columns_of([{"a": 1}, {"b": 2}])
    assert set(counts) == {"a", "b"}


def test_b_the_callers_preference_wins_over_the_column_order():
    """Hints are ordered; columns are not. `dauer_start` must not lose to a
    column merely because that column comes first in the record."""
    columns = ["eintritt_von", "dauer_start"]
    assert resolve_column(columns, ("dauer_start", "von")) == "dauer_start"


def test_b_an_unmatched_hint_resolves_to_none_so_absence_stays_sayable():
    assert resolve_column(["name", "partei"], ("wahlkreis",)) is None


# --- C. the chamber, by equality --------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "Gemeinderat",
        "Gemeinderat der Stadt Zürich",
        "  gemeinderat  zürich  ",
    ],
)
def test_c_the_chamber_is_recognised_under_its_spellings(name):
    assert is_the_chamber(name) is True


@pytest.mark.parametrize(
    "name",
    [
        # The dangerous one: the city's nine-member EXECUTIVE, in the same
        # index as the legislature.
        "Stadtrat",
        "Stadtrat der Stadt Zürich",
        # Organs of the chamber, which are not the chamber.
        "Büro des Gemeinderats",
        "Geschäftsleitung des Gemeinderats",
        "Rechnungsprüfungskommission des Gemeinderats",
        # Other parliaments entirely.
        "Kantonsrat",
        "Nationalrat",
    ],
)
def test_c_neither_the_executive_nor_an_organ_of_the_chamber_matches(name):
    assert is_the_chamber(name) is False


def test_c_an_absent_date_column_is_inconclusive_not_contradicted():
    """The rule ``classify_seat_memberships`` exists for. A missing column and
    an empty one are the same shape through ``.get()`` and mean the opposite."""
    verdict, detail, _ = classify_dates([{"a": 1}], None, None)
    assert verdict == INCONCLUSIVE
    assert "no such column" in detail.lower()


def test_c_a_present_but_unparseable_date_column_is_contradicted():
    """Here the column IS there, so "no dates" is a fact about the data."""
    rows = [{"dauer_start": ""}, {"dauer_start": None}]
    verdict, _, _ = classify_dates(rows, "dauer_start", None)
    assert verdict == CONTRADICTED


def test_c_the_cmi_eight_digit_date_is_a_date():
    """CMI writes 20220501 as well as ISO, and reading it as absent would
    report a fully dated index as undated."""
    assert _as_date("20220501") == date(2022, 5, 1)
    assert _as_date("2022-05-01") == date(2022, 5, 1)
    assert _as_date("2022-05-01T00:00:00") == date(2022, 5, 1)
    assert _as_date("") is None
    assert _as_date("nonsense") is None


def test_c_a_fully_dated_index_is_confirmed_and_counts_the_open_rows():
    rows = [
        {"dauer_start": "2022-05-01", "dauer_end": None},
        {"dauer_start": "2018-05-01", "dauer_end": "2022-04-30"},
    ]
    verdict, detail, lines = classify_dates(rows, "dauer_start", "dauer_end")
    assert verdict == CONFIRMED
    assert "1 open-ended" in "\n".join(lines)
    assert "2" in detail


def test_c_exactly_125_people_confirms_the_index_is_current():
    """The chamber's size is the strongest single signal, one level down from
    the federal 200/46 and the cantonal 180."""
    verdict, detail = classify_seat_count(open_rows=128, people=125)
    assert verdict == CONFIRMED
    assert "125" in detail


def test_c_the_verdict_is_on_people_and_the_row_count_is_only_printed():
    """A presiding member can hold one seat and carry two rows. Scoring rows
    would call a correct chamber wrong — the cantonal probe's 186 against 180.
    """
    verdict, detail = classify_seat_count(open_rows=128, people=130)
    assert verdict == CONTRADICTED
    assert "128 rows" in detail


def test_c_no_open_rows_says_nothing_rather_than_saying_no():
    verdict, _ = classify_seat_count(open_rows=0, people=0)
    assert verdict == INCONCLUSIVE


# --- D. the join -------------------------------------------------------------
def test_d_identifier_columns_still_do_not_make_a_join():
    """Run 23's finding, stated for the city: an identifier needs a value on
    BOTH sides, and no Wikidata property holds a Gever key. Having keys of its
    own is not the same as being joinable."""
    verdict, detail = classify_join(["obj_guid", "kontakt_obj_guid"])
    assert verdict == CONTRADICTED
    assert "name-matched" in detail


def test_d_no_identifier_column_at_all_is_a_different_answer():
    verdict, _ = classify_join([])
    assert verdict == INCONCLUSIVE


# --- C. the far-future sentinel ---------------------------------------------
def test_c_the_9999_end_date_means_open_not_ended():
    """`parliament.NULL_DATE` at the other end of the axis.

    Run 35 read all 870 of the chamber's rows as ended and printed "0
    open-ended" one line above a sample of three sitting members. Federally the
    sentinel was SQL Server's `datetime` minimum standing for "no date"; here
    it is the maximum standing for "no end", and reading it as a date reports a
    full chamber as departed — or, in an adapter, files a P582 of 9999-12-31 on
    every sitting member.
    """
    assert end_date("9999-12-31T23:59:59") is None
    assert end_date("9999-12-31") is None
    assert end_date(None) is None
    assert end_date("2022-04-30") == date(2022, 4, 30)


def test_c_the_sentinel_threshold_is_loose_rather_than_an_equality_test():
    """A service that spells its infinity differently means the same by it, and
    no parliament records a mandate ending in the 30th century. Same shape as
    NULL_DATE's 'anything below'."""
    assert end_date("2999-12-31") is None
    assert OPEN_END_FROM.year > date.today().year + 100
    # ...and a date a real mandate could carry is still a date.
    assert end_date("2030-05-01") == date(2030, 5, 1)


def test_c_the_open_rows_are_the_ones_the_sentinel_marks():
    """The verdict this feeds: 'ended' must not count the sentinel."""
    rows = [
        {"s": "2026-05-06", "e": "9999-12-31T23:59:59"},
        {"s": "2026-05-06", "e": "9999-12-31T23:59:59"},
        {"s": "2022-05-01", "e": "2026-05-05"},
    ]
    _, _, lines = classify_dates(rows, "s", "e")
    text = "\n".join(lines)
    assert "1 ended, 2 open-ended" in text
    assert "sentinel" in text
