"""Tests for the Markdown / JSON / HTML rendering."""

import json
from datetime import date

import pytest

from wd_parliament.models import (
    KIND_ADD_MEMBERSHIP,
    KIND_NO_WIKIDATA_ITEM,
    QID_FROM_IDENTIFIER,
    QID_FROM_NAME,
    Body,
    Suggestion,
)
from wd_parliament.report import (
    BodyResult,
    body_filename,
    by_kind,
    group_suggestions,
    render_body_markdown,
    render_html,
    render_index_markdown,
    to_json,
    write_reports,
)

BODY = Body(council="N", label="Swiss National Council", position_qid="Q18510612")
GENERATED = "2026-07-29 10:00 UTC"


def make_suggestion(kind=KIND_ADD_MEMBERSHIP, label="Anna Muster", canton="ZH", **kw):
    defaults = dict(
        kind=kind,
        body=BODY,
        member_label=label,
        detail="Add a statement.",
        person_qid="Q7",
        person_number=1101,
        qid_source=QID_FROM_IDENTIFIER,
        canton=canton,
        parl_group="V",
        payload={"biography": "https://www.parlament.ch/de/biografie/wd/1101"},
    )
    defaults.update(kw)
    return Suggestion(**defaults)


@pytest.fixture
def result():
    return BodyResult(
        body=BODY,
        suggestions=[
            make_suggestion(),
            make_suggestion(label="Beat Zoss", canton="BE"),
            make_suggestion(kind=KIND_NO_WIKIDATA_ITEM, label="Carla Ott",
                            canton=None, person_qid=None, qid_source=None),
        ],
        member_count=200,
        matched_by_identifier=180,
        matched_by_name=15,
        unmatched=5,
        wikidata_open=195,
    )


def test_identifier_hit_rate(result):
    assert result.identifier_hit_rate == pytest.approx(90.0)


def test_identifier_hit_rate_of_an_empty_result():
    assert BodyResult(body=BODY).identifier_hit_rate == 0.0


def test_body_filename(result):
    assert body_filename(BODY) == "N-swiss-national-council.md"


def test_grouping_puts_other_last(result):
    names = [name for name, _ in group_suggestions(result.suggestions, "canton")]
    assert names == ["BE", "ZH", "Other"]


def test_grouping_by_parliamentary_group(result):
    names = [name for name, _ in group_suggestions(result.suggestions, "group")]
    assert names == ["V"]


def test_by_kind_orders_by_priority():
    suggestions = [
        make_suggestion(kind=KIND_NO_WIKIDATA_ITEM),
        make_suggestion(kind=KIND_ADD_MEMBERSHIP),
    ]
    assert [kind for kind, _ in by_kind(suggestions)] == [
        KIND_ADD_MEMBERSHIP,
        KIND_NO_WIKIDATA_ITEM,
    ]


# --- Markdown ---------------------------------------------------------------
def test_body_markdown_reports_the_hit_rate(result):
    md = render_body_markdown(result, GENERATED, "canton")
    assert "# Swiss National Council — Wikidata TODO" in md
    assert "Matched by Swiss parliament ID (P1307): 180 (90.0%)" in md
    assert "## Canton: ZH (1)" in md


def test_body_markdown_links_members_and_biographies(result):
    md = render_body_markdown(result, GENERATED, "canton")
    assert "[Anna Muster](https://www.wikidata.org/wiki/Q7)" in md
    assert "[#1101](https://www.parlament.ch/de/biografie/wd/1101)" in md


def test_name_matched_members_are_flagged_in_markdown():
    r = BodyResult(body=BODY, suggestions=[make_suggestion(qid_source=QID_FROM_NAME)],
                   member_count=1, matched_by_name=1)
    assert "⚠️" in render_body_markdown(r, GENERATED, "canton")


def test_an_empty_body_says_so():
    r = BodyResult(body=BODY, member_count=200, matched_by_identifier=200)
    assert "Nothing to do" in render_body_markdown(r, GENERATED, "canton")


def test_an_error_is_surfaced():
    r = BodyResult(body=BODY, error="WDQS timed out")
    assert "WDQS timed out" in render_body_markdown(r, GENERATED, "canton")


def test_index_markdown(result):
    md = render_index_markdown([result], GENERATED, quickstatements=42)
    assert "**3 suggested edits** across **200 sitting members**" in md
    assert "42 of them are mechanical" in md
    assert "[details](N-swiss-national-council.md)" in md
    assert "## Suggestions by kind" in md


# --- JSON -------------------------------------------------------------------
def test_json_shape(result):
    data = to_json([result], GENERATED, quickstatements=42)
    assert data["total_suggestions"] == 3
    assert data["total_members"] == 200
    assert data["quickstatements"] == 42
    body = data["bodies"][0]
    assert body["council"] == "N"
    assert body["identifier_hit_rate"] == 90.0
    assert body["suggestions"][0]["member"] == "Anna Muster"


def test_json_serialises_dates_in_the_payload():
    r = BodyResult(
        body=BODY,
        suggestions=[make_suggestion(payload={"start": date(2019, 12, 2), "biography": "x"})],
    )
    data = to_json([r], GENERATED)
    payload = data["bodies"][0]["suggestions"][0]["payload"]
    assert payload["start"] == "2019-12-02"
    json.dumps(data)  # must be serialisable


# --- HTML -------------------------------------------------------------------
def test_html_is_self_contained(result):
    html = render_html([result], GENERATED, "canton", quickstatements=42)
    assert html.startswith("<!doctype html>")
    assert "<script" not in html
    assert "http-equiv" not in html
    # No external asset references.
    assert "src=" not in html
    assert "stylesheet" not in html


def test_html_shows_the_totals_and_hit_rate(result):
    html = render_html([result], GENERATED, "canton", quickstatements=42)
    assert "Swiss National Council" in html
    assert "90.0%" in html
    assert ">42<" in html


def test_html_escapes_member_names():
    r = BodyResult(body=BODY, suggestions=[make_suggestion(label="<script>x</script>")],
                   member_count=1)
    html = render_html([r], GENERATED, "canton")
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_html_of_an_empty_run():
    html = render_html([BodyResult(body=BODY)], GENERATED, "canton")
    assert "Nothing to do" in html


# --- writing ----------------------------------------------------------------
def test_write_reports_creates_every_artifact(tmp_path, result):
    reports = tmp_path / "reports"
    docs = tmp_path / "docs"
    write_reports(
        [result], reports, docs, generated_at=GENERATED, group_by="canton",
        quickstatements_text="# nothing\n", quickstatements_count=0,
    )
    assert (reports / "README.md").exists()
    assert (reports / "N-swiss-national-council.md").exists()
    assert (docs / "index.html").exists()
    assert (docs / "suggestions.qs").read_text(encoding="utf-8") == "# nothing\n"
    data = json.loads((docs / "data.json").read_text(encoding="utf-8"))
    assert data["total_suggestions"] == 3


def test_write_reports_without_quickstatements(tmp_path, result):
    docs = tmp_path / "docs"
    write_reports([result], tmp_path / "reports", docs, generated_at=GENERATED)
    assert not (docs / "suggestions.qs").exists()
