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

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_departures import (  # noqa: E402
    CONFIRMED,
    CONTRADICTED,
    INCONCLUSIVE,
    MIN_COMPARABLE,
    NAME_DIFFERENT,
    NAME_EXACT,
    NAME_NEAR,
    NAME_VARIANT,
    Departure,
    chained_end,
    classify_identity,
    classify_leaving_dates,
    classify_reach,
    classify_statement_ambiguity,
    fold_name,
    name_relation,
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
    assert name_relation("Ruth Beispiel", "Beispiel") == NAME_EXACT


def test_a_surname_anywhere_in_the_label_counts():
    """'Badran Jacqueline' on one side, 'Jacqueline Badran' on the other."""
    assert name_relation("Jacqueline Badran", "Badran") == NAME_EXACT


def test_a_different_surname_contradicts():
    assert name_relation("Ruth Beispiel", "Muster") == NAME_DIFFERENT


def test_a_missing_name_on_either_side_is_unknown_not_a_mismatch():
    assert name_relation(None, "Muster") is None
    assert name_relation("Ruth Beispiel", None) is None
    assert name_relation("Ruth Beispiel", "  ") is None


def test_a_q_id_label_is_unknown_rather_than_a_mismatch():
    """An item with no label in any queried language falls back to its Q-ID."""
    assert name_relation("Q42", "Muster") is None


# Every pair below is a real one from run 16, where all 29 "contradictions"
# turned out to be the same person spelt differently. A check that calls these
# wrong people hides the one real mismatch nobody would then go looking for.
@pytest.mark.parametrize(
    "label, history_name",
    [
        ("Ernst Börlin", "Boerlin"),               # umlaut transliterated
        ("Josef Bürgi", "Bürgi-Gretener"),         # married name on one side
        ("Alfred Vonderweid", "von der Weid"),     # particle spacing
        ("Simon Ettlin", "Etlin"),                 # doubled letter
        ("Franz Bünzli", "Bünzli(y)"),             # two spellings in one field
        ("Giuseppe Patocchi", "Pattocchi"),
        ("Jean-Louis Demiéville", "de Demiéville"),
        ("Ulrich Bremi", "Bremi-Forrer"),
        ("Ulrich Meyer", "Meyer-Boller"),
        ("Karl Wilhelm von Grafenried", "von Graffenried"),
        ("Ruth Mascarin", "Mascarin-Bircher"),
        ("Adolphe Travelletti", "Traveletti"),
    ],
)
def test_run_16s_false_alarms_are_recognised_as_variants(label, history_name):
    assert name_relation(label, history_name) == NAME_VARIANT


# Run 18's five survivors, after the variant folding. Every one is the same
# person one character apart: a trailing consonant, a y for an i, an inserted
# n. They are reported, never accepted — see `_near`.
@pytest.mark.parametrize(
    "label, history_name",
    [
        ("Johann Zünd", "Zündt"),                    # trailing consonant
        ("Maurice Despland", "Desplands"),           # trailing s
        ("Camille Desfayes", "Défayes"),             # an s inside
        ("Hans Wunderly-von Muralt", "Wunderli"),    # y for i
        ("Jeannot de Crousaz", "Decrousnaz"),        # an inserted n
    ],
)
def test_run_18s_survivors_are_near_misses_not_wrong_people(label, history_name):
    assert name_relation(label, history_name) == NAME_NEAR


def test_a_near_miss_is_reported_and_blocks_but_never_accepted():
    """The bucket exists to stop the probe calling somebody a different person
    on one character. It must not become a way of agreeing with them."""
    people = agreeing(3) + [
        departure(qid="Q9", label="Johann Zünd", history_name="Zündt")
    ]
    verdict, detail, lines = classify_identity(people)
    assert verdict == INCONCLUSIVE          # not CONFIRMED: still unsettled
    assert verdict != CONTRADICTED          # not a wrong person either
    assert "one character apart" in detail
    text = "\n".join(lines)
    assert "one character apart:            1 (unsettled)" in text
    assert "Zündt" in text


def test_a_genuinely_different_surname_still_contradicts_over_a_near_miss():
    people = agreeing(3) + [
        departure(qid="Q8", label="Johann Zünd", history_name="Zündt"),
        departure(qid="Q9", label="Ruth Beispiel", history_name="Andereleute"),
    ]
    verdict, detail, _ = classify_identity(people)
    assert verdict == CONTRADICTED
    assert "1 of 5" in detail


def test_folding_does_not_merge_two_different_families():
    """The loosening must not reach the case the check exists for."""
    assert name_relation("Ruth Beispiel", "Mascarin") == NAME_DIFFERENT
    assert name_relation("Ernst Börlin", "Bircher") == NAME_DIFFERENT
    assert fold_name("Boerlin") == fold_name("Börlin")
    assert fold_name("Meyer") != fold_name("Mascarin")


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


def test_variants_are_counted_and_printed_rather_than_swallowed():
    """Accepting a spelling difference silently is how a check stops checking."""
    people = agreeing(3) + [
        departure(qid="Q9", label="Ernst Börlin", history_name="Boerlin")
    ]
    verdict, detail, lines = classify_identity(people)
    assert verdict == CONFIRMED
    assert "3 exactly and 1 after folding" in detail
    text = "\n".join(lines)
    assert "same name, spelt differently:   1" in text
    assert "Boerlin" in text


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
    assert "cannot be corrected by QuickStatements" in detail
    # One bad row in a population that otherwise agrees is a per-person
    # problem; the detail must say so rather than condemning the source.
    assert "All 1 are listed above" in detail
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


def test_a_q_id_two_people_claim_is_skipped_not_scored_as_a_disagreement():
    """Run 17's only 'disagreement' was this, and it was not one.

    Alfred Gehrig left in 1971; the probe reported OpenParlData saying
    2014-05-31. The join runs person -> wikidata_id -> Q-ID and nothing makes
    that field unique, so two person records naming one item pool their
    memberships and `chained_end` answers with whichever has the later row.
    That is the probe's arithmetic, not the source's date.
    """
    pooled = departure(
        qid="Q9", source_start="1967-12-04", source_end="1971-11-28",
        opd_end="2014-05-31",
    )
    pooled.opd_ambiguous = True
    pooled.opd_rows = 9
    verdict, _, lines = classify_leaving_dates(agreeing(MIN_COMPARABLE) + [pooled])
    assert verdict == CONFIRMED
    text = "\n".join(lines)
    assert "Q-ID claimed by several people: 1 (skipped)" in text
    assert "2014-05-31" not in text


def test_a_disagreement_names_the_rows_it_came_from():
    """The person id is what would have shown run 17's bug at a glance."""
    odd = departure(qid="Q9", source_end="2019-12-01", opd_end="2019-11-30")
    odd.opd_person_ids = [4242]
    _, _, lines = classify_leaving_dates(agreeing(MIN_COMPARABLE) + [odd])
    assert "person id(s) 4242" in "\n".join(lines)


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


def test_several_statements_for_one_seat_are_excluded_not_a_veto():
    """The existing ambiguous_statement rule already refuses these people.

    Run 16 found 3 of them among 1,969. Treating that as a verdict on the whole
    population would be reading a per-person exclusion as a systemic failure.
    """
    people = agreeing(3) + [departure(qid="Q9", statements=2)]
    verdict, detail, lines = classify_statement_ambiguity(people)
    assert verdict == CONFIRMED
    assert "ambiguous_statement" in detail
    assert "several P39 for the same seat:  1 (excludable)" in "\n".join(lines)


def test_a_statement_about_an_earlier_spell_would_be_closed_wrongly():
    """Both dates real, the span nonsense: P580 2003 with P582 from a 2019 spell."""
    people = agreeing(3) + [
        departure(qid="Q9", statement_start="2003-12-01", source_start="2015-11-30")
    ]
    verdict, detail, lines = classify_statement_ambiguity(people)
    assert verdict == CONTRADICTED
    # Unlike the ambiguous case, is_mechanical cannot see this one, so it is
    # not excludable and does block the population.
    assert "cannot be excluded automatically" in detail
    assert "statement starts 2003-12-01" in "\n".join(lines)


def test_an_undated_statement_is_counted_but_is_not_a_mismatch():
    people = agreeing(3) + [departure(qid="Q9", statement_start=None)]
    verdict, _, lines = classify_statement_ambiguity(people)
    assert verdict == CONFIRMED
    assert "open statement has no P580:     1" in "\n".join(lines)


def test_the_control_group_tells_no_p580_apart_from_no_p580_read():
    """Run 16 returned 1,969 of 1,969 undated. Only a control says which it is."""
    people = [departure(qid=f"Q{i}", statement_start=None) for i in range(3)]
    _, _, lines = classify_statement_ambiguity(
        people, statements_total=6000, statements_with_start=2400
    )
    assert "carrying a P580: 2400 of 6000 (40.0%)" in "\n".join(lines)

    # Omitted, the line is absent rather than a misleading 0 of 0.
    _, _, bare = classify_statement_ambiguity(people)
    assert "control" not in "\n".join(bare)


# --- the overall answer ------------------------------------------------------
def test_the_worst_section_is_the_answer():
    """A bulk apply needs every section clean, so it is the weakest link."""
    assert overall([CONFIRMED, CONFIRMED, CONFIRMED, CONFIRMED]) == CONFIRMED
    assert overall([CONFIRMED, INCONCLUSIVE, CONFIRMED, CONFIRMED]) == INCONCLUSIVE
    assert overall([CONFIRMED, INCONCLUSIVE, CONTRADICTED, CONFIRMED]) == CONTRADICTED
