"""The Gever config, end to end, offline.

The third of these files, after ``test_pipeline.py`` (federal) and
``test_cantonal_pipeline.py`` (OpenParlData). What it pins down that neither of
those can is a source with **no joinable identifier at all**: this run matches
every member by name, and the two flags that make that safe rather than merely
unfortunate are asserted here, both of them, because each prevents a different
failure.

``config/gemeinderat-zuerich.yaml`` is loaded rather than rebuilt, unlike
``test_cantonal_pipeline.py``. The difference is deliberate: there the tests
were about the OpenParlData *path* and broke when a shipped config changed
source; here the shipped config's own claims — no join, report-only, an
explicit biography URL — are the thing under test.
"""

from datetime import date
from functools import partial

import pytest

from wd_parliament.app import build_source
from wd_parliament.app import process as _process
from wd_parliament.config import SOURCE_GEVER, load_config
from wd_parliament.gever import GeverClient
from wd_parliament.models import (
    KIND_ADD_IDENTIFIER,
    KIND_MISSING_IDENTIFIER,
    KIND_NO_WIKIDATA_ITEM,
    QID_FROM_IDENTIFIER,
    QID_FROM_NAME,
    PersonMatch,
    WikidataPerson,
)
from wd_parliament.quickstatements import is_mechanical, render

CONFIG = "config/gemeinderat-zuerich.yaml"
TODAY = date(2026, 8, 18)
OPEN = "9999-12-31T23:59:59"
process = partial(_process, today=TODAY)


class FakeApi:
    """Stands in for swissparlpy's Gever backend.

    The rows carry the column names run 35 read off the live service — which
    is the only kind of fixture that could have caught what it found.
    """

    MANDATES = [
        # A sitting member, with the open-end sentinel the service really uses.
        {"kontaktguid": "g-anna", "gremium": "Gemeinderat", "funktion": "Mitglied",
         "dauer_start": "2022-05-04", "dauer_end": OPEN,
         "name": "Meier", "vorname": "Anna", "wahlkreis": "4 und 5",
         "partei": "SP"},
        # The presiding member: no separate `Mitglied` row, so this IS her seat.
        {"kontaktguid": "g-bea", "gremium": "Gemeinderat", "funktion": "Präsidium",
         "dauer_start": "2026-05-06", "dauer_end": OPEN,
         "name": "Huber", "vorname": "Bea", "wahlkreis": "11", "partei": "GLP"},
        # The city's EXECUTIVE, inside the council's own mandate list.
        {"kontaktguid": "g-carl", "gremium": "Gemeinderat",
         "funktion": "Mitglied Stadtrat", "dauer_start": "2022-05-04",
         "dauer_end": OPEN, "name": "Frei", "vorname": "Carl"},
        # A party — this service models them as Gremien too.
        {"kontaktguid": "g-anna", "gremium": "SP", "funktion": "Mitglied",
         "dauer_start": "2022-05-04", "dauer_end": OPEN,
         "name": "Meier", "vorname": "Anna"},
        # Somebody who has left: not a member, but a tenure.
        {"kontaktguid": "g-old", "gremium": "Gemeinderat", "funktion": "Mitglied",
         "dauer_start": "1990-04-04", "dauer_end": "1998-02-26",
         "name": "Alt", "vorname": "Otto"},
    ]
    PERSONS = [
        {"obj_guid": "g-anna", "name": "Meier", "vorname": "Anna",
         "namevorname": "Meier Anna", "beruf": "Lehrerin", "jahrgang": "1975",
         "partei": "SP", "fraktion": "SP"},
        {"obj_guid": "g-bea", "name": "Huber", "vorname": "Bea",
         "namevorname": "Huber Bea", "homepageprivat": "https://example.ch"},
        {"obj_guid": "g-carl", "name": "Frei", "vorname": "Carl"},
    ]

    def get_data(self, table, **params):
        if table == "behoerdenmandat":
            return list(self.MANDATES)
        if table == "kontakt":
            return list(self.PERSONS)
        raise AssertionError(f"unexpected index {table!r}")


class FakeWikidata:
    """Anna Meier has an item; nobody else does."""

    def __init__(self, people=None):
        self.people = people if people is not None else self._default()
        self.identifier_properties = []

    @staticmethod
    def _default():
        return {
            "Q1": WikidataPerson(qid="Q1", label="Anna Meier", parliament_id=None)
        }

    def get_position_holders(self, position_qids, language="de",
                             identifier_property="P1307", person_data_properties=(),
                             extra_identifiers=()):
        self.identifier_properties.append(identifier_property)
        return dict(self.people)

    def search_people(self, names, position_qids, language="de",
                      identifier_property="P1307"):
        """``name -> [PersonMatch]``, which is the real client's shape.

        The name fallback is the ONLY way a member reaches an item under this
        config, so a fake that got the shape wrong would leave every one of
        these tests asserting about an empty run.
        """
        out = {}
        for name in names:
            hits = [
                PersonMatch(name=name, qid=p.qid, label=p.label,
                            birth_date=p.birth_date)
                for p in self.people.values()
                if p.label.casefold() == name.casefold()
            ]
            if hits:
                out[name] = hits
        return out


