"""Read members of a cantonal parliament from OpenParlData.

The second source, alongside :mod:`parliament`. Same contract, same
dataclasses: everything downstream — :mod:`resolve`, :mod:`diff`,
:mod:`period_overlap`, :mod:`quickstatements` — works off :class:`~.models.Member`
and cannot tell which source produced it. As in ``parliament.py`` the
row-mapping functions are module-level and pure, and that is how they are
tested: from fixture rows, never by mocking the API.

Everything here was measured before it was written (README step 7, runs 13-15
on 2026-07-30). The four facts that shape the code:

**A body is a level of parliament, a chamber is a group.** The canton is the
body (``ZH``) and the Kantonsrat is a *group* under it — id 5077, ``name_de``
``'Kantonsrat Zürich'``. A seat is a ``memberships`` row pointing at that group,
so the members are fetched per group id rather than from any chamber-shaped
table.

**Counting open rows is not counting members.** The rows carry a
``role_name_de``, and the group holds ``Gast`` and presidium rows besides
``Mitglied``. Run 15's funnel: 186 open → 182 already begun → **180** in a seat
role, which is the chamber exactly. Three of those 180 are the presiding
officers, whose ``Präsidium`` / ``Vizepräsidium`` row *is* their seat — they
have no ``Mitglied`` row — which is why :data:`DEFAULT_SEAT_ROLES` is an
allowlist of roles that hold a seat rather than a denylist of roles that do not.

**The rows are per *tenure*, not per term.** 913 memberships across 834 people,
with each election contributing only ~40-50 new rows rather than 180. So unlike
``MemberCouncil.DateJoining`` — a mandate *segment* start that needs
``MemberCouncilHistory`` and :func:`parliament.tenure_start` to repair —
``begin_date`` **is** the tenure start, and :meth:`OpenParlDataClient.get_member_segments`
correctly returns nothing to correct.

**There are no legislative periods to read.** No table carries them, so
:meth:`OpenParlDataClient.get_periods` returns an empty list. That is a
fail-safe, not a gap being papered over: with no periods
:func:`period_overlap.assign_periods` assigns none, ``diff`` emits no P2937, and
the ``terms:`` map stays as empty as the federal one ships. Run 15 found P2937
on *no* statement for the seat anyway, so there is no convention to follow yet.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import Body, Member, Period, Tenure

log = logging.getLogger(__name__)

MEMBERSHIP_TABLE = "memberships"
PERSON_TABLE = "persons"
GROUP_TABLE = "groups"

DEFAULT_LANGUAGE = "de"

# Roles that hold a seat. See the module docstring: a presiding member has no
# ``Mitglied`` row, so excluding the presidium loses three real members, while
# including ``Gast`` gains one who is not a member at all. Neither an allowlist
# nor a denylist self-corrects when the source adds a role — what protects the
# count is ``scripts/verify_kantonsrat.py`` printing every role it sees and
# refusing to call a total that is not the chamber's size CONFIRMED.
DEFAULT_SEAT_ROLES = ("Mitglied", "Präsidium", "1. Vizepräsidium", "2. Vizepräsidium")

# Column names, resolved from the rows rather than assumed. "No such column" and
# "column full of nulls" are indistinguishable through ``.get()`` and mean
# opposite things — reading ``date_start`` (which is what ``speeches`` calls it)
# instead of ``begin_date`` once made every seat membership look undated.
BEGIN_FIELDS = ("begin_date", "date_begin", "start_date")
END_FIELDS = ("end_date", "date_end", "stop_date")
ROLE_FIELDS = ("role_name_de", "role_name", "role_harmonized", "role")
DISTRICT_FIELDS = (
    "electoral_district_de",
    "electoral_district",
    "district_de",
    "district",
)
PARTY_NAME_FIELDS = ("party_name_de", "party_name", "party_harmonized", "party")
BIRTH_FIELDS = ("birthdate", "birth_date", "date_of_birth")
DEATH_FIELDS = ("deathdate", "death_date", "date_of_death")

# Person columns the personal-data checks read (README step 9). **None of these
# has been measured**: the ZH person records this project has seen carry a
# name, a district, a party and a birth date, and whether the API also
# publishes a birthplace, an occupation or a website is exactly what
# ``scripts/verify_person_data.py`` exists to find out.
#
# Guessing here is safe in a way guessing a Q-ID is not — a name that matches
# no column yields no value and therefore no suggestion — but it is still a
# guess, and the probe prints the real column list so it need not stay one.
BIRTHPLACE_FIELDS = (
    "birthplace",
    "birth_place",
    "place_of_birth",
    "birthplace_de",
    "hometown_de",
)
ORIGIN_FIELDS = ("place_of_origin", "origin", "citizenship", "hometown")
OCCUPATION_FIELDS = (
    "occupation_de",
    "occupation",
    "profession_de",
    "profession",
    "job_title",
)
WEBSITE_FIELDS = ("website", "url", "homepage", "website_url")
CHILDREN_FIELDS = ("number_of_children", "children")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _tidy(value: Any) -> str:
    """Collapse runs of whitespace, keeping case.

    The district names are not tidy: run 15 returned ``'I      Zürich 1+2'``
    with six spaces. That string becomes the key the ``cantons`` map is looked
    up by and the heading the report groups under, so it is normalised at the
    mapping boundary rather than everywhere it is used.
    """
    return " ".join(_text(value).split())


def _as_date(value: Any) -> Optional[date]:
    """Coerce an API date (or ISO string) to a ``date``; ``None`` if unusable.

    Unlike ``parliament._as_date`` there is no null-date sentinel to map: this
    API sends a real ``null``. Any new source needs its own audit here — the
    1753-01-01 that ``parliament.NULL_DATE`` catches is a fact about SQL Server,
    not about dates in general.
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
    try:
        return date.fromisoformat(text.replace(" ", "T").split("T")[0])
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


