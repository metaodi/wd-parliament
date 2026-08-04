"""Measure the personal-data checks: does the source carry them, and how many?

README step 9. The checks themselves (P19, P1321, P106, P102, P856, P1971) are
safe by construction — a source that has nothing to say produces no suggestion
— but "safe" is not "useful", and this answers two questions about them.

**A. Which columns the source really has.** Run 19 (2026-08-04) settled it, and
found more wrong with the *adapters* than with the checks: federally there is
no occupation and no website column at all, while cantonally the party and the
birth date were being read from names OpenParlData does not use
(``party_name_de`` for ``party_de``, ``birthdate`` for ``birthday``) — two
silent failures nothing else would have shown. This section prints the columns
the service actually returns, which is what made that visible. It is the same
discipline ``openparldata.classify_seat_memberships`` follows: "no such column"
and "column full of nulls" are indistinguishable through ``.get()`` and mean
opposite things, so the column list is printed rather than inferred.

**B. How many suggestions each check would make.** These are *coverage* checks
across the whole chamber, not exceptions, so a property Wikidata records for
half the members produces of the order of a hundred rows. That is a legitimate
worklist, but it is a number an operator should see before turning the check on
rather than after. This section runs the real comparison — the same
``diff.compute_suggestions`` the pipeline calls — and counts what comes out.

Nothing here gates anything: the checks are report-only by construction (see
``quickstatements.is_mechanical``), so no answer this probe could give would
make the pipeline more or less safe to run. It exists so the config's
``person_data:`` list can be an informed choice.

    uv run python scripts/verify_person_data.py
    uv run python scripts/verify_person_data.py --config config/kantonsrat-zh.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Make the ``src`` layout importable without installing the package.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wd_parliament import openparldata, parliament  # noqa: E402
from wd_parliament.app import build_source  # noqa: E402
from wd_parliament.config import (  # noqa: E402
    SOURCE_OPENPARLDATA,
    SOURCE_PARLAMENT,
    Config,
    load_config,
)
from wd_parliament.diff import compute_suggestions, source_values  # noqa: E402
from wd_parliament.http_client import HttpClient  # noqa: E402
from wd_parliament.models import (  # noqa: E402
    KIND_ADD_PERSON_DATA,
    PERSON_DATA_BY_PROPERTY,
    Member,
    PersonDataCheck,
)
from wd_parliament.resolve import resolve_members  # noqa: E402
from wd_parliament.wikidata import WikidataClient  # noqa: E402

CONFIRMED = "CONFIRMED"
CONTRADICTED = "CONTRADICTED"
INCONCLUSIVE = "INCONCLUSIVE"

# Which source columns each check reads, per source. Kept here rather than
# imported wholesale because the *certain* names are inline in the mapping
# functions while the *guessed* ones are module constants — a reader of this
# probe needs to see both, and ``tests/test_verify_person_data.py`` pins that
# every supported property appears for every source, so a new check cannot be
# added without saying where it would come from.
SOURCE_COLUMNS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    SOURCE_PARLAMENT: {
        "P19": ("BirthPlace_City", "BirthPlace_Canton"),
        "P1321": ("Citizenship",),
        "P106": tuple(parliament.OCCUPATION_FIELDS),
        "P102": ("PartyName", "PartyAbbreviation"),
        "P856": tuple(parliament.WEBSITE_FIELDS),
        "P1971": ("NumberOfChildren",),
    },
    SOURCE_OPENPARLDATA: {
        "P19": tuple(openparldata.BIRTHPLACE_FIELDS),
        "P1321": tuple(openparldata.ORIGIN_FIELDS),
        "P106": tuple(openparldata.OCCUPATION_FIELDS),
        "P102": tuple(openparldata.PARTY_NAME_FIELDS),
        "P856": tuple(openparldata.WEBSITE_FIELDS),
        "P1971": tuple(openparldata.CHILDREN_FIELDS),
    },
}


# --- pure ------------------------------------------------------------------
def source_coverage(members: Sequence[Member], check: PersonDataCheck) -> int:
    """How many members the source gives a value for. Pure."""
    return sum(1 for m in members if source_values(m, check))


def present_columns(
    row_keys: Optional[Sequence[str]], candidates: Sequence[str]
) -> Optional[List[str]]:
    """Which candidate columns the source actually returns. Pure.

    ``None`` in, ``None`` out: a column listing that could not be read is not
    the same as a listing that came back without these columns, and
    :func:`classify_source` must not report the first as the second.
    """
    if row_keys is None:
        return None
    keys = set(row_keys)
    return [c for c in candidates if c in keys]


def classify_source(
    check: PersonDataCheck,
    covered: int,
    total: int,
    columns: Optional[Sequence[str]],
    candidates: Sequence[str],
) -> Tuple[str, str]:
    """What this source can say about one check. Pure.

    Three answers, and the difference between the last two is the whole reason
    the column list is printed rather than inferred:

    ``CONFIRMED``
        the source fills the column in for at least one member, so the check
        can fire and the count says how often.
    ``CONTRADICTED``
        **no** candidate column exists at all. The check is dead weight in
        ``person_data:`` — either drop it, or read the column list above and
        add the real name to the candidate tuple.
    ``INCONCLUSIVE``
        the column exists but is empty for every sitting member. That is a
        fact about today's members, not about the schema, so it is not a
        contradiction: a column filled in for nobody now may be filled in
        later, and the check costs nothing meanwhile.
    """
    if covered:
        share = covered / total * 100 if total else 0.0
        return (
            CONFIRMED,
            f"{covered} of {total} members ({share:.1f}%) carry a value, from "
            f"{', '.join(columns or []) or 'the mapped fields'}.",
        )
    if columns is None:
        return (
            INCONCLUSIVE,
            "The column listing could not be read, so an empty result cannot "
            "be attributed: it is either a missing column or an empty one, "
            "and those mean opposite things.",
        )
    if not columns:
        return (
            CONTRADICTED,
            "No such column. The source returns none of "
            f"{', '.join(candidates)}, so this check can never fire — remove "
            f"{check.property_id} from person_data:, or add the real column "
            "name to the candidate tuple once the listing above names it.",
        )
    return (
        INCONCLUSIVE,
        f"{', '.join(columns)} exists but is empty for all {total} sitting "
        "members. Nothing to suggest from today's chamber; the check costs "
        "nothing and may start firing when the source fills it in.",
    )


def count_by_property(suggestions: Sequence[Any]) -> Dict[str, int]:
    """Personal-data suggestions per property. Pure."""
    out: Dict[str, int] = {}
    for suggestion in suggestions:
        if suggestion.kind != KIND_ADD_PERSON_DATA:
            continue
        prop = str(suggestion.payload.get("property") or "?")
        out[prop] = out.get(prop, 0) + 1
    return out


def volume_note(count: int, members: int, covered: Optional[int] = None) -> str:
    """One line putting a suggestion count in proportion. Pure.

    A zero has two entirely different causes and run 19 printed the wrong one
    for three of seven checks: with ``covered=0`` the *source* said nothing, so
    there was never anything to compare, while with ``covered>0`` Wikidata
    genuinely already records it. Reading the first as the second is how a
    missing column comes to look like a well-maintained property.
    """
    if not count:
        if covered == 0:
            return (
                "no comparison was made — the source has no value for any "
                "member (see section A)."
            )
        return "nothing to do — every matched item already records it."
    share = count / members * 100 if members else 0.0
    scale = "a bulk worklist" if share >= 50 else "a clean-up"
    return f"{count} suggestion(s), {share:.0f}% of the chamber — {scale}."


# --- fetching --------------------------------------------------------------
def sample_columns(config: Config, source: Any) -> List[str]:
    """Every column name one row of the source's person data has.

    One page, not the whole table. An empty list means the listing could not be
    read, which :func:`classify_source` must not mistake for "no such column" —
    hence the caller passes ``None`` rather than ``[]`` when this fails.
    """
    if config.source == SOURCE_OPENPARLDATA:
        rows = source._rows(openparldata.PERSON_TABLE, body_key=config.body_key)
        return sorted({k for r in rows[:5] for k in r})
    rows = source._first_rows(
        parliament.MEMBER_TABLE, 1, Language=config.language.upper(), Active=True
    )
    return sorted({k for r in rows for k in r})


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config/parliament.yaml")
    parser.add_argument(
        "--skip-wikidata",
        action="store_true",
        help="Section A only: no SPARQL, so no suggestion counts.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    checks = config.person_data_checks
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)
    source = build_source(config, http)
    columns_for = SOURCE_COLUMNS.get(config.source, {})

    print("=" * 70)
    print(f"Personal-data checks for {args.config} ({config.source_name})")
    print("=" * 70)
    if not checks:
        print("person_data: is empty — no personal-data check is configured.")
        return 0
    print("configured: " + ", ".join(f"{c.property_id} ({c.label})" for c in checks))
    print()

    members = source.get_members(councils=config.councils)
    print(f"{len(members)} sitting member(s) read from the source.")

    # --- A. what the source carries ----------------------------------------
    print()
    print("=" * 70)
    print("A. Does the source carry these at all?")
    print("=" * 70)
    columns: Optional[List[str]]
    try:
        columns = sample_columns(config, source)
        print("columns the source returns:")
        for i in range(0, len(columns), 4):
            print("    " + ", ".join(columns[i : i + 4]))
    except Exception as exc:  # noqa: BLE001 - a gap in the listing, not a crash
        print(f"  ! could not list the columns: {exc}")
        columns = None
    print()

    verdicts: Dict[str, str] = {}
    covered_by_property: Dict[str, int] = {}
    for check in checks:
        candidates = columns_for.get(check.property_id, ())
        found = present_columns(columns, candidates)
        covered = source_coverage(members, check)
        covered_by_property[check.property_id] = covered
        verdict, message = classify_source(
            check, covered, len(members), found, candidates
        )
        verdicts[check.property_id] = verdict
        print(f"{check.property_id} {check.label}")
        print(f"    {verdict}: {message}")
        examples = [
            v
            for m in members[:200]
            for v in source_values(m, check)
        ][:3]
        if examples:
            print(f"    e.g. {'; '.join(examples)}")
    if args.skip_wikidata:
        print()
        print("(section B skipped: --skip-wikidata)")
        return 0 if all(v == CONFIRMED for v in verdicts.values()) else 1

    # --- B. how much work each check would create ---------------------------
    print()
    print("=" * 70)
    print("B. How many suggestions would each check make?")
    print("=" * 70)
    wikidata = WikidataClient(http)
    people = wikidata.get_position_holders(
        config.position_qids,
        config.language,
        config.identifier_property,
        config.person_data,
    )
    resolve_members(
        members,
        people.values(),
        wikidata,
        config.position_qids,
        config.language,
        config.identifier_property,
    )
    matched = [m for m in members if m.qid]
    queried = sum(
        1
        for m in matched
        if m.qid in people and people[m.qid].person_data_known
    )
    print(f"{len(matched)} of {len(members)} members matched to an item; "
          f"{queried} of those were reached by the personal-data query.")
    print()

    counts: Dict[str, int] = {}
    for body in config.bodies:
        chamber = [m for m in members if m.council.upper() == body.council.upper()]
        if not chamber:
            continue
        suggestions = compute_suggestions(body, chamber, people, [], config)
        for prop, count in count_by_property(suggestions).items():
            counts[prop] = counts.get(prop, 0) + count

    for check in checks:
        print(f"{check.property_id} {check.label}")
        print(
            "    "
            + volume_note(
                counts.get(check.property_id, 0),
                len(matched),
                covered_by_property.get(check.property_id),
            )
        )

    total = sum(counts.values())
    print()
    print("=" * 70)
    for check in checks:
        print(f"{check.property_id:<6} {verdicts[check.property_id]}")
    print(f"total personal-data suggestions this config would make: {total}")
    print("(none of them is mechanical — nothing here reaches suggestions.qs)")
    print("=" * 70)
    return 0 if all(v == CONFIRMED for v in verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