@pytest.fixture
def config():
    return load_config(CONFIG)


@pytest.fixture
def source(config):
    return GeverClient(bodies=config.bodies, client=FakeApi())


# --- what the config claims --------------------------------------------------
def test_the_config_reads_the_gever(config):
    assert config.source == SOURCE_GEVER
    assert config.gever_backend == "gever_city_zurich"
    assert config.councils == ["GR"]


def test_the_chamber_is_named_for_an_exact_match(config):
    """`Gemeinderat`, not `Gemeinderat Zürich` and not a substring of either.

    Equality is what keeps the city's executive, the Büro and the parties out
    — all three of which carry mandate rows in this service.
    """
    assert config.bodies[0].group_name == "Gemeinderat"


def test_neither_flag_lets_anything_be_written(config):
    assert config.identifier_from_source is False
    assert config.joins_on_identifier is False
    assert config.quickstatements is False


def test_build_source_returns_the_gever_client(config):
    class FakeHttp:
        session = None

    assert isinstance(build_source(config, FakeHttp()), GeverClient)


# --- the run -----------------------------------------------------------------
def test_a_municipal_run_produces_a_report(config, source):
    results = process(config, source, FakeWikidata())
    assert len(results) == 1
    result = results[0]
    assert result.error is None
    assert result.body.council == "GR"
    # Anna and Bea. Carl is the executive; the SP row is a party, not the
    # chamber; Otto has left.
    assert result.member_count == 2


def test_the_presiding_members_row_is_her_seat(config, source):
    """She has no separate `Mitglied` row. Excluding the presidium would lose
    a real member — the correction run 14 made to run 13, one level down."""
    members = source.get_members(today=TODAY)
    assert {m.last_name for m in members} == {"Meier", "Huber"}


def test_nobody_is_matched_by_the_identifier(config, source):
    """Not "the join found nothing" — the join is never made. A hit between a
    GUID and a P14527 value would be a coincidence carrying the provenance
    `is_mechanical` trusts."""
    results = process(config, source, FakeWikidata())
    assert all(
        s.qid_source != QID_FROM_IDENTIFIER for s in results[0].suggestions
    )


def test_the_matched_member_is_matched_by_name(config, source):
    results = process(config, source, FakeWikidata())
    matched = [s for s in results[0].suggestions if s.qid_source == QID_FROM_NAME]
    assert matched and all(s.person_qid == "Q1" for s in matched)


def test_no_suggestion_ever_offers_the_gever_guid_as_a_property_value(
    config, source
):
    """The failure this config's `identifier_from_source: false` exists to
    prevent. ADD_IDENTIFIER would paste `g-anna` into P14527 — a number from a
    different id space, written confidently onto a real item, where no later
    run could detect it.
    """
    results = process(config, source, FakeWikidata())
    kinds = {s.kind for s in results[0].suggestions}
    assert KIND_ADD_IDENTIFIER not in kinds
    assert KIND_MISSING_IDENTIFIER in kinds
    # No payload anywhere carries the source's person key as a *value* to
    # paste. `parliament_id` is the field `quickstatements` renders from, and
    # it is the one that would put a GUID into P14527.
    assert not any(
        s.payload.get("parliament_id") for s in results[0].suggestions
    )


def test_the_missing_identifier_says_why_no_number_is_offered(config, source):
    results = process(config, source, FakeWikidata())
    missing = [
        s for s in results[0].suggestions if s.kind == KIND_MISSING_IDENTIFIER
    ]
    assert missing
    assert "no value is offered" in missing[0].detail.lower()
    # …and it gives the reason peculiar to this source, rather than the
    # "different register" wording that presupposes a join.
    assert "guid" in missing[0].detail.lower()


def test_a_member_without_an_item_is_reported_for_creation(config, source):
    results = process(config, source, FakeWikidata())
    kinds = {s.kind for s in results[0].suggestions}
    assert KIND_NO_WIKIDATA_ITEM in kinds


def test_nothing_is_mechanical_and_nothing_is_rendered(config, source):
    """Two independent refusals: name provenance, and `quickstatements: false`.

    Either alone would do it, and asserting the rendered file is empty is what
    checks they actually meet.
    """
    results = process(config, source, FakeWikidata())
    suggestions = results[0].suggestions
    assert not any(is_mechanical(s) for s in suggestions)
    assert render(suggestions, statement_model=config.statement_model) == []


def test_the_district_reaches_the_report_from_the_mandate(config, source):
    members = source.get_members(today=TODAY)
    anna = next(m for m in members if m.last_name == "Meier")
    assert anna.constituency_abbreviation == "4 und 5"
    assert config.constituency_qid(anna.constituency_abbreviation) == "Q117787885"


def test_a_departed_member_has_a_tenure_but_is_not_a_member(config, source):
    assert ("g-old", "GR") in source.get_tenures()
    assert "g-old" not in {m.person_number for m in source.get_members(today=TODAY)}


def test_no_periods_means_no_p2937_whatever_the_config_says(config, source):
    assert source.get_periods() == []
    assert config.terms == {}