def _present(rows: Sequence[Dict[str, Any]], candidates: Sequence[str]) -> Optional[str]:
    """The first candidate column that exists on these rows. Pure."""
    keys = {key for row in rows for key in row}
    return next((c for c in candidates if c in keys), None)


def _first(row: Dict[str, Any], candidates: Sequence[str]) -> Any:
    for name in candidates:
        if row.get(name) not in (None, ""):
            return row[name]
    return None


def _split_list(value: Any) -> List[str]:
    """A multi-valued free-text field as a list. Pure.

    Semicolons and JSON-style lists separate; a comma does not, because an
    occupation or a place name may contain one and splitting on it would invent
    values. Whitespace is tidied for the same reason it is on the districts.
    """
    if isinstance(value, (list, tuple)):
        return [_tidy(v) for v in value if _tidy(v)]
    text = _tidy(value)
    if not text:
        return []
    return [p.strip() for p in text.split(";") if p.strip()]


def _split_name(person: Dict[str, Any]) -> tuple:
    """``(first, last)`` from a person record. Pure.

    Prefers the explicit fields; falls back to splitting ``fullname`` on the
    last space, which is wrong for compound surnames but is only ever used for
    display and for the name fallback that ``resolve`` corroborates anyway.
    """
    first = _tidy(person.get("firstname"))
    last = _tidy(person.get("lastname"))
    if first or last:
        return first, last
    full = _tidy(person.get("fullname"))
    if not full:
        return "", ""
    head, _, tail = full.rpartition(" ")
    return (head, tail) if head else ("", full)


def has_seat_role(
    row: Dict[str, Any], seat_roles: Optional[Sequence[str]] = None
) -> bool:
    """Does this membership's role hold a seat? Pure.

    An empty ``seat_roles`` accepts every row, which is what a caller that has
    not been told the allowlist should get: the roles are a fact about one
    source, and inventing one here would be the guess :data:`DEFAULT_SEAT_ROLES`
    exists to avoid.
    """
    if not seat_roles:
        return True
    role = _tidy(_first(row, ROLE_FIELDS)).casefold()
    return role in {_tidy(r).casefold() for r in seat_roles}


