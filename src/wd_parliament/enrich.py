"""A second, independent source read alongside the first.

Every other module here answers to one source. This one exists because two
sources can do something one cannot: **disagree**. parlament.ch is
authoritative about the Federal Assembly, and that is exactly why a claim it
makes alone cannot be checked — the tool emits P580 mechanically from it, so a
wrong value is written unreviewed. OpenParlData carries the same seats,
harmonised from the same official record but assembled independently, and
comparing the two is the only check available that does not consult the source
under test.

The join has no shared key and goes through Wikidata, exactly as
``scripts/compare_tenure_dates.py`` does::

    MemberCouncil.PersonNumber ──P1307──▶ Q-ID ◀──wikidata_id── OpenParlData

Both ends are measured: P1307 == ``PersonNumber`` (README step 1) and 3,685 of
3,686 federal members carry a ``wikidata_id`` (step 6).

Four rules this module exists to keep, each one paid for by a wrong answer:

**Keyed by ``(Q-ID, council)``, never by Q-ID alone.** A member who moved
NR→SR has a National Council row ending the day before their Council of States
row begins. Pooled, the two chain into one span across a chamber change and
every long-serving member reads as a disagreement — run 11 scored 22 of them
that way, all the same mistake.

**A Q-ID claimed by several person records is skipped.** Nothing makes
``wikidata_id`` unique; two records naming one item pool their memberships and
whatever reads "the latest row" gets the other person's. Run 17 reported a
member who left in 1971 against a leaving date of 2014 before this rule
existed. Skipped, never arbitrated, and reported as
``DUPLICATE_SOURCE_LINK`` so somebody can fix it at the source.

**Chained by the same rule both sides use.** ``MemberCouncilHistory`` segments
and OpenParlData's per-term rows are both per-*period*, and the tenure the tool
models spans all of them. Comparing a chained span against a single term would
report every re-elected member as a disagreement.

**Enrichment never becomes a source.** Nothing here produces a
:class:`~.models.Member`, and no value from it is ever emitted to Wikidata. It
can only *withhold* — a disagreement suppresses the mechanical edit for that
member — which is the one direction a second opinion may safely act in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import Member, SourceSpan
from .openparldata import (
    BEGIN_FIELDS,
    END_FIELDS,
    _as_date,
    _as_int,
    _first,
    _present,
    _tidy,
    as_qid,
    link_conflicts_from_rows,
)
from .parliament import MAX_SEGMENT_GAP_DAYS

log = logging.getLogger(__name__)

MEMBERSHIP_TABLE = "memberships"
PERSON_TABLE = "persons"
GROUP_TABLE = "groups"

# Federally the body is the country and the chambers are groups under it, the
# same shape the cantonal adapter reads. The names are configuration
# (``enrich.groups``) rather than constants, and matched by **equality**: a
# substring match on "Nationalrat" also takes "Büro NR" and the committees.
DEFAULT_BODY_KEY = "CHE"


@dataclass
class Enrichment:
    """Everything the second source has to say about this run.

    ``spans`` is keyed ``(Q-ID, council)``; ``link_conflicts`` is keyed
    ``(council, Q-ID)`` because it is reported per chamber and there is no one
    person to attach it to — that is the whole finding.
    """

    spans: Dict[tuple, SourceSpan] = field(default_factory=dict)
    link_conflicts: Dict[tuple, List[int]] = field(default_factory=dict)
    people_with_links: int = 0
    skipped_conflicted: int = 0

    def span_for(self, qid: Optional[str], council: str) -> Optional[SourceSpan]:
        if not qid:
            return None
        return self.spans.get((qid, council.upper()))


def chained_span(rows: Sequence[Dict[str, Any]]) -> Tuple[Optional[date], Optional[date]]:
    """Start and end of the **current continuous run** through these rows. Pure.

    The same walk :func:`parliament.tenure_start` performs on
    ``MemberCouncilHistory``, applied to the other source's per-term rows so the
    two are like for like. Adjacent rows chain — a legislature boundary is a
    one-day join — and a real break stops the walk, so somebody who left and
    returned gets the return.

    The end is the newest row's, and every earlier row ends inside the span, so
    unlike the start it needs no walk. ``None`` there means the seat is open
    per this source, which is an answer and never a date to fall back on.

    Column names are resolved from the rows rather than assumed, for the reason
    recorded in ``verify_openparldata.classify_seat_memberships``: "no such
    column" and "column full of nulls" are indistinguishable through ``.get()``
    and mean opposite things.
    """
    if not rows:
        return None, None
    begin_field = _present(rows, BEGIN_FIELDS)
    if begin_field is None:
        return None, None
    end_field = _present(rows, END_FIELDS)

    spans: List[Tuple[date, Optional[date]]] = []
    for row in rows:
        start = _as_date(row.get(begin_field))
        if start is None:
            continue
        end = _as_date(row.get(end_field)) if end_field else None
        spans.append((start, end))
    if not spans:
        return None, None

    spans.sort(key=lambda s: s[0])
    start, end = spans[-1]
    for (earlier_start, earlier_end), (later_start, _) in zip(
        reversed(spans[:-1]), reversed(spans[1:])
    ):
        if earlier_end is None:
            # An open earlier row cannot be chained: no end means no gap to
            # measure, and overlapping rows are not a continuous run.
            break
        gap = (later_start - earlier_end).days
        if gap > MAX_SEGMENT_GAP_DAYS or gap < 0:
            break
        start = earlier_start
    return start, end


def spans_from_rows(
    memberships: Iterable[Dict[str, Any]],
    qid_by_person: Dict[Any, str],
    council: str,
) -> Dict[tuple, SourceSpan]:
    """``(Q-ID, council)`` → what this source says about that seat. Pure.

    ``qid_by_person`` must already have the contested Q-IDs removed — see the
    module docstring. A membership whose person carries no link is not an
    absence worth recording: there is nothing to compare it against.
    """
    council = council.upper()
    by_qid: Dict[str, List[Dict[str, Any]]] = {}
    people: Dict[str, set] = {}
    for row in memberships:
        person_id = _as_int(row.get("person_id"))
        qid = qid_by_person.get(person_id)
        if not qid:
            continue
        by_qid.setdefault(qid, []).append(row)
        people.setdefault(qid, set()).add(person_id)

    out: Dict[tuple, SourceSpan] = {}
    for qid, rows in by_qid.items():
        start, end = chained_span(rows)
        if start is None:
            continue
        out[(qid, council)] = SourceSpan(
            council=council,
            start=start,
            end=end,
            rows=len(rows),
            person_ids=sorted(people[qid]),
        )
    return out


# --- what counts as a disagreement -------------------------------------------
# Each of these is a claim the *first* source makes that the second contradicts.
# Silence is never a contradiction: a value only one side knows is skipped, the
# same rule the rest of the tool follows for unmapped cantons and parties.
DISAGREE_START = "start"
DISAGREE_STILL_SITTING = "still_sitting"
DISAGREE_ALREADY_LEFT = "already_left"


@dataclass
class Disagreement:
    """One thing the two sources say differently about one member."""

    kind: str
    ours: Optional[date] = None
    theirs: Optional[date] = None


def disagreements(member: Member, span: Optional[SourceSpan]) -> List[Disagreement]:
    """Everything the second source contradicts about ``member``. Pure.

    Deliberately narrow. The two sources describe the same seats but not the
    same *records*: only facts both of them state are compared, and a fact only
    one holds is not a disagreement but a gap.
    """
    if span is None:
        return []
    out: List[Disagreement] = []

    if member.start_date and span.start and member.start_date != span.start:
        out.append(
            Disagreement(DISAGREE_START, ours=member.start_date, theirs=span.start)
        )

    # The seat's *state*, which is what a reader would notice first. Compared
    # only when the two sources are talking about the same spell — a member the
    # first source says is sitting, whose second-source row closed years before
    # their tenure began, is a stale row rather than a claim about today.
    if member.active and member.date_leaving is None and span.end is not None:
        if member.start_date is None or span.end >= member.start_date:
            out.append(Disagreement(DISAGREE_STILL_SITTING, theirs=span.end))
    elif not member.active and member.date_leaving is not None and span.end is None:
        out.append(Disagreement(DISAGREE_ALREADY_LEFT, ours=member.date_leaving))
    return out


class OpenParlDataEnricher:
    """Read the federal seats from api.openparldata.ch, to compare against.

    Deliberately *not* an :class:`~.openparldata.OpenParlDataClient`: that one
    produces members and can be a source. This produces spans and conflicts,
    and produces no :class:`~.models.Member` at all, which is what keeps
    "enrichment never becomes a source" true by construction rather than by
    discipline.
    """

    def __init__(
        self,
        session: Any = None,
        body_key: str = DEFAULT_BODY_KEY,
        groups: Optional[Dict[str, str]] = None,
        client: Any = None,
    ) -> None:
        self.body_key = body_key or DEFAULT_BODY_KEY
        self.groups = dict(groups or {})
        self._client = client
        self._session = session

    @property
    def client(self) -> Any:
        if self._client is None:
            import swissparlpy as spp

            self._client = spp.SwissParlClient(
                session=self._session, backend="openparldata"
            )
        return self._client

    def _rows(self, table: str, **filters: Any) -> List[Dict[str, Any]]:
        log.debug("Enrichment: fetching %s %s", table, filters)
        return [dict(r) for r in self.client.get_data(table, **filters)]

    def resolve_group_ids(self, councils: Optional[Sequence[str]] = None) -> Dict[str, int]:
        """``council`` → the group id whose name equals the configured one.

        **Equality, never a substring.** "Nationalrat" is the chamber;
        "Büro NR", "Präsidium des Nationalrates" and every committee contain
        the word. Matching one of those would compare the chamber against a
        committee of eight and call the result a disagreement, which is how
        ``verify_openparldata`` section B once went wrong.
        """
        wanted = {c.strip().upper() for c in councils} if councils else None
        rows = self._rows(GROUP_TABLE, body_key=self.body_key)
        found: Dict[str, int] = {}
        for council, name in self.groups.items():
            if wanted is not None and council.upper() not in wanted:
                continue
            target = _tidy(name).casefold()
            match = next(
                (
                    r
                    for r in rows
                    if any(
                        _tidy(v).casefold() == target
                        for k, v in r.items()
                        if k.startswith(("name", "title"))
                    )
                ),
                None,
            )
            group_id = _as_int((match or {}).get("id"))
            if group_id is None:
                log.warning(
                    "Enrichment: no group under body %r is named %r exactly; "
                    "%s will not be cross-checked",
                    self.body_key,
                    name,
                    council,
                )
                continue
            found[council.upper()] = group_id
        return found

    def fetch(self, councils: Optional[Sequence[str]] = None) -> Enrichment:
        """Everything the second source has to say. One pass, cached nowhere.

        A failure here must reach the caller: ``app.process`` degrades to a
        single-source run and says so, because a *silent* loss of the
        cross-check would leave the report looking as though the two sources
        agreed.
        """
        persons = self._rows(PERSON_TABLE, body_key=self.body_key)
        qid_by_person: Dict[Any, str] = {}
        for row in persons:
            qid = as_qid(_first(row, ("wikidata_id", "wikidata_qid", "wikidata")))
            person_id = _as_int(row.get("id"))
            if qid and person_id is not None:
                qid_by_person[person_id] = qid

        group_ids = self.resolve_group_ids(councils)
        conflicts: Dict[tuple, List[int]] = {}
        spans: Dict[tuple, SourceSpan] = {}
        contested: set = set()

        for council, group_id in sorted(group_ids.items()):
            rows = self._rows(MEMBERSHIP_TABLE, group_id=group_id)
            seat_ids = {
                pid
                for pid in (_as_int(r.get("person_id")) for r in rows)
                if pid is not None
            }
            for qid, ids in link_conflicts_from_rows(persons, seat_ids).items():
                conflicts[(council, qid)] = ids
                contested.add(qid)
            # The contested Q-IDs are removed *before* the spans are built, so
            # a pooled pair can never become a date this run compares against.
            usable = {p: q for p, q in qid_by_person.items() if q not in contested}
            spans.update(spans_from_rows(rows, usable, council))
            log.info(
                "Enrichment [%s]: %d membership row(s), %d seat(s) joined to an item",
                council,
                len(rows),
                sum(1 for k in spans if k[1] == council),
            )

        if conflicts:
            log.warning(
                "Enrichment: %d Wikidata item(s) are claimed by several person "
                "records and were skipped — reported as a data error",
                len(conflicts),
            )
        return Enrichment(
            spans=spans,
            link_conflicts=conflicts,
            people_with_links=len(qid_by_person),
            skipped_conflicted=len(contested),
        )
