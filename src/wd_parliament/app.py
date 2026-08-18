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
from typing import Any, Dict, List, Optional, Sequence

from .config import (
    SOURCE_GEVER,
    SOURCE_KRDATEN,
    SOURCE_OPENPARLDATA,
    Config,
    load_config,
)
from .diff import compute_suggestions
from .enrich import Enrichment
from .http_client import HttpClient
from .models import QID_FROM_IDENTIFIER, QID_FROM_NAME, Member, Period, Tenure
from .parliament import (
    HISTORIC_MEMBER_TABLE,
    ParliamentClient,
    apply_tenure_starts,
)
from .quickstatements import render, render_file
from .report import BodyResult, now_iso, write_reports
from .resolve import resolve_members
from .wikidata import WikidataClient

log = logging.getLogger(__name__)


def build_source(config: Config, http: HttpClient):
    """The member source this config asks for.

    The one place the two sources are chosen between. Both are handed the
    :class:`~.http_client.HttpClient`'s session, so the APIs see the same
    descriptive User-Agent as the Wikimedia ones do, and both return the same
    dataclasses — everything after this point cannot tell which it got.
    """
    if config.source == SOURCE_KRDATEN:
        from .krdaten import KrDatenClient

        log.info(
            "Source: the Staatsarchiv's KR-Daten register, joined on %s",
            config.identifier_property,
        )
        return KrDatenClient(session=http.session, language=config.language)
    if config.source == SOURCE_GEVER:
        from .gever import GeverClient

        log.info(
            "Source: %s, matched by NAME (it supplies no value for %s)",
            config.source_name,
            config.identifier_property,
        )
        return GeverClient(
            session=http.session,
            language=config.language,
            backend=config.gever_backend,
            bodies=config.bodies,
        )
    if config.source == SOURCE_OPENPARLDATA:
        from .openparldata import OpenParlDataClient

        log.info(
            "Source: OpenParlData, body %r, joined on %s",
            config.body_key,
            config.identifier_property,
        )
        return OpenParlDataClient(
            session=http.session,
            language=config.language,
            body_key=config.body_key,
            bodies=config.bodies,
        )
    log.info("Source: parlament.ch OData, joined on %s", config.identifier_property)
    return ParliamentClient(session=http.session, language=config.language)


def build_enrichment(config: Config, http: HttpClient):
    """The *second* source this config asks for, or ``None``.

    Absent by default. A run reads one source unless a config says otherwise,
    and what a second one may do is bounded by :mod:`enrich`: it produces no
    members, and a disagreement can only withhold a mechanical edit, never
    supply a value.
    """
    if not config.enrich.enabled:
        return None
    if config.enrich.source != SOURCE_OPENPARLDATA:
        log.warning(
            "No enrichment client for source %r; the cross-check is skipped",
            config.enrich.source,
        )
        return None
    from .enrich import OpenParlDataEnricher

    log.info(
        "Cross-checking against %s, body %r",
        config.enrich.source_name,
        config.enrich.body_key,
    )
    return OpenParlDataEnricher(
        session=http.session,
        body_key=config.enrich.body_key,
        groups=config.enrich.groups,
    )


