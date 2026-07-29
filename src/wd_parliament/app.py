"""Wire the pipeline together: fetch, resolve, diff, report.

``run`` does the network setup and file writing; ``process`` does the work and
takes its clients as arguments, so a caller (or a test) can substitute them.
As in wd-squads, the per-chamber loop catches its own exceptions and records
them on the result, so one failing chamber never aborts the whole run.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import List, Optional, Sequence

from .config import Config, load_config
from .diff import compute_suggestions
from .http_client import HttpClient
from .models import QID_FROM_IDENTIFIER, QID_FROM_NAME, Member, Period
from .parliament import ParliamentClient
from .quickstatements import render, render_file
from .report import BodyResult, now_iso, write_reports
from .resolve import resolve_members
from .wikidata import WikidataClient

log = logging.getLogger(__name__)


def run(
    config_path: str | Path,
    reports_dir: str | Path = "reports",
    docs_dir: str | Path = "docs",
    limit: Optional[int] = None,
) -> List[BodyResult]:
    """Execute a full run and write the report files. Returns the results."""
    config = load_config(config_path)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)
    # Hand the OData client the same session, so parlament.ch sees the same
    # descriptive User-Agent as the Wikimedia APIs do.
    parliament = ParliamentClient(session=http.session, language=config.language)
    wikidata = WikidataClient(http)

    results = process(config, parliament, wikidata, limit=limit)

    generated_at = now_iso()
    all_suggestions = [s for r in results for s in r.suggestions]
    statements = render(all_suggestions, statement_model=config.statement_model)
    qs_text = (
        render_file(
            all_suggestions,
            retrieved=date.today(),
            statement_model=config.statement_model,
            generated_at=generated_at,
        )
        if config.quickstatements
        else None
    )

    write_reports(
        results,
        reports_dir,
        docs_dir,
        generated_at=generated_at,
        group_by=config.group_by,
        quickstatements_text=qs_text,
        quickstatements_count=len(statements),
    )
    log.info(
        "Wrote reports for %d chamber(s) (%d suggestions, %d QuickStatements) "
        "to %s and %s",
        len(results),
        len(all_suggestions),
        len(statements),
        reports_dir,
        docs_dir,
    )
    return results


def process(
    config: Config,
    parliament: ParliamentClient,
    wikidata: WikidataClient,
    limit: Optional[int] = None,
) -> List[BodyResult]:
    """Fetch everything once, then build a :class:`BodyResult` per chamber.

    Both the member list and the Wikidata view are fetched **once** for all
    chambers rather than per chamber: a member can move between councils, and
    the Wikidata query is the expensive one.
    """
    periods = parliament.get_periods()
    members = parliament.get_members(councils=config.councils)
    log.info(
        "parlament.ch: %d sitting members across %d legislative periods",
        len(members),
        len(periods),
    )

    # A run that read no members is not a run with nothing to say — it is a
    # broken read of the source, and it must not be allowed to look like the
    # former. With the member list empty every Wikidata seat holder falls
    # through to the diff's second pass and is reported as having left, which
    # produces thousands of confident, wrong suggestions. Fail loudly instead,
    # so the Action stops before committing anything.
    if not members:
        raise RuntimeError(
            "parlament.ch returned no sitting members for "
            f"{', '.join(config.councils)}. Expected roughly 246. Either the "
            "OData filters (Language/Active) or the CouncilAbbreviation values "
            "this config filters on are wrong — run "
            "'python scripts/verify_source.py' to see which."
        )

    people = wikidata.get_position_holders(config.position_qids, config.language)
    resolve_members(
        members, people.values(), wikidata, config.position_qids, config.language
    )

    results: List[BodyResult] = []
    for body in config.bodies:
        chamber_members = [m for m in members if m.council.upper() == body.council.upper()]
        if limit:
            chamber_members = chamber_members[:limit]
        log.info("[%s] %s: %d members", body.council, body.label, len(chamber_members))

        result = BodyResult(body=body)
        try:
            _fill_counts(result, chamber_members, people, body.position_qid)
            result.suggestions = compute_suggestions(
                body, chamber_members, people, periods, config
            )
        except Exception as exc:  # keep going even if one chamber fails
            log.exception("Failed to process %s", body.label)
            result.error = str(exc)
        results.append(result)
    return results


def _fill_counts(
    result: BodyResult,
    members: Sequence[Member],
    people: dict,
    position_qid: str,
) -> None:
    result.member_count = len(members)
    result.matched_by_identifier = sum(
        1 for m in members if m.qid_source == QID_FROM_IDENTIFIER
    )
    result.matched_by_name = sum(1 for m in members if m.qid_source == QID_FROM_NAME)
    result.unmatched = sum(1 for m in members if not m.qid)
    result.wikidata_open = sum(
        1
        for p in people.values()
        if any(s.is_open for s in p.statements_for(position_qid))
    )


def validate_periods(
    parliament: ParliamentClient,
    config: Config,
    vote_ids: Sequence[int],
) -> dict:
    """Cross-check the interval join against real roll-call attendance.

    Verification step 2 from the README. Returns, per legislative period, the
    members the overlap assigned, the ``PersonNumber``s that actually voted,
    and the two differences. They should agree modulo absences; a systematic
    mismatch means :mod:`period_overlap` is wrong.

    Takes explicit ``vote_ids`` (one roll-call per period) because unbounded
    ``Voting`` queries return 500s and must be batched.

    Two known holes, per the README: Ständerat roll-call votes only exist from
    the 2010s, and a very short tenure may include no recorded vote. So
    ``assigned_but_did_not_vote`` is expected to be non-zero (absences);
    ``voted_but_not_assigned`` is the number that must stay near zero, because
    somebody who voted in a period demonstrably sat in it.
    """
    from .period_overlap import coverage_report

    periods = parliament.get_periods()
    members = parliament.get_members(councils=config.councils)
    assigned = coverage_report(members, periods)
    attended = parliament.get_period_attendance(vote_ids)

    # ``Voting.IdLegislativePeriod`` is the LegislativePeriod row ID, while
    # ``coverage_report`` keys by LegislativePeriodNumber; translate before
    # comparing, or every period would look like a total mismatch.
    number_by_id = {p.id: p.number for p in periods if p.id is not None}

    out = {}
    for period_id, voters in attended.items():
        number = number_by_id.get(period_id, period_id)
        expected = assigned.get(number, set())
        out[number] = {
            "period_id": period_id,
            "assigned": len(expected),
            "voted": len(voters),
            "voted_but_not_assigned": sorted(voters - expected),
            "assigned_but_did_not_vote": len(expected - voters),
        }
    return out
