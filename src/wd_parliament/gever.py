"""Read members of a parliament from a CMI CDWS *Gever*.

The fourth source, beside :mod:`parliament`, :mod:`openparldata` and
:mod:`krdaten`, and the first one this tool reads because the obvious source
**had no seats**. Run 34 (2026-08-18) found OpenParlData holding 807 person
records for the city of Zürich and zero membership rows under the Gemeinderat's
group, so ``config/gemeinderat-zuerich.yaml`` could not produce a single
member. Run 35 then measured the city's own Geschäftsverwaltungssystem and
found exactly the table that was missing.

Same contract as the other three: everything downstream — :mod:`resolve`,
:mod:`diff`, :mod:`period_overlap`, :mod:`quickstatements` — works off
:class:`~.models.Member` and cannot tell which source produced it. As
everywhere else the row-mapping functions are module-level and pure, and that
is how they are tested: from fixture rows, never by mocking the API.

Five facts about this source shape the code, all measured (runs 35 and 36):

**The person and the mandate are separate indexes.** ``behoerdenmandat`` is
one row per mandate (3,700 of them across 61 Gremien) and ``kontakt`` is one
row per person (708). They join on ``kontaktguid`` → ``obj_guid``. This is
unlike the canton's Gever, which has a single ``MITGLIEDER`` index — so do not
carry assumptions across from :mod:`scripts.verify_gever`.

**A mandate that has not ended carries 9999-12-31 23:59:59, not a null.** See
:data:`OPEN_END_FROM`. This is ``parliament.NULL_DATE`` at the other end of the
axis and it does the same kind of damage: unmapped, every sitting member gets a
P582 of 9999-12-31, and the whole chamber reads as departed. It is mapped at
this boundary because nothing downstream can catch it — ``diff`` has no way to
tell a sentinel from a date somebody meant.

**The chamber is a ``gremium`` value, matched by equality.** The city's
**Stadtrat** — its nine-member executive — sits inside the council's own
mandate list as ``Mitglied Stadtrat`` / ``Präsidium Stadtrat``, and ``Büro des
Gemeinderats`` is an organ of the chamber. The parties are modelled as Gremien
too. A substring match takes all of them.

**The seat roles are an allowlist, and it was derived.** Among the chamber's
143 open rows: ``Mitglied`` 122, plus one each of ``Präsidium``,
``1. Vizepräsidium`` and ``2. Vizepräsidium`` = **125**, the chamber, with
``Mitglied Stadtrat`` 8, ``Präsidium Stadtrat`` 1, ``Stimmenzählende`` 6 and
``Ratssekretariat`` 3 outside it. That is the cantonal shape exactly — a
presiding member has no separate ``Mitglied`` row, so their presidium row *is*
their seat — reached from this parliament's own values rather than borrowed.

**The electoral district is on the *mandate*.** ``wahlkreis`` is filled on
3,360 of 3,700 mandate rows, and that is strictly better than OpenParlData,
which keeps the district on the person: there, somebody who also sat
cantonally or federally carries *that* seat's district, which is why body 261
showed 17 districts for 9 seats.

What this source **cannot** do, and must not pretend to:

**It has no identifier any Wikidata property holds.** Run 23 established it for
the canton and run 35 confirmed it for the city: six GUID columns, none of them
the value of any property. So a Gever config sets
``identifier_from_source: false``, every member is matched **by name**, and
``quickstatements.is_mechanical`` refuses everything — which is the correct
first release, and the same place the federal side started.

**Its birth data is a *year*, not a date.** ``jahrgang`` is ``'1936'``. That is
not a P569 value and, more importantly, it cannot corroborate a name match the
way a date can — :func:`resolve.select_person_match` needs a date to tell two
namesakes apart. It is deliberately **not** mapped to
:attr:`~.models.Member.date_of_birth`: a 1 January would be a precision this
source never claimed, and it would make the name fallback confidently wrong
rather than merely uncertain.

**It has no legislative periods**, so :meth:`GeverClient.get_periods` returns
an empty list and no P2937 is ever suggested — the same fail-safe as
:mod:`openparldata`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import Body, Member, Period, Tenure

log = logging.getLogger(__name__)

PERSON_INDEX = "kontakt"
MANDATE_INDEX = "behoerdenmandat"

DEFAULT_LANGUAGE = "de"
# ``swissparlpy.client.GEVER_INSTANCES`` maps this to the ``city_zurich``
# instance of ``backends/gever_config.py``. Named rather than given as a URL,
# because the instance carries the whole index map and a URL carries one
# endpoint.
DEFAULT_BACKEND = "gever_city_zurich"

# ⚠️ An open mandate carries ``9999-12-31 23:59:59``.
#
# The threshold is loose rather than an equality test, the same shape as
# ``parliament.NULL_DATE``'s "anything below": a service that spells its
# infinity 2999-12-31 means the same thing by it, and no parliament records a
# mandate ending in the 30th century. Run 35 is what this cost — the probe
# printed "870 ended, 0 open-ended" one line above three sitting members.
OPEN_END_FROM = date(2900, 1, 1)

# Roles that hold a seat. **Derived** (run 36), not copied from the canton, and
# the agreement with the cantonal list is evidence precisely because it was not
# assumed. See the module docstring for the funnel.
DEFAULT_SEAT_ROLES = (
    "Mitglied",
    "Präsidium",
    "1. Vizepräsidium",
    "2. Vizepräsidium",
)

# Column names resolved from the rows rather than assumed — the rule
# ``openparldata.BEGIN_FIELDS`` states and this module has no reason to break.
# The first name in each tuple is the one run 35 measured; the rest cost
# nothing and another CMI instance may use them.
BEGIN_FIELDS = ("dauer_start", "beginn", "eintritt")
END_FIELDS = ("dauer_end", "ende", "austritt")
BODY_FIELDS = ("gremium", "behoerde", "gremiumname")
ROLE_FIELDS = ("funktion", "rolle", "role")
PERSON_REF_FIELDS = ("kontaktguid", "personguid", "kontakt_guid")
PERSON_KEY_FIELDS = ("obj_guid", "objguid", "guid")
DISTRICT_FIELDS = ("wahlkreis",)
PARTY_FIELDS = ("partei", "parteizugehoerigkeit")
GROUP_FIELDS = ("fraktion",)
LAST_NAME_FIELDS = ("name", "nachname")
FIRST_NAME_FIELDS = ("vorname",)
OCCUPATION_FIELDS = ("beruf",)
WEBSITE_FIELDS = ("homepageprivat", "homepage")
# Read, reported, and deliberately NOT mapped to a date. See the docstring.
BIRTH_YEAR_FIELDS = ("jahrgang",)
DEATH_YEAR_FIELDS = ("todesjahr",)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _tidy(value: Any) -> str:
    """Collapse runs of whitespace, keeping case."""
    return " ".join(_text(value).split())


def _first(row: Dict[str, Any], candidates: Sequence[str]) -> Any:
    for name in candidates:
        if row.get(name) not in (None, ""):
            return row[name]
    return None


def _present(rows: Sequence[Dict[str, Any]], candidates: Sequence[str]) -> Optional[str]:
    """The first candidate column any of these rows carries. Pure.

    ``None`` is a real answer: "no such column" and "column full of nulls" are
    indistinguishable through ``.get()`` and mean opposite things.
    """
    for name in candidates:
        if any(name in row for row in rows):
            return name
    return None


def _as_date(value: Any) -> Optional[date]:
    """Coerce a CDWS value to a ``date``; ``None`` if unusable. Pure.

    ``swissparlpy``'s Gever backend hands back ``datetime`` objects, but the
    service also writes plain 8-digit strings and ISO text depending on the
    field, and a probe reading raw XML sees those. All three are accepted;
    anything else is ``None`` rather than an exception, for the reason
    ``parliament._as_date`` gives — one malformed date must not cost the whole
    run.

    This does **not** map the open-end sentinel: that is :func:`end_date`'s
    job, and keeping them separate is what lets a caller ask "what does this
    field literally say" as well as "has it ended".
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:]))
        except ValueError:
            return None
    try:
        return date.fromisoformat(text.replace(" ", "T").split("T")[0])
    except ValueError:
        return None


