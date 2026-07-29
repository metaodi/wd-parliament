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
    KIND_ADD_MEMBERSHIP,
    KIND_NO_WIKIDATA_ITEM,
    MODEL_TENURE,
    QID_FROM_IDENTIFIER,
    Body,
    PositionStatement,
    WikidataPerson,
)
from wd_parliament.parliament import members_from_rows, periods_from_rows
from wd_parliament.quickstatements import render_file
from wd_parliament.report import write_reports

NATIONAL = Body(council="N", label="Swiss National Council", position_qid="Q18510612")
STATES = Body(council="S", label="Swiss Council of States", position_qid="Q18510613")


class FakeParliament:
    """Serves the committed fixtures instead of calling parlament.ch."""

    def __init__(self, member_rows, period_rows):
        self._member_rows = member_rows
        self._period_rows = period_rows

    def get_members(self, councils=None, active_only=True, table=None):
        return members_from_rows(
            self._member_rows, councils=councils, active_only=active_only
        )

    def get_periods(self):
        return periods_from_rows(self._period_rows)


class FakeWikidata:
    def __init__(self, people=None, matches=None):
        self._people = people or {}
        self._matches = matches or {}

    def get_position_holders(self, position_qids, language="de"):
        return self._people

    def search_people(self, names, position_qids, language="de"):
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
    assert [r.body.council for r in results] == ["N", "S"]


def test_only_sitting_members_are_processed(pipeline):
    results = pipeline()
    # 10 people in the fixture, 2 of them inactive.
    assert sum(r.member_count for r in results) == 8


def test_unmatched_members_are_reported_not_dropped(pipeline):
    results = pipeline()
    national = results[0]
    assert national.unmatched == national.member_count
    assert all(s.kind == KIND_NO_WIKIDATA_ITEM for s in national.suggestions)


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
    states = next(r for r in results if r.body.council == "S")
    assert any(s.person_qid == "Q1104" for s in states.suggestions)
    national = next(r for r in results if r.body.council == "N")
    assert not any(s.person_qid == "Q1104" for s in national.suggestions)


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
        def get_position_holders(self, position_qids, language="de"):
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
    assert (reports / "N-swiss-national-council.md").exists()
    assert (reports / "S-swiss-council-of-states.md").exists()
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
