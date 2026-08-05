"""End-to-end run over the fixtures, with the two network clients faked.

This is the test that would catch a wiring mistake between stages: real
fixture rows go in at ``ParliamentClient``, and reports plus a QuickStatements
file come out the other end.
"""

import json
from datetime import date

import pytest

from wd_parliament.app import process
from wd_parliament.config import Config
from wd_parliament.models import (
    KIND_ADD_END_DATE,
    KIND_ADD_MEMBERSHIP,
    KIND_DUPLICATE_SOURCE_LINK,
    KIND_NO_WIKIDATA_ITEM,
    KIND_SOURCES_DISAGREE,
    MODEL_TENURE,
    QID_FROM_IDENTIFIER,
    Body,
    PositionStatement,
    WikidataPerson,
)
from wd_parliament.parliament import (
    members_from_rows,
    periods_from_rows,
    segments_from_rows,
    tenures_from_segments,
)
from wd_parliament.quickstatements import render_file
from wd_parliament.report import write_reports

NATIONAL = Body(council="NR", label="Swiss National Council", position_qid="Q18510612")
STATES = Body(council="SR", label="Swiss Council of States", position_qid="Q18510613")


class FakeParliament:
    """Serves the committed fixtures instead of calling parlament.ch."""

    def __init__(self, member_rows, period_rows, history_rows=None):
        self._member_rows = member_rows
        self._period_rows = period_rows
        self._history_rows = history_rows or []

    def get_members(self, councils=None, active_only=True, table=None):
        return members_from_rows(
            self._member_rows, councils=councils, active_only=active_only
        )

    def get_periods(self):
        return periods_from_rows(self._period_rows)

    def get_member_segments(self, councils=None):
        return segments_from_rows(self._history_rows, councils=councils)

    def get_tenures(self, councils=None):
        return tenures_from_segments(self.get_member_segments(councils=councils))


class HistorylessParliament(FakeParliament):
    """No MemberCouncilHistory at all — the degradation path."""

    def get_member_segments(self, councils=None):
        raise RuntimeError("MemberCouncilHistory unavailable")


class FakeWikidata:
    def __init__(self, people=None, matches=None):
        self._people = people or {}
        self._matches = matches or {}

    def get_position_holders(self, position_qids, language="de", identifier_property="P1307",
                             person_data_properties=(), extra_identifiers=()):
        self.person_data_properties = list(person_data_properties)
        self.extra_identifiers = list(extra_identifiers)
        return self._people

    def search_people(self, names, position_qids, language="de", identifier_property="P1307"):
        return self._matches


@pytest.fixture
def config():
    return Config(
        statement_model=MODEL_TENURE,
        bodies=[NATIONAL, STATES],
        cantons={"ZH": "Q11943", "TI": "Q12724", "SG": "Q12746", "BE": "Q11911"},
    )


@pytest.fixture
def pipeline(member_rows, period_rows, config):
    def run(people=None, matches=None):
        return process(
            config,
            FakeParliament(member_rows, period_rows),
            FakeWikidata(people, matches),
        )

    return run


def test_every_chamber_gets_a_result(pipeline):
    results = pipeline()
    assert [r.body.council for r in results] == ["NR", "SR"]


def test_only_sitting_members_are_processed(pipeline):
    results = pipeline()
    # 10 people in the fixture, 2 of them inactive.
    assert sum(r.member_count for r in results) == 8


def test_unmatched_members_are_reported_not_dropped(pipeline):
    results = pipeline()
    national = results[0]
    assert national.unmatched == national.member_count
    assert all(s.kind == KIND_NO_WIKIDATA_ITEM for s in national.suggestions)


def test_the_configured_person_data_properties_reach_the_query(member_rows, period_rows, config):
    """The config decides what is asked for, and an empty list asks nothing.

    The properties travel all the way to the SPARQL builder, so a config that
    turns the personal-data checks off does not pay for the extra query.
    """
    config.person_data = ["P19", "P1971"]
    wikidata = FakeWikidata()
    process(config, FakeParliament(member_rows, period_rows), wikidata)
    assert wikidata.person_data_properties == ["P19", "P1971"]

    config.person_data = []
    wikidata = FakeWikidata()
    process(config, FakeParliament(member_rows, period_rows), wikidata)
    assert wikidata.person_data_properties == []


