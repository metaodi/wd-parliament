"""Read the Kantonsrat Zürich from the Staatsarchiv's KR-Daten register.

The third source, alongside :mod:`parliament` and :mod:`openparldata`. Same
contract, same dataclasses: everything downstream — :mod:`resolve`,
:mod:`diff`, :mod:`period_overlap`, :mod:`quickstatements` — works off
:class:`~.models.Member` and cannot tell which source produced it. The
row-mapping functions are module-level and pure, and that is how they are
tested: from fixture rows, never by mocking the service.

The source is the KRRR application's own Excel export — *Kantonsrat und
Regierungsrat*, the register the Staatsarchiv keeps:

    https://www.web.statistik.zh.ch:8443/KRRR/app?show_page=EXCEL&operation=EXCEL

**This is the first cantonal source whose identifier join is verified.** Run 28
(2026-08-10) compared every P13468 value on Wikidata against the export and
found ``id_person_new`` carrying it for **591 of 638** people (92.6%), with the
shortfall accounted for: 29 Wikidata values the register disagrees with — the
suggestion this tool exists to make — 14 name-matching artefacts, 4 undecided.
The same run read P13468's formatter URL from Wikidata and confirmed it
resolves through ``wahlen.zh.ch/krdaten_staatsarchiv/``, this register's own
query page. So ``config/kantonsrat-zh.yaml`` ships
``identifier_verified: true`` and P13468 is in
``models.VERIFIED_IDENTIFIER_PROPERTIES`` — the claim that cost three failed
sources to establish (OpenParlData run 20, the canton's Gever run 23, the
openZH LOD CSV measured offline; none of them publishes the value).

Five facts about the export shape this code, every one of them measured:

**It is a workbook, not a table.** Personen (4,665 rows), Einsitze (6,767),
Parteien (7,129), Funktionen (7,112), and more. People and mandates live in
different sheets, joined on ``id_person_new`` and ``id_einsitz_new``. Read
through :mod:`workbook`, which the probe reads it through too — one parser, so
what was measured is what is consumed.

**A ``Rat`` is not the Kantonsrat.** ``einsitze.rat`` holds ``Kantonsrat``,
``Grosser Rat``, ``Regierungsrat`` and ``Kleiner Rat``. :func:`is_seat_row`
matches it by **equality**, for the reason ``openparldata.is_kantonsrat`` does:
the row it must not match is a seven-member cantonal *executive*.

**Every date is split into day, month and year, and most are incomplete.**
A register reaching back to 1803 routinely knows the year and not the day, so
:func:`compose_date` returns ``None`` unless all three parts are present.
Filling a missing day with the 1st would state a precision the register does
not have — and P569, P580 and P582 are *dates*, so a wrong precision is a wrong
claim, written mechanically onto a real person. Skipping is the same fail-safe
``period_overlap`` applies to a member with no start date.

**The rows are per tenure, not per term.** 4,421 Kantonsrat seats across 3,367
people, ~1.3 each. So ``datum_eintritt`` **is** the tenure start —
:meth:`KrDatenClient.get_member_segments` correctly returns nothing to correct,
as OpenParlData's does and unlike ``MemberCouncil.DateJoining``, which needs
``MemberCouncilHistory`` to repair.

**There are no legislative periods.** No sheet carries them, so
:meth:`KrDatenClient.get_periods` returns ``[]`` and no P2937 is ever
suggested. Run 15 found P2937 on no statement for this seat anyway.

One thing this source cannot do, and it is worth saying where somebody will
look for it: it asserts nothing about Wikidata, so there is no
``get_link_conflicts`` here. ``app.process`` treats that absence as ordinary.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import Body, Member, Period, Tenure
from .workbook import Sheets, parse_export, resolve_column

log = logging.getLogger(__name__)

KRRR_URL = (
    "https://www.web.statistik.zh.ch:8443/KRRR/app?show_page=EXCEL&operation=EXCEL"
)

# Sheet names, as the export writes them. Looked up case-insensitively and by
# prefix (:func:`find_sheet`), because a workbook's sheet names are typed by
# hand somewhere and "Zivilstände" is one umlaut away from not matching.
SHEET_PERSONS = "Personen"
SHEET_SEATS = "Einsitze"
SHEET_PARTIES = "Parteien"
SHEET_FUNCTIONS = "Funktionen"

# The chamber. Matched by equality against ``einsitze.rat`` — the Regierungsrat
# is a seven-member executive and the Grosser Rat a predecessor body, and
# neither is this parliament.
KANTONSRAT = "Kantonsrat"

# What a client with no config is about. Q21518678 is "Mitglied des Zürcher
# Kantonsrat", confirmed by run 14 — and *not* Q19479543, which run 13 spent a
# whole run on before finding it is a Wikimedia category held by nobody.
DEFAULT_BODY = Body(
    council="KR", label="Cantonal Council of Zürich", position_qid="Q21518678"
)

# The join keys between sheets.
PERSON_KEY = "id_person_new"  # == P13468, verified by run 28
SEAT_KEY = "id_einsitz_new"

# Column candidates, resolved against the sheet's real headers by
# ``workbook.resolve_column``. Measured in run 28; the fallbacks are for the
# openZH CSV export of the same register, which names them in upper case with
# the same words.
NAME_FIELDS = ("nachname",)
FIRST_NAME_FIELDS = ("vorname",)
BIRTH_DATE_FIELDS = ("datum_geburt",)
DEATH_DATE_FIELDS = ("datum_tod",)
OCCUPATION_FIELDS = ("beruf",)
DISTRICT_FIELDS = ("wahlkreis",)
PARTY_FIELDS = ("parteibezeichnung",)
GROUP_FIELDS = ("fraktion",)
RAT_FIELDS = ("rat",)

# Date parts. The register splits every date; see the module docstring.
ENTRY_PARTS = ("datum_eintritt_tag", "datum_eintritt_monat", "datum_eintritt_jahr")
EXIT_PARTS = ("datum_austritt_tag", "datum_austritt_monat", "datum_austritt_jahr")


# --- pure: reading the workbook ---------------------------------------------
def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _tidy(value: Any) -> str:
    """Collapse the register's runs of spaces. Pure.

    ``'1.  Wahlkreis  (Zürich 1+2)'`` is one district however it is spaced, and
    the district is a *key* into the config's Q-ID map — an untidied one misses
    the map and silently makes no suggestion.
    """
    return " ".join(_text(value).split())


def _as_int(value: Any) -> Optional[int]:
    text = _text(value)
    try:
        return int(float(text)) if text else None
    except (TypeError, ValueError):
        return None


def compose_date(
    row: Dict[str, Any], parts: Sequence[str], headers: Sequence[str] = ()
) -> Optional[date]:
    """Day + month + year → a date, or ``None`` unless all three are there. Pure.

    **A partial date is no date.** The register knows the year for most of its
    older members and the day for few, and inventing 1 January would state a
    precision it does not have. Since these values become P569, P580 and P582 —
    and the seat dates are *mechanical* kinds — a wrong precision is a wrong
    statement written without review. See ``period_overlap``, which refuses in
    the same way for the same reason.
    """
    values: List[int] = []
    for part in parts:
        column = resolve_column(headers, part) if headers else part
        number = _as_int(row.get(column)) if column else None
        if number is None:
            return None
        values.append(number)
    day, month, year = values
    try:
        return date(year, month, day)
    except ValueError:
        log.debug("Skipping an impossible date: %s-%s-%s", year, month, day)
        return None


def parse_full_date(value: Any) -> Optional[date]:
    """``'1897-05-04 00:00:00.0'`` → a date. Pure. Anything else is ``None``."""
    text = _text(value).replace("T", " ")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def find_sheet(sheets: Sheets, wanted: str) -> Optional[str]:
    """The workbook's own name for a sheet, matched loosely. Pure.

    Case-insensitively and by prefix, because sheet names are typed by hand and
    an umlaut or a trailing space should not lose a whole table. Returns the
    real name so callers report what the workbook says, not what they hoped.
    """
    target = wanted.casefold()
    for name in sheets:
        if name.casefold() == target:
            return name
    for name in sheets:
        if name.casefold().startswith(target[:6]):
            return name
    return None


def rows_of(sheets: Sheets, wanted: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """``(headers, rows)`` of a sheet, or ``([], [])`` if it is absent. Pure."""
    name = find_sheet(sheets, wanted)
    return sheets.get(name, ([], [])) if name else ([], [])


def index_rows(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, List[Dict]]:
    """Group rows by a key column, keeping every one. Pure."""
    out: Dict[str, List[Dict]] = {}
    for row in rows:
        value = _text(row.get(key))
        if value:
            out.setdefault(value, []).append(row)
    return out


# --- pure: the seat ---------------------------------------------------------
def is_kantonsrat_row(row: Dict[str, Any], headers: Sequence[str] = ()) -> bool:
    """Is this Einsitz a seat in the Kantonsrat? Pure.

    Equality, never a substring: ``Regierungsrat`` contains ``rat`` and is a
    cantonal *executive*, and ``Grosser Rat`` is a predecessor body.
    """
    column = resolve_column(headers, RAT_FIELDS[0]) if headers else RAT_FIELDS[0]
    return _tidy(row.get(column or RAT_FIELDS[0])) == KANTONSRAT


def is_open_seat(
    row: Dict[str, Any], headers: Sequence[str] = (), today: Optional[date] = None
) -> bool:
    """Does somebody hold this seat today? Pure.

    Computed, not read: the export has no active flag. A seat is open when it
    records **no exit year at all** — note the *year*, not the composed date,
    because a member who left in 1917 with no recorded day still left. Reading
    the composed date would make every incompletely-dated departure look like a
    sitting member, which is precisely the failure that put 2,234 wrong
    suggestions into this repo's first live run.
    """
    exit_year_column = resolve_column(headers, EXIT_PARTS[2]) if headers else EXIT_PARTS[2]
    if _text(row.get(exit_year_column or EXIT_PARTS[2])):
        return False
    entry = compose_date(row, ENTRY_PARTS, headers)
    return entry is None or entry <= (today or date.today())


def party_for_seat(
    seat_id: str,
    parties_by_seat: Dict[str, List[Dict]],
    headers: Sequence[str] = (),
) -> Tuple[Optional[str], Optional[str]]:
    """``(party, parliamentary group)`` for a seat. Pure.

    The last row wins, because the sheet is one row per affiliation *spell* and
    a member who changed party mid-tenure has several. Only the current one is
    a claim about them now; the earlier ones are history this tool does not
    emit.
    """
    rows = parties_by_seat.get(seat_id) or []
    party = group = None
    for row in rows:
        column = resolve_column(headers, PARTY_FIELDS[0]) if headers else PARTY_FIELDS[0]
        value = _tidy(row.get(column or PARTY_FIELDS[0]))
        if value:
            party = value
        column = resolve_column(headers, GROUP_FIELDS[0]) if headers else GROUP_FIELDS[0]
        value = _tidy(row.get(column or GROUP_FIELDS[0]))
        if value:
            group = value
    return party, group


def member_from_rows(
    seat: Dict[str, Any],
    person: Dict[str, Any],
    body: Body,
    seat_headers: Sequence[str] = (),
    person_headers: Sequence[str] = (),
    party: Optional[str] = None,
    group: Optional[str] = None,
    today: Optional[date] = None,
) -> Optional[Member]:
    """One Einsitz plus its person → a :class:`Member`. Pure.

    ``None`` without a person id: that id is P13468's value and the join key to
    Wikidata, and a row lacking it cannot be reconciled — the rule
    ``parliament.member_from_row`` applies to ``PersonNumber`` and
    ``openparldata.member_from_rows`` to ``person_id``.
    """
    person_number = _as_int(seat.get(PERSON_KEY) or person.get(PERSON_KEY))
    if person_number is None:
        log.warning("Skipping an Einsitz with no %s: %s", PERSON_KEY, seat.get(SEAT_KEY))
        return None

    def field(row: Dict[str, Any], names: Sequence[str], headers: Sequence[str]) -> str:
        column = resolve_column(headers, names[0]) if headers else names[0]
        return _tidy(row.get(column or names[0]))

    district = field(seat, DISTRICT_FIELDS, seat_headers) or None
    occupation = field(person, OCCUPATION_FIELDS, person_headers)
    birth_column = (
        resolve_column(person_headers, BIRTH_DATE_FIELDS[0])
        if person_headers
        else BIRTH_DATE_FIELDS[0]
    )
    death_column = (
        resolve_column(person_headers, DEATH_DATE_FIELDS[0])
        if person_headers
        else DEATH_DATE_FIELDS[0]
    )
    return Member(
        person_number=person_number,
        first_name=field(person, FIRST_NAME_FIELDS, person_headers),
        last_name=field(person, NAME_FIELDS, person_headers),
        active=is_open_seat(seat, seat_headers, today=today),
        council=body.council,
        council_name=body.label,
        council_number=body.council_number,
        # The Wahlkreis — ``Member.constituency_*`` is the generic P768 key
        # and report-grouping field for both. Same convention as
        # ``openparldata``.
        constituency_abbreviation=district,
        constituency_name=district,
        party_name=party,
        party_abbreviation=party,
        parl_group_name=group,
        parl_group_abbreviation=group,
        # Per-tenure rows, so this really is the tenure start.
        date_joining=compose_date(seat, ENTRY_PARTS, seat_headers),
        date_leaving=compose_date(seat, EXIT_PARTS, seat_headers),
        date_of_birth=parse_full_date(person.get(birth_column or "")),
        date_of_death=parse_full_date(person.get(death_column or "")),
        occupations=[occupation] if occupation else [],
    )


def members_from_sheets(
    sheets: Sheets,
    body: Body,
    active_only: bool = True,
    today: Optional[date] = None,
) -> List[Member]:
    """The whole workbook → members of one chamber. Pure.

    De-duplicated on ``(person_number, council)`` keeping the latest-starting
    seat, so somebody who left and returned is one member holding their current
    tenure — the rule both other adapters follow. A seat whose person is
    missing from Personen still produces a member with a blank name: the seat
    is the fact being reconciled, and dropping it would quietly shrink the
    chamber.
    """
    person_headers, person_rows = rows_of(sheets, SHEET_PERSONS)
    seat_headers, seat_rows = rows_of(sheets, SHEET_SEATS)
    party_headers, party_rows = rows_of(sheets, SHEET_PARTIES)
    persons = {_text(r.get(PERSON_KEY)): r for r in person_rows}
    parties_by_seat = index_rows(party_rows, SEAT_KEY)

    best: Dict[Tuple[int, str], Member] = {}
    for seat in seat_rows:
        if not is_kantonsrat_row(seat, seat_headers):
            continue
        person = persons.get(_text(seat.get(PERSON_KEY)), {})
        party, group = party_for_seat(
            _text(seat.get(SEAT_KEY)), parties_by_seat, party_headers
        )
        member = member_from_rows(
            seat,
            person,
            body,
            seat_headers=seat_headers,
            person_headers=person_headers,
            party=party,
            group=group,
            today=today,
        )
        if member is None or (active_only and not member.active):
            continue
        key = (member.person_number, member.council)
        current = best.get(key)
        if current is None or _later(member, current):
            best[key] = member
    return sorted(best.values(), key=lambda m: (m.last_name, m.first_name))


def _later(candidate: Member, current: Member) -> bool:
    """Does ``candidate`` start after ``current``? Pure.

    An active seat always beats an inactive one whatever the dates say: the
    question this ordering answers is "which tenure is theirs *now*", and a
    seat with no exit is the answer regardless of when it began. Dates only
    break the remaining ties, and a member with no start date loses to one who
    has one rather than winning by accident.
    """
    if candidate.active != current.active:
        return candidate.active
    if candidate.date_joining is None:
        return False
    if current.date_joining is None:
        return True
    return candidate.date_joining > current.date_joining


def tenures_from_sheets(
    sheets: Sheets, council: str, today: Optional[date] = None
) -> Dict[Tuple[int, str], Tenure]:
    """When did each person hold this seat, including people who have left?

    Pure. Keyed ``(person_number, council)`` for the reason ``Tenure`` is: a
    person is not a seat, and somebody who sat in two chambers has two tenures.
    Read by the reverse walk in ``diff`` to date a departure Wikidata still
    records as open — which is the whole reason a *historic* register is worth
    reading at all.
    """
    seat_headers, seat_rows = rows_of(sheets, SHEET_SEATS)
    out: Dict[Tuple[int, str], Tenure] = {}
    for seat in seat_rows:
        if not is_kantonsrat_row(seat, seat_headers):
            continue
        person_number = _as_int(seat.get(PERSON_KEY))
        if person_number is None:
            continue
        start = compose_date(seat, ENTRY_PARTS, seat_headers)
        end = compose_date(seat, EXIT_PARTS, seat_headers)
        key = (person_number, council)
        current = out.get(key)
        if current is None or _later_tenure(start, end, current):
            out[key] = Tenure(
                person_number=person_number,
                council=council,
                start=start,
                end=end,
            )
    return out


def _later_tenure(
    start: Optional[date], end: Optional[date], current: Tenure
) -> bool:
    """Is this spell more recent than the one already recorded? Pure."""
    if start is None:
        return False
    if current.start is None:
        return True
    return start > current.start


# --- network ----------------------------------------------------------------
class KrDatenClient:
    """Fetch and map the KRRR export. The only network caller in this module.

    The workbook is read **once** and cached: it is a single request that
    answers every question this class is asked, and re-fetching per chamber
    would multiply a several-megabyte download by nothing useful.
    """

    def __init__(
        self,
        session: Any,
        url: str = KRRR_URL,
        language: str = "de",
        bodies: Optional[Sequence[Body]] = None,
        timeout: int = 120,
    ) -> None:
        self.session = session
        self.url = url
        self.language = language
        # The chambers this config asks for. Defaulted rather than required so
        # a probe can construct the client with nothing but a session.
        self.bodies: List[Body] = list(bodies or [DEFAULT_BODY])
        self.timeout = timeout
        self._sheets: Optional[Sheets] = None

    def sheets(self) -> Sheets:
        """The workbook, fetched at most once."""
        if self._sheets is None:
            log.info("Fetching the KR-Daten export from %s", self.url)
            response = self.session.get(self.url, timeout=self.timeout)
            response.raise_for_status()
            fmt, sheets, error = parse_export(
                response.content, response.headers.get("Content-Type", "")
            )
            if error:
                raise RuntimeError(f"Could not read the KR-Daten export ({fmt}): {error}")
            log.info(
                "Read %d sheet(s): %s",
                len(sheets),
                ", ".join(f"{n} ({len(r)})" for n, (_, r) in sheets.items()),
            )
            self._sheets = sheets
        return self._sheets

    def _wanted(self, councils: Optional[Sequence[str]]) -> List[Body]:
        """The bodies this call is about. ``None`` means all of them."""
        if councils is None:
            return list(self.bodies)
        # A bare string is a plausible slip and iterating it would ask for
        # councils 'K' and 'R', matching nothing and returning an empty list
        # that reads as "this chamber has no members" — the failure shape this
        # repo has paid for twice.
        if isinstance(councils, str):
            councils = [councils]
        wanted = {c.strip().upper() for c in councils}
        return [b for b in self.bodies if b.council.upper() in wanted]

    def get_members(
        self,
        councils: Optional[Sequence[str]] = None,
        active_only: bool = True,
        today: Optional[date] = None,
    ) -> List[Member]:
        """Sitting members of the configured chambers, as dataclasses."""
        out: List[Member] = []
        for body in self._wanted(councils):
            out.extend(
                members_from_sheets(
                    self.sheets(), body, active_only=active_only, today=today
                )
            )
        log.info("Read %d member(s) from the KR-Daten register", len(out))
        return out

    def get_tenures(
        self, councils: Optional[Sequence[str]] = None
    ) -> Dict[Tuple[int, str], Tenure]:
        """``(person id, council)`` → their latest spell, ended ones included.

        Costs no extra request: the ended Einsitze rows are in the same
        workbook ``get_members`` filters down to seats held today.
        """
        out: Dict[Tuple[int, str], Tenure] = {}
        for body in self._wanted(councils):
            out.update(tenures_from_sheets(self.sheets(), body.council))
        log.info("Tenure dates available for %d (person, council) pair(s)", len(out))
        return out

    def get_periods(self) -> List[Period]:
        """No sheet carries legislative periods, so no P2937 is ever suggested.

        A fail-safe, not a gap being papered over: with no periods
        ``period_overlap.assign_periods`` assigns none and ``diff`` emits no
        qualifier. Do not "fix" this by inventing four-year windows — the
        register records mandates, not terms.
        """
        return []

    def get_member_segments(self, *args: Any, **kwargs: Any) -> Dict[Any, Any]:
        """Nothing to correct: the Einsitze rows are already per tenure.

        ``MemberCouncil.DateJoining`` needs ``MemberCouncilHistory`` because it
        is a mandate *segment* start. Here 4,421 seats span 3,367 people, so
        ``datum_eintritt`` is the tenure start already.
        """
        return {}
