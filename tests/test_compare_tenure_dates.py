"""Tests for the pure decisions in ``scripts/compare_tenure_dates.py``.

The three fetches are network code and are left alone, as elsewhere. What is
worth pinning down is the verdict: this script decides whether P580 may be
emitted from ``MemberCouncil.DateJoining``, and both kinds that emit it are
mechanical, so a wrong CONFIRMED here writes wrong dates to Wikidata at scale.
"""

import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from compare_tenure_dates import (  # noqa: E402
    CONFIRMED,
    CONTRADICTED,
    INCONCLUSIVE,
    MIN_JOINED,
    Pair,
    classify_start_dates,
    current_start,
)


def pair(number=1101, odata="2023-12-04", opd="2023-12-04", council="NR"):
    return Pair(
        person_number=number,
        name=f"Member {number}",
        qid=f"Q{number}",
        council=council,
        odata_start=date.fromisoformat(odata) if odata else None,
        opd_start=date.fromisoformat(opd) if opd else None,
    )


def agreeing(count, start="2023-12-04"):
    return [pair(1100 + i, start, start) for i in range(count)]


def membership(start=None, end=None):
    return {"begin_date": start, "end_date": end}


# --- which tenure is the current one? ---------------------------------------
def test_the_open_ended_row_is_the_current_tenure():
    """A member's seat history has many rows; only one is still being served."""
    rows = [
        membership("2015-11-30", "2019-12-01"),
        membership("2019-12-02", "2023-12-03"),
        membership("2023-12-04", None),
    ]
    assert current_start(rows) == date(2023, 12, 4)


def test_the_latest_open_row_wins_when_several_are_open():
    rows = [membership("2019-12-02", None), membership("2023-12-04", None)]
    assert current_start(rows) == date(2023, 12, 4)


def test_the_latest_start_is_used_when_every_row_is_closed():
    rows = [
        membership("2015-11-30", "2019-12-01"),
        membership("2019-12-02", "2023-12-03"),
    ]
    assert current_start(rows) == date(2019, 12, 2)


def test_no_rows_and_no_dates_give_nothing():
    assert current_start([]) is None
    assert current_start([membership(None, None)]) is None


def test_an_absent_begin_column_gives_nothing_rather_than_a_wrong_date():
    """Same rule as classify_seat_memberships: never read a column blind."""
    assert current_start([{"type_harmonized": "council_legislative"}]) is None


def test_a_legacy_column_name_is_still_understood():
    assert current_start([{"date_start": "2019-12-02"}]) == date(2019, 12, 2)


# --- the verdict ------------------------------------------------------------
def test_full_agreement_answers_step_0c():
    verdict, detail, _ = classify_start_dates(agreeing(MIN_JOINED))
    assert verdict == CONFIRMED
    assert "DateJoining is a tenure start" in detail
    assert "Step 0c is\nanswered" in detail or "Step 0c is answered" in detail


def test_every_disagreement_being_a_1_january_names_the_predicted_failure():
    """The specific shape 0c is about: a reporting-year start, not a tenure."""
    pairs = agreeing(MIN_JOINED) + [
        pair(2001, "2026-01-01", "2023-12-04"),
        pair(2002, "2026-01-01", "2019-12-02"),
    ]
    verdict, detail, lines = classify_start_dates(pairs)
    assert verdict == CONTRADICTED
    assert "reporting-year start" in detail
    assert "take P580 from" in detail
    assert any("<- 1 Jan" in ln for ln in lines)


def test_a_uniformly_later_odata_date_names_the_two_readings():
    """What run 9 actually found: 11 of 244 differ, none a 1 January, every
    OData date later than the same OpenParlData legislature start."""
    pairs = agreeing(MIN_JOINED) + [
        pair(2001, "2025-09-16", "2023-12-04"),
        pair(2002, "2023-12-11", "2023-12-04"),
    ]
    verdict, detail, _ = classify_start_dates(pairs)
    assert verdict == CONTRADICTED
    assert "not the reporting-year failure" in detail
    assert "swearing-in" in detail and "segment" in detail
    assert "MemberCouncilHistory" in detail
    assert "2023-12-04" in detail  # the shared date is named


def test_a_shared_opd_date_is_only_claimed_when_it_is_shared():
    pairs = agreeing(MIN_JOINED) + [
        pair(2001, "2025-09-16", "2023-12-04"),
        pair(2002, "2021-03-05", "2019-12-02"),
    ]
    _, detail, _ = classify_start_dates(pairs)
    assert "legislature start" not in detail


def test_a_mixed_disagreement_says_so_rather_than_overclaiming():
    """Neither shape dominates, so claim neither."""
    pairs = agreeing(MIN_JOINED) + [
        pair(2001, "2026-01-01", "2023-12-04"),
        pair(2002, "2023-12-01", "2023-12-04"),
    ]
    verdict, detail, _ = classify_start_dates(pairs)
    assert verdict == CONTRADICTED
    assert "no single shape" in detail
    assert "read the rows above" in detail


def test_too_few_joined_members_is_inconclusive_not_agreement():
    """The dangerous default: a tiny sample must never read as CONFIRMED."""
    verdict, detail, _ = classify_start_dates(agreeing(MIN_JOINED - 1))
    assert verdict == INCONCLUSIVE
    assert "a finding about the join, not" in detail


def test_members_missing_a_date_on_either_side_are_not_compared():
    pairs = agreeing(MIN_JOINED) + [pair(2001, None, "2023-12-04"),
                                    pair(2002, "2023-12-04", None)]
    verdict, _, lines = classify_start_dates(pairs)
    assert verdict == CONFIRMED  # the two undated ones cannot disagree
    assert any("of which both sources dated:    10" in ln for ln in lines)


def test_unjoined_members_are_reported_but_do_not_change_the_verdict():
    """Failing to match somebody says nothing about the dates of those matched."""
    verdict, _, lines = classify_start_dates(agreeing(MIN_JOINED), unjoined=57)
    assert verdict == CONFIRMED
    assert any("not joined (skipped, not guessed): 57" in ln for ln in lines)


def test_the_agreement_share_is_reported():
    pairs = agreeing(MIN_JOINED) + [pair(2001, "2026-01-01", "2023-12-04")]
    _, _, lines = classify_start_dates(pairs)
    assert any("agree exactly:                    10 (90.9%)" in ln for ln in lines)
