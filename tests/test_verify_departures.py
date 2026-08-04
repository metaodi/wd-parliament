"""Tests for the pure decisions in ``scripts/verify_departures.py``.

The fetches are network code and are left alone, as elsewhere. What is worth
pinning down is what this probe *concludes*: it decides whether ADD_END_DATE
may be applied mechanically to people the source's current-members table does
not contain, and ADD_END_DATE is a mechanical kind — so a wrong CONFIRMED here
is the argument for removing the two gates in ``diff._departed_suggestion``,
and a P582 written to Wikidata cannot be corrected by QuickStatements
afterwards.

The verdicts are therefore tested the way ``test_compare_tenure_dates.py``
tests its own: every path that could return CONFIRMED, and every trap that must
stop it.
"""

import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_departures import (  # noqa: E402
    CONFIRMED,
    CONTRADICTED,
    INCONCLUSIVE,
    MIN_COMPARABLE,
    Departure,
    chained_end,
    classify_identity,
    classify_leaving_dates,
    classify_reach,
    classify_statement_ambiguity,
    names_agree,
    overall,
    surname_in_history,
)


def departure(
    qid="Q99",
    label="Ruth Beispiel",
    number=3432,
    history_name="Beispiel",
    source_start="2011-12-05",
    source_end="2019-12-01",
    opd_end="2019-12-01",
    statement_start="2011-12-05",
    statements=1,
    opd_rows=None,
):
    def d(value):
        return date.fromisoformat(value) if value else None

    # A dated person necessarily has rows on the OpenParlData side; keeping the
    # two in step here is what stops the fixture describing a state `collect`
    # cannot produce.
    if opd_rows is None:
        opd_rows = 1 if opd_end else 0

    return Departure(
        qid=qid,
        label=label,
        council="NR",
        identifier=str(number) if number else None,
        person_number=number,
        statement_start=d(statement_start),
        statements_for_seat=statements,
        open_statements=1,
        history_name=history_name,
        source_start=d(source_start),
        source_end=d(source_end),
        opd_end=d(opd_end),
        opd_rows=opd_rows,
    )


def agreeing(count):
    return [departure(qid=f"Q{100 + i}", number=3400 + i) for i in range(count)]


def membership(start=None, end=None):
    return {"begin_date": start, "end_date": end}


# --- the end of the current run ---------------------------------------------
def test_the_newest_terms_end_is_the_tenures_end():
    rows = [
        membership("2011-12-05", "2015-11-29"),
        membership("2015-11-30", "2019-12-01"),
    ]
    assert chained_end(rows) == date(2019, 12, 1)


def test_an_open_newest_row_means_no_end_rather_than_a_fallback():
    """'Still sitting per this source' is an answer, not a missing value."""
    rows = [membership("2011-12-05", "2015-11-29"), membership("2015-11-30", None)]
    assert chained_end(rows) is None


def test_rows_out_of_order_still_find_the_newest():
    rows = [membership("2015-11-30", "2019-12-01"), membership("2011-12-05", "2015-11-29")]
    assert chained_end(rows) == date(2019, 12, 1)


def test_no_rows_and_no_columns_give_nothing():
    assert chained_end([]) is None
    # "No such column" and "column full of nulls" mean opposite things.
    assert chained_end([{"date_from": "2011-12-05"}]) is None


# --- the identity corroboration ---------------------------------------------
def test_a_matching_surname_corroborates():
    assert names_agree("Ruth Beispiel", "Beispiel") is True


def test_a_surname_anywhere_in_the_label_counts():
    """'Badran Jacqueline' on one side, 'Jacqueline Badran' on the other."""
    assert names_agree("Jacqueline Badran", "Badran") is True


def test_a_different_surname_contradicts():
    assert names_agree("Ruth Beispiel", "Muster") is False


def test_a_missing_name_on_either_side_is_unknown_not_a_mismatch():
    assert names_agree(None, "Muster") is None
    assert names_agree("Ruth Beispiel", None) is None
    assert names_agree("Ruth Beispiel", "  ") is None


def test_a_q_id_label_is_unknown_rather_than_a_mismatch():
    """An item with no label in any queried language falls back to its Q-ID."""
    assert names_agree("Q42", "Muster") is None


def test_the_surname_comes_from_the_segments_already_fetched():
    class Row:
        last_name = "Bregy"

    segments = {(4230, "NR"): [Row()]}
    assert surname_in_history(segments, 4230, "nr") == "Bregy"
    assert surname_in_history(segments, 9999, "NR") is None
    assert surname_in_history(segments, None, "NR") is None


# --- A. reach ----------------------------------------------------------------
def test_an_empty_population_is_inconclusive_not_agreement():
    """A tidy Wikidata leaves nothing to measure. Good news, but not evidence."""
    verdict, detail, _ = classify_reach([])
    assert verdict == INCONCLUSIVE
    assert "nothing to measure" in detail


def test_reaching_nobody_contradicts():
    unreachable = [departure(number=None, source_start=None, source_end=None)]
    verdict, _, lines = classify_reach(unreachable)
    assert verdict == CONTRADICTED
    assert "no identifier value at all:     1" in "\n".join(lines)


def test_reach_reports_how_many_have_a_date_to_emit():
    people = agreeing(3) + [departure(qid="Q1", number=1, source_end=None, opd_end=None)]
    verdict, detail, lines = classify_reach(people)
    assert verdict == CONFIRMED
    assert "with a closed tenure (a date):  3" in "\n".join(lines)
    assert "75.0%" in detail


# --- B. identity -------------------------------------------------------------
def test_matching_surnames_corroborate_the_identifier():
    verdict, detail, _ = classify_identity(agreeing(4))
    assert verdict == CONFIRMED
    assert "not proof" in detail