def end_date(value: Any) -> Optional[date]:
    """The end of a mandate, or ``None`` if it has not ended. Pure.

    See :data:`OPEN_END_FROM`. ``None`` means open, and it is the **only**
    thing that may mean open — a sentinel read as a date turns every sitting
    member into a departed one and files a P582 of 9999-12-31.
    """
    parsed = _as_date(value)
    if parsed is None or parsed >= OPEN_END_FROM:
        return None
    return parsed


def body_of(row: Dict[str, Any], bodies: Sequence[Body]) -> Optional[Body]:
    """The configured chamber this mandate row belongs to, or ``None``. Pure.

    Matched by **equality** against the body's ``group_name`` (falling back to
    its label), never by substring. The city makes that discipline earn its
    keep three times over: ``Mitglied Stadtrat`` rows sit in the chamber's own
    list because the executive attends it, ``Büro des Gemeinderats`` is an
    organ of the chamber, and the parties are modelled as Gremien too.
    """
    value = _tidy(_first(row, BODY_FIELDS)).casefold()
    if not value:
        return None
    for body in bodies:
        wanted = _tidy(body.group_name or body.label).casefold()
        if wanted and wanted == value:
            return body
    return None


def has_seat_role(
    row: Dict[str, Any], seat_roles: Optional[Sequence[str]] = None
) -> bool:
    """Does this mandate's role hold a seat? Pure.

    An allowlist, not a denylist, for the reason run 36 established: a
    presiding member has no separate ``Mitglied`` row, so excluding the
    presidium loses three real members, while including ``Mitglied Stadtrat``
    gains nine people who hold no seat in this chamber at all. Neither polarity
    self-corrects when the source adds a role — what protects the count is
    ``scripts/verify_gever_city.py`` printing every role it sees and refusing
    to call a total that is not the chamber's size CONFIRMED.

    ``None`` means "every role counts", which is only ever right for a
    parliament whose roles nobody has looked at yet.
    """
    roles = DEFAULT_SEAT_ROLES if seat_roles is None else seat_roles
    if not roles:
        return True
    value = _tidy(_first(row, ROLE_FIELDS)).casefold()
    return any(_tidy(r).casefold() == value for r in roles)