def test_the_identifier_join_drives_the_hit_rate(pipeline):
    """A P1307 on every item takes the hit rate to 100% and clears the unmatched."""
    people = {
        f"Q{n}": WikidataPerson(qid=f"Q{n}", label=f"Member {n}", parliament_id=str(n))
        for n in (1101, 1102, 1103, 1104, 1105, 1108, 1109, 1110)
    }
    results = pipeline(people)
    assert sum(r.unmatched for r in results) == 0
    assert sum(r.matched_by_identifier for r in results) == 8
    for r in results:
        assert r.identifier_hit_rate == pytest.approx(100.0)


def test_a_matched_member_gets_an_add_membership(pipeline):
    people = {
        "Q1101": WikidataPerson(qid="Q1101", label="Andrea Bachmann", parliament_id="1101")
    }
    results = pipeline(people)
    national = results[0]
    for_member = [s for s in national.suggestions if s.person_qid == "Q1101"]
    assert [s.kind for s in for_member] == [KIND_ADD_MEMBERSHIP]
    assert for_member[0].qid_source == QID_FROM_IDENTIFIER
    assert for_member[0].payload["start"] == date(2015, 11, 30)
    assert for_member[0].payload["district"] == "Q11943"


def test_a_complete_statement_produces_nothing_for_that_member(pipeline):
    people = {
        "Q1101": WikidataPerson(
            qid="Q1101",
            label="Andrea Bachmann",
            parliament_id="1101",
            statements=[
                PositionStatement(
                    person_qid="Q1101",
                    statement_id="S1",
                    position_qid="Q18510612",
                    start=date(2015, 11, 30),
                    districts=["Q11943"],
                )
            ],
        )
    }
    results = pipeline(people)
    assert [s for s in results[0].suggestions if s.person_qid == "Q1101"] == []


def test_members_are_routed_to_the_right_chamber(pipeline):
    people = {
        "Q1104": WikidataPerson(qid="Q1104", label="Daniel Egger", parliament_id="1104")
    }
    results = pipeline(people)
    states = next(r for r in results if r.body.council == "SR")
    assert any(s.person_qid == "Q1104" for s in states.suggestions)
    national = next(r for r in results if r.body.council == "NR")
    assert not any(s.person_qid == "Q1104" for s in national.suggestions)


def test_a_broken_source_read_fails_the_run_instead_of_reporting(
    period_rows, config
):
    """The 2026-07-29 failure, from the top.

    parlament.ch returned nothing and the run still produced 2,234 confident
    'this member has left' suggestions and committed them. A run that read no
    members is a broken read, and it must stop before anything is written.
    """
    people = {
        f"Q{n}": WikidataPerson(
            qid=f"Q{n}",
            label=f"Seat holder {n}",
            statements=[
                PositionStatement(
                    person_qid=f"Q{n}", statement_id=f"S{n}",
                    position_qid="Q18510612", start=date(1900, 1, 1),
                )
            ],
        )
        for n in range(10)
    }
    with pytest.raises(RuntimeError, match="no sitting members"):
        process(config, FakeParliament([], period_rows), FakeWikidata(people))


def test_a_sitting_member_is_never_given_an_end_date(pipeline, config):
    """The null-date sentinel, from the top.

    parlament.ch spells "still sitting" as ``DateLeaving = 1753-01-01``. Taken
    literally that is a leaving date, so every sitting member with an open P39
    would draw an ADD_END_DATE — and ADD_END_DATE is mechanical, so a P582 of
    1753-01-01 would be written to Wikidata for most of the chamber.
    """
    people = {
        f"Q{n}": WikidataPerson(
            qid=f"Q{n}",
            label=f"Member {n}",
            parliament_id=str(n),
            statements=[
                PositionStatement(
                    person_qid=f"Q{n}",
                    statement_id=f"S{n}",
                    position_qid="Q18510612",
                    start=date(2015, 11, 30),
                )
            ],
        )
        for n in (1101, 1102, 1103, 1108)
    }
    results = pipeline(people)
    all_suggestions = [s for r in results for s in r.suggestions]

    assert not any(s.kind == KIND_ADD_END_DATE for s in all_suggestions)
    qs = render_file(all_suggestions, date(2026, 7, 29), config.statement_model)
    assert "P582" not in qs
    assert "1753" not in qs


