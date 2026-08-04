"""Tests for the QuickStatements renderer, above all its safety rule."""

from datetime import date

import pytest

from wd_parliament.models import (
    KIND_ADD_END_DATE,
    KIND_ADD_IDENTIFIER,
    KIND_ADD_MEMBERSHIP,
    KIND_ADD_QUALIFIER,
    KIND_ADD_START_DATE,
    KIND_ADD_TERM,
    KIND_FIX_START_DATE,
    KIND_NO_WIKIDATA_ITEM,
    KIND_REVIEW_ENDED,
    KIND_REVIEW_PARTY,
    MODEL_PERIOD,
    MODEL_TENURE,
    QID_FROM_IDENTIFIER,
    QID_FROM_NAME,
    Body,
    Suggestion,
)
from wd_parliament.quickstatements import (
    format_date,
    is_mechanical,
    render,
    render_file,
    render_suggestion,
)

POSITION = "Q18510612"
BODY = Body(council="N", label="Swiss National Council", position_qid=POSITION)
BIO = "https://www.parlament.ch/de/biografie/wd/1108"
RETRIEVED = date(2026, 7, 29)


def make_suggestion(kind=KIND_ADD_MEMBERSHIP, source=QID_FROM_IDENTIFIER, **payload):
    base = {"position": POSITION, "biography": BIO}
    base.update(payload)
    return Suggestion(
        kind=kind,
        body=BODY,
        member_label="Guy Parmelin",
        detail="",
        person_qid="Q121160",
        person_number=1108,
        qid_source=source,
        payload=base,
    )


# --- the safety rule --------------------------------------------------------
def test_a_name_matched_member_is_never_mechanical():
    """The rule the whole file exists to enforce."""
    s = make_suggestion(source=QID_FROM_NAME, start=date(2015, 12, 7))
    assert is_mechanical(s, MODEL_TENURE) is False
    assert render_suggestion(s, RETRIEVED, MODEL_TENURE) is None


def test_an_identifier_matched_member_is_mechanical():
    s = make_suggestion(start=date(2015, 12, 7))
    assert is_mechanical(s, MODEL_TENURE) is True


def test_a_suggestion_without_a_qid_is_never_mechanical():
    s = make_suggestion(start=date(2015, 12, 7))
    s.person_qid = None
    assert is_mechanical(s, MODEL_TENURE) is False


@pytest.mark.parametrize(
    "kind",
    [KIND_FIX_START_DATE, KIND_REVIEW_ENDED, KIND_REVIEW_PARTY,
     KIND_NO_WIKIDATA_ITEM, KIND_ADD_IDENTIFIER],
)
def test_review_and_correction_kinds_are_never_mechanical(kind):
    """QuickStatements can only add, so 'this value is wrong' cannot be applied."""
    s = make_suggestion(kind=kind, start=date(2015, 12, 7), end=date(2019, 12, 1),
                        terms=["Q51"], district="Q11943")
    assert is_mechanical(s, MODEL_TENURE) is False


@pytest.mark.parametrize(
    "kind,payload",
    [
        (KIND_ADD_MEMBERSHIP, {}),                       # no start date
        (KIND_ADD_START_DATE, {}),                       # no start date
        (KIND_ADD_END_DATE, {}),                         # no end date
        (KIND_ADD_TERM, {}),                             # no terms
        (KIND_ADD_QUALIFIER, {}),                        # no district or group
    ],
)
def test_an_incomplete_payload_is_never_mechanical(kind, payload):
    s = make_suggestion(kind=kind, **payload)
    assert is_mechanical(s, MODEL_TENURE) is False


def test_a_missing_biography_reference_is_never_mechanical():
    s = make_suggestion(start=date(2015, 12, 7))
    s.payload.pop("biography")
    assert is_mechanical(s, MODEL_TENURE) is False


def test_a_missing_position_is_never_mechanical():
    s = make_suggestion(start=date(2015, 12, 7))
    s.payload.pop("position")
    assert is_mechanical(s, MODEL_TENURE) is False


