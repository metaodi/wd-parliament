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

from .models import Member, Period, Tenure

log = logging.getLogger(__name__)

MEMBER_TABLE = "MemberCouncil"
HISTORIC_MEMBER_TABLE = "MemberCouncilHistory"  # same shape; future extension
PERIOD_TABLE = "LegislativePeriod"
VOTING_TABLE = "Voting"
VOTE_TABLE = "Vote"

# What a ``Vote`` row might call its own identifier. ``Voting`` joins to it on
# ``IdVote``, but the row that *is* the vote may well call the same number
# ``ID`` — so the column is resolved from the row rather than assumed, and an
# unrecognised row yields nothing instead of a wrong number. Same discipline as
# ``verify_openparldata.classify_seat_memberships``: "no such column" and
# "column full of nulls" look identical through ``.get()`` and mean opposite
# things.
VOTE_ID_FIELDS = ("IdVote", "ID", "Id")

DEFAULT_LANGUAGE = "DE"

# SQL Server's ``datetime`` minimum. The OData service is backed by one and
# sends this value for "no date" rather than a null — every sitting member's
# ``DateLeaving`` arrives as 1753-01-01 (confirmed against the live service on
# 2026-07-29). Read literally it means "this member left in 1753", which is
# not a harmless wrong number: ``diff`` would raise ADD_END_DATE for the whole
# chamber and ADD_END_DATE *is* mechanical, so it would reach QuickStatements
# as a P582 backfill. It also reverses every tenure interval, which
# ``period_overlap`` correctly refuses, silently costing every P2937 term.
#
# Compared with ``<=`` rather than ``==`` because a smaller value cannot be a
# real date either: SQL Server cannot represent one, so anything at or below
# the floor is the absence of a date however it was encoded.
NULL_DATE = date(1753, 1, 1)


def _as_date(value: Any) -> Optional[date]:
    """Coerce an OData ``Edm.DateTime`` (or an ISO string) to a ``date``.

    ``swissparlpy`` hands back ``datetime`` objects, but the JSON fixtures the
    tests use carry ISO strings, so both are accepted. Anything unparseable
    becomes ``None`` rather than raising: a single malformed date must not cost
    us the whole member. So does the :data:`NULL_DATE` sentinel, which is how
    the service spells an absent date.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _unless_null(value.date())
    if isinstance(value, date):
        return _unless_null(value)
    text = str(value).strip()
    if not text:
        return None
    # Tolerate "2023-12-04T00:00:00", "2023-12-04 00:00:00" and "2023-12-04".
    text = text.replace(" ", "T").split("T")[0]
    try:
        return _unless_null(date.fromisoformat(text))
    except ValueError:
        log.debug("Could not parse date %r", value)
        return None


def _unless_null(value: date) -> Optional[date]:
    """``None`` for the service's null-date sentinel, otherwise ``value``."""
    if value <= NULL_DATE:
        return None
    return value


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

    ``councils`` filters on ``CouncilAbbreviation`` ("NR" / "SR" — the German
    abbreviations, since the service is queried with ``Language=DE``);
    ``None`` keeps every chamber. Members are de-duplicated on
    ``(person_number, council)``, keeping the row with the latest
    ``DateJoining``: the service returns several rows per person — one per
    language, and sometimes more than one within a language — so a caller that
    forgets the language filter would otherwise see each member several times
    over, under French abbreviations ("CN" for the National Council) that the
    filter does not want.
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


# Two segments belong to the same continuous tenure when the second starts the
# day after the first ends. Legislature boundaries are exactly that — Bregy's
# 2019-12-01 / 2019-12-02 pair spans the 50th into the 51st — so a member
# re-elected without a break has one tenure, not two.
MAX_SEGMENT_GAP_DAYS = 1