def test_a_limit_caps_each_chamber(member_rows, period_rows, config):
    results = process(
        config, FakeParliament(member_rows, period_rows), FakeWikidata(), limit=2
    )
    assert all(r.member_count <= 2 for r in results)


def test_a_failing_chamber_records_the_error_instead_of_aborting(
    member_rows, period_rows, config
):
    class Landmine(WikidataPerson):
        """Resolves fine, then blows up once the diff inspects it."""

        def statements_for(self, position_qid):
            raise RuntimeError("boom")

    class Exploding(FakeWikidata):
        def get_position_holders(self, position_qids, language="de", identifier_property="P1307",
                                 person_data_properties=(), extra_identifiers=()):
            return {"Q1": Landmine(qid="Q1", label="Landmine")}

    results = process(config, FakeParliament(member_rows, period_rows), Exploding())
    assert len(results) == 2
    assert all(r.error == "boom" for r in results)
    assert all(r.suggestions == [] for r in results)


# --- the whole way through to files -----------------------------------------
def test_a_full_run_writes_every_artifact(tmp_path, pipeline, config):
    people = {
        "Q1101": WikidataPerson(qid="Q1101", label="Andrea Bachmann", parliament_id="1101")
    }
    results = pipeline(people)
    all_suggestions = [s for r in results for s in r.suggestions]
    qs_text = render_file(
        all_suggestions, date(2026, 7, 29), config.statement_model, "2026-07-29 10:00 UTC"
    )

    reports, docs = tmp_path / "reports", tmp_path / "docs"
    write_reports(
        results, reports, docs,
        generated_at="2026-07-29 10:00 UTC",
        group_by=config.group_by,
        quickstatements_text=qs_text,
        quickstatements_count=qs_text.count("\nQ"),
    )

    assert (reports / "README.md").exists()
    assert (reports / "NR-swiss-national-council.md").exists()
    assert (reports / "SR-swiss-council-of-states.md").exists()
    assert (docs / "index.html").exists()
    json.loads((docs / "data.json").read_text(encoding="utf-8"))

    qs = (docs / "suggestions.qs").read_text(encoding="utf-8")
    lines = [ln for ln in qs.splitlines() if ln and not ln.startswith("#")]
    # Exactly one member was matched by P1307, so exactly one command.
    assert len(lines) == 1
    assert lines[0].startswith("Q1101|P39|Q18510612|P580|+2015-11-30T00:00:00Z/11|")
    assert '|S854|"https://www.parlament.ch/de/biografie/wd/1101"|S813|' in lines[0]


def test_unmatched_members_never_reach_the_quickstatements_file(pipeline, config):
    """The safety rule, observed end to end."""
    results = pipeline()  # nothing matched at all
    all_suggestions = [s for r in results for s in r.suggestions]
    assert all_suggestions  # there are findings...
    qs = render_file(all_suggestions, date(2026, 7, 29), config.statement_model)
    assert [ln for ln in qs.splitlines() if ln and not ln.startswith("#")] == []


# --- P580 comes from the tenure start, not the segment start ------------------
# README step 0c. MemberCouncil gives Bachmann 2015-11-30, but a history of
# adjacent segments shows her holding the seat since 2011-12-05 — and P580 is
# emitted by two mechanical kinds, so the wrong one would be written unreviewed.
def _history(person, spans, abbr="NR"):
    return [
        {
            "PersonNumber": person,
            "Language": "DE",
            "Active": end is None,
            "CouncilAbbreviation": abbr,
            "CouncilName": "Nationalrat",
            "FirstName": "Andrea",
            "LastName": "Bachmann",
            "DateJoining": start,
            "DateLeaving": end or "1753-01-01T00:00:00",
        }
        for start, end in spans
    ]


BACHMANN_HISTORY = _history(
    1101,
    [
        ("2011-12-05T00:00:00", "2015-11-29T00:00:00"),
        ("2015-11-30T00:00:00", None),
    ],
)