def is_seat_row(
    row: Dict[str, Any],
    today: Optional[date] = None,
    seat_roles: Optional[Sequence[str]] = None,
) -> bool:
    """Is this membership a seat somebody holds **today**? Pure.

    The three filters run 15 measured, and the reason ``active`` is computed
    here rather than read: the rows carry no usable active flag, and the
    difference between 186 and 180 is entirely this function.
    """
    today = today or date.today()
    if _text(_first(row, END_FIELDS)).strip():
        return False
    begin = _as_date(_first(row, BEGIN_FIELDS))
    if begin is None or begin > today:
        return False
    return has_seat_role(row, seat_roles)


def member_from_rows(
    membership: Dict[str, Any],
    person: Dict[str, Any],
    body: Body,
    today: Optional[date] = None,
    seat_roles: Optional[Sequence[str]] = None,
) -> Optional[Member]:
    """Map a membership plus its person record to a :class:`Member`. Pure.

    ``None`` for a row with no person id: that id is the join key to Wikidata's
    identifier property, and a row lacking it cannot be reconciled — the same
    rule ``parliament.member_from_row`` applies to ``PersonNumber``.
    """
    person_id = _as_int(membership.get("person_id") or person.get("id"))
    if person_id is None:
        log.warning("Skipping membership without a person id: %s", membership.get("id"))
        return None

    first, last = _split_name(person)
    district = _tidy(_first(person, DISTRICT_FIELDS)) or None
    party = _tidy(_first(person, PARTY_NAME_FIELDS)) or None
    return Member(
        person_number=person_id,
        first_name=first,
        last_name=last,
        active=is_seat_row(membership, today=today, seat_roles=seat_roles),
        council=body.council,
        council_name=body.label,
        council_number=body.council_number,
        # The Wahlkreis rather than a canton. ``Member`` keeps the federal field
        # name because everything downstream reads it as "the P768 key and the
        # report's grouping", which is exactly what this is.
        canton_abbreviation=district,
        canton_name=district,
        party_name=party,
        party_abbreviation=party,
        # Per-tenure rows, so this really is the tenure start — see the module
        # docstring. No MemberCouncilHistory equivalent is needed or wanted.
        date_joining=_as_date(_first(membership, BEGIN_FIELDS)),
        date_leaving=_as_date(_first(membership, END_FIELDS)),
        date_of_birth=_as_date(_first(person, BIRTH_FIELDS)),
        date_of_death=_as_date(_first(person, DEATH_FIELDS)),
        # Personal data for the presence checks, all of it optional and none of
        # it measured on this source — see the field tuples above. A column
        # this API does not have costs an empty value and no suggestion.
        place_of_birth=_tidy(_first(person, BIRTHPLACE_FIELDS)) or None,
        places_of_origin=_split_list(_first(person, ORIGIN_FIELDS)),
        occupations=_split_list(_first(person, OCCUPATION_FIELDS)),
        website=_tidy(_first(person, WEBSITE_FIELDS)) or None,
        number_of_children=_as_int(_first(person, CHILDREN_FIELDS)),
        id=_as_int(membership.get("id")),
    )


def members_from_rows(
    memberships: Iterable[Dict[str, Any]],
    persons: Iterable[Dict[str, Any]],
    body: Body,
    active_only: bool = True,
    today: Optional[date] = None,
    seat_roles: Optional[Sequence[str]] = None,
) -> List[Member]:
    """Map and filter membership rows against their person records. Pure.

    De-duplicated on ``(person_number, council)`` keeping the latest-starting
    row, so somebody with two spells is one member with their current tenure —
    matching ``parliament.members_from_rows``. A membership whose person is
    missing from ``persons`` still produces a member, with the name blank: the
    seat is the fact being reconciled, and dropping it would quietly shrink the
    chamber.
    """
    by_id = {
        _as_int(p.get("id")): p for p in persons if _as_int(p.get("id")) is not None
    }
    best: Dict[tuple, Member] = {}
    for row in memberships:
        person_id = _as_int(row.get("person_id"))
        member = member_from_rows(
            row, by_id.get(person_id, {}), body, today=today, seat_roles=seat_roles
        )
        if member is None:
            continue
        if active_only and not member.active:
            continue
        key = (member.person_number, member.council.upper())
        current = best.get(key)
        if current is None or (member.date_joining or date.min) > (
            current.date_joining or date.min
        ):
            best[key] = member
    return sorted(best.values(), key=lambda m: (m.council, m.sort_name.casefold()))