def test_one_wrong_person_blocks_everything():
    """The worst finding available: the date would go on another person's item."""
    people = agreeing(9) + [departure(qid="Q9", label="Ruth Beispiel",
                                      history_name="Andereleute")]
    verdict, detail, lines = classify_identity(people)
    assert verdict == CONTRADICTED
    assert "wrong person's item" in detail
    assert "Andereleute" in "\n".join(lines)


def test_unknown_names_are_neither_agreement_nor_contradiction():
    verdict, detail, _ = classify_identity(agreeing(3) + [departure(qid="Q9",
                                                                   label="Q9")])
    assert verdict == CONFIRMED
    assert "1 unknown" in detail


def test_nobody_reachable_leaves_no_identity_to_judge():
    verdict, _, _ = classify_identity([departure(number=None, source_start=None)])
    assert verdict == INCONCLUSIVE


# --- C. the leaving dates ----------------------------------------------------
def test_full_agreement_is_the_evidence_the_gates_wait_for():
    verdict, detail, _ = classify_leaving_dates(agreeing(MIN_COMPARABLE))
    assert verdict == CONFIRMED
    assert "report-only gates" in detail
    # Even a CONFIRMED here does not license the apply on its own.
    assert "section D" in detail


def test_one_disagreement_blocks_the_bulk_apply():
    people = agreeing(MIN_COMPARABLE) + [
        departure(qid="Q9", source_end="2019-12-01", opd_end="2019-11-30")
    ]
    verdict, detail, lines = classify_leaving_dates(people)
    assert verdict == CONTRADICTED
    assert "not correctable by QuickStatements" in detail
    assert "source 2019-12-01, OpenParlData 2019-11-30" in "\n".join(lines)


def test_a_small_population_cannot_license_an_apply():
    verdict, detail, _ = classify_leaving_dates(agreeing(MIN_COMPARABLE - 1))
    assert verdict == INCONCLUSIVE
    assert "bulk apply" in detail


def test_the_sentinel_date_is_the_0b_failure_and_says_so():
    """1753-01-01 means 'no date'. It must never reach a P582."""
    people = agreeing(MIN_COMPARABLE) + [
        departure(qid="Q9", source_start="1753-01-01", source_end="1753-01-01",
                  opd_end="1753-01-01")
    ]
    verdict, detail, _ = classify_leaving_dates(people)
    assert verdict == CONTRADICTED
    assert "sentinel" in detail


def test_a_tenure_that_ends_before_it_starts_is_caught():
    people = agreeing(MIN_COMPARABLE) + [
        departure(qid="Q9", source_start="2019-12-01", source_end="2011-12-05",
                  opd_end="2011-12-05")
    ]
    verdict, detail, _ = classify_leaving_dates(people)
    assert verdict == CONTRADICTED
    assert "end before they start" in detail


def test_undated_people_are_not_compared():
    people = agreeing(MIN_COMPARABLE) + [departure(qid="Q9", opd_end=None)]
    verdict, _, lines = classify_leaving_dates(people)
    assert verdict == CONFIRMED
    assert "also dated by OpenParlData:     5" in "\n".join(lines)


def test_not_in_openparldata_is_counted_apart_from_still_open_there():
    """Opposite findings: a gap in the join, versus 'they have not left'."""
    unjoined = departure(qid="Q8", opd_end=None)
    open_there = departure(qid="Q9", opd_end=None, opd_rows=2)
    _, _, lines = classify_leaving_dates(agreeing(MIN_COMPARABLE) + [unjoined, open_there])
    text = "\n".join(lines)
    assert "not in OpenParlData at all:     1" in text
    assert "OpenParlData still shows open:  1" in text


# --- D. which statement would it close? --------------------------------------
def test_a_single_matching_statement_can_be_targeted():
    verdict, _, _ = classify_statement_ambiguity(agreeing(3))
    assert verdict == CONFIRMED


def test_several_statements_for_one_seat_cannot_be_targeted():
    """QuickStatements matches on property + main value, which names neither."""
    people = agreeing(3) + [departure(qid="Q9", statements=2)]
    verdict, detail, _ = classify_statement_ambiguity(people)
    assert verdict == CONTRADICTED
    assert "ambiguous_statement" in detail


def test_a_statement_about_an_earlier_spell_would_be_closed_wrongly():
    """Both dates real, the span nonsense: P580 2003 with P582 from a 2019 spell."""
    people = agreeing(3) + [
        departure(qid="Q9", statement_start="2003-12-01", source_start="2015-11-30")
    ]
    verdict, detail, lines = classify_statement_ambiguity(people)
    assert verdict == CONTRADICTED
    assert "means the wrong one" in detail
    assert "statement starts 2003-12-01" in "\n".join(lines)


def test_an_undated_statement_is_counted_but_is_not_a_mismatch():
    people = agreeing(3) + [departure(qid="Q9", statement_start=None)]
    verdict, _, lines = classify_statement_ambiguity(people)
    assert verdict == CONFIRMED
    assert "open statement has no P580:     1" in "\n".join(lines)


# --- the overall answer ------------------------------------------------------
def test_the_worst_section_is_the_answer():
    """A bulk apply needs every section clean, so it is the weakest link."""
    assert overall([CONFIRMED, CONFIRMED, CONFIRMED, CONFIRMED]) == CONFIRMED
    assert overall([CONFIRMED, INCONCLUSIVE, CONFIRMED, CONFIRMED]) == INCONCLUSIVE
    assert overall([CONFIRMED, INCONCLUSIVE, CONTRADICTED, CONFIRMED]) == CONTRADICTED
