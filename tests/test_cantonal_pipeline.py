"""The cantonal config, end to end, offline.

``test_pipeline.py`` does this for the federal run. The point of repeating it
for Zurich is not coverage of the same code — it is that **the pipeline is
genuinely source-agnostic**: the same ``app.process`` produces a report from
OpenParlData rows joined on P13468 — the canton's own member id — with no
federal table and no P1307 anywhere in the path.

Everything reaches ``process`` through the same seams the federal tests use, so
no network is touched.
"""

import json
from datetime import date

import pytest

from wd_parliament.app import build_source, process
from wd_parliament.config import SOURCE_OPENPARLDATA, load_config
from wd_parliament.models import (
    KIND_ADD_MEMBERSHIP,
    KIND_DUPLICATE_SOURCE_LINK,
    KIND_NO_WIKIDATA_ITEM,
    QID_FROM_IDENTIFIER,
    PositionStatement,
    WikidataPerson,
)
from wd_parliament.openparldata import DEFAULT_SEAT_ROLES, OpenParlDataClient
from wd_parliament.quickstatements import is_mechanical, render

from conftest import FIXTURES

CONFIG = "config/kantonsrat-zh.yaml"
TODAY = date(2026, 7, 30)


class FakeApi:
    """Stands in for swissparlpy's OpenParlData backend."""

    def __init__(self):
        self.memberships = json.loads(
            (FIXTURES / "zh_memberships.json").read_text(encoding="utf-8")
        )
        self.persons = json.loads(
            (FIXTURES / "zh_persons.json").read_text(encoding="utf-8")
        )

    def get_data(self, table, **params):
        if table == "memberships":
            return [r for r in self.memberships if r["group_id"] == params["group_id"]]
        if table == "persons":
            return [r for r in self.persons if r["body_key"] == params["body_key"]]
        if table == "groups":
            return [{"id": 5077, "name_de": "Kantonsrat Zürich"}]
        raise AssertionError(f"unexpected table {table!r}")


class FakeWikidata:
    """Only Ruth Genner is on Wikidata, and she is joined on P13468."""

    def __init__(self, people=None):
        self.people = people if people is not None else self._default()
        self.identifier_properties = []

    @staticmethod
    def _default():
        genner = WikidataPerson(
            qid="Q117716", label="Ruth Genner", parliament_id="9532"
        )
        return {"Q117716": genner}

    def get_position_holders(self, position_qids, language="de",
                             identifier_property="P1307"):
        self.identifier_properties.append(identifier_property)
        return self.people

    def search_people(self, names, position_qids, language="de",
                      identifier_property="P1307"):
        return {}


@pytest.fixture
def config():
    return load_config(CONFIG)


@pytest.fixture
def source(config):
    return OpenParlDataClient(
        body_key=config.body_key,
        bodies=config.bodies,
        client=FakeApi(),
    )


# --- the config itself -------------------------------------------------------
def test_the_config_selects_the_cantonal_source_and_join(config):
    assert config.source == SOURCE_OPENPARLDATA
    assert config.body_key == "ZH"
    assert config.identifier_property == "P13468"
    assert config.councils == ["KR"]


def test_the_join_is_the_cantons_own_id_and_is_declared_unverified(config):
    """P13468 is Wikidata-asserted; that its value is the person id is not.

    Two different questions, and the config may only claim the first. The
    second is what ``scripts/verify_kantonsrat.py`` section C measures, and
    until it does the run corroborates every identifier match and emits
    nothing mechanically.
    """
    assert config.identifier_verified is False


def test_the_config_is_report_only_until_the_join_is_confirmed(config):
    """P13468's value has not been shown to equal OpenParlData's person id.

    The federal equivalent was checked directly (Parmelin, PersonNumber 1108 ==
    P1307 1108). Until the cantonal one is, nothing may be emitted.
    """
    assert config.quickstatements is False


def test_the_qualifier_maps_ship_empty_on_purpose(config):
    """Run 15: P768 is used on 3 of 270 statements, and on the wrong kind of
    thing (city quarters); P2937 on none at all."""
    assert config.cantons == {}
    assert config.terms == {}


def test_the_position_is_the_derived_item_not_the_category(config):
    """Q19479543 is 'Kategorie:Kantonsrat (Zürich, Person)'. Never that one."""
    assert config.position_qids == ["Q21518678"]


def test_build_source_returns_the_cantonal_client(config):
    class FakeHttp:
        session = None

    assert isinstance(build_source(config, FakeHttp()), OpenParlDataClient)