def tenures_from_rows(
    memberships: Iterable[Dict[str, Any]],
    body: Body,
    seat_roles: Optional[Sequence[str]] = None,
) -> Dict[tuple, Tenure]:
    """``(person_id, council)`` → their latest spell in this chamber. Pure.

    Every membership row, the **ended** ones included — which is the point:
    :func:`members_from_rows` keeps only seats held today, so a person Wikidata
    still records as sitting is absent from it and the report has no leaving
    date to offer. Here they are, in the same rows.

    No ``begin_date``/``today`` filter either, for the same reason: a spell that
    has ended is exactly what is wanted, and a future start is still the row
    that says when this person's seat begins. Only the role allowlist applies,
    because a ``Gast`` row was never a seat and its dates are not this seat's
    dates.

    Per-tenure rows (913 across 834 people), so a row *is* a tenure and the
    latest-starting one is the current spell — no chaining, unlike
    :func:`parliament.latest_tenure`.
    """
    council = body.council.upper()
    best: Dict[tuple, Tenure] = {}
    for row in memberships:
        person_id = _as_int(row.get("person_id"))
        if person_id is None or not has_seat_role(row, seat_roles):
            continue
        tenure = Tenure(
            person_number=person_id,
            council=council,
            start=_as_date(_first(row, BEGIN_FIELDS)),
            end=_as_date(_first(row, END_FIELDS)),
        )
        current = best.get(tenure.key)
        if current is None or (tenure.start or date.min) > (current.start or date.min):
            best[tenure.key] = tenure
    return best


