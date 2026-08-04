"""Find the Wikidata item behind a sitting member.

This is where wd-parliament differs most sharply from its sibling wd-squads.
wd-squads has only names to work with and so refuses to guess between
namesakes; here the primary path is an **exact join on an identifier**:
Wikidata's P1307 ("Swiss parliament ID") against
``MemberCouncil.PersonNumber``. That match is a fact, and it is the only
provenance :mod:`quickstatements` will emit an edit from.

The name search is a fallback for members whose item does not carry P1307 yet,
and it is corroborated rather than guessed: ``MemberCouncil.DateOfBirth``
upgrades "refuse to guess between namesakes" into "pick the one born on the
right day". A candidate whose *known* birth date contradicts the member's is
rejected outright, however good the name match.

Everything except :func:`resolve_members` (which wraps the one network call) is
pure.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Sequence

from .models import (
    P_PARLIAMENT_ID,
    QID_FROM_IDENTIFIER,
    QID_FROM_NAME,
    Member,
    PersonMatch,
    WikidataPerson,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .wikidata import WikidataClient

log = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def _normalise_name(name: str) -> str:
    return _WHITESPACE_RE.sub(" ", (name or "").strip()).casefold()


def _normalise_identifier(value: Optional[str]) -> Optional[str]:
    """Normalise a P1307 value for comparison with a ``PersonNumber``.

    P1307 is a numeric string; comparing it as an ``int`` makes "0123" and
    "123" agree and keeps stray whitespace from breaking the join.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(int(text))
    except ValueError:
        return text


def index_by_identifier(
    people: Iterable[WikidataPerson],
) -> Dict[str, List[WikidataPerson]]:
    """Map a normalised P1307 value to the items carrying it.

    A list rather than a single item because P1307 is only a *should-be-unique*
    constraint on Wikidata: two items claiming the same ID is a data problem we
    must notice rather than silently pick a side in.
    """
    index: Dict[str, List[WikidataPerson]] = defaultdict(list)
    for person in people:
        key = _normalise_identifier(person.parliament_id)
        if key:
            index[key].append(person)
    return dict(index)


def match_by_identifier(
    members: Sequence[Member], people: Iterable[WikidataPerson]
) -> Dict[int, WikidataPerson]:
    """Join members to items on P1307 == ``PersonNumber``. Pure.

    Sets ``qid``/``qid_source`` on each matched member in place and returns the
    ``PersonNumber`` → item map. An identifier claimed by more than one item is
    skipped — never arbitrated — and the claiming items are **recorded on the
    member** so the run can say so out loud. Logging it was not enough: the
    member then looks simply unmatched, and an unmatched member draws
    "no item was found, they may need a new one", which is the worst possible
    advice about somebody who already has two.
    """
    index = index_by_identifier(people)
    matched: Dict[int, WikidataPerson] = {}
    for member in members:
        candidates = index.get(str(member.person_number), [])
        if len(candidates) > 1:
            member.duplicate_identifier_qids = sorted(c.qid for c in candidates)
            log.warning(
                "P1307 '%s' (%s) is claimed by %d items: %s — skipping the join",
                member.person_number,
                member.full_name,
                len(candidates),
                ", ".join(c.qid for c in candidates),
            )
            continue
        if not candidates:
            continue
        person = candidates[0]
        member.qid = person.qid
        member.qid_source = QID_FROM_IDENTIFIER
        matched[member.person_number] = person
    return matched


def unresolved_names(members: Iterable[Member]) -> List[str]:
    """Names of members still without a Q-ID, deduplicated."""
    return list(
        dict.fromkeys(
            m.full_name for m in members if not m.qid and m.full_name.strip()
        )
    )