def test_p580_uses_the_tenure_start_from_the_history(
    member_rows, period_rows, config
):
    people = {
        "Q1101": WikidataPerson(qid="Q1101", label="Andrea Bachmann", parliament_id="1101")
    }
    results = process(
        config,
        FakeParliament(member_rows, period_rows, BACHMANN_HISTORY),
        FakeWikidata(people),
    )
    suggestion = next(
        s for r in results for s in r.suggestions if s.person_qid == "Q1101"
    )
    # The adjacent segments chain, so the tenure begins at the earlier one.
    assert suggestion.payload["start"] == date(2011, 12, 5)

    qs = render_file([suggestion], date(2026, 7, 29), config.statement_model)
    assert "P580|+2011-12-05T00:00:00Z/11" in qs
    assert "2015-11-30" not in qs


def test_a_broken_history_degrades_to_the_raw_field_instead_of_aborting(
    member_rows, period_rows, config
):
    """A worse P580 beats no report at all — but only because it is logged."""
    people = {
        "Q1101": WikidataPerson(qid="Q1101", label="Andrea Bachmann", parliament_id="1101")
    }
    results = process(
        config,
        HistorylessParliament(member_rows, period_rows),
        FakeWikidata(people),
    )
    suggestion = next(
        s for r in results for s in r.suggestions if s.person_qid == "Q1101"
    )
    assert suggestion.payload["start"] == date(2015, 11, 30)  # MemberCouncil's value


def test_a_real_break_in_the_history_does_not_backdate_p580(
    member_rows, period_rows, config
):
    """Left and returned: P580 is the return, not the first-ever election."""
    history = _history(
        1101,
        [
            ("2003-12-01T00:00:00", "2007-12-02T00:00:00"),
            ("2015-11-30T00:00:00", None),
        ],
    )
    people = {
        "Q1101": WikidataPerson(qid="Q1101", label="Andrea Bachmann", parliament_id="1101")
    }
    results = process(
        config, FakeParliament(member_rows, period_rows, history), FakeWikidata(people)
    )
    suggestion = next(
        s for r in results for s in r.suggestions if s.person_qid == "Q1101"
    )
    assert suggestion.payload["start"] == date(2015, 11, 30)


# --- the departed member's dates, end to end ---------------------------------
# MemberCouncil carries only today's members, so somebody Wikidata still records
# as sitting used to reach the report with no link and no dates. Both are in
# MemberCouncilHistory, which the run already reads for the tenure starts.
DEPARTED_HISTORY = _history(
    3432,
    [
        ("2011-12-05T00:00:00", "2015-11-29T00:00:00"),
        ("2015-11-30T00:00:00", "2019-12-01T00:00:00"),
    ],
)


def _still_recorded_as_sitting():
    return {
        "Q3432": WikidataPerson(
            qid="Q3432",
            label="Departed Member",
            parliament_id="3432",
            statements=[
                PositionStatement(
                    person_qid="Q3432",
                    statement_id="S3432",
                    position_qid="Q18510612",
                    start=date(2011, 12, 5),
                )
            ],
        )
    }


def test_a_departed_member_reaches_the_report_with_a_link_and_both_dates(
    member_rows, period_rows, config
):
    results = process(
        config,
        FakeParliament(member_rows, period_rows, DEPARTED_HISTORY),
        FakeWikidata(_still_recorded_as_sitting()),
    )
    suggestion = next(
        s for r in results for s in r.suggestions if s.person_qid == "Q3432"
    )
    assert suggestion.kind == KIND_ADD_END_DATE
    assert suggestion.person_number == 3432
    assert (
        suggestion.payload["biography"]
        == "https://www.parlament.ch/de/biografie/wd/3432"
    )
    assert suggestion.payload["start"] == date(2011, 12, 5)
    assert suggestion.payload["end"] == date(2019, 12, 1)


def test_the_departed_members_end_date_still_reaches_no_quickstatement(
    member_rows, period_rows, config
):
    """A named date is for a human to apply, not a P582 backfill of the chamber."""
    results = process(
        config,
        FakeParliament(member_rows, period_rows, DEPARTED_HISTORY),
        FakeWikidata(_still_recorded_as_sitting()),
    )
    all_suggestions = [s for r in results for s in r.suggestions]
    qs = render_file(all_suggestions, date(2026, 7, 29), config.statement_model)
    assert "Q3432" not in qs


