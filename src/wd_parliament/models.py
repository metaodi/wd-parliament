"""Dataclasses shared across the pipeline.

Everything here is plain data: no network, no I/O. The pure stages of the
pipeline (:mod:`period_overlap`, :mod:`resolve`, :mod:`diff`,
:mod:`quickstatements`) consume and produce only these types, which is what
makes them unit-testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional


# --- Suggestion kinds -------------------------------------------------------
# Each suggestion has a ``kind`` from this list. ``PRIORITY`` maps the kind to
# a sort weight (lower = more urgent), ``KIND_LABEL`` to a human string used in
# the reports. Adding a kind means touching all three maps plus ``diff.py``.
KIND_ADD_IDENTIFIER = "ADD_IDENTIFIER"
KIND_DUPLICATE_IDENTIFIER = "DUPLICATE_IDENTIFIER"
KIND_REVIEW_ENDED = "REVIEW_ENDED"
KIND_ADD_END_DATE = "ADD_END_DATE"
KIND_ADD_MEMBERSHIP = "ADD_MEMBERSHIP"
KIND_FIX_START_DATE = "FIX_START_DATE"
KIND_ADD_START_DATE = "ADD_START_DATE"
KIND_ADD_TERM = "ADD_TERM"
KIND_ADD_QUALIFIER = "ADD_QUALIFIER"
KIND_REVIEW_PARTY = "REVIEW_PARTY"
KIND_NO_WIKIDATA_ITEM = "NO_WIKIDATA_ITEM"
KIND_ADD_PERSON_DATA = "ADD_PERSON_DATA"

PRIORITY = {
    # Adding the identifier is the highest-leverage edit there is: it turns
    # every future run's fuzzy name match into an exact join.
    KIND_ADD_IDENTIFIER: 1,
    # A conflict rather than a gap, and it poisons everything downstream: the
    # join is skipped, so the member looks unmatched, and the advice they would
    # otherwise draw is "create an item" — which would make a third duplicate.
    KIND_DUPLICATE_IDENTIFIER: 1,
    KIND_REVIEW_ENDED: 1,
    KIND_ADD_END_DATE: 2,
    KIND_ADD_MEMBERSHIP: 2,
    KIND_FIX_START_DATE: 2,
    KIND_ADD_START_DATE: 3,
    KIND_ADD_TERM: 3,
    KIND_ADD_QUALIFIER: 4,
    KIND_REVIEW_PARTY: 4,
    KIND_NO_WIKIDATA_ITEM: 5,
    # Last, deliberately. Biographical enrichment is worth having, but nothing
    # here affects the question the tool exists to answer — who sits today —
    # and there is potentially one of these per member per property, so a
    # higher priority would bury the seat findings under them.
    KIND_ADD_PERSON_DATA: 6,
}

KIND_LABEL = {
    KIND_ADD_IDENTIFIER: "Item matched by name but has no unique ID (P1307/P14527)",
    KIND_DUPLICATE_IDENTIFIER: "One identifier claimed by several Wikidata items",
    KIND_REVIEW_ENDED: "Membership recorded as ended, but the member is still sitting",
    KIND_ADD_END_DATE: "Recorded as sitting, but the member has left",
    KIND_ADD_MEMBERSHIP: "Sitting member, but no position held (P39) statement",
    KIND_FIX_START_DATE: "Start date (P580) disagrees with source",
    KIND_ADD_START_DATE: "Open membership without a start date (P580)",
    KIND_ADD_TERM: "Membership missing a parliamentary term (P2937)",
    KIND_ADD_QUALIFIER: "Membership missing an electoral district (P768) or group (P4100)",
    KIND_REVIEW_PARTY: "Political party (P102) missing or disagreeing with source",
    KIND_NO_WIKIDATA_ITEM: "Sitting member, but no Wikidata item could be found",
    KIND_ADD_PERSON_DATA: "Personal data the source publishes and Wikidata does not record",
}

# How a member's Q-ID was established (see ``resolve``).
#
# ``QID_FROM_IDENTIFIER`` is an exact join on P1307 and is the *only* source
# trusted enough to emit QuickStatements from — see
# ``quickstatements.is_mechanical``. ``QID_FROM_NAME`` is a name match
# corroborated by the birth date; good enough to report, not to automate.
QID_FROM_IDENTIFIER = "identifier"  # P1307 == MemberCouncil.PersonNumber
QID_FROM_NAME = "name"  # label/alias match, birth date agreed

# Wikidata properties this tool reasons about, kept in one place so the SPARQL,
# the diff and the QuickStatements renderer cannot drift apart.
P_POSITION_HELD = "P39"
P_PARLIAMENT_ID = "P1307"

# The identifier properties a source can be joined on, and what each one's
# value is. ``QID_FROM_IDENTIFIER`` — the provenance ``quickstatements`` gates
# on — means *Wikidata itself* asserted one of these, which is what makes the
# match a fact rather than a guess. Both qualify; a Q-ID asserted by a third
# party **about** Wikidata (OpenParlData's own ``wikidata_id`` field) does not,
# and would need its own constant before it could ever be emitted from.
IDENTIFIER_PROPERTIES = {
    "P1307": "Swiss parliament ID (MemberCouncil.PersonNumber)",
    "P14527": "OpenParlData ID",
}
P_START_TIME = "P580"
P_END_TIME = "P582"
P_ELECTORAL_DISTRICT = "P768"
P_PARLIAMENTARY_GROUP = "P4100"
P_PARLIAMENTARY_TERM = "P2937"
P_POLITICAL_PARTY = "P102"
P_DATE_OF_BIRTH = "P569"
P_DATE_OF_DEATH = "P570"
P_PLACE_OF_BIRTH = "P19"
P_PLACE_OF_ORIGIN = "P1321"  # "Bürgerort", the municipality of origin
P_OCCUPATION = "P106"
P_OFFICIAL_WEBSITE = "P856"
P_NUMBER_OF_CHILDREN = "P1971"


# --- Personal-data checks ---------------------------------------------------
# The properties that are *about the person* rather than about the seat, and
# that the source publishes alongside the mandate. Unlike every other check in
# this tool these compare **presence, not value**: the source gives a place or
# an occupation as free text, and turning "Bern (BE)" into an item is a
# judgement — the same one ``config``'s Q-ID maps exist to keep out of the code.
#
# So a check fires only when the *source* has a value and Wikidata has **no**
# statement for the property at all. That asymmetry is what makes it safe: the
# tool never claims a recorded value is wrong, only that a published fact is
# missing, and it says so with the source's own string for a human to resolve.


@dataclass(frozen=True)
class PersonDataCheck:
    """One "the source knows this, Wikidata does not record it" comparison.

    ``attribute`` is the :class:`Member` field holding the source's value(s);
    ``value_kind`` is what a Wikidata editor has to do with it, which is the
    difference between "resolve this string to an item" and "use it as it
    stands" and is the only per-property guidance the suggestion carries.
    """

    property_id: str
    label: str  # the property's English name, as it appears in the report
    attribute: str  # the Member field carrying the source's value(s)
    value_kind: str = "item"  # "item", "url" or "quantity"

    @property
    def needs_item(self) -> bool:
        return self.value_kind == "item"


PERSON_DATA_CHECKS = (
    PersonDataCheck(P_PLACE_OF_BIRTH, "place of birth", "place_of_birth"),
    PersonDataCheck(P_PLACE_OF_ORIGIN, "place of origin", "places_of_origin"),
    PersonDataCheck(P_OCCUPATION, "occupation", "occupations"),
    PersonDataCheck(P_POLITICAL_PARTY, "member of political party", "party_name"),
    PersonDataCheck(
        P_OFFICIAL_WEBSITE, "official website", "website", value_kind="url"
    ),
    PersonDataCheck(
        P_NUMBER_OF_CHILDREN,
        "number of children",
        "number_of_children",
        value_kind="quantity",
    ),
)

PERSON_DATA_BY_PROPERTY = {c.property_id: c for c in PERSON_DATA_CHECKS}
PERSON_DATA_PROPERTIES = tuple(c.property_id for c in PERSON_DATA_CHECKS)

# The two statement models this tool can target; see ``config.Config`` and the
# ``statement_model`` key in config/parliament.yaml.
MODEL_TENURE = "tenure"  # one P39 per continuous tenure, P2937 repeated
MODEL_PERIOD = "period"  # one P39 per legislative period
STATEMENT_MODELS = (MODEL_TENURE, MODEL_PERIOD)


@dataclass
class Body:
    """One chamber of the Federal Assembly, as configured."""

    council: str  # MemberCouncil.CouncilAbbreviation, "NR" or "SR"
    label: str
    position_qid: str  # the P39 value for a seat in this chamber
    council_number: Optional[int] = None  # MemberCouncil.Council, the numeric code
    # OpenParlData only: the ``groups`` row that *is* this chamber. A body is a
    # level of parliament there, so the chamber is a group under it and a seat
    # is a membership pointing at that group. Measured at 5077 for the
    # Kantonsrat Zürich; left None, the client looks it up by exact name.
    group_id: Optional[int] = None
    group_name: Optional[str] = None

    @property
    def slug_source(self) -> str:
        return f"{self.council}-{self.label}"


@dataclass
class Member:
    """A sitting member, mapped from one ``MemberCouncil`` row.

    One row spans a whole continuous tenure in one council, so ``date_joining``
    / ``date_leaving`` may cover several legislative periods (see
    :mod:`period_overlap`).
    """

    person_number: int  # -> P1307
    first_name: str = ""
    last_name: str = ""
    active: bool = True
    council: str = ""  # CouncilAbbreviation, "NR" / "SR"
    council_name: str = ""
    council_number: Optional[int] = None
    canton_abbreviation: Optional[str] = None  # -> P768, via the canton map
    canton_name: Optional[str] = None
    parl_group_name: Optional[str] = None  # -> P4100, via the group map
    parl_group_abbreviation: Optional[str] = None
    party_name: Optional[str] = None  # -> P102, via the party map
    party_abbreviation: Optional[str] = None
    date_joining: Optional[date] = None  # a *segment* start — see start_date
    date_leaving: Optional[date] = None  # -> P582
    date_election: Optional[date] = None
    date_oath: Optional[date] = None
    date_resignation: Optional[date] = None  # context for P1534
    date_of_birth: Optional[date] = None  # -> P569, and the name-match tiebreak
    date_of_death: Optional[date] = None  # -> P570
    # Personal data the source publishes about the member, as the source's own
    # free text. Read only by the presence checks in :data:`PERSON_DATA_CHECKS`
    # — never resolved to a Q-ID here, and never emitted mechanically. Absent
    # from a source (or from a row) they stay empty, which makes no suggestion.
    place_of_birth: Optional[str] = None  # -> P19
    places_of_origin: List[str] = field(default_factory=list)  # -> P1321
    occupations: List[str] = field(default_factory=list)  # -> P106
    website: Optional[str] = None  # -> P856
    number_of_children: Optional[int] = None  # -> P1971
    person_id_code: Optional[int] = None
    id: Optional[int] = None
    # Filled in from MemberCouncilHistory by ``parliament.tenure_start``; see
    # ``start_date`` and README step 0c.
    tenure_start: Optional[date] = None
    # Filled in by ``resolve.match_members``.
    qid: Optional[str] = None
    qid_source: Optional[str] = None  # one of the QID_FROM_* constants
    # Every item claiming this member's identifier, when more than one does.
    # The identifier join refuses to arbitrate between them, so this records
    # *why* the member went unmatched — without it the run says "no item was
    # found" about somebody who has two.
    duplicate_identifier_qids: List[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip()

    @property
    def sort_name(self) -> str:
        return " ".join(p for p in (self.last_name, self.first_name) if p).strip()

    @property
    def start_date(self) -> Optional[date]:
        """When the member took office — **the only start P580 may come from**.

        ``date_joining`` is the start of the *current mandate segment*, not of
        the tenure. parlament.ch re-segments a sitting member's row at later
        dates: Philipp Bregy's ``MemberCouncil`` row says 2025-09-16 while his
        ``MemberCouncilHistory`` shows him holding the seat continuously from
        2019-03-04. Measured over the 244 sitting members who could be
        cross-checked, 11 carry a segment start rather than a tenure start
        (README step 0c), and P580 is emitted by two *mechanical* kinds — so
        reading ``date_joining`` here would write a wrong date without review.

        ``tenure_start``, when :func:`parliament.tenure_start` could establish
        it from the history, is that continuous run's first day. Falling back to
        ``date_joining`` keeps the tool working when the history is unavailable,
        at the accuracy the raw field allows.
        """
        return self.tenure_start or self.date_joining


@dataclass
class Period:
    """A ``LegislativePeriod`` row: one legislature of the Federal Assembly."""

    number: int
    name: str = ""
    abbreviation: Optional[str] = None
    start: Optional[date] = None
    end: Optional[date] = None  # unset on the current, running period
    id: Optional[int] = None

    @property
    def label(self) -> str:
        return self.name or self.abbreviation or f"Period {self.number}"


@dataclass
class Tenure:
    """One continuous spell in one chamber, as the **source** records it.

    ``Member`` answers "who sits today"; this answers "when did this person
    hold this seat", and it is asked about people who are *not* in the
    current-members set — the ones the diff's second pass finds still recorded
    as sitting on Wikidata. Their dates live in the source's historic tables
    (``MemberCouncilHistory`` federally, the ended ``memberships`` rows in
    OpenParlData), which the pipeline already reads for
    :func:`parliament.tenure_start`.

    ``start`` is a chained tenure start, not a mandate-segment start, for the
    same reason :attr:`Member.start_date` is; ``end`` is ``None`` for a spell
    the source has not closed, which is a "look it up by hand", never a
    licence to guess.
    """

    person_number: int
    council: str = ""
    start: Optional[date] = None
    end: Optional[date] = None

    @property
    def key(self) -> tuple:
        """``(person_number, council)`` — how the tenure maps are keyed.

        A person is not a seat: someone who moved from the National Council to
        the Council of States has two tenures, and keying on the person alone
        would let one chamber's dates be reported for the other's statement.
        """
        return (self.person_number, self.council.upper())


@dataclass
class PositionStatement:
    """One P39 statement on a Wikidata item, with the qualifiers we care about.

    The qualifier fields are lists because Wikidata permits repeats — and in
    the ``tenure`` statement model ``terms`` is *expected* to hold one entry per
    legislative period the tenure covers.
    """

    person_qid: str
    statement_id: str
    position_qid: str
    person_label: str = ""
    start: Optional[date] = None  # P580
    end: Optional[date] = None  # P582
    districts: List[str] = field(default_factory=list)  # P768 Q-ids
    groups: List[str] = field(default_factory=list)  # P4100 Q-ids
    terms: List[str] = field(default_factory=list)  # P2937 Q-ids

    @property
    def is_open(self) -> bool:
        """True when the statement carries no end date (i.e. still sitting)."""
        return self.end is None


@dataclass
class WikidataPerson:
    """A Wikidata item, as far as this tool is concerned."""

    qid: str
    label: str = ""
    parliament_id: Optional[str] = None  # P1307, as the raw string
    birth_date: Optional[date] = None  # P569
    death_date: Optional[date] = None  # P570
    parties: List[str] = field(default_factory=list)  # open P102 Q-ids
    statements: List[PositionStatement] = field(default_factory=list)
    # Which of the :data:`PERSON_DATA_CHECKS` properties this item already
    # carries, and whether it was asked about at all. The two are separate on
    # purpose: an empty ``properties`` on an item nobody queried means "unknown"
    # and must produce no suggestion, while on a queried item it means "carries
    # none of them" and is exactly the finding. Collapsing them would turn
    # every item outside the query's population into a false positive.
    properties: List[str] = field(default_factory=list)
    person_data_known: bool = False

    def statements_for(self, position_qid: str) -> List[PositionStatement]:
        return [s for s in self.statements if s.position_qid == position_qid]

    def has_property(self, property_id: str) -> bool:
        return property_id in self.properties


@dataclass
class PersonMatch:
    """A Wikidata item whose label/alias matches a member's name.

    Produced by ``WikidataClient.search_people`` for members that the P1307
    join did not resolve. ``birth_date`` is what turns wd-squads' "refuse to
    guess between namesakes" into "pick the one born on the right day".
    """

    name: str  # the searched name that matched
    qid: str
    label: str = ""
    birth_date: Optional[date] = None
    has_position: bool = False  # already has a P39 for one of the configured seats
    parliament_id: Optional[str] = None


@dataclass
class QuickStatement:
    """One rendered QuickStatements V1 line, with its provenance."""

    line: str
    suggestion_kind: str
    person_qid: str
    member_label: str


@dataclass
class Suggestion:
    """A single suggested edit for a Wikidata user to review."""

    kind: str
    body: Body
    member_label: str
    detail: str
    person_qid: Optional[str] = None
    person_number: Optional[int] = None
    qid_source: Optional[str] = None
    links: Dict[str, str] = field(default_factory=dict)
    # Grouping keys for the reports (canton / parliamentary group).
    canton: Optional[str] = None
    parl_group: Optional[str] = None
    # The concrete values behind the suggestion. ``quickstatements`` renders
    # from these, so anything it needs must be filled in by ``diff``.
    payload: Dict[str, object] = field(default_factory=dict)

    @property
    def priority(self) -> int:
        return PRIORITY.get(self.kind, 99)

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)