def test_an_unverified_identifier_is_never_mechanical():
    """Provenance is only half the claim, and this is the other half.

    Wikidata asserting the property is what ``qid_source`` records. Whether its
    *value* is the source's person id is what a probe measures, and until one
    has, an exact match can be exactly wrong — correct data written onto
    somebody else's item, which no later run can detect. ``diff`` stamps this
    on every suggestion for such a member.
    """
    s = make_suggestion(start=date(2015, 12, 7), identifier_unverified=True)
    assert is_mechanical(s, MODEL_TENURE) is False
    assert render_suggestion(s, RETRIEVED, MODEL_TENURE) is None


def test_the_reverse_walk_end_date_is_not_mechanical():
    """No leaving date is known for someone outside the current-members set."""
    s = make_suggestion(kind=KIND_ADD_END_DATE, statement_id="S1")
    assert is_mechanical(s, MODEL_TENURE) is False


# --- the period-model ambiguity guard ---------------------------------------
@pytest.mark.parametrize(
    "kind,payload",
    [
        (KIND_ADD_START_DATE, {"start": date(2019, 12, 2)}),
        (KIND_ADD_END_DATE, {"end": date(2023, 12, 3)}),
        (KIND_ADD_QUALIFIER, {"district": "Q11943"}),
    ],
)
def test_qualifier_only_commands_need_a_term_under_the_period_model(kind, payload):
    """Without P2937 the command could land on the wrong same-seat statement."""
    without = make_suggestion(kind=kind, **payload)
    assert is_mechanical(without, MODEL_PERIOD) is False
    # The same suggestion is fine under the tenure model, where there is only
    # ever one statement per seat.
    assert is_mechanical(without, MODEL_TENURE) is True
    # ...and fine under the period model once the term pins it down.
    with_term = make_suggestion(kind=kind, terms=["Q51"], **payload)
    assert is_mechanical(with_term, MODEL_PERIOD) is True


@pytest.mark.parametrize(
    "kind,payload",
    [
        (KIND_ADD_START_DATE, {"start": date(2019, 12, 2)}),
        (KIND_ADD_END_DATE, {"end": date(2023, 12, 3)}),
        (KIND_ADD_TERM, {"terms": ["Q51"]}),
        (KIND_ADD_QUALIFIER, {"district": "Q11943"}),
    ],
)
def test_several_same_seat_statements_block_qualifier_only_commands(kind, payload):
    """A member who left and returned: property + value names two statements."""
    ambiguous = make_suggestion(kind=kind, ambiguous_statement=True, **payload)
    assert is_mechanical(ambiguous, MODEL_TENURE) is False
    assert is_mechanical(ambiguous, MODEL_PERIOD) is False
    # The same suggestion is fine when the member holds only one.
    assert is_mechanical(make_suggestion(kind=kind, **payload), MODEL_TENURE) is True


def test_ambiguity_does_not_block_creating_a_new_statement():
    """ADD_MEMBERSHIP creates a statement, so it names nothing existing."""
    s = make_suggestion(
        kind=KIND_ADD_MEMBERSHIP, start=date(2019, 12, 2), ambiguous_statement=True
    )
    assert is_mechanical(s, MODEL_TENURE) is True


def test_add_membership_does_not_need_a_term_under_the_period_model():
    """Creating a statement is unambiguous; only qualifier-adds are not."""
    s = make_suggestion(kind=KIND_ADD_MEMBERSHIP, start=date(2019, 12, 2))
    assert is_mechanical(s, MODEL_PERIOD) is True


# --- rendering --------------------------------------------------------------
def test_format_date_uses_day_precision():
    assert format_date(date(2015, 12, 7)) == "+2015-12-07T00:00:00Z/11"


def test_add_membership_line_matches_the_documented_shape():
    s = make_suggestion(
        kind=KIND_ADD_MEMBERSHIP, start=date(2015, 12, 7), district="Q11943"
    )
    line = render_suggestion(s, RETRIEVED, MODEL_TENURE).line
    assert line == (
        "Q121160|P39|Q18510612|P580|+2015-12-07T00:00:00Z/11|P768|Q11943"
        '|S854|"https://www.parlament.ch/de/biografie/wd/1108"'
        "|S813|+2026-07-29T00:00:00Z/11"
    )


