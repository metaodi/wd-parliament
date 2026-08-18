"""The KR-Daten config, end to end, offline.

The third source through the same ``app.process``, and the first cantonal one
whose identifier join is **verified** — so this file's job is to pin the thing
that follows from that and from nothing else: a member matched on P13468 can
reach QuickStatements.

Both other pipeline tests assert the opposite for their config (federally
because ``quickstatements: false`` is an operator switch, cantonally because
P14527's value was measured at 34 of 35 and the join is declared unverified).
Here the *blanket* refusal is gone, which makes the remaining gates worth
naming: the run still emits nothing until an operator turns
``quickstatements`` on, and review kinds are still excluded because
QuickStatements can only add.

The workbook is built in memory and served through a fake session, so the whole
path — HTTP body → :mod:`workbook` → :mod:`krdaten` → :mod:`resolve` →
:mod:`diff` → :mod:`quickstatements` — runs with no network and no mocking of
anything but the socket.
"""

import io
import zipfile
from datetime import date
from functools import partial

import pytest

from wd_parliament.app import build_source
from wd_parliament.app import process as _process
from wd_parliament.config import SOURCE_KRDATEN, load_config
from wd_parliament.krdaten import KrDatenClient
from wd_parliament.models import (
    KIND_ADD_MEMBERSHIP,
    QID_FROM_IDENTIFIER,
    WikidataPerson,
)
from wd_parliament.quickstatements import is_mechanical

CONFIG = "config/kantonsrat-zh.yaml"
TODAY = date(2026, 8, 10)
# Pinned so "sitting"/"open seat" fixture rows stay pinned to the day this
# file was written rather than drifting as the real calendar advances past
# a fixture row's date — see app.process's `today` parameter, and the same
# fix in test_cantonal_pipeline.py.
process = partial(_process, today=TODAY)

PERSONEN = [
    ["ID_PERSON_NEW", "NACHNAME", "VORNAME", "DATUM_GEBURT", "BERUF"],
    ["21984", "Rueff-Frenkel", "Sonja", "1972-11-15 00:00:00.0", "Rechtsanwältin"],
    ["22518", "Genner", "Ruth", "1956-01-01 00:00:00.0", "Architektin"],
    ["13", "Petermann", "Albert L.", "", "Gewerbesekretär"],
]
EINSITZE = [
    ["ID_EINSITZ_NEW", "ID_PERSON_NEW", "RAT",
     "DATUM_EINTRITT_TAG", "DATUM_EINTRITT_MONAT", "DATUM_EINTRITT_JAHR",
     "DATUM_AUSTRITT_TAG", "DATUM_AUSTRITT_MONAT", "DATUM_AUSTRITT_JAHR",
     "WAHLKREIS"],
    # sitting
    ["21984_E1", "21984", "Kantonsrat", "06", "05", "2019", "", "", "",
     "3. Wahlkreis  (Zürich 4+5)"],
    # left, and with a year-only exit — still departed
    ["22518_E1", "22518", "Kantonsrat", "01", "05", "1987", "", "", "1995",
     "4. Wahlkreis (Zürich 6 + 10)"],
    # a different chamber entirely
    ["13_E1", "13", "Regierungsrat", "26", "05", "1975", "19", "08", "1985",
     "1. Wahlkreis (Zürich 1+2)"],
]
PARTEIEN = [
    ["ID_PARTEI_NEW", "ID_EINSITZ_NEW", "ID_PERSON_NEW", "PARTEIBEZEICHNUNG",
     "FRAKTION"],
    ["21984_E1_P1", "21984_E1", "21984", "FDP", ""],
    ["22518_E1_P1", "22518_E1", "22518", "Grüne", ""],
]


def workbook_bytes():
    """A real OOXML workbook, built the way the standard says."""
    sheets = {"Personen": PERSONEN, "Einsitze": EINSITZE, "Parteien": PARTEIEN}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        entries, rels = [], []
        for i, (name, grid) in enumerate(sheets.items(), start=1):
            rows = []
            for r, row in enumerate(grid, start=1):
                cells = "".join(
                    f'<c r="A{r}" t="inlineStr"><is><t>{v}</t></is></c>' for v in row
                )
                rows.append(f'<row r="{r}">{cells}</row>')
            zf.writestr(
                f"xl/worksheets/sheet{i}.xml",
                '<?xml version="1.0"?><worksheet xmlns='
                '"http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(rows)}</sheetData></worksheet>',
            )
            entries.append(
                f'<sheet name="{name}" sheetId="{i}" xmlns:r='
                '"http://schemas.openxmlformats.org/officeDocument/2006/'
                f'relationships" r:id="rId{i}"/>'
            )
            rels.append(
                f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/worksheet"/>'
            )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns='
            '"http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheets>{"".join(entries)}</sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?><Relationships xmlns='
            '"http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{"".join(rels)}</Relationships>',
        )
    return buf.getvalue()


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.headers = {"Content-Type": "application/vnd.ms-excel"}

    def raise_for_status(self):
        return None


class FakeSession:
    """Serves the workbook, and counts how often it is asked for it."""

    def __init__(self):
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return FakeResponse(workbook_bytes())


