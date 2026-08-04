"""Answer README step 8: may the departed members' P582 be applied in bulk?

The question, and why it matters
--------------------------------
The diff's second pass walks Wikidata's *open* memberships back to the source
and finds people the source does not list as sitting: they have left, and
Wikidata has never been told. Since the "Link and date the departed members"
change those suggestions carry a concrete leaving date, read from the source's
historic record — ``MemberCouncilHistory`` federally, the ended ``memberships``
rows in OpenParlData.

They are nevertheless **report-only**, gated twice in ``diff`` (no ``qid_source``
and no ``position`` in the payload), and this script is the probe that has to
come back clean before anyone considers removing those gates. Everything it
measures is what would go wrong if they were removed today:

- the population is defined by *Wikidata*, not by the source, so it is exactly
  the set nobody has measured. ``ADD_END_DATE`` is a **mechanical** kind, so
  ungating it writes P582 to every one of these items without a human reading
  a single date;
- the person number comes from the identifier value **Wikidata** carries, not
  from a member the source handed us. P1307 == ``PersonNumber`` was confirmed
  for a *sitting* member (Parmelin, README step 1). Nothing has checked it for
  somebody who left in 1987;
- the date comes from a table this pipeline has only ever read for people who
  are still in office. ``MemberCouncilHistory`` is where P580's tenure start
  comes from, and step 0c found the *other* field in the same table to be a
  segment start rather than a tenure start — so "it is in the history" has
  already been shown, once, not to mean what it looked like.

The four sections
-----------------
**A. Reach** — of the people the reverse walk reports, how many carry an
identifier value at all, how many of those resolve to a row in the source's
historic record, and how many of *those* have a closed tenure, i.e. an actual
date to emit. Reach is not correctness; it bounds how much the rest can say.

**B. Identity** — is the person the history returns the same human as the
Wikidata item? The departed twin of the Parmelin check, done the only way it
can be done without a second identifier: the surname on the history row against
the names the item carries. A disagreement here is the worst finding available,
because it means the number points at somebody else and the date is another
person's.

Three names are compared, not one, because an item's label is not the whole of
what Wikidata says a person is called:

- the **label**, as before;
- its **aliases** ("also known as"), where a second spelling of a
  nineteenth-century name usually lives — ``Johann Zünd`` carries
  ``Johannes Zündt``;
- **P1810 "subject named as"** as a qualifier on the identifier statement,
  which is not a spelling in general but a statement about *this source*: the
  person is written thus in the parlament.ch council-member database. Where it
  exists it settles the question outright.

The strongest reading wins, so the extra names can only move a row towards
agreement — they cannot manufacture the CONTRADICTED that would block a bulk
apply. Which name settled a row is counted and printed, because a match found
only in an alias is weaker evidence than one the label gives.

**C. Agreement** — does the source's leaving date agree with an independent
one? Same join ``compare_tenure_dates.py`` uses, for the same reason::

    MemberCouncil.PersonNumber ──P1307──▶ Q-ID ◀──wikidata_id── OpenParlData

keyed by ``(Q-ID, council)`` and never by Q-ID alone: a member who moved
NR→SR has an NR row ending the day before their SR row begins, and pooling the
two reads a chamber change as a contradiction. This is the verdict to read.

**D. Which statement would it close?** QuickStatements matches a statement by
property and main value, so a P582 aimed at an item holding several P39
statements for the same seat cannot say which one it means. ``diff`` already
refuses those for sitting members via ``ambiguous_statement``; this section
sizes the same problem in the departed population, plus the subtler version of
it — an open statement whose P580 is not the source's tenure start is probably
about a *different* spell, and closing it with this tenure's end date would be
wrong even though both dates are real.

Reading the result
------------------
``CONFIRMED``
    The two sources agree on every comparable leaving date, the identities
    corroborate, and no statement is ambiguous. That is the evidence needed to
    consider letting ADD_END_DATE reach QuickStatements for departed members.
``CONTRADICTED``
    They disagree. Leave the gates in ``diff`` alone; the detail says how the
    disagreement is shaped.
``INCONCLUSIVE``
    Too few people could be joined to conclude anything — a finding about the
    reach, not about the dates. Note this is the *expected* answer on a run
    where Wikidata is already tidy: no open memberships for departed members
    means nothing to measure, which is good news about the data and no news
    about the question.

Run it locally::

    uv run python scripts/verify_departures.py

or dispatch the "Verify assumptions" workflow, which runs it alongside the
other probes. Like them it reports without gating: what it decides is whether a
*bulk apply* is safe, and today nothing it could falsify is emitted at all —
the suggestions it measures are report-only by construction.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Make the ``src`` layout importable without installing the package.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wd_parliament.config import load_config  # noqa: E402
from wd_parliament.http_client import HttpClient  # noqa: E402
from wd_parliament.models import (  # noqa: E402
    NAME_FROM_ALIAS,
    NAME_FROM_NAMED_AS,
    P_SUBJECT_NAMED_AS,
    NameVariant,
)
from wd_parliament.parliament import (  # noqa: E402
    NULL_DATE,
    ParliamentClient,
    _as_date,
    tenures_from_segments,
)
from wd_parliament.resolve import match_by_identifier  # noqa: E402
from wd_parliament.wikidata import WikidataClient  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_openparldata import (  # noqa: E402
    BEGIN_FIELDS,
    CONFIRMED,
    CONTRADICTED,
    END_FIELDS,
    INCONCLUSIVE,
    _present,
    chamber_of,
    fetch,
)

# Below this many comparable people the verdict says nothing either way. Lower
# than ``compare_tenure_dates.MIN_JOINED`` on purpose: that script measures the
# whole sitting chamber and can demand a decent sample, while this population is
# whatever Wikidata happens to have left open — a tidy Wikidata makes it small,
# and demanding 10 would report a shortage of *errors* as a failed probe.
MIN_COMPARABLE = 5

# How many offending rows a section prints. Also the line between "these people
# need checking" and "the sources disagree": a reader can act on a list they
# can see, and cannot act on a count.
_MAX_ROWS = 20


@dataclass
class Departure:
    """One person the reverse walk reports, seen from every side at once."""

    qid: str
    label: str
    council: str
    # From Wikidata: the identifier value, and the open statement being judged.
    identifier: Optional[str] = None
    # The other names the item carries: aliases, and the P1810 "subject named
    # as" qualifier on the identifier statement. Empty is ordinary — most items
    # have neither — and must read as "nothing more to compare".
    name_variants: List[NameVariant] = field(default_factory=list)
    person_number: Optional[int] = None
    statement_start: Optional[date] = None
    statements_for_seat: int = 0
    open_statements: int = 0
    # From the source's historic record.
    history_name: Optional[str] = None
    source_start: Optional[date] = None
    source_end: Optional[date] = None
    # From OpenParlData, the independent second source.
    opd_end: Optional[date] = None
    opd_rows: int = 0
    # The person record(s) those rows came from, and whether several of them
    # claim this Q-ID. Printed on every disagreement: run 17's single dissenter
    # was not a date dispute at all but rows belonging to somebody else, and
    # the person id is what says so at a glance.
    opd_person_ids: List[int] = field(default_factory=list)
    opd_ambiguous: bool = False

    @property
    def reachable(self) -> bool:
        """Did the identifier resolve to a row in the historic record?"""
        return self.person_number is not None and self.source_start is not None

    @property
    def has_date_to_emit(self) -> bool:
        """Is there a leaving date a P582 could actually be built from?"""
        return self.source_end is not None

    @property
    def comparable(self) -> bool:
        """Both sources dated it, and they are talking about the same person.

        A Q-ID several OpenParlData person records claim is **not** comparable:
        its rows are two people's pooled together, so the date is whichever of
        them has the later membership. That is a broken join, not a dispute
        about when somebody left, and scoring it as a disagreement blames the
        source for the probe's arithmetic.
        """
        return (
            self.source_end is not None
            and self.opd_end is not None
            and not self.opd_ambiguous
        )

    @property
    def agrees(self) -> bool:
        return self.comparable and self.source_end == self.opd_end

    @property
    def ambiguous(self) -> bool:
        """Several P39 statements for this seat: property + value names none."""
        return self.statements_for_seat > 1

    @property
    def start_disagrees(self) -> bool:
        """Is the open statement plausibly about a *different* spell?

        A statement whose P580 is not this tenure's start is most likely the
        member's earlier service, and closing it with this tenure's end would
        put a wrong span on a real statement. An undated statement says nothing
        either way and is not counted here — it is counted in section D's own
        line, because "no P580" is a different problem from "the wrong P580".
        """
        return (
            self.statement_start is not None
            and self.source_start is not None
            and self.statement_start != self.source_start
        )


# What a name comparison can conclude. ``VARIANT`` is the bucket run 16 forced
# into existence: all 29 "contradictions" it found were the *same* person spelt
# differently on the two sides — Börlin/Boerlin, Ettlin/Etlin, Bremi/
# Bremi-Forrer, Vonderweid/von der Weid. A check that calls those wrong people
# is not strict, it is broken, because the 29 false alarms hide the one real
# mismatch nobody would then look for. So variants are recognised, counted and
# **printed** rather than folded into agreement: a check that stops showing its
# work has stopped checking.
NAME_EXACT = "exact"
NAME_VARIANT = "variant"
NAME_NEAR = "near"
NAME_DIFFERENT = "different"

_PARENTHESISED = re.compile(r"\(.*?\)")
_NOT_LETTERS = re.compile(r"[^a-z]")
_DOUBLED = re.compile(r"(.)\1+")


def surname_of(label: str) -> str:
    """The surname in a Wikidata label. Pure.

    Everything after the last space, lowercased. Crude, and deliberately so:
    this corroborates an identifier, it does not establish one.
    """
    return label.strip().rpartition(" ")[2].casefold()


def fold_name(text: Optional[str]) -> str:
    """Reduce a surname to what two spellings of it share. Pure.

    Every transformation here was paid for by a real pair from run 16, and each
    is applied to **both** sides, so it can only ever merge spellings — never
    tell two people apart that the raw comparison would have distinguished:

    - parentheses go: ``Bünzli(y)`` is the source recording two spellings in
      one field;
    - umlauts fold both ways, ``ö`` → ``o`` and ``oe`` → ``o``, because the two
      sides transliterate differently: ``Börlin`` / ``Boerlin``;
    - accents go by NFKD: ``Demiéville`` / ``Demieville``;
    - spaces, hyphens and apostrophes go: ``von der Weid`` / ``Vonderweid``;
    - doubled letters collapse: ``Patocchi`` / ``Pattocchi``, ``Etlin`` /
      ``Ettlin``, ``Traveletti`` / ``Travelletti``.

    The last one is the loosest and the one to watch: it also merges names that
    differ only by a doubled letter. That is a deliberate trade — this is
    corroboration, and a false *alarm* costs more here than a missed one,
    because 29 of them buried the question of whether any real mismatch exists.
    """
    text = _PARENTHESISED.sub("", text or "").casefold()
    for src, dst in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _NOT_LETTERS.sub("", text)
    for src, dst in (("ae", "a"), ("oe", "o"), ("ue", "u")):
        text = text.replace(src, dst)
    return _DOUBLED.sub(r"\1", text)


def _name_parts(text: Optional[str]) -> List[str]:
    """The folded components of a compound surname. Pure.

    ``Bremi-Forrer`` is one person's married name and ``Bremi`` is the same
    person; splitting on the separators is what lets either side carry the
    fuller form.
    """
    return [p for p in (fold_name(part) for part in re.split(r"[-\s]+", text or "")) if p]


def name_relation(label: Optional[str], last_name: Optional[str]) -> Optional[str]:
    """How a Wikidata label relates to a history row's surname. Pure.

    ``None`` when either side is missing — unknown, which is neither agreement
    nor contradiction and must not be scored as either. An item whose label is
    a Q-ID (the fallback when no label exists in any queried language) is also
    unknown rather than a mismatch.
    """
    if not label or not last_name:
        return None
    label = label.strip()
    if label.startswith("Q") and label[1:].isdigit():
        return None
    wanted = last_name.strip()
    if not wanted:
        return None

    if wanted.casefold() == surname_of(label) or wanted.casefold() in label.casefold():
        return NAME_EXACT

    folded_label = fold_name(label)
    folded_wanted = fold_name(wanted)
    if folded_wanted and folded_wanted in folded_label:
        return NAME_VARIANT

    # A compound or married surname on either side: any shared component is the
    # same family name recorded at different lengths.
    label_parts = set(_name_parts(label))
    if label_parts & set(_name_parts(wanted)):
        return NAME_VARIANT

    if _near(folded_label, folded_wanted):
        return NAME_NEAR
    return NAME_DIFFERENT


def _within_one_edit(a: str, b: str) -> bool:
    """Are these one insertion, deletion or substitution apart? Pure."""
    if abs(len(a) - len(b)) > 1:
        return False
    if a == b:
        return True
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1:] == b[i + 1:]
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    return longer[i + 1:] == shorter[i:]


def _near(folded_label: str, folded_wanted: str) -> bool:
    """Is the surname one character away from somewhere in the label? Pure.

    Run 18's five survivors, and every one of them is the same person:
    ``Zünd``/``Zündt``, ``Despland``/``Desplands``, ``Desfayes``/``Défayes``,
    ``Wunderly``/``Wunderli``, ``de Crousaz``/``Decrousnaz`` — a trailing
    consonant, a y for an i, an inserted n. Nineteenth-century names, two
    sources, one letter.

    Windowed rather than whole-string because the label carries the forenames
    too, and the surname can sit anywhere in it.

    **This bucket accepts nothing.** It is a reporting refinement, not a
    loosening: a near miss still stops the section returning CONFIRMED, it just
    stops the probe calling somebody a different person on the strength of one
    character. That is why widening it cannot cost safety — the worst it can do
    is move a row from "wrong person" to "check this one".
    """
    if not folded_wanted or not folded_label:
        return False
    wanted_len = len(folded_wanted)
    for size in (wanted_len - 1, wanted_len, wanted_len + 1):
        if size <= 0:
            continue
        for start in range(0, max(0, len(folded_label) - size) + 1):
            if _within_one_edit(folded_label[start:start + size], folded_wanted):
                return True
    return False


# How conclusive each relation is, best first. Only used to pick the *strongest*
# reading among the names an item carries; the buckets themselves are unchanged.
NAME_RANK = {NAME_EXACT: 0, NAME_VARIANT: 1, NAME_NEAR: 2, NAME_DIFFERENT: 3}

# Where the name that produced the verdict came from. ``ORIGIN_LABEL`` is the
# item's label, the only one this check read before; the other two come from
# ``models`` because ``WikidataClient.get_name_variants`` stamps them.
ORIGIN_LABEL = "label"


@dataclass(frozen=True)
class NameCheck:
    """The strongest reading of an item's names against the source's surname."""

    relation: Optional[str] = None
    origin: Optional[str] = None  # ORIGIN_LABEL, NAME_FROM_ALIAS, NAME_FROM_NAMED_AS
    matched: Optional[str] = None  # the name that produced ``relation``

    @property
    def from_variant(self) -> bool:
        """Did a name other than the label settle this?"""
        return self.origin is not None and self.origin != ORIGIN_LABEL


def check_name(
    label: Optional[str],
    variants: Sequence[NameVariant],
    last_name: Optional[str],
) -> NameCheck:
    """Judge every name the item carries and keep the strongest. Pure.

    The label alone was never the whole of what Wikidata says a person is
    called. Two other assertions exist and both are about *this item*:

    - an **alias** ("also known as"), which is where a second spelling of a
      nineteenth-century name usually lives;
    - **P1810 "subject named as"** on the identifier statement, which records
      how the person is written in the very database the identifier points at.
      Run 18's ``Johann Zünd`` / ``Zündt`` is the shape it exists for: the item
      is labelled one way and states, on the P1307 statement itself, that
      parlament.ch spells it the other.

    Trying all of them can only move a row *towards* agreement, never away from
    it — the best relation wins, and a candidate that reads DIFFERENT cannot
    displace one that reads EXACT. So this cannot manufacture a CONTRADICTED,
    which is the finding that would block a bulk apply; it can only stop the
    probe reporting a name it already had the answer to.

    What it is not: proof. An alias is asserted by whoever wrote the item, so
    matching one is corroboration of the same kind the label gives. P1810 is
    stronger — it is a claim about the source — but it is still a claim on
    Wikidata rather than a second identifier. That is why agreement here is
    reported as corroboration and only a *disagreement* is treated as
    conclusive.

    The label is tried first so that the ordinary case keeps saying "the label
    matches" rather than crediting an alias that happens to say the same thing.
    """
    best = NameCheck()
    candidates = [(ORIGIN_LABEL, label)] + [(v.origin, v.name) for v in variants]
    for origin, text in candidates:
        relation = name_relation(text, last_name)
        if relation is None:
            continue
        if best.relation is None or NAME_RANK[relation] < NAME_RANK[best.relation]:
            best = NameCheck(relation=relation, origin=origin, matched=text)
        if best.relation == NAME_EXACT:
            break
    return best


def _variants_seen(departure: "Departure") -> str:
    """What else was compared, for a row the names did not settle. Pure.

    A near miss reads differently depending on whether the item had other names
    that also failed or had none at all: the first says the sources really do
    spell this person differently everywhere, the second says nobody has
    recorded the source's spelling yet — and *that* one is fixable by adding a
    P1810, which is a job a reader can do.
    """
    if not departure.name_variants:
        return "no alias or P1810 to check"
    return "also checked: " + ", ".join(
        f"{v.origin} '{v.name}'" for v in departure.name_variants[:4]
    )


def chained_end(rows: Sequence[Dict[str, Any]]) -> Optional[date]:
    """OpenParlData's end of the **current continuous run**. Pure.

    The mirror of ``compare_tenure_dates.chained_start``, and simpler than it
    for a reason worth stating: rows are per-term federally, so a member who
    sat 2011-2019 has two of them and the tenure the tool models spans both —
    but every row after the newest ends *inside* that span, so the tenure's end
    is the newest row's end and no chaining changes it. The start needs the
    walk; the end does not.

    ``None`` when the newest row is open. That means "still sitting per this
    source", which is an answer, and it must never fall back to an earlier
    row's end — that would manufacture a leaving date for somebody who has not
    left.
    """
    if not rows:
        return None
    begin_field = _present(rows, BEGIN_FIELDS)
    end_field = _present(rows, END_FIELDS)
    if begin_field is None or end_field is None:
        # "No such column" and "column full of nulls" are indistinguishable
        # through .get() and mean opposite things — see
        # verify_openparldata.classify_seat_memberships.
        return None

    spans: List[Tuple[date, Optional[date]]] = []
    for row in rows:
        start = _as_date(row.get(begin_field))
        if start is None:
            continue
        spans.append((start, _as_date(row.get(end_field))))
    if not spans:
        return None
    spans.sort(key=lambda s: s[0])
    return spans[-1][1]


def classify_reach(departures: Sequence[Departure]) -> Tuple[str, str, List[str]]:
    """Can the reverse walk's people be found in the source at all? Pure.

    Every count is derived from the population itself rather than passed in
    alongside it, so the "no identifier" line cannot drift out of step with the
    people it is describing.
    """
    lines: List[str] = []
    identified = [d for d in departures if d.person_number is not None]
    reachable = [d for d in departures if d.reachable]
    dated = [d for d in departures if d.has_date_to_emit]

    lines.append(f"people reported as departed:      {len(departures)}")
    lines.append(f"  carrying an identifier value:   {len(identified)}")
    lines.append(f"  no identifier value at all:     {len(departures) - len(identified)}")
    lines.append(f"  found in the historic record:   {len(reachable)}")
    lines.append(f"  with a closed tenure (a date):  {len(dated)}")

    if not departures:
        return (
            INCONCLUSIVE,
            "Wikidata has no open membership for anybody the source does not "
            "list as sitting, so this pass reports nobody and there is nothing "
            "to measure. Good news about the data, no news about the question.",
            lines,
        )
    if not reachable:
        return (
            CONTRADICTED,
            f"None of the {len(departures)} people could be found in the "
            "historic record. Either the identifier values are not person "
            "numbers or the history does not carry departed members — check "
            "section B's sample rows before believing any date this tool "
            "prints for them.",
            lines,
        )
    share = 100.0 * len(dated) / len(departures)
    return (
        CONFIRMED,
        f"{len(reachable)} of {len(departures)} resolve into the historic "
        f"record and {len(dated)} ({share:.1f}%) have a leaving date to "
        "suggest. Reach only — whether the dates are right is section C.",
        lines,
    )


def classify_identity(departures: Sequence[Departure]) -> Tuple[str, str, List[str]]:
    """Is the person the number reached the person the item is about? Pure.

    The departed twin of README step 1. It cannot be as strong: step 1 compared
    two identifiers, this compares a name against a label, so agreement is
    corroboration rather than proof. A *disagreement*, though, is conclusive in
    the direction that matters — the number is somebody else's.

    "A name against a label" is now "a name against every name Wikidata gives
    the item": its label, its aliases, and the P1810 "subject named as"
    qualifier on the identifier statement, which states outright how the source
    spells this person. Those are counted and printed separately from the label
    matches, because a check that stops showing its work has stopped checking —
    and because a match found only in an alias is a weaker reading than one the
    label gives, even though both are the same item's own assertions.
    """
    lines: List[str] = []
    judged = [d for d in departures if d.reachable]
    checks = [(d, check_name(d.label, d.name_variants, d.history_name)) for d in judged]
    agree = [d for d, c in checks if c.relation == NAME_EXACT]
    variants = [(d, c) for d, c in checks if c.relation == NAME_VARIANT]
    near = [d for d, c in checks if c.relation == NAME_NEAR]
    differ = [d for d, c in checks if c.relation == NAME_DIFFERENT]
    unknown = [d for d, c in checks if c.relation is None]

    # Settled by a name the label does not carry. Split by origin because the
    # two are different strengths of evidence: an alias is any spelling anybody
    # recorded, P1810 is the spelling *this source* uses.
    settled = [(d, c) for d, c in checks if c.from_variant and c.relation != NAME_DIFFERENT]
    by_alias = [(d, c) for d, c in settled if c.origin == NAME_FROM_ALIAS]
    by_named_as = [(d, c) for d, c in settled if c.origin == NAME_FROM_NAMED_AS]
    carry_variants = [d for d in judged if d.name_variants]

    lines.append(f"identities checked:               {len(judged)}")
    lines.append(f"  surname matches the label:      {len(agree)}")
    lines.append(f"  same name, spelt differently:   {len(variants)}")
    lines.append(f"  one character apart:            {len(near)} (unsettled)")
    lines.append(f"  surname contradicts the label:  {len(differ)}")
    lines.append(f"  one side has no name:           {len(unknown)}")
    lines.append("")
    lines.append(f"  items carrying another name:    {len(carry_variants)}")
    lines.append(f"    settled by an alias:          {len(by_alias)}")
    lines.append(f"    settled by P1810 'named as':  {len(by_named_as)}")
    if settled:
        lines.append("")
        lines.append("  settled by a name the label does not carry — read them:")
        for d, c in settled[:_MAX_ROWS]:
            lines.append(
                f"    {d.qid} '{d.label}' ({d.council}) -> #{d.person_number} "
                f"'{d.history_name}' via {c.origin} '{c.matched}' ({c.relation})"
            )
    if near:
        lines.append("")
        lines.append("  one character apart — settle these by hand:")
        for d in near[:_MAX_ROWS]:
            lines.append(
                f"    {d.qid} '{d.label}' ({d.council}) -> #{d.person_number} "
                f"'{d.history_name}' [{_variants_seen(d)}]"
            )
    if variants:
        lines.append("")
        lines.append("  accepted as spelling variants — read them:")
        for d, c in variants[:_MAX_ROWS]:
            source = f" via {c.origin} '{c.matched}'" if c.from_variant else ""
            lines.append(
                f"    {d.qid} '{d.label}' ({d.council}) -> #{d.person_number} "
                f"'{d.history_name}'{source}"
            )
    if differ:
        lines.append("")
        lines.append("  NOT the same name:")
        for d in differ[:_MAX_ROWS]:
            lines.append(
                f"    {d.qid} '{d.label}' ({d.council}) -> #{d.person_number} "
                f"'{d.history_name}' [{_variants_seen(d)}]"
            )

    if not judged:
        return (
            INCONCLUSIVE,
            "Nobody resolved into the historic record, so there was no identity "
            "to check.",
            lines,
        )
    if differ:
        return (
            CONTRADICTED,
            f"{len(differ)} of {len(judged)} identifier values reach a person "
            "whose surname is none of the names the item carries — not its "
            "label, not an alias, not the P1810 'subject named as' on the "
            "identifier statement — even after folding umlauts, accents, "
            "particles, married names and doubled letters, and not within one "
            "character of any of them either. Each one is a date that would be "
            "written to the wrong person's item. This alone blocks a bulk "
            "apply; check them by hand on the biography pages.",
            lines,
        )
    if near:
        return (
            INCONCLUSIVE,
            f"No identifier value reaches a different surname, but {len(near)} "
            f"of {len(judged)} are one character apart from every name the item "
            "carries and this comparison cannot settle them — a "
            "nineteenth-century name spelt two ways and a wrong person one "
            "letter away look identical from here. Each row above says whether "
            "the item offered any other name at all: where it says 'no alias or "
            "P1810 to check', adding the source's spelling as a P1810 qualifier "
            "on the identifier statement both records the finding on Wikidata "
            "and settles it here. Until somebody does, the population is not "
            "clean enough to apply in bulk. Note what this is *not*: not one "
            "case of the identifier reaching a plainly different person.",
            lines,
        )
    return (
        CONFIRMED,
        f"All {len(judged) - len(unknown)} checkable identifier values reach a "
        f"person whose surname is one the item carries — {len(agree)} matching "
        f"exactly and {len(variants)} after folding a spelling difference, "
        f"{len(settled)} of them found in an alias or a P1810 rather than in "
        f"the label ({len(unknown)} unknown). Corroboration, not proof: it is a "
        "name check, not the two-identifier comparison README step 1 could do "
        "for sitting members, and an alias is asserted by whoever wrote the "
        "item. Read the rows above rather than trusting the count.",
        lines,
    )


def classify_leaving_dates(
    departures: Sequence[Departure],
) -> Tuple[str, str, List[str]]:
    """Do the two sources agree on when these people left? Pure.

    **The verdict this script exists for.** ``ADD_END_DATE`` is mechanical, so
    a CONFIRMED here is what would justify letting the departed suggestions
    reach QuickStatements — and a wrong CONFIRMED writes a wrong P582 to every
    item in the population.
    """
    lines: List[str] = []
    dated = [d for d in departures if d.has_date_to_emit]
    comparable = [d for d in dated if d.comparable]
    sentinels = [d for d in dated if d.source_end and d.source_end <= NULL_DATE]
    reversed_spans = [
        d
        for d in dated
        if d.source_start and d.source_end and d.source_end < d.source_start
    ]

    # Two different reasons a date cannot be checked, and they mean opposite
    # things: OpenParlData not knowing the person is a gap in the join, while
    # OpenParlData knowing them and leaving the row open is a disagreement
    # about whether they left at all — the one case where "not comparable" is
    # itself the finding.
    unjoined = [d for d in dated if not d.opd_rows]
    still_open = [d for d in dated if d.opd_rows and d.opd_end is None]
    pooled = [d for d in dated if d.opd_ambiguous]

    lines.append(f"leaving dates from the source:    {len(dated)}")
    lines.append(f"  also dated by OpenParlData:     {len(comparable)}")
    lines.append(f"  not in OpenParlData at all:     {len(unjoined)}")
    lines.append(f"  OpenParlData still shows open:  {len(still_open)}")
    lines.append(f"  Q-ID claimed by several people: {len(pooled)} (skipped)")
    lines.append(f"  null-date sentinel (1753):      {len(sentinels)}")
    lines.append(f"  end before start:               {len(reversed_spans)}")

    if sentinels:
        return (
            CONTRADICTED,
            f"{len(sentinels)} leaving date(s) are the 1753-01-01 sentinel, "
            "which means 'no date' and not 'left in 1753'. parliament.NULL_DATE "
            "should have mapped these to None at the boundary — this is the "
            "0b failure, and it must be fixed before anything else here is "
            "believed.",
            lines,
        )
    if reversed_spans:
        return (
            CONTRADICTED,
            f"{len(reversed_spans)} tenure(s) end before they start. The "
            "segment walk is returning ends and starts from different spells, "
            "so neither date can be trusted.",
            lines,
        )
    if len(comparable) < MIN_COMPARABLE:
        return (
            INCONCLUSIVE,
            f"Only {len(comparable)} leaving date(s) could be checked against "
            f"OpenParlData, fewer than the {MIN_COMPARABLE} this needs. That is "
            "a finding about the join and the population size, not about the "
            "dates — a tidy Wikidata leaves little for this pass to report, and "
            "a small sample cannot license a bulk apply.",
            lines,
        )

    agree = [d for d in comparable if d.agrees]
    differ = [d for d in comparable if not d.agrees]
    share = 100.0 * len(agree) / len(comparable)
    lines.append("")
    lines.append(f"agree exactly:                    {len(agree)} ({share:.1f}%)")
    lines.append(f"disagree:                         {len(differ)}")
    lines.append("")
    for d in differ[:_MAX_ROWS]:
        lines.append(
            f"  #{d.person_number} {d.label} ({d.council}): "
            f"source {d.source_end}, OpenParlData {d.opd_end} "
            f"(from {d.opd_rows} row(s), person id(s) "
            f"{', '.join(str(i) for i in d.opd_person_ids) or '?'})"
        )

    if not differ:
        return (
            CONFIRMED,
            f"All {len(comparable)} comparable leaving dates agree. The historic "
            "record gives the same end as an independent source for people the "
            "current-members table does not contain — which is the evidence "
            "the departed suggestions' report-only gates were waiting for. "
            "Read section D before removing them: agreement on the date is not "
            "agreement on which statement it closes.",
            lines,
        )

    earlier = [d for d in differ if d.source_end < d.opd_end]  # type: ignore[operator]
    # Whether this is "a few bad rows" or "the sources disagree" is decided by
    # whether every disagreement is on the page above, not by a rate: a rate
    # threshold says different things about 1-in-6 and 1-in-2000 while meaning
    # the same arithmetic, and the reader can act on a list.
    shape = (
        f"All {len(differ)} are listed above, so the remedy is checking those "
        "people by hand rather than distrusting the source — but nothing "
        "excludes them today, and a P582 cannot be corrected by "
        "QuickStatements once written. "
        if len(differ) <= _MAX_ROWS
        else "That is more than can be listed, so this is the sources telling "
        "different stories rather than a handful of bad rows. "
    )
    return (
        CONTRADICTED,
        f"{len(differ)} of {len(comparable)} leaving dates disagree — "
        f"{len(earlier)} with the source earlier than OpenParlData and "
        f"{len(differ) - len(earlier)} later. " + shape + "Leave the gates in "
        "diff alone; the report already asks a human to read these dates.",
        lines,
    )


def classify_statement_ambiguity(
    departures: Sequence[Departure],
    statements_total: int = 0,
    statements_with_start: int = 0,
) -> Tuple[str, str, List[str]]:
    """Could a P582 name the statement it means to close? Pure.

    Two problems that look alike and are not. **Several P39 statements for one
    seat** is excludable: QuickStatements matches on property + main value, so
    such a command names none of them — and ``diff`` stamps
    ``ambiguous_statement`` on exactly those, which ``is_mechanical`` already
    refuses. A handful of them is a handful of people skipped, not a reason to
    distrust the rest. **An open statement whose P580 is not this tenure's
    start** is not excludable: it is most likely about an *earlier* spell, so
    closing it with this tenure's end writes a wrong span out of two
    individually correct dates, and nothing in ``is_mechanical`` can see that.
    Only the second blocks the population as a whole.

    ``statements_total`` / ``statements_with_start`` are the control group for
    the third count, and exist because run 16 returned "no P580" for **1,969 of
    1,969** — a number that is either a fact about Wikidata's undated P39
    imports or a broken read, and the two are indistinguishable from inside the
    subset. If the seats' statements carry P580 elsewhere, the read works.
    """
    lines: List[str] = []
    dated = [d for d in departures if d.has_date_to_emit]
    ambiguous = [d for d in dated if d.ambiguous]
    mismatched = [d for d in dated if d.start_disagrees]
    undated = [d for d in dated if d.statement_start is None]

    lines.append(f"people with a date to emit:       {len(dated)}")
    lines.append(f"  several P39 for the same seat:  {len(ambiguous)} (excludable)")
    lines.append(f"  open statement's P580 differs:  {len(mismatched)}")
    lines.append(f"  open statement has no P580:     {len(undated)}")
    if statements_total:
        share = 100.0 * statements_with_start / statements_total
        lines.append(
            f"  control: P39 statements for these seats carrying a P580: "
            f"{statements_with_start} of {statements_total} ({share:.1f}%)"
        )
    lines.append("")
    for d in mismatched[:_MAX_ROWS]:
        lines.append(
            f"  {d.qid} {d.label} ({d.council}): statement starts "
            f"{d.statement_start}, source tenure starts {d.source_start}"
        )

    if not dated:
        return (
            INCONCLUSIVE,
            "No leaving dates to emit, so no statement to aim one at.",
            lines,
        )
    if mismatched:
        return (
            CONTRADICTED,
            f"{len(mismatched)} open statement(s) start on a date that is not "
            "this tenure's start, so they are most likely about an earlier "
            "spell and closing them with this end would write a wrong span from "
            "two correct dates. Nothing in is_mechanical can see that, so it "
            "cannot be excluded automatically — read the rows above.",
            lines,
        )
    if ambiguous:
        return (
            CONFIRMED,
            f"{len(ambiguous)} of {len(dated)} item(s) hold several P39 "
            "statements for this seat, which the existing ambiguous_statement "
            "rule already refuses — they are skipped, not a reason to distrust "
            f"the other {len(dated) - len(ambiguous)}. No statement starts on "
            "the wrong date, so a P582 would land where it is meant to.",
            lines,
        )
    return (
        CONFIRMED,
        f"Every one of the {len(dated)} people holds a single P39 for the "
        "seat and none starts on a date other than this tenure's, so a P582 "
        "would land on the statement it is meant for.",
        lines,
    )


def overall(verdicts: Sequence[str]) -> str:
    """The weakest of the section verdicts. Pure.

    CONTRADICTED beats INCONCLUSIVE beats CONFIRMED: a bulk apply needs *every*
    section to come back clean, so the answer to "may it be applied" is the
    worst news, never the average and never the best.
    """
    if CONTRADICTED in verdicts:
        return CONTRADICTED
    if INCONCLUSIVE in verdicts:
        return INCONCLUSIVE
    return CONFIRMED


def _section(title: str, result: Tuple[str, str, List[str]]) -> str:
    verdict, detail, lines = result
    print("-" * 70)
    print(title)
    print("-" * 70)
    for line in lines:
        print("  " + line if line else "")
    print()
    print(f"{verdict}: {detail}")
    print()
    return verdict


def collect(
    config: Any,
    parliament: ParliamentClient,
    wikidata: WikidataClient,
    opd_client: Any = None,
    body_key: str = "CHE",
) -> Tuple[List[Departure], int, int]:
    """Build the population, from all three sources. Network.

    A person Wikidata says nothing identifying about is kept, not dropped:
    "no identifier value" is a reach finding that section A counts, and
    dropping them would quietly shrink the population the verdict is about.

    Also returns the control group for section D: how many P39 statements for
    these seats carry a P580 at all, across *everyone* rather than the departed
    subset. Without it, "none of the departed statements has a start" cannot be
    told apart from "the start is not being read".
    """
    members = parliament.get_members(councils=config.councils)
    print(f"parlament.ch: {len(members)} sitting member(s)")
    if not members:
        raise RuntimeError(
            "The source returned no sitting members, so every seat holder on "
            "Wikidata would look departed. Run scripts/verify_source.py first."
        )

    people = wikidata.get_position_holders(
        config.position_qids, config.language, config.identifier_property
    )
    print(f"Wikidata: {len(people)} item(s) hold one of the configured seats")

    seat_statements = [
        s
        for person in people.values()
        for qid in config.position_qids
        for s in person.statements_for(qid)
    ]
    with_start = sum(1 for s in seat_statements if s.start is not None)
    print(
        f"Wikidata: {len(seat_statements)} statement(s) for those seats, "
        f"{with_start} with a P580, "
        f"{sum(1 for s in seat_statements if s.end is not None)} with a P582"
    )

    # The same exact join the pipeline's first pass makes. Members matched only
    # by *name* are not excluded here, and cannot be: their items carry no
    # identifier value, so they land in the "no identifier" bucket below, which
    # no verdict is drawn from.
    match_by_identifier(members, people.values())
    sitting_qids = {m.qid for m in members if m.qid}

    # One read of MemberCouncilHistory answers both questions this needs: the
    # tenure dates, and the name that corroborates the identifier reached the
    # right person.
    segments = parliament.get_member_segments(councils=config.councils)
    tenures = tenures_from_segments(segments)
    print(f"parlament.ch: tenure dates for {len(tenures)} (person, council) pair(s)")

    seats_by_seat: Dict[tuple, List[Dict[str, Any]]] = {}
    claimed_twice: set = set()
    if opd_client:
        seats_by_seat, claimed_twice = _openparldata_seats(opd_client, body_key)

    departures: List[Departure] = []
    for body in config.bodies:
        for qid, person in sorted(people.items()):
            if qid in sitting_qids:
                continue
            statements = person.statements_for(body.position_qid)
            open_statements = [s for s in statements if s.is_open]
            if not open_statements:
                continue

            identifier = (person.parliament_id or "").strip() or None
            number = int(identifier) if identifier and identifier.isdigit() else None
            tenure = tenures.get((number, body.council.upper())) if number else None
            rows = seats_by_seat.get((qid, body.council.upper()), [])
            departures.append(
                Departure(
                    qid=qid,
                    label=person.label or qid,
                    council=body.council,
                    identifier=identifier,
                    person_number=number,
                    statement_start=open_statements[0].start,
                    statements_for_seat=len(statements),
                    open_statements=len(open_statements),
                    history_name=surname_in_history(segments, number, body.council),
                    source_start=tenure.start if tenure else None,
                    source_end=tenure.end if tenure else None,
                    opd_end=chained_end(rows),
                    opd_rows=len(rows),
                    opd_person_ids=sorted(
                        {r["person_id"] for r in rows if r.get("person_id") is not None}
                    ),
                    opd_ambiguous=qid in claimed_twice,
                )
            )

    _attach_name_variants(wikidata, config, departures)
    return departures, len(seat_statements), with_start


def _attach_name_variants(
    wikidata: WikidataClient, config: Any, departures: List[Departure]
) -> None:
    """Read the other names Wikidata gives these items, for section B. Network.

    Asked only for the people whose identity is actually judged — those the
    identifier reached a history row for — because that is the population
    section B draws a verdict from, and a query is bounded by the list it is
    handed. Everyone else is already counted as unreachable by section A.

    A failure here is **not** fatal and does not abort the run: without the
    variants the check is exactly the label comparison it was before, which
    reports more near misses rather than fewer. Losing evidence that can only
    settle rows must never cost the four verdicts.
    """
    qids = sorted({d.qid for d in departures if d.reachable})
    if not qids:
        return
    try:
        variants = wikidata.get_name_variants(
            qids, config.language, config.identifier_property
        )
    except Exception as exc:
        print(f"  ! aliases/{P_SUBJECT_NAMED_AS}: {exc}")
        print("  -> section B falls back to comparing the label alone.")
        return
    for d in departures:
        d.name_variants = variants.get(d.qid, [])
    named_as = sum(
        1
        for names in variants.values()
        if any(v.origin == NAME_FROM_NAMED_AS for v in names)
    )
    print(
        f"Wikidata: {len(variants)} of {len(qids)} judged item(s) carry an "
        f"alias or a {P_SUBJECT_NAMED_AS} value ({named_as} with "
        f"{P_SUBJECT_NAMED_AS} on the {config.identifier_property} statement)"
    )


def surname_in_history(
    segments: Dict[tuple, List[Any]], number: Optional[int], council: str
) -> Optional[str]:
    """The surname the historic record carries for this person number. Pure.

    Read off the segments already fetched for the dates, so it costs no extra
    request. ``None`` when the number reaches nothing — section A counts that,
    and section B then has no identity to judge rather than a failed one.
    """
    if number is None:
        return None
    rows = segments.get((number, council.upper()), [])
    return (rows[0].last_name or None) if rows else None


def _openparldata_seats(
    client: Any, body_key: str
) -> Tuple[Dict[tuple, List[Dict[str, Any]]], set]:
    """``(Q-ID, council)`` → the seat rows OpenParlData has for it.

    Keyed by seat and never by person, for the reason recorded in
    ``compare_tenure_dates``: a member who moved NR→SR chains one chamber's
    years onto the other's statement if the two are pooled, and every one of
    run 11's 22 "disagreements" was that.

    Also returns the Q-IDs that **more than one person record claims**. The
    join runs person → ``wikidata_id`` → Q-ID, and nothing makes that field
    unique: two records naming the same item pool their memberships under one
    key, so ``chained_end`` then answers with whichever of the two people has
    the later row. Run 17 is what found it — Alfred Gehrig, who left in 1971,
    was reported against a leaving date of 2014. Skipped and reported rather
    than arbitrated, the same rule ``resolve.match_by_identifier`` applies to a
    P1307 claimed by two items: a source contradicting itself about who
    somebody is cannot be resolved by picking one.
    """
    people, _ = fetch(client, "persons", body_key=body_key)
    qid_by_person: Dict[Any, str] = {}
    people_by_qid: Dict[str, List[Any]] = {}
    for row in people:
        qid = str(row.get("wikidata_id") or "").strip()
        if not qid:
            continue
        qid_by_person[row.get("id")] = qid
        people_by_qid.setdefault(qid, []).append(row.get("id"))

    claimed_twice = {q for q, ids in people_by_qid.items() if len(ids) > 1}
    print(
        f"OpenParlData: {len(qid_by_person)} person(s) carry a wikidata_id, "
        f"naming {len(people_by_qid)} distinct Q-ID(s)"
    )
    if claimed_twice:
        print(
            f"OpenParlData: {len(claimed_twice)} Q-ID(s) are claimed by several "
            "person records — their rows are pooled and cannot be compared, so "
            "they are skipped:"
        )
        for qid in sorted(claimed_twice)[:_MAX_ROWS]:
            print(f"    {qid} <- person ids {people_by_qid[qid]}")

    groups, _ = fetch(client, "groups", body_key=body_key)
    chambers: Dict[str, Dict[str, Any]] = {}
    for row in groups:
        council = chamber_of(row)
        if council and council not in chambers:
            chambers[council] = row
    print(f"OpenParlData: chambers {', '.join(sorted(chambers)) or 'NONE'}")

    seats: Dict[tuple, List[Dict[str, Any]]] = {}
    for council, group in chambers.items():
        rows, _ = fetch(client, "memberships", group_id=group.get("id"))
        for row in rows:
            qid = qid_by_person.get(row.get("person_id"))
            if qid:
                seats.setdefault((qid, council), []).append(row)
    print(f"OpenParlData: seat rows for {len(seats)} (Q-ID, council) pair(s)")
    return seats, claimed_twice


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config/parliament.yaml")
    parser.add_argument(
        "--body-key", default="CHE", help="OpenParlData body for the Federal Assembly."
    )
    parser.add_argument(
        "--no-openparldata",
        action="store_true",
        help="Skip the independent source. Section C is then INCONCLUSIVE by "
        "construction — a single source cannot corroborate itself.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)

    print("=" * 70)
    print("Step 8: may the departed members' P582 be applied in bulk?")
    print("=" * 70)

    parliament = ParliamentClient(session=http.session, language=config.language)
    wikidata = WikidataClient(http)

    opd_client = None
    if not args.no_openparldata:
        try:
            import swissparlpy as spp

            opd_client = spp.SwissParlClient(
                session=http.session, backend="openparldata"
            )
        except Exception as exc:  # a missing second source is a finding
            print(f"  ! OpenParlData: {exc}")
            print("  -> section C cannot compare and will say so.")

    try:
        departures, statements_total, statements_with_start = collect(
            config, parliament, wikidata, opd_client, args.body_key
        )
    except Exception as exc:
        print(f"  ! {exc}")
        return 1
    print()

    verdicts = [
        _section("A. Reach: can these people be found in the source?",
                 classify_reach(departures)),
        _section("B. Identity: is it the same person?",
                 classify_identity(departures)),
        _section("C. Agreement: do two sources give the same leaving date?",
                 classify_leaving_dates(departures)),
        _section("D. Which statement would the P582 close?",
                 classify_statement_ambiguity(
                     departures, statements_total, statements_with_start
                 )),
    ]

    result = overall(verdicts)
    print("=" * 70)
    print(f"Reach                 : {verdicts[0]}")
    print(f"Identity              : {verdicts[1]}")
    print(f"Leaving dates         : {verdicts[2]}")
    print(f"Statement ambiguity   : {verdicts[3]}")
    print(f"May P582 be bulk-applied for departed members? {result}")
    print("=" * 70)
    if result != CONFIRMED:
        print(
            "Leave the two gates in diff._departed_suggestion alone (no "
            "qid_source, no 'position' in the payload). The suggestions stay in "
            "the report with their dates, for a human to apply."
        )
    else:
        print(
            "Every section is clean. That is the evidence for removing those "
            "gates — do it deliberately, and paste one line into "
            "QuickStatements by hand first (README step 5)."
        )
    # Reports rather than gates, like the other three: what it decides is a bulk
    # apply, and the suggestions it measures are report-only today. Non-zero
    # only when nothing could be measured at all.
    return 0 if result != INCONCLUSIVE else 1


if __name__ == "__main__":
    sys.exit(main())
