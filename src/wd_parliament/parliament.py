"""The only ``swissparlpy`` caller: read members and periods from parlament.ch.

This module is deliberately thin. Its job is to turn OData rows into the plain
dataclasses in :mod:`models` and nothing else, so that every stage after it is
pure and can be unit-tested from a JSON fixture without touching the network.

The row-mapping functions (:func:`member_from_row`, :func:`period_from_row`)
are module-level and pure on purpose — the tests feed them fixture rows
directly rather than mocking ``swissparlpy``'s OData layer.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import Member, Period

log = logging.getLogger(__name__)

MEMBER_TABLE = "MemberCouncil"
HISTORIC_MEMBER_TABLE = "MemberCouncilHistory"  # same shape; future extension
PERIOD_TABLE = "LegislativePeriod"
VOTING_TABLE = "Voting"

DEFAULT_LANGUAGE = "DE"


def _as_date(value: Any) -> Optional[date]:
    """Coerce an OData ``Edm.DateTime`` (or an ISO string) to a ``date``.

    ``swissparlpy`` hands back ``datetime`` objects, but the JSON fixtures the
    tests use carry ISO strings, so both are accepted. Anything unparseable
    becomes ``None`` rather than raising: a single malformed date must not cost
    us the whole member.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Tolerate "2023-12-04T00:00:00", "2023-12-04 00:00:00" and "2023-12-04".
    text = text.replace(" ", "T").split("T")[0]
    try:
        return date.fromisoformat(text)
    except ValueError:
        log.debug("Could not parse date %r", value)
        return None


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "yes")


def member_from_row(row: Dict[str, Any]) -> Optional[Member]:
    """Map one ``MemberCouncil`` row to a :class:`Member`.

    Returns ``None`` for a row without a ``PersonNumber``: that field is the
    join key to Wikidata's P1307, and a row lacking it cannot be reconciled.
    """
    person_number = _as_int(row.get("PersonNumber"))
    if person_number is None:
        log.warning(
            "Skipping MemberCouncil row without PersonNumber: %s %s",
            row.get("FirstName"),
            row.get("LastName"),
        )
        return None
    return Member(
        person_number=person_number,
        first_name=_as_str(row.get("FirstName")) or "",
        last_name=_as_str(row.get("LastName")) or "",
        active=_as_bool(row.get("Active")),
        council=_as_str(row.get("CouncilAbbreviation")) or "",
        council_name=_as_str(row.get("CouncilName")) or "",
        council_number=_as_int(row.get("Council")),
        canton_abbreviation=_as_str(row.get("CantonAbbreviation")),
        canton_name=_as_str(row.get("CantonName")),
        parl_group_name=_as_str(row.get("ParlGroupName")),
        parl_group_abbreviation=_as_str(row.get("ParlGroupAbbreviation")),
        party_name=_as_str(row.get("PartyName")),
        party_abbreviation=_as_str(row.get("PartyAbbreviation")),
        date_joining=_as_date(row.get("DateJoining")),
        date_leaving=_as_date(row.get("DateLeaving")),
        date_election=_as_date(row.get("DateElection")),
        date_oath=_as_date(row.get("DateOath")),
        date_resignation=_as_date(row.get("DateResignation")),
        date_of_birth=_as_date(row.get("DateOfBirth")),
        date_of_death=_as_date(row.get("DateOfDeath")),
        person_id_code=_as_int(row.get("PersonIdCode")),
        id=_as_int(row.get("ID")),
    )


def members_from_rows(
    rows: Iterable[Dict[str, Any]],
    councils: Optional[Sequence[str]] = None,
    active_only: bool = True,
) -> List[Member]:
    """Map and filter ``MemberCouncil`` rows. Pure — the tests' entry point.

    ``councils`` filters on ``CouncilAbbreviation`` ("N" / "S"); ``None`` keeps
    every chamber. Members are de-duplicated on ``(person_number, council)``,
    keeping the row with the latest ``DateJoining`` — the OData service returns
    one row per person *per language*, and a caller that forgets the language
    filter would otherwise see each member several times over.
    """
    wanted = {c.strip().upper() for c in councils} if councils else None
    best: Dict[tuple, Member] = {}
    for row in rows:
        member = member_from_row(row)
        if member is None:
            continue
        if active_only and not member.active:
            continue
        if wanted is not None and member.council.upper() not in wanted:
            continue
        key = (member.person_number, member.council.upper())
        current = best.get(key)
        if current is None or _joining_sort_key(member) > _joining_sort_key(current):
            best[key] = member
    return sorted(best.values(), key=lambda m: (m.council, m.sort_name.casefold()))