class OpenParlDataClient:
    """Fetch cantonal members from api.openparldata.ch.

    Mirrors :class:`~.parliament.ParliamentClient`'s surface so ``app.process``
    can use either without knowing which it has. The client is constructed
    lazily for the same reason: building it fetches the API's OpenAPI document
    over the network, which must not happen at import time.
    """

    def __init__(
        self,
        session: Any = None,
        language: str = DEFAULT_LANGUAGE,
        body_key: str = "",
        bodies: Optional[Sequence[Body]] = None,
        seat_roles: Optional[Sequence[str]] = None,
        client: Any = None,
    ) -> None:
        self.language = (language or DEFAULT_LANGUAGE).lower()
        self.body_key = body_key
        self.bodies = list(bodies or [])
        self.seat_roles = list(seat_roles or DEFAULT_SEAT_ROLES)
        self._client = client
        self._session = session
        # Membership rows per group id. ``get_members`` and ``get_tenures``
        # read the same rows for different questions ("who sits today" and
        # "when did they hold it"), so the fetch is cached rather than repeated.
        self._memberships: Dict[int, List[Dict[str, Any]]] = {}

    @property
    def client(self) -> Any:
        if self._client is None:
            import swissparlpy as spp

            self._client = spp.SwissParlClient(
                session=self._session, backend="openparldata"
            )
        return self._client

    def _rows(self, table: str, **filters: Any) -> List[Dict[str, Any]]:
        log.debug("Fetching %s %s", table, filters)
        return [dict(r) for r in self.client.get_data(table, **filters)]

    def resolve_group_ids(self) -> Dict[str, int]:
        """``council`` → group id, from the config or by name from the API.

        A configured ``group_id`` is used as-is; that is the measured, stable
        answer (5077 for the Kantonsrat) and it costs no request. Only a body
        without one is looked up, by **exact** name match against the groups of
        this body_key — the discipline ``verify_kantonsrat.is_kantonsrat``
        exists for, because a substring match picks up "Büro des Kantonsrates"
        and, far worse, could reach the *Regierungsrat*.
        """
        found: Dict[str, int] = {}
        missing = [b for b in self.bodies if b.group_id is None]
        for body in self.bodies:
            if body.group_id is not None:
                found[body.council] = body.group_id
        if not missing:
            return found

        rows = self._rows(GROUP_TABLE, body_key=self.body_key)
        for body in missing:
            wanted = _tidy(body.group_name or body.label).casefold()
            match = next(
                (
                    r
                    for r in rows
                    if any(
                        _tidy(v).casefold() == wanted
                        for k, v in r.items()
                        if k.startswith(("name", "title"))
                    )
                ),
                None,
            )
            group_id = _as_int((match or {}).get("id"))
            if group_id is None:
                log.warning(
                    "No group under body %r is named %r exactly; %s will have no "
                    "members. Run scripts/verify_kantonsrat.py to see the names.",
                    self.body_key,
                    body.group_name or body.label,
                    body.council,
                )
                continue
            found[body.council] = group_id
        return found

    def get_members(
        self, councils: Optional[Sequence[str]] = None, active_only: bool = True
    ) -> List[Member]:
        """Sitting members of the configured groups, as dataclasses."""
        wanted = {c.strip().upper() for c in councils} if councils else None
        group_ids = self.resolve_group_ids()
        persons = self._rows(PERSON_TABLE, body_key=self.body_key)
        log.info("OpenParlData: %d person record(s) for body %r",
                 len(persons), self.body_key)

        out: List[Member] = []
        for body in self.bodies:
            if wanted is not None and body.council.upper() not in wanted:
                continue
            group_id = group_ids.get(body.council)
            if group_id is None:
                continue
            rows = self._membership_rows(group_id)
            members = members_from_rows(
                rows,
                persons,
                body,
                active_only=active_only,
                seat_roles=self.seat_roles,
            )
            log.info(
                "[%s] %s (group %s): %d of %d membership row(s) are seats held today",
                body.council,
                body.label,
                group_id,
                len(members),
                len(rows),
            )
            out.extend(members)
        return out

    def _membership_rows(self, group_id: int) -> List[Dict[str, Any]]:
        """Every membership row of one group, fetched at most once."""
        if group_id not in self._memberships:
            self._memberships[group_id] = self._rows(
                MEMBERSHIP_TABLE, group_id=group_id
            )
        return self._memberships[group_id]

    def get_tenures(
        self, councils: Optional[Sequence[str]] = None
    ) -> Dict[tuple, Tenure]:
        """``(person id, council)`` → their latest spell, ended ones included.

        The cantonal twin of :meth:`parliament.ParliamentClient.get_tenures`,
        and the reason it can exist at all: the ended ``memberships`` rows are
        in the same result ``get_members`` filters down to seats held today, so
        this costs no extra request.
        """
        wanted = {c.strip().upper() for c in councils} if councils else None
        group_ids = self.resolve_group_ids()
        out: Dict[tuple, Tenure] = {}
        for body in self.bodies:
            if wanted is not None and body.council.upper() not in wanted:
                continue
            group_id = group_ids.get(body.council)
            if group_id is None:
                continue
            out.update(
                tenures_from_rows(
                    self._membership_rows(group_id), body, seat_roles=self.seat_roles
                )
            )
        log.info("Tenure dates available for %d (person, council) pair(s)", len(out))
        return out

    def get_periods(self) -> List[Period]:
        """No legislative periods: this API carries none. See the docstring."""
        log.info(
            "OpenParlData has no legislative-period table; P2937 will not be "
            "suggested, which is what the empty 'terms' map says anyway."
        )
        return []

    def get_member_segments(
        self, councils: Optional[Sequence[str]] = None
    ) -> Dict[tuple, List[Member]]:
        """Nothing to correct: ``begin_date`` is already the tenure start.

        Federally this reads ``MemberCouncilHistory`` because
        ``MemberCouncil.DateJoining`` is a mandate *segment* start. Here the
        rows are one per tenure (913 across 834 people, ~40-50 new per
        election), so returning an empty map leaves ``Member.start_date``
        falling through to ``date_joining``, which is the right date already.
        """
        return {}