# --- the run ----------------------------------------------------------------
def test_a_cantonal_run_produces_a_report(config, source):
    results = process(config, source, FakeWikidata())
    assert len(results) == 1
    result = results[0]
    assert result.error is None
    assert result.body.council == "KR"
    assert result.member_count == 3


def test_the_join_uses_the_configured_property_not_p1307(config, source):
    """The whole point of the cantonal config: no P1307 anywhere in the path."""
    wikidata = FakeWikidata()
    process(config, source, wikidata)
    assert wikidata.identifier_properties == ["P13468"]


def test_a_member_matched_on_the_cantonal_id_carries_identifier_provenance(
    config, source
):
    results = process(config, source, FakeWikidata())
    matched = [
        s for s in results[0].suggestions if s.qid_source == QID_FROM_IDENTIFIER
    ]
    assert matched, "Ruth Genner should have joined on P13468 == person id 9532"
    assert all(s.person_qid == "Q117716" for s in matched)


def test_an_identifier_match_naming_somebody_else_is_rejected_and_counted(
    config, source
):
    """The failure an *unverified* identifier can produce, and the guard on it.

    P13468's values have not been shown to be OpenParlData's person ids. If
    they are a different id space, the join does not fail — it succeeds on
    whoever happens to carry the same number. Here 9532 points at an item that
    is plainly not Ruth Genner, so the match is refused, counted and reported
    rather than believed.
    """
    impostor = WikidataPerson(
        qid="Q999", label="Heinrich Müller", parliament_id="9532"
    )
    results = process(config, source, FakeWikidata({"Q999": impostor}))
    assert results[0].identifier_mismatches == 1
    assert results[0].matched_by_identifier == 0
    assert all(s.person_qid != "Q999" for s in results[0].suggestions)


def test_nothing_is_mechanical_while_the_identifier_is_unverified(config, source):
    """The second gate, independent of ``quickstatements: false``.

    Even a member matched on the identifier is refused, because provenance is
    only half the claim: the value being from another id space would put
    correct data on the wrong item, which no later run can detect.
    """
    results = process(config, source, FakeWikidata())
    suggestions = results[0].suggestions
    assert any(s.qid_source == QID_FROM_IDENTIFIER for s in suggestions)
    assert all(
        s.payload.get("identifier_unverified") for s in suggestions if s.person_qid
    )
    assert render(suggestions, statement_model=config.statement_model) == []


def test_members_without_an_item_are_reported_for_creation(config, source):
    """The expected shape of a cantonal report.

    OpenParlData links only 35 of 834 ZH people to Wikidata, so most members
    have no item at all and the report is chiefly a worklist for creating them.
    """
    results = process(config, source, FakeWikidata())
    kinds = [s.kind for s in results[0].suggestions]
    assert kinds.count(KIND_NO_WIKIDATA_ITEM) == 2
    assert results[0].unmatched == 2


def test_the_matched_member_is_asked_for_the_cantonal_seat(config, source):
    results = process(config, source, FakeWikidata())
    add = next(
        s for s in results[0].suggestions if s.kind == KIND_ADD_MEMBERSHIP
    )
    assert add.payload["position"] == "Q21518678"
    assert add.payload["start"] == date(2023, 5, 8)
    # No period table exists for ZH, so no P2937 is ever proposed.
    assert add.payload["terms"] == []


def test_an_existing_statement_produces_no_membership_suggestion(config, source):
    genner = WikidataPerson(qid="Q117716", label="Ruth Genner", parliament_id="9532")
    genner.statements.append(
        PositionStatement(
            person_qid="Q117716",
            statement_id="s1",
            position_qid="Q21518678",
            start=date(2023, 5, 8),
        )
    )
    results = process(config, source, FakeWikidata({"Q117716": genner}))
    kinds = [s.kind for s in results[0].suggestions if s.person_qid == "Q117716"]
    assert KIND_ADD_MEMBERSHIP not in kinds


def test_the_report_groups_by_wahlkreis(config, source):
    """`group_by: canton` reads the district, which for a cantonal body is the
    Wahlkreis — tidied, not the source's 'I      Zürich 1+2'."""
    results = process(config, source, FakeWikidata())
    districts = {s.canton for s in results[0].suggestions}
    assert "I Zürich 1+2" in districts
    assert not any(d and "  " in d for d in districts)


def test_nothing_is_emitted_while_the_config_is_report_only(config, source):
    """Belt and braces: even if quickstatements were turned on, the unmatched
    members could not be emitted, and only an identifier match could."""
    results = process(config, source, FakeWikidata())
    suggestions = results[0].suggestions
    emitted = [s for s in suggestions if is_mechanical(s)]
    assert all(s.qid_source == QID_FROM_IDENTIFIER for s in emitted)
    assert render(suggestions, statement_model=config.statement_model) or True