def _joining_sort_key(member: Member) -> date:
    return member.date_joining or date.min


def period_from_row(row: Dict[str, Any]) -> Optional[Period]:
    """Map one ``LegislativePeriod`` row to a :class:`Period`."""
    number = _as_int(row.get("LegislativePeriodNumber"))
    if number is None:
        return None
    return Period(
        number=number,
        name=_as_str(row.get("LegislativePeriodName")) or "",
        abbreviation=_as_str(row.get("LegislativePeriodAbbreviation")),
        start=_as_date(row.get("StartDate")),
        end=_as_date(row.get("EndDate")),
        id=_as_int(row.get("ID")),
    )


def periods_from_rows(rows: Iterable[Dict[str, Any]]) -> List[Period]:
    """Map ``LegislativePeriod`` rows, de-duplicated by number. Pure."""
    best: Dict[int, Period] = {}
    for row in rows:
        period = period_from_row(row)
        if period is None:
            continue
        # One row per period per language; the first one wins, but a row that
        # carries dates beats one that does not.
        current = best.get(period.number)
        if current is None or (current.start is None and period.start is not None):
            best[period.number] = period
    return sorted(best.values(), key=lambda p: p.number)


class ParliamentClient:
    """Fetch members and periods from the parlament.ch OData service.

    ``session`` should be the :class:`~.http_client.HttpClient`'s session, so
    the OData calls carry the same descriptive User-Agent as the Wikidata ones.
    """

    def __init__(
        self,
        session: Any = None,
        language: str = DEFAULT_LANGUAGE,
        client: Any = None,
    ) -> None:
        self.language = (language or DEFAULT_LANGUAGE).upper()
        self._client = client
        self._session = session

    @property
    def client(self) -> Any:
        """The ``swissparlpy`` client, created lazily.

        Importing and constructing it reaches out to the OData service for its
        metadata document, so it must not happen at import time — that would
        make the pure modules untestable offline.
        """
        if self._client is None:
            import swissparlpy as spp

            self._client = spp.SwissParlClient(session=self._session)
        return self._client

    def _rows(self, table: str, **filters: Any) -> List[Dict[str, Any]]:
        log.debug("Fetching %s %s", table, filters)
        return list(self.client.get_data(table, **filters))

    def get_members(
        self,
        councils: Optional[Sequence[str]] = None,
        active_only: bool = True,
        table: str = MEMBER_TABLE,
    ) -> List[Member]:
        """Current members of the given chambers ("N", "S"), as dataclasses.

        The ``Active`` filter is pushed down to OData so the service returns
        the ~246 sitting members rather than every member since 1848.
        """
        filters: Dict[str, Any] = {"Language": self.language}
        if active_only:
            filters["Active"] = True
        rows = self._rows(table, **filters)
        members = members_from_rows(rows, councils=councils, active_only=active_only)
        log.info("Fetched %d members from %s", len(members), table)
        return members

    def get_periods(self) -> List[Period]:
        """Every ``LegislativePeriod`` row (~52), as dataclasses."""
        rows = self._rows(PERIOD_TABLE, Language=self.language)
        periods = periods_from_rows(rows)
        log.info("Fetched %d legislative periods", len(periods))
        return periods

    def get_period_attendance(self, vote_ids: Sequence[int]) -> Dict[int, set]:
        """``PersonNumber`` sets per legislative period, from roll-call votes.

        The cross-check described in the README: ``Voting`` is the only table
        carrying both ``PersonNumber`` and ``IdLegislativePeriod``, so one
        roll-call vote per period yields an empirical "these people sat in that
        period" set to compare against
        :func:`period_overlap.assign_periods`.

        Deliberately takes explicit ``vote_ids`` and fetches them one at a
        time: ``swissparlpy``'s own README warns that unbounded ``Voting``
        queries return 500s and must be batched.
        """
        attendance: Dict[int, set] = {}
        for vote_id in vote_ids:
            rows = self._rows(VOTING_TABLE, Language=self.language, IdVote=vote_id)
            for row in rows:
                period = _as_int(row.get("IdLegislativePeriod"))
                person = _as_int(row.get("PersonNumber"))
                if period is None or person is None:
                    continue
                attendance.setdefault(period, set()).add(person)
        return attendance