def is_seat_row(
    row: Dict[str, Any],
    today: Optional[date] = None,
    seat_roles: Optional[Sequence[str]] = None,
) -> bool:
    """Is this mandate a seat held *today*? Pure.

    Three conditions, and the source has no flag for any of them — ``active``
    is computed here, exactly as in :mod:`openparldata`. A row must have begun
    (run 36 saw future starts among the open rows), must not have ended, and
    must be in a seat role.
    """
    today = today or date.today()
    start = _as_date(_first(row, BEGIN_FIELDS))
    if start is None or start > today:
        return False
    end = end_date(_first(row, END_FIELDS))
    if end is not None and end < today:
        return False
    return has_seat_role(row, seat_roles)


def person_key(row: Dict[str, Any]) -> str:
    """The person a mandate row points at, as a string GUID. Pure.

    A string and not an int, which every other source's person id is. That is
    load-bearing rather than incidental: it is *why* this source declares
    ``identifier_from_source: false``. A GUID is not the value of any Wikidata
    property, so it must never be offered as one — see ``diff`` and the module
    docstring.
    """
    return _text(_first(row, PERSON_REF_FIELDS)).strip()


def index_persons(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """``obj_guid`` → the person record. Pure."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = _text(_first(row, PERSON_KEY_FIELDS)).strip()
        if key:
            out[key] = row
    return out


def member_from_rows(
    mandate: Dict[str, Any],
    person: Optional[Dict[str, Any]],
    body: Body,
) -> Member:
    """One :class:`~.models.Member` from a mandate row and its person. Pure.

    The mandate wins where both carry a field. That is not arbitrary: the
    district on the mandate is the district of *this* seat, while the one on a
    person record is whatever seat was recorded last — the failure that gave
    OpenParlData 17 districts for 9 seats.
    """
    person = person or {}
    district = _first(mandate, DISTRICT_FIELDS) or _first(person, DISTRICT_FIELDS)
    party = _first(mandate, PARTY_FIELDS) or _first(person, PARTY_FIELDS)
    website = _first(person, WEBSITE_FIELDS)
    occupation = _first(person, OCCUPATION_FIELDS)

    return Member(
        # A GUID, deliberately. See ``person_key``.
        person_number=person_key(mandate),
        first_name=_tidy(_first(mandate, FIRST_NAME_FIELDS)
                         or _first(person, FIRST_NAME_FIELDS)),
        last_name=_tidy(_first(mandate, LAST_NAME_FIELDS)
                        or _first(person, LAST_NAME_FIELDS)),
        active=True,
        council=body.council,
        council_name=body.label,
        constituency_abbreviation=_tidy(district) or None,
        constituency_name=_tidy(district) or None,
        party_name=_tidy(party) or None,
        party_abbreviation=_tidy(party) or None,
        parl_group_name=_tidy(_first(person, GROUP_FIELDS)) or None,
        parl_group_abbreviation=_tidy(_first(person, GROUP_FIELDS)) or None,
        date_joining=_as_date(_first(mandate, BEGIN_FIELDS)),
        date_leaving=end_date(_first(mandate, END_FIELDS)),
        # date_of_birth is left EMPTY on purpose: `jahrgang` is a year, and a
        # year invented into 1 January is a precision this source never
        # claimed. It would also make the name fallback confidently wrong
        # about two namesakes rather than honestly uncertain.
        occupations=[_tidy(occupation)] if occupation else [],
        website=_tidy(website) or None,
    )


def members_from_rows(
    mandates: Sequence[Dict[str, Any]],
    persons: Sequence[Dict[str, Any]],
    bodies: Sequence[Body],
    today: Optional[date] = None,
    seat_roles: Optional[Sequence[str]] = None,
) -> List[Member]:
    """Sitting members of the configured chambers. Pure.

    De-duplicated on ``(person, council)``: a member who holds both a
    ``Mitglied`` row and a presidium row in the same chamber is one member with
    one seat, and counting rows would report 143 where the chamber has 125.
    The *earliest* start wins, because the tenure is the span the person has
    held the seat and a presidium row starts partway through it.
    """
    by_guid = index_persons(persons)
    best: Dict[tuple, Member] = {}
    for row in mandates:
        body = body_of(row, bodies)
        if body is None:
            continue
        if not is_seat_row(row, today=today, seat_roles=seat_roles):
            continue
        member = member_from_rows(row, by_guid.get(person_key(row)), body)
        if not member.person_number:
            continue
        key = (member.person_number, body.council.upper())
        current = best.get(key)
        if current is None or (
            member.date_joining is not None
            and (current.date_joining is None or member.date_joining < current.date_joining)
        ):
            best[key] = member
    return list(best.values())


def tenures_from_rows(
    mandates: Sequence[Dict[str, Any]],
    bodies: Sequence[Body],
    seat_roles: Optional[Sequence[str]] = None,
) -> Dict[tuple, Tenure]:
    """``(person, council)`` → their latest spell, ended ones included. Pure.

    A different question from :func:`members_from_rows` — "when did somebody
    hold this seat", asked about people the members table does not contain —
    off the same rows, so it costs no extra request. Keyed by ``(person,
    council)`` and not by person, for the reason ``seats_by_seat`` is: a person
    is not a seat, and somebody who moved between chambers would otherwise
    have one chained onto the other.
    """
    best: Dict[tuple, Tenure] = {}
    for row in mandates:
        body = body_of(row, bodies)
        if body is None:
            continue
        if not has_seat_role(row, seat_roles):
            continue
        key_guid = person_key(row)
        if not key_guid:
            continue
        tenure = Tenure(
            person_number=key_guid,
            council=body.council.upper(),
            start=_as_date(_first(row, BEGIN_FIELDS)),
            end=end_date(_first(row, END_FIELDS)),
        )
        current = best.get(tenure.key)
        if current is None or (tenure.start or date.min) > (current.start or date.min):
            best[tenure.key] = tenure
    return best


class GeverClient:
    """Fetch members of a parliament from a CMI CDWS Gever.

    Mirrors the surface of :class:`~.openparldata.OpenParlDataClient` so
    ``app.process`` can use either without knowing which it has. The client is
    built lazily for the same reason: constructing it reads the service's
    schema over the network, which must not happen at import time.
    """

    def __init__(
        self,
        session: Any = None,
        language: str = DEFAULT_LANGUAGE,
        backend: str = DEFAULT_BACKEND,
        bodies: Optional[Sequence[Body]] = None,
        seat_roles: Optional[Sequence[str]] = None,
        client: Any = None,
    ) -> None:
        self.language = (language or DEFAULT_LANGUAGE).lower()
        self.backend = backend or DEFAULT_BACKEND
        self.bodies = list(bodies or [])
        self.seat_roles = list(seat_roles) if seat_roles is not None else list(
            DEFAULT_SEAT_ROLES
        )
        self._client = client
        self._session = session
        # Both indexes are read whole, once. ``get_members`` and
        # ``get_tenures`` ask different questions of the same mandate rows.
        self._mandates: Optional[List[Dict[str, Any]]] = None
        self._persons: Optional[List[Dict[str, Any]]] = None

    @property
    def client(self) -> Any:
        if self._client is None:
            import swissparlpy as spp

            self._client = spp.SwissParlClient(
                session=self._session, backend=self.backend
            )
        return self._client

    def _rows(self, table: str) -> List[Dict[str, Any]]:
        log.debug("Fetching %s", table)
        return [dict(r) for r in self.client.get_data(table)]

    def _mandate_rows(self) -> List[Dict[str, Any]]:
        if self._mandates is None:
            self._mandates = self._rows(MANDATE_INDEX)
            log.info(
                "Gever: %d mandate row(s) in %r", len(self._mandates), MANDATE_INDEX
            )
        return self._mandates

    def _person_rows(self) -> List[Dict[str, Any]]:
        if self._persons is None:
            self._persons = self._rows(PERSON_INDEX)
            log.info(
                "Gever: %d person record(s) in %r", len(self._persons), PERSON_INDEX
            )
        return self._persons

    def get_members(
        self,
        councils: Optional[Sequence[str]] = None,
        active_only: bool = True,
        today: Optional[date] = None,
    ) -> List[Member]:
        """Sitting members of the configured chambers, as dataclasses.

        ``today`` is "as of what date is a mandate a seat" — pinned by the
        tests so a fixture's future ``dauer_start`` does not silently become a
        seat as the calendar moves past it.
        """
        wanted = {c.strip().upper() for c in councils} if councils else None
        bodies = [
            b for b in self.bodies
            if wanted is None or b.council.upper() in wanted
        ]
        if not bodies:
            return []
        mandates = self._mandate_rows()
        members = members_from_rows(
            mandates,
            self._person_rows(),
            bodies,
            today=today,
            seat_roles=self.seat_roles,
        )
        for body in bodies:
            n = sum(1 for m in members if m.council.upper() == body.council.upper())
            log.info(
                "[%s] %s: %d member(s) from %d mandate row(s)",
                body.council,
                body.label,
                n,
                len(mandates),
            )
        return members

    def get_tenures(
        self, councils: Optional[Sequence[str]] = None
    ) -> Dict[tuple, Tenure]:
        """``(person, council)`` → their latest spell, from the same rows."""
        wanted = {c.strip().upper() for c in councils} if councils else None
        bodies = [
            b for b in self.bodies
            if wanted is None or b.council.upper() in wanted
        ]
        out = tenures_from_rows(
            self._mandate_rows(), bodies, seat_roles=self.seat_roles
        )
        log.info("Tenure dates available for %d (person, council) pair(s)", len(out))
        return out

    def get_periods(self) -> List[Period]:
        """No legislative periods: this service carries none.

        A fail-safe rather than a gap papered over — with no periods,
        ``period_overlap.assign_periods`` assigns none, ``diff`` emits no
        P2937, and the ``terms:`` map stays as empty as it ships.
        """
        log.info(
            "Gever has no legislative-period index; P2937 will not be "
            "suggested, which is what the empty 'terms' map says anyway."
        )
        return []

    def get_member_segments(
        self, councils: Optional[Sequence[str]] = None
    ) -> Dict[tuple, List[Member]]:
        """Nothing to correct: ``dauer_start`` is already the tenure start.

        Federally this reads ``MemberCouncilHistory`` because
        ``MemberCouncil.DateJoining`` is a mandate *segment* start. Here a
        mandate row spans the whole spell, and :func:`members_from_rows`
        already takes the earliest of a member's rows in the chamber, so
        there is nothing left to chain.
        """
        return {}