def segments_from_rows(
    rows: Iterable[Dict[str, Any]],
    councils: Optional[Sequence[str]] = None,
) -> Dict[tuple, List[Member]]:
    """Group ``MemberCouncilHistory`` rows into segments per person and council.

    Pure. Unlike :func:`members_from_rows` this must **not** de-duplicate on
    ``(person_number, council)`` — the several rows per person are the mandate
    segments, and collapsing them is exactly what loses the tenure start. Only
    the language duplicates are dropped, on the full
    ``(person, council, joining, leaving)`` tuple, so two genuinely different
    spans always survive.

    Returned lists are sorted by ``date_joining``, oldest first, with undated
    segments discarded: a segment with no start cannot be chained to anything.
    """
    wanted = {c.strip().upper() for c in councils} if councils else None
    seen: set = set()
    out: Dict[tuple, List[Member]] = {}
    for row in rows:
        member = member_from_row(row)
        if member is None or member.date_joining is None:
            continue
        council = member.council.upper()
        if wanted is not None and council not in wanted:
            continue
        fingerprint = (
            member.person_number,
            council,
            member.date_joining,
            member.date_leaving,
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.setdefault((member.person_number, council), []).append(member)
    for segments in out.values():
        segments.sort(key=lambda m: m.date_joining)  # type: ignore[arg-type,return-value]
    return out


def tenure_start(segments: Sequence[Member]) -> Optional[date]:
    """First day of the **current continuous run** through ``segments``. Pure.

    Walks back from the newest segment for as long as each one begins within
    :data:`MAX_SEGMENT_GAP_DAYS` of the previous ending, and returns where that
    chain starts. A real break — a member who left and returned years later —
    stops the walk, so their current tenure begins at the return and the earlier
    service is a separate P39 statement.

    ``None`` when there is nothing to say: no segments, or the newest one has no
    start. The caller then keeps ``date_joining``, which is the accuracy the raw
    field allows rather than a guess.
    """
    dated = [s for s in segments if s.date_joining is not None]
    if not dated:
        return None
    ordered = sorted(dated, key=lambda m: m.date_joining)  # type: ignore[arg-type,return-value]
    start = ordered[-1].date_joining
    for earlier, later in zip(reversed(ordered[:-1]), reversed(ordered[1:])):
        if earlier.date_leaving is None:
            # An open earlier segment cannot be chained: without an end there is
            # no gap to measure, and overlapping rows are not a continuous run.
            break
        gap = (later.date_joining - earlier.date_leaving).days  # type: ignore[operator]
        if gap > MAX_SEGMENT_GAP_DAYS or gap < 0:
            break
        start = earlier.date_joining
    return start


def latest_tenure(segments: Sequence[Member]) -> Optional[Tenure]:
    """The most recent continuous run through ``segments``, dated. Pure.

    :func:`tenure_start` says where that run began; the newest segment says
    where it ended, and its ``date_leaving`` is ``None`` exactly when the spell
    is still open. That combination is what the diff's second pass needs about
    somebody who is **not** in the current-members set: ``MemberCouncil`` only
    carries today's ~246 people, but ``MemberCouncilHistory`` carries everyone
    since 1848, so a member Wikidata still records as sitting has their leaving
    date here even though the members table has never heard of them.

    ``None`` when no segment carries a start, matching :func:`tenure_start`:
    the honest answer is then "the source does not say", which the report
    prints as such rather than filling in.
    """
    dated = [s for s in segments if s.date_joining is not None]
    if not dated:
        return None
    newest = max(dated, key=lambda m: m.date_joining)  # type: ignore[arg-type,return-value]
    return Tenure(
        person_number=newest.person_number,
        council=newest.council.upper(),
        start=tenure_start(dated),
        end=newest.date_leaving,
    )


def tenures_from_segments(
    segments: Dict[tuple, List[Member]]
) -> Dict[tuple, Tenure]:
    """``(person_number, council)`` → their latest :class:`Tenure`. Pure.

    Keyed by the segment map's own key rather than by the person, because a
    person is not a seat — see :attr:`models.Tenure.key`.
    """
    out: Dict[tuple, Tenure] = {}
    for key, group in segments.items():
        tenure = latest_tenure(group)
        if tenure is not None:
            out[key] = tenure
    return out


def apply_tenure_starts(
    members: Iterable[Member], segments: Dict[tuple, List[Member]]
) -> int:
    """Set ``tenure_start`` on each member from their history. Pure.

    Returns how many members got a start *earlier* than their ``date_joining``
    — the count of rows whose raw value was a segment start, which is the
    number worth reporting. Members with no history are left alone rather than
    being given a guessed date.
    """
    corrected = 0
    for member in members:
        found = tenure_start(segments.get((member.person_number, member.council.upper()), []))
        if found is None:
            continue
        member.tenure_start = found
        if member.date_joining is not None and found < member.date_joining:
            corrected += 1
    return corrected


def vote_id_from_row(row: Dict[str, Any]) -> Optional[int]:
    """The ``IdVote`` of one ``Vote`` row, or ``None``. Pure.

    Tries :data:`VOTE_ID_FIELDS` in order and returns the first that holds a
    number. ``None`` means the row cannot be identified — which is a reason to
    skip it, never to fall back to a positional guess: a wrong ``IdVote`` would
    fetch some other period's roll-call and report a mismatch that is entirely
    an artefact of the lookup.
    """
    for field in VOTE_ID_FIELDS:
        value = _as_int(row.get(field))
        if value is not None:
            return value
    return None


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
        # ``MemberCouncilHistory`` is the largest read the pipeline makes —
        # every member since 1848 — and two callers now want it: the tenure
        # *start* correction and the tenure *end* the reverse walk reports.
        # Cached so asking twice costs one fetch.
        self._history_rows: Optional[List[Dict[str, Any]]] = None

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

    def _first_rows(
        self, table: str, count: int, **filters: Any
    ) -> List[Dict[str, Any]]:
        """The first ``count`` rows, without paging through the whole result.

        ``swissparlpy``'s response iterator follows the OData ``next`` link to
        exhaustion, so ``list()`` on a large table is a lot of requests.
        Slicing loads only as far as the slice reaches, which is one page.
        """
        log.debug("Fetching first %d of %s %s", count, table, filters)
        return list(self.client.get_data(table, **filters)[0:count])

    def get_members(
        self,
        councils: Optional[Sequence[str]] = None,
        active_only: bool = True,
        table: str = MEMBER_TABLE,
    ) -> List[Member]:
        """Current members of the given chambers ("NR", "SR"), as dataclasses.

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

    def get_member_segments(
        self, councils: Optional[Sequence[str]] = None
    ) -> Dict[tuple, List[Member]]:
        """Mandate segments per ``(person_number, council)`` from the history.

        One request for the whole ``MemberCouncilHistory``, which is the only
        way to get it: the table has no filter that selects "the segments of
        these 246 people", and a per-person fetch would be 246 round trips.
        It is the largest read the pipeline makes — every member since 1848 —
        and it is what P580 comes from, so that the start is a tenure start
        rather than a segment start (README step 0c). :meth:`get_tenures` reads
        the same rows for the leaving dates of people who have already gone.
        """
        rows = self._history()
        segments = segments_from_rows(rows, councils=councils)
        log.info(
            "Fetched %d history rows covering %d (person, council) pair(s)",
            len(rows),
            len(segments),
        )
        return segments

    def _history(self) -> List[Dict[str, Any]]:
        """The whole ``MemberCouncilHistory``, fetched at most once per client."""
        if self._history_rows is None:
            self._history_rows = self._rows(
                HISTORIC_MEMBER_TABLE, Language=self.language
            )
        return self._history_rows

    def get_tenures(
        self, councils: Optional[Sequence[str]] = None
    ) -> Dict[tuple, Tenure]:
        """``(PersonNumber, council)`` → their latest spell in that chamber.

        Everyone the history knows, not just today's members — which is the
        whole point: it is the *departed* members the reverse walk has no dates
        for, because ``MemberCouncil`` lists only those still in office. Costs
        no extra request beyond :meth:`get_member_segments`.
        """
        tenures = tenures_from_segments(self.get_member_segments(councils=councils))
        log.info("Tenure dates available for %d (person, council) pair(s)", len(tenures))
        return tenures

    def get_periods(self) -> List[Period]:
        """Every ``LegislativePeriod`` row (~52), as dataclasses."""
        rows = self._rows(PERIOD_TABLE, Language=self.language)
        periods = periods_from_rows(rows)
        log.info("Fetched %d legislative periods", len(periods))
        return periods

    def find_votes(self, periods: Sequence[Period]) -> Dict[int, int]:
        """One ``IdVote`` per legislative period, discovered from ``Vote``.

        So that the period cross-check can be *run* without somebody first
        hunting roll-call numbers by hand — which is the only reason README
        step 4 sat untouched. ``Vote`` is the table of votes themselves (one row
        each), not ``Voting``'s per-member ballots, so this is a small read;
        only the first page is loaded per period.

        Periods that yield nothing are simply absent from the result. That is
        the honest outcome: the Council of States has no roll-call record
        before the 2010s, and a period with no vote to sample is a gap in the
        cross-check, not a failure of the interval logic.
        """
        found: Dict[int, int] = {}
        for period in periods:
            if period.id is None:
                continue
            try:
                rows = self._first_rows(
                    VOTE_TABLE,
                    1,
                    Language=self.language,
                    IdLegislativePeriod=period.id,
                )
            except Exception as exc:  # noqa: BLE001 - a gap, not a crash
                log.warning(
                    "Could not read %s for period %d: %s", VOTE_TABLE, period.number, exc
                )
                continue
            vote_id = next(
                (v for v in (vote_id_from_row(r) for r in rows) if v is not None), None
            )
            if vote_id is None:
                log.info("No usable %s row for period %d", VOTE_TABLE, period.number)
                continue
            found[period.number] = vote_id
        log.info("Discovered roll-call votes for %d period(s)", len(found))
        return found

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