def select_person_match(
    candidates: Sequence[PersonMatch], member: Member
) -> Optional[PersonMatch]:
    """Pick the item a member's name refers to, or ``None`` if unclear.

    The rules, in order:

    1. A candidate whose birth date is known and *differs* from the member's is
       dropped — a namesake, not this person.
    2. If exactly one survivor's birth date positively agrees, it wins. This is
       the case the birth date exists for.
    3. Otherwise fall back to wd-squads' rule: accept only if exactly one
       candidate remains at all, preferring those that already hold one of the
       configured seats. Guessing between namesakes would put a wrong Q-ID in
       front of a human editor, which is worse than reporting nothing.
    """
    if not candidates:
        return None

    birth = member.date_of_birth
    survivors = [
        c
        for c in candidates
        if birth is None or c.birth_date is None or c.birth_date == birth
    ]
    if not survivors:
        return None

    if birth is not None:
        agreeing = [c for c in survivors if c.birth_date == birth]
        if len(agreeing) == 1:
            return agreeing[0]
        if len(agreeing) > 1:
            return None  # several people born the same day with the same name

    seated = [c for c in survivors if c.has_position]
    if len(seated) == 1:
        return seated[0]
    if not seated and len(survivors) == 1:
        return survivors[0]
    return None


def apply_person_matches(
    members: Sequence[Member], matches: Dict[str, List[PersonMatch]]
) -> None:
    """Fill in Q-IDs from ``search_people`` results, in place.

    A Q-ID already claimed — by the P1307 join or by an earlier member in this
    pass — is never handed to a second member: two sitting members resolving to
    the same item means at least one is wrong.
    """
    by_name: Dict[str, List[PersonMatch]] = defaultdict(list)
    for name, candidates in matches.items():
        by_name[_normalise_name(name)].extend(candidates)

    taken = {m.qid for m in members if m.qid}
    for member in members:
        if member.qid:
            continue
        candidates = [
            c
            for c in by_name.get(_normalise_name(member.full_name), [])
            if c.qid not in taken
        ]
        match = select_person_match(candidates, member)
        if not match:
            continue
        member.qid = match.qid
        member.qid_source = QID_FROM_NAME
        taken.add(match.qid)


def match_members(
    members: Sequence[Member],
    people: Iterable[WikidataPerson],
    matches: Optional[Dict[str, List[PersonMatch]]] = None,
) -> Dict[int, WikidataPerson]:
    """Resolve every member to a Wikidata item. Pure — the tests' entry point.

    Runs the P1307 join, then applies ``matches`` (the already-fetched name
    search results, if any) to whatever is left. Returns ``PersonNumber`` →
    item for the members that resolved, so the diff can look an item up without
    re-indexing.
    """
    people = list(people)
    matched = match_by_identifier(members, people)
    if matches:
        apply_person_matches(members, matches)
        by_qid = {p.qid: p for p in people}
        for member in members:
            if member.qid and member.person_number not in matched:
                person = by_qid.get(member.qid)
                if person is not None:
                    matched[member.person_number] = person
    return matched


def resolve_members(
    members: Sequence[Member],
    people: Iterable[WikidataPerson],
    wikidata: "WikidataClient",
    position_qids: Sequence[str],
    language: str = "de",
    identifier_property: str = P_PARLIAMENT_ID,
) -> Dict[int, WikidataPerson]:
    """Wire :func:`match_members` around the one name-search network call.

    The search is a best-effort extra: if WDQS refuses it (a timeout on a large
    batch, say), the members it would have resolved simply stay unresolved and
    get reported as ``NO_WIKIDATA_ITEM`` instead of failing the whole run.
    """
    people = list(people)
    matched = match_by_identifier(members, people)
    hit_rate = (len(matched) / len(members) * 100) if members else 0.0
    log.info(
        "%s join: %d/%d members matched by identifier (%.1f%%)",
        identifier_property,
        len(matched),
        len(members),
        hit_rate,
    )

    names = unresolved_names(members)
    if not names:
        return matched

    log.info("Falling back to a name search for %d members", len(names))
    try:
        matches = wikidata.search_people(
            names, position_qids, language, identifier_property
        )
    except Exception as exc:  # non-fatal: report the rest of the run anyway
        log.warning("Name search failed: %s", exc)
        return matched

    apply_person_matches(members, matches)
    by_qid = {p.qid: p for p in people}
    for member in members:
        if member.qid and member.person_number not in matched:
            person = by_qid.get(member.qid)
            if person is not None:
                matched[member.person_number] = person
    log.info(
        "After the name fallback: %d/%d members resolved",
        sum(1 for m in members if m.qid),
        len(members),
    )
    return matched
