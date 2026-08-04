"""Tests for the second source: the span walk, and what counts as a disagreement.

This module is the only place two sources meet, and the failure it guards
against is subtle in both directions. Read too loosely, it reports every
re-elected member as a disagreement (run 11 did exactly that, 22 times, by
pooling a member's two chambers). Read too eagerly, it lets a value two sources
dispute be written to Wikidata unreviewed.

The client's fetching is network code and left alone, as elsewhere; the walk,
the join and the comparison are pure and are fed rows directly.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wd_parliament.enrich import (  # noqa: E402
    DISAGREE_ALREADY_LEFT,
    DISAGREE_START,
    DISAGREE_STILL_SITTING,
    Enrichment,
    OpenParlDataEnricher,
    chained_span,
    disagreements,
    spans_from_rows,
)
from wd_parliament.models import Member, SourceSpan  # noqa: E402


def row(begin=None, end=None, person_id=1):
    return {"person_id": person_id, "begin_date": begin, "end_date": end}


def member(start="2015-11-30", leaving=None, active=True, council="NR"):
    m = Member(
        person_number=1101,
        first_name="Anna",
        last_name="Muster",
        council=council,
        active=active,
        date_joining=date.fromisoformat(start) if start else None,
        date_leaving=date.fromisoformat(leaving) if leaving else None,
    )
    return m


def span(start="2015-11-30", end=None, council="NR"):
    return SourceSpan(
        council=council,
        start=date.fromisoformat(start) if start else None,
        end=date.fromisoformat(end) if end else None,
        rows=1,
        person_ids=[42],
    )


# --- the span walk ----------------------------------------------------------
def test_adjacent_terms_chain_into_one_tenure():
    """A legislature boundary is a one-day join, the rule both sides use."""
    rows = [
        row("2015-11-30", "2019-12-01"),
        row("2019-12-02", "2023-12-03"),
        row("2023-12-04", None),
    ]
    assert chained_span(rows) == (date(2015, 11, 30), None)


def test_a_real_break_stops_the_walk_at_the_return():
    rows = [row("2003-12-01", "2007-12-02"), row("2015-11-30", None)]
    assert chained_span(rows) == (date(2015, 11, 30), None)


def test_the_end_is_the_newest_rows_end():
    rows = [row("2011-12-05", "2015-11-29"), row("2015-11-30", "2019-12-01")]
    assert chained_span(rows) == (date(2011, 12, 5), date(2019, 12, 1))


def test_rows_out_of_order_still_chain():
    rows = [row("2019-12-02", None), row("2015-11-30", "2019-12-01")]
    assert chained_span(rows) == (date(2015, 11, 30), None)


def test_an_open_earlier_row_stops_the_chain_rather_than_extending_it():
    """No end means no gap to measure; overlapping rows are not a run."""
    rows = [row("2011-12-05", None), row("2019-12-02", None)]
    assert chained_span(rows) == (date(2019, 12, 2), None)


def test_no_rows_or_no_begin_column_gives_nothing():
    assert chained_span([]) == (None, None)
    # "No such column" and "column full of nulls" mean opposite things.
    assert chained_span([{"person_id": 1, "from": "2015-11-30"}]) == (None, None)


# --- building the spans -----------------------------------------------------
def test_spans_are_keyed_by_seat_not_by_person():
    """A person is not a seat: an NR span says nothing about an SR statement."""
    spans = spans_from_rows([row("2015-11-30", None, 42)], {42: "Q7"}, "nr")
    assert set(spans) == {("Q7", "NR")}
    assert spans[("Q7", "NR")].start == date(2015, 11, 30)


def test_a_person_with_no_link_contributes_no_span():
    """Nothing to compare it against, so recording it would be noise."""
    assert spans_from_rows([row("2015-11-30", None, 42)], {}, "NR") == {}


def test_an_undated_row_contributes_no_span():
    assert spans_from_rows([row(None, None, 42)], {42: "Q7"}, "NR") == {}


def test_the_span_records_which_records_it_came_from():
    rows = [row("2015-11-30", "2019-12-01", 42), row("2019-12-02", None, 43)]
    spans = spans_from_rows(rows, {42: "Q7", 43: "Q7"}, "NR")
    assert spans[("Q7", "NR")].person_ids == [42, 43]
    assert spans[("Q7", "NR")].rows == 2


# --- what counts as a disagreement ------------------------------------------
def test_agreeing_sources_disagree_about_nothing():
    assert disagreements(member(), span()) == []


def test_no_second_source_is_not_a_disagreement():
    """Silence is never a contradiction."""
    assert disagreements(member(), None) == []


def test_a_different_start_is_a_disagreement():
    found = disagreements(member(start="2015-11-30"), span(start="2019-12-02"))
    assert [d.kind for d in found] == [DISAGREE_START]
    assert found[0].ours == date(2015, 11, 30)
    assert found[0].theirs == date(2019, 12, 2)


def test_a_start_only_one_side_knows_is_a_gap_not_a_disagreement():
    assert disagreements(member(start=None), span()) == []
    assert disagreements(member(), span(start=None)) == []


def test_a_closed_seat_against_a_sitting_member_is_a_disagreement():
    found = disagreements(member(), span(end="2023-12-03"))
    assert [d.kind for d in found] == [DISAGREE_STILL_SITTING]
    assert found[0].theirs == date(2023, 12, 3)


def test_a_stale_row_predating_the_tenure_is_not_a_claim_about_today():
    """They left, came back, and the other source only knows the first spell.

    Its row closed before this tenure began, so it is not contradicting the
    claim that the member sits today — it simply has not been updated.
    """
    found = disagreements(member(start="2019-12-02"), span(start="2003-12-01",
                                                           end="2007-12-02"))
    assert DISAGREE_STILL_SITTING not in [d.kind for d in found]


def test_an_open_seat_against_a_departed_member_is_a_disagreement():
    found = disagreements(
        member(leaving="2023-12-03", active=False), span(end=None)
    )
    assert [d.kind for d in found] == [DISAGREE_ALREADY_LEFT]


def test_both_kinds_can_be_reported_at_once():
    found = disagreements(member(start="2015-11-30"),
                          span(start="2019-12-02", end="2023-12-03"))
    assert {d.kind for d in found} == {DISAGREE_START, DISAGREE_STILL_SITTING}


# --- the client's own decisions ---------------------------------------------
class FakeApi:
    """Two chambers, one of them named the way a committee would be."""

    GROUPS = [
        {"id": 1, "name_de": "Büro NR"},
        {"id": 2, "name_de": "Präsidium des Nationalrates"},
        {"id": 3, "name_de": "Nationalrat"},
        {"id": 4, "name_de": "Ständerat"},
    ]

    def __init__(self, persons=None, memberships=None):
        self.persons = persons if persons is not None else [
            {"id": 42, "fullname": "Anna Muster", "wikidata_id": "Q7"},
        ]
        self.memberships = memberships if memberships is not None else {
            3: [row("2015-11-30", None, 42)],
            4: [],
        }
        self.calls = []

    def get_data(self, table, **params):
        self.calls.append((table, params))
        if table == "groups":
            return self.GROUPS
        if table == "persons":
            return self.persons
        if table == "memberships":
            return self.memberships.get(params["group_id"], [])
        raise AssertionError(table)


def enricher(api=None, groups=None):
    return OpenParlDataEnricher(
        body_key="CHE",
        groups=groups or {"NR": "Nationalrat", "SR": "Ständerat"},
        client=api or FakeApi(),
    )


def test_a_chamber_is_matched_by_exact_name_never_as_a_substring():
    """'Büro NR' and 'Präsidium des Nationalrates' both contain the chamber."""
    assert enricher().resolve_group_ids() == {"NR": 3, "SR": 4}


def test_a_group_that_matches_nothing_is_left_out_rather_than_guessed():
    assert enricher(groups={"NR": "Chambre basse"}).resolve_group_ids() == {}


def test_only_the_requested_chambers_are_resolved():
    assert enricher().resolve_group_ids(councils=["NR"]) == {"NR": 3}


def test_the_fetch_produces_spans_but_never_members():
    result = enricher().fetch()
    assert isinstance(result, Enrichment)
    assert result.spans[("Q7", "NR")].start == date(2015, 11, 30)
    assert not hasattr(result, "members")


def test_a_q_id_two_records_claim_is_skipped_and_reported():
    """The Gehrig rule: pooled memberships answer with the other person's row.

    The conflict must reach the report, and the span must never be built — a
    date from two people's rows is worse than no date at all.
    """
    api = FakeApi(
        persons=[
            {"id": 42, "fullname": "Anna Muster", "wikidata_id": "Q7"},
            {"id": 43, "fullname": "Anna Muster (dup)", "wikidata_id": "Q7"},
        ],
        memberships={
            3: [row("1967-12-04", "1971-11-28", 42), row("2011-05-09", None, 43)],
            4: [],
        },
    )
    result = enricher(api).fetch()
    assert result.link_conflicts == {("NR", "Q7"): [42, 43]}
    assert ("Q7", "NR") not in result.spans
    assert result.skipped_conflicted == 1


def test_an_unlinked_person_is_not_a_conflict():
    api = FakeApi(persons=[{"id": 42, "fullname": "Anna Muster"}])
    result = enricher(api).fetch()
    assert result.link_conflicts == {}
    assert result.spans == {}
    assert result.people_with_links == 0