def run(
    config_path: str | Path,
    reports_dir: str | Path = "reports",
    docs_dir: str | Path = "docs",
    limit: Optional[int] = None,
) -> List[BodyResult]:
    """Execute a full run and write the report files. Returns the results."""
    config = load_config(config_path)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)
    parliament = build_source(config, http)
    wikidata = WikidataClient(http)

    results = process(
        config,
        parliament,
        wikidata,
        limit=limit,
        enricher=build_enrichment(config, http),
    )

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
        source_name=config.source_name,
        identifier_property=config.identifier_property,
        district_label=config.district_label,
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
    enricher: Any = None,
    today: Optional[date] = None,
) -> List[BodyResult]:
    """Fetch everything once, then build a :class:`BodyResult` per chamber.

    Both the member list and the Wikidata view are fetched **once** for all
    chambers rather than per chamber: a member can move between councils, and
    the Wikidata query is the expensive one.

    ``today`` is "as of what date is a source row an active seat" — passed
    straight through to ``get_members``. ``None`` (the default, and what a
    live run always uses) means the real wall-clock date; a test pins it so a
    fixture stays pinned to the day it was written rather than drifting as
    the calendar moves past a fixture row's date.
    """
    periods = parliament.get_periods()
    members = parliament.get_members(councils=config.councils, today=today)
    log.info(
        "parlament.ch: %d sitting members across %d legislative periods",
        len(members),
        len(periods),
    )

    # P580 must come from the tenure start, and MemberCouncil.DateJoining is a
    # *segment* start — see models.Member.start_date and README step 0c. The
    # history is the only place the real one lives, so it is fetched here rather
    # than per chamber. A failure degrades to the raw field instead of aborting:
    # a slightly-wrong P580 on 11 of 246 members is a worse report, whereas no
    # report at all is a worse outcome.
    try:
        segments = parliament.get_member_segments(councils=config.councils)
        corrected = apply_tenure_starts(members, segments)
        log.info(
            "MemberCouncilHistory: tenure start set for %d member(s); %d had a "
            "later segment start in MemberCouncil",
            sum(1 for m in members if m.tenure_start),
            corrected,
        )
    except Exception:  # noqa: BLE001 - degrade, do not abort
        log.warning(
            "Could not read %s; P580 falls back to MemberCouncil.DateJoining, "
            "which is a segment start for some members (README step 0c)",
            HISTORIC_MEMBER_TABLE,
            exc_info=True,
        )

    # A run that read no members is not a run with nothing to say — it is a
    # broken read of the source, and it must not be allowed to look like the
    # former. With the member list empty every Wikidata seat holder falls
    # through to the diff's second pass and is reported as having left, which
    # produces thousands of confident, wrong suggestions. Fail loudly instead,
    # so the Action stops before committing anything.
    if not members:
        probe = {
            SOURCE_OPENPARLDATA: "scripts/verify_kantonsrat.py",
            SOURCE_KRDATEN: "scripts/verify_krrr.py",
            SOURCE_GEVER: "scripts/verify_gever_city.py",
        }.get(config.source, "scripts/verify_source.py")
        raise RuntimeError(
            f"The source returned no sitting members for "
            f"{', '.join(config.councils)}. Either the filters or the "
            "chamber keys this config uses are wrong — run "
            f"'python {probe}' to see what the source actually has."
        )

    # Dates for people the members table has never heard of. The diff's second
    # pass finds members Wikidata still records as sitting, and until now could
    # only say "look the leaving date up by hand" — the source knows it, in the
    # historic table this pipeline already reads. Optional in the same way the
    # tenure starts are: a source that cannot answer leaves the report saying
    # what it said before, which is worse than the dates and better than a
    # guess.
    tenures: Dict[tuple, Tenure] = {}
    try:
        tenures = parliament.get_tenures(councils=config.councils)
    except Exception:  # noqa: BLE001 - degrade, do not abort
        log.warning(
            "Could not read tenure dates from the source; members recorded as "
            "sitting on Wikidata will be reported without a leaving date",
            exc_info=True,
        )

    # Data errors in the *source's* own Wikidata links. Only a source that
    # asserts such links has any, so a missing method is the ordinary case and
    # not a degradation: parlament.ch says nothing about Wikidata at all.
    link_conflicts: Dict[tuple, List[int]] = {}
    reader = getattr(parliament, "get_link_conflicts", None)
    if reader is not None:
        try:
            link_conflicts = reader(councils=config.councils)
        except Exception:  # noqa: BLE001 - degrade, do not abort
            log.warning(
                "Could not read the source's Wikidata links; duplicated links "
                "will not be reported",
                exc_info=True,
            )

    # The second source, when one is configured. Its failure degrades the run
    # to a single-source one and says so out loud: a *silent* loss of the
    # cross-check would leave the report looking as though the two agreed.
    enrichment = Enrichment()
    if enricher is not None:
        try:
            enrichment = enricher.fetch(councils=config.councils)
            log.info(
                "Cross-check: %d seat(s) from %d linked person record(s); "
                "%d item(s) claimed by several records and skipped",
                len(enrichment.spans),
                enrichment.people_with_links,
                enrichment.skipped_conflicted,
            )
        except Exception:  # noqa: BLE001 - degrade, do not abort
            log.warning(
                "Could not read the cross-check source; this run compares "
                "against nothing and no disagreement can be reported",
                exc_info=True,
            )
    if enrichment.link_conflicts:
        link_conflicts.update(enrichment.link_conflicts)

    people = wikidata.get_position_holders(
        config.position_qids,
        config.language,
        config.identifier_property,
        config.person_data,
        # The identifier properties reported but not joined on — a
        # parliament's own member id beside the OpenParlData id, or the
        # reverse. One query each, read for presence only.
        extra_identifiers=[
            c.property_id
            for c in config.identifier_checks
            if c.property_id != config.identifier_property
        ],
    )
    resolve_members(
        members,
        people.values(),
        wikidata,
        config.position_qids,
        config.language,
        config.identifier_property,
        # An identifier property whose value has not been measured against the
        # source's person id may join the wrong people rather than none, so
        # every match it makes is checked against the item's own name and
        # birth date. See ``config.Config.identifier_verified``.
        corroborate=not config.identifier_verified,
        # A source that supplies no value for the property must not join on it
        # at all: the comparison would be between two id spaces, and a hit
        # would be a coincidence carrying the provenance ``is_mechanical``
        # trusts. See ``config.Config.identifier_from_source``.
        join_on_identifier=config.joins_on_identifier,
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
                body,
                chamber_members,
                people,
                periods,
                config,
                tenures=tenures,
                link_conflicts=link_conflicts,
                enrichment=enrichment.spans,
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
    result.identifier_mismatches = sum(1 for m in members if m.identifier_mismatch_qids)
    result.wikidata_open = sum(
        1
        for p in people.values()
        if any(s.is_open for s in p.statements_for(position_qid))
    )


def validate_periods(
    parliament: ParliamentClient,
    config: Config,
    vote_ids: Optional[Sequence[int]] = None,
    max_periods: int = 3,
) -> dict:
    """Cross-check the interval join against real roll-call attendance.

    README step 4. Returns, per legislative period, the members the overlap
    assigned, the ``PersonNumber``s that actually voted, and the differences
    between them. They should agree modulo absences; a systematic mismatch
    means :mod:`period_overlap` is wrong.

    ``vote_ids`` is one roll-call per period, taken explicitly because
    unbounded ``Voting`` queries return 500s and must be batched. Left empty,
    the votes are discovered with :meth:`ParliamentClient.find_votes` for the
    ``max_periods`` most recent periods the overlap assigned anybody to — the
    only periods where sitting members give this check any power.

    Two kinds of voter cannot testify about the interval logic, and both are
    counted separately rather than scored as mismatches:

    - **people who have since left.** The member list is the ~246 currently in
      office, so a roll-call from any period also contains members who have
      gone; they are absent from the list entirely. Counting them as "not
      assigned" would report a false mismatch that grows with the age of the
      vote. They are ``voted_but_no_longer_sitting``.
    - **people who sat under an earlier mandate.** The tool models one P39 per
      *seat*: someone who moved from the National Council to the Council of
      States has a Council of States tenure that rightly begins at the move,
      and their earlier votes were cast under a National Council statement this
      run is not about. The same goes for anyone who left and returned. Run 11
      scored 25 of these as mismatches, all in the 50th and 51st periods, and
      every one was a chamber switch. They are
      ``voted_before_their_current_tenure``, recognised by the period ending
      before :attr:`models.Member.start_date` — a plain date comparison, not
      :func:`period_overlap.assign_periods`, so a bug in that function cannot
      excuse itself here.

    Two known holes, per the README: Ständerat roll-call votes only exist from
    the 2010s, and a very short tenure may include no recorded vote. So
    ``assigned_but_did_not_vote`` is expected to be non-zero (absences);
    ``voted_but_not_assigned`` is the number that must stay at zero, because
    somebody who voted in a period demonstrably sat in it.
    """
    from .period_overlap import coverage_report

    periods = parliament.get_periods()
    members = parliament.get_members(councils=config.councils)

    # The same correction ``process`` applies, for the same reason: the overlap
    # reads ``Member.start_date``, so validating it against a member list whose
    # starts were never corrected would validate an interval the pipeline does
    # not use. Degrades rather than aborts, exactly as the run does.
    try:
        apply_tenure_starts(
            members, parliament.get_member_segments(councils=config.councils)
        )
    except Exception:  # noqa: BLE001 - degrade, do not abort
        log.warning(
            "Could not read %s; validating against MemberCouncil.DateJoining, "
            "which is a segment start for some members (README step 0c)",
            HISTORIC_MEMBER_TABLE,
            exc_info=True,
        )

    assigned = coverage_report(members, periods)
    if not vote_ids:
        candidates = sorted(
            (p for p in periods if p.id is not None and assigned.get(p.number)),
            key=lambda p: p.number,
            reverse=True,
        )
        discovered = parliament.find_votes(candidates[:max_periods])
        vote_ids = sorted(discovered.values())
        log.info("Discovered vote ids: %s", vote_ids)
    if not vote_ids:
        raise RuntimeError(
            "No roll-call votes could be found to validate against. Pass one "
            "IdVote per period explicitly: --validate-periods 12345 23456."
        )

    attended = parliament.get_period_attendance(vote_ids)

    # ``Voting.IdLegislativePeriod`` is the LegislativePeriod row ID, while
    # ``coverage_report`` keys by LegislativePeriodNumber; translate before
    # comparing, or every period would look like a total mismatch.
    number_by_id = {p.id: p.number for p in periods if p.id is not None}
    period_by_number = {p.number: p for p in periods}
    member_by_number = {m.person_number: m for m in members}
    sitting = set(member_by_number)

    out = {}
    for period_id, voters in attended.items():
        number = number_by_id.get(period_id, period_id)
        expected = assigned.get(number, set())
        comparable = voters & sitting
        period = period_by_number.get(number)

        earlier, unexplained = [], []
        for person in sorted(comparable - expected):
            if _predates_current_tenure(member_by_number[person], period):
                earlier.append(person)
            else:
                unexplained.append(person)

        out[number] = {
            "period_id": period_id,
            "assigned": len(expected),
            "voted": len(voters),
            "voted_and_still_sitting": len(comparable),
            "voted_but_no_longer_sitting": len(voters - sitting),
            "voted_before_their_current_tenure": earlier,
            "voted_but_not_assigned": unexplained,
            "assigned_but_did_not_vote": len(expected - voters),
        }
    return out


def _predates_current_tenure(member: Member, period: Optional[Period]) -> bool:
    """Did this period end before the member's current tenure began? Pure.

    If so, their vote in it was cast under an earlier mandate — another
    chamber, or a spell before a break in service — which the tool models as a
    separate P39 statement. Not being assigned that period is then correct.

    Deliberately a bare date comparison rather than a call into
    :mod:`period_overlap`: this is what stops the check excusing the very
    function it exists to test.
    """
    if period is None or period.end is None or member.start_date is None:
        return False
    return period.end < member.start_date