class FakeWikidata:
    """Sonja Rueff-Frenkel is on Wikidata, joined on P13468 == 21984."""

    def __init__(self, people=None):
        self.people = people if people is not None else self._default()
        self.identifier_properties = []

    @staticmethod
    def _default():
        return {
            "Q107275": WikidataPerson(
                qid="Q107275",
                label="Sonja Rueff-Frenkel",
                parliament_id="21984",
                birth_date=date(1972, 11, 15),
            )
        }

    def get_position_holders(self, position_qids, language="de",
                             identifier_property="P1307", person_data_properties=(),
                             extra_identifiers=()):
        self.identifier_properties.append(identifier_property)
        self.extra_identifiers = list(extra_identifiers)
        return self.people

    def search_people(self, names, position_qids, language="de",
                      identifier_property="P1307"):
        return {}


@pytest.fixture
def config():
    return load_config(CONFIG)


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def source(session):
    return KrDatenClient(session=session)


# --- the config -------------------------------------------------------------
def test_the_config_selects_the_register_and_its_own_identifier(config):
    assert config.source == SOURCE_KRDATEN
    assert config.identifier_property == "P13468"
    assert config.councils == ["KR"]


def test_the_join_is_declared_verified(config):
    """Run 28: 591 of 638 (92.6%), and the shortfall accounted for. This is
    the first cantonal config that can say so."""
    assert config.identifier_verified is True


def test_the_other_identifier_is_reported_without_a_value(config):
    """A parliament has two, and only the join property's value is in hand."""
    assert config.identifiers == ["P13468", "P14527"]


def test_the_numbers_link_through_the_registers_own_page(config):
    """P13468's formatter URL, read from Wikidata by run 28 rather than
    assumed — a number linked through another register's template resolves to
    nothing."""
    assert config.biography_url_for(21984).endswith("abfrage.php?id=21984")


def test_build_source_returns_the_register_client(config):
    class Http:
        session = FakeSession()

    assert isinstance(build_source(config, Http()), KrDatenClient)


# --- the run ----------------------------------------------------------------
def test_a_run_reads_the_chamber_and_no_other(config, source):
    """Regierungsrat is in the same sheet and is not this parliament."""
    results = process(config, source, FakeWikidata())
    assert len(results) == 1 and results[0].error is None
    assert results[0].member_count == 1  # only the sitting Kantonsrat member


def test_the_workbook_is_fetched_once(config, source, session):
    """One request answers every question; re-fetching per chamber would
    multiply a several-megabyte download by nothing."""
    process(config, source, FakeWikidata())
    source.get_tenures("KR")
    assert session.calls == 1


def test_the_join_uses_p13468_not_p1307(config, source):
    wikidata = FakeWikidata()
    process(config, source, wikidata)
    assert wikidata.identifier_properties == ["P13468"]


def test_a_member_matched_on_the_register_id_carries_identifier_provenance(
    config, source
):
    results = process(config, source, FakeWikidata())
    matched = [
        s for s in results[0].suggestions if s.qid_source == QID_FROM_IDENTIFIER
    ]
    assert matched, "Rueff-Frenkel should have joined on P13468 == 21984"
    assert all(s.person_qid == "Q107275" for s in matched)


def test_a_verified_join_lets_a_mechanical_suggestion_through(config, source):
    """What being verified buys, and the only thing it buys.

    Under ``identifier_verified: false`` every suggestion is stamped
    ``identifier_unverified`` and ``is_mechanical`` refuses it outright. Here
    it is not, so a mechanical kind matched on the identifier passes — which
    is exactly why the flag costs a measurement.
    """
    results = process(config, source, FakeWikidata())
    membership = [
        s for s in results[0].suggestions if s.kind == KIND_ADD_MEMBERSHIP
    ]
    assert membership, "the matched member holds no P39 for the seat yet"
    assert any(is_mechanical(s) for s in membership)


def test_nothing_is_emitted_while_quickstatements_is_off(config):
    """The remaining gate, and an operator's decision rather than a
    measurement: README step 5 still wants a human to paste one line and look
    at where it landed."""
    assert config.quickstatements is False


# --- the historic half ------------------------------------------------------
def test_tenures_reach_people_the_members_table_does_not_contain(source):
    """The reason a register going back to 1803 is worth reading: it can date
    a departure Wikidata still records as open."""
    tenures = source.get_tenures("KR")
    assert (22518, "KR") in tenures
    assert tenures[(22518, "KR")].start == date(1987, 5, 1)


def test_a_year_only_departure_leaves_no_end_date_but_still_departs(source):
    """Two rules meeting: the seat is closed, and its end date is unknown
    rather than invented as 1 January."""
    tenure = source.get_tenures("KR")[(22518, "KR")]
    assert tenure.end is None
    members = source.get_members()
    assert 22518 not in [m.person_number for m in members]


def test_no_periods_means_no_p2937_can_ever_be_suggested(source):
    assert source.get_periods() == []
    assert source.get_member_segments() == {}


def test_the_statement_model_is_tenure_not_period(config):
    """A silent nothing, caught by the test above rather than by review.

    This source has no legislative periods, so under the default `period`
    model ``expected_statements`` returns an empty list and the run emits no
    seat suggestion at all — while reporting success. The seats are per tenure
    anyway (4,421 across 3,367 people).
    """
    assert config.statement_model == "tenure"


def test_a_bare_council_string_does_not_read_as_an_empty_chamber(source):
    """``councils='KR'`` iterates to {'K', 'R'} and matches nothing, which
    would read as "this chamber has no members" — the failure shape this repo
    has paid for twice."""
    assert source.get_members(councils="KR") == source.get_members(councils=["KR"])
    assert source.get_members(councils=["KR"])