def test_without_the_history_the_report_says_so_instead_of_guessing(
    member_rows, period_rows, config
):
    results = process(
        config,
        HistorylessParliament(member_rows, period_rows),
        FakeWikidata(_still_recorded_as_sitting()),
    )
    suggestion = next(
        s for r in results for s in r.suggestions if s.person_qid == "Q3432"
    )
    assert "end" not in suggestion.payload
    assert "looked up by hand" in suggestion.detail
    # The link does not depend on the history: it comes from the identifier.
    assert suggestion.payload["biography"].endswith("/3432")


def test_a_source_with_no_wikidata_links_is_not_a_degradation(
    member_rows, period_rows, config
):
    """parlament.ch asserts nothing about Wikidata, so there is nothing to read.

    FakeParliament has no `get_link_conflicts` at all — the ordinary case for
    that source, and it must not be logged as a failure or crash the run.
    """
    results = process(
        config, FakeParliament(member_rows, period_rows), FakeWikidata()
    )
    assert all(
        s.kind != KIND_DUPLICATE_SOURCE_LINK for r in results for s in r.suggestions
    )


# --- the second source, end to end -------------------------------------------
class FakeEnricher:
    """Stands in for the OpenParlData cross-check."""

    def __init__(self, spans=None, conflicts=None, explode=False):
        self._spans = spans or {}
        self._conflicts = conflicts or {}
        self._explode = explode

    def fetch(self, councils=None):
        if self._explode:
            raise RuntimeError("OpenParlData unavailable")
        from wd_parliament.enrich import Enrichment

        return Enrichment(
            spans=self._spans,
            link_conflicts=self._conflicts,
            people_with_links=len(self._spans),
        )


def _bachmann():
    return {
        "Q1101": WikidataPerson(qid="Q1101", label="Andrea Bachmann",
                                parliament_id="1101")
    }


def test_a_second_source_that_agrees_changes_nothing(
    member_rows, period_rows, config
):
    from wd_parliament.models import SourceSpan

    spans = {("Q1101", "NR"): SourceSpan(council="NR", start=date(2015, 11, 30))}
    results = process(
        config, FakeParliament(member_rows, period_rows), FakeWikidata(_bachmann()),
        enricher=FakeEnricher(spans),
    )
    suggestion = next(
        s for r in results for s in r.suggestions if s.person_qid == "Q1101"
    )
    assert suggestion.kind == KIND_ADD_MEMBERSHIP
    assert "sources_disagree" not in suggestion.payload


def test_a_second_source_that_disagrees_withholds_the_quickstatement(
    member_rows, period_rows, config
):
    """parlament.ch says 2015-11-30; the other source says 2019-12-02."""
    from wd_parliament.models import SourceSpan

    spans = {("Q1101", "NR"): SourceSpan(council="NR", start=date(2019, 12, 2))}
    results = process(
        config, FakeParliament(member_rows, period_rows), FakeWikidata(_bachmann()),
        enricher=FakeEnricher(spans),
    )
    all_suggestions = [s for r in results for s in r.suggestions]
    assert any(s.kind == KIND_SOURCES_DISAGREE for s in all_suggestions)

    qs = render_file(all_suggestions, date(2026, 8, 4), config.statement_model)
    assert "Q1101" not in qs


def test_the_second_sources_link_conflicts_reach_the_report(
    member_rows, period_rows, config
):
    results = process(
        config, FakeParliament(member_rows, period_rows), FakeWikidata(),
        enricher=FakeEnricher(conflicts={("NR", "Q999"): [42, 43]}),
    )
    conflict = next(
        s for r in results for s in r.suggestions
        if s.kind == KIND_DUPLICATE_SOURCE_LINK
    )
    assert conflict.payload["source_person_ids"] == [42, 43]


def test_a_broken_second_source_degrades_to_a_single_source_run(
    member_rows, period_rows, config
):
    """A worse report beats no report — and the loss must not be silent."""
    results = process(
        config, FakeParliament(member_rows, period_rows), FakeWikidata(_bachmann()),
        enricher=FakeEnricher(explode=True),
    )
    suggestion = next(
        s for r in results for s in r.suggestions if s.person_qid == "Q1101"
    )
    assert suggestion.kind == KIND_ADD_MEMBERSHIP
    assert "sources_disagree" not in suggestion.payload