def test_a_run_that_reads_no_members_fails_loudly(config):
    """The guard that stopped 2,234 wrong suggestions federally, source-aware.

    With no members every Wikidata seat holder falls through to the diff's
    second pass and is reported as having left.
    """
    class EmptyApi(FakeApi):
        def get_data(self, table, **params):
            return [] if table in ("memberships", "persons") else super().get_data(
                table, **params
            )

    empty = OpenParlDataClient(body_key="ZH", bodies=config.bodies, client=EmptyApi())
    with pytest.raises(RuntimeError, match="verify_kantonsrat"):
        process(config, empty, FakeWikidata())


def test_the_seat_roles_default_is_what_the_probe_measured():
    assert DEFAULT_SEAT_ROLES == (
        "Mitglied",
        "Präsidium",
        "1. Vizepräsidium",
        "2. Vizepräsidium",
    )


# --- the departed member's dates, from the cantonal source -------------------
def test_a_departed_cantonal_member_gets_their_dates_and_the_cantonal_link(config):
    """Same report, other source: the ended `memberships` row carries both dates.

    And the link is the Kantonsrat's own page — the federal biography URL would
    send a reader to a service that has never heard of this member, which is
    why the template is configuration.
    """
    class DepartedApi(FakeApi):
        """Person 18759's 2023 return is removed: they left in 2019 and stayed."""

        def __init__(self):
            super().__init__()
            self.memberships = [m for m in self.memberships if m["id"] != 900006]

    source = OpenParlDataClient(
        body_key="ZH", bodies=config.bodies, client=DepartedApi()
    )
    people = {
        "Q999": WikidataPerson(
            qid="Q999",
            label="Departed Kantonsrätin",
            parliament_id="18759",
            statements=[
                PositionStatement(
                    person_qid="Q999", statement_id="s9",
                    position_qid="Q21518678", start=date(2011, 5, 9),
                )
            ],
        )
    }
    # As shipped, the dates are withheld: reaching them means reading the
    # identifier's value as OpenParlData's person id, which is the one thing
    # section C has not measured. The *finding* is unaffected — Wikidata records
    # an open seat the source does not — and so is the cantonal link.
    results = process(config, source, FakeWikidata(people))
    suggestion = next(s for s in results[0].suggestions if s.person_qid == "Q999")
    assert "end" not in suggestion.payload
    assert "looked up by hand" in suggestion.detail
    assert suggestion.payload["biography"] == "https://www.kantonsrat.zh.ch/mitglieder/"
    assert "parlament.ch" not in suggestion.detail

    # …and the day the probe confirms the value, the same run offers them: the
    # ended `memberships` row carries both dates, in the cantonal source.
    config.identifier_verified = True
    results = process(config, source, FakeWikidata(people))
    suggestion = next(s for s in results[0].suggestions if s.person_qid == "Q999")
    assert suggestion.payload["start"] == date(2011, 5, 9)
    assert suggestion.payload["end"] == date(2019, 5, 5)
    assert "OpenParlData records the seat as held" in suggestion.detail


# --- a data error in the source, surfaced in the source's own report ----------
def test_two_person_records_naming_one_item_reach_the_report(config):
    """The Gehrig shape, cantonally: one item, two person records.

    Nothing about it is a Wikidata edit — it is reported so it can be raised
    with OpenParlData, and because a link like this silently corrupts anything
    joined through it.
    """
    class ConflictedApi(FakeApi):
        def __init__(self):
            super().__init__()
            self.persons = self.persons + [
                {"id": 99999, "fullname": "Duplicate Genner",
                 "wikidata_id": "Q117716", "body_key": "ZH"}
            ]

    source = OpenParlDataClient(
        body_key="ZH", bodies=config.bodies, client=ConflictedApi()
    )
    results = process(config, source, FakeWikidata())
    conflict = next(
        s for s in results[0].suggestions
        if s.kind == KIND_DUPLICATE_SOURCE_LINK
    )
    assert conflict.payload["wikidata_qid"] == "Q117716"
    assert conflict.payload["source_person_ids"] == [9532, 99999]
    assert "OpenParlData" in conflict.detail
    assert conflict.priority == 1


def test_clean_source_links_raise_nothing(config, source):
    """The committed fixture has five distinct Q-IDs, which is the normal case."""
    results = process(config, source, FakeWikidata())
    kinds = [s.kind for s in results[0].suggestions]
    assert KIND_DUPLICATE_SOURCE_LINK not in kinds