def test_add_membership_renders_every_known_qualifier():
    s = make_suggestion(
        kind=KIND_ADD_MEMBERSHIP,
        start=date(2019, 12, 2),
        end=date(2023, 12, 3),
        district="Q11943",
        group="Q123",
        terms=["Q51"],
    )
    line = render_suggestion(s, RETRIEVED, MODEL_TENURE).line
    assert "|P580|+2019-12-02T00:00:00Z/11|" in line
    assert "|P582|+2023-12-03T00:00:00Z/11|" in line
    assert "|P768|Q11943|" in line
    assert "|P4100|Q123|" in line
    assert "|P2937|Q51|" in line


def test_every_line_carries_both_reference_parts():
    s = make_suggestion(kind=KIND_ADD_MEMBERSHIP, start=date(2015, 12, 7))
    line = render_suggestion(s, RETRIEVED, MODEL_TENURE).line
    assert line.endswith(
        '|S854|"https://www.parlament.ch/de/biografie/wd/1108"'
        "|S813|+2026-07-29T00:00:00Z/11"
    )


def test_add_term_line():
    s = make_suggestion(kind=KIND_ADD_TERM, terms=["Q51", "Q52"])
    line = render_suggestion(s, RETRIEVED, MODEL_TENURE).line
    assert line.startswith("Q121160|P39|Q18510612|P2937|Q51|P2937|Q52|S854|")


def test_add_start_date_line_pins_the_statement_with_its_term():
    s = make_suggestion(kind=KIND_ADD_START_DATE, start=date(2019, 12, 2), terms=["Q51"])
    line = render_suggestion(s, RETRIEVED, MODEL_PERIOD).line
    assert line.startswith(
        "Q121160|P39|Q18510612|P2937|Q51|P580|+2019-12-02T00:00:00Z/11|"
    )


def test_add_qualifier_line():
    s = make_suggestion(kind=KIND_ADD_QUALIFIER, district="Q11943", group="Q123", terms=["Q52"])
    line = render_suggestion(s, RETRIEVED, MODEL_PERIOD).line
    assert "|P2937|Q52|P768|Q11943|P4100|Q123|" in line


def test_render_skips_non_mechanical_suggestions():
    suggestions = [
        make_suggestion(kind=KIND_ADD_MEMBERSHIP, start=date(2015, 12, 7)),
        make_suggestion(kind=KIND_REVIEW_PARTY, party="Q1"),
        make_suggestion(source=QID_FROM_NAME, start=date(2015, 12, 7)),
    ]
    rendered = render(suggestions, RETRIEVED, MODEL_TENURE)
    assert len(rendered) == 1
    assert rendered[0].suggestion_kind == KIND_ADD_MEMBERSHIP


def test_render_preserves_provenance_on_the_rendered_statement():
    s = make_suggestion(kind=KIND_ADD_MEMBERSHIP, start=date(2015, 12, 7))
    statement = render(s and [s], RETRIEVED, MODEL_TENURE)[0]
    assert statement.person_qid == "Q121160"
    assert statement.member_label == "Guy Parmelin"


# --- the file ---------------------------------------------------------------
def test_render_file_has_a_header_and_the_counts():
    suggestions = [
        make_suggestion(kind=KIND_ADD_MEMBERSHIP, start=date(2015, 12, 7)),
        make_suggestion(source=QID_FROM_NAME, start=date(2015, 12, 7)),
    ]
    text = render_file(suggestions, RETRIEVED, MODEL_TENURE, generated_at="2026-07-29 10:00 UTC")
    assert "# wd-parliament — QuickStatements V1" in text
    assert "# Statement model: tenure" in text
    assert "# 1 of 2 suggestions are mechanical." in text
    assert text.rstrip().endswith("+2026-07-29T00:00:00Z/11")


def test_render_file_with_nothing_mechanical_is_header_only():
    text = render_file([make_suggestion(source=QID_FROM_NAME)], RETRIEVED, MODEL_TENURE)
    assert [ln for ln in text.splitlines() if ln and not ln.startswith("#")] == []


def test_render_file_of_an_empty_run():
    text = render_file([], RETRIEVED, MODEL_TENURE)
    assert "# 0 of 0 suggestions are mechanical." in text
