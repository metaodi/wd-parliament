"""Join a member's tenure to the legislative periods it covers (P2937).

``MemberCouncil`` carries no legislative-period field, and the OData metadata
confirms there is no association between ``LegislativePeriod`` and
``MemberCouncil`` — its only associations are to ``Session``, ``Business`` and
``Vote``. The join therefore has to be *constructed*, and the primary way to do
that is interval overlap: intersect each period's ``StartDate``/``EndDate``
with the member's ``DateJoining``/``DateLeaving``.

That covers 100% of members in both chambers, costs a single request for the
~52 period rows, and is exactly what P2937 ("parliamentary term") means. A
tenure spanning several periods yields several P2937 values.

Everything here is pure. It is also the most consequential logic in the tool —
it decides a qualifier on every statement emitted — so it is tested
exhaustively in ``tests/test_period_overlap.py``.

Interval semantics
------------------
Both intervals are treated as **closed**: a tenure ending exactly on a period's
``EndDate`` is inside that period, and a tenure starting exactly on the
``StartDate`` is too. An unset ``DateLeaving`` (a sitting member) or an unset
period ``EndDate`` (the running legislature) means "open ended", i.e. +infinity.

A member with no ``DateJoining`` gets **no** periods rather than all of them.
Treating an unknown start as -infinity would silently assign all ~52 periods
and emit a flood of wrong P2937 qualifiers, which is far worse than making no
suggestion at all.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, List, Optional, Sequence

from .models import Member, Period

# Sentinels for the open ends of a half-known interval.
_MIN = date.min
_MAX = date.max


def intervals_overlap(
    a_start: Optional[date],
    a_end: Optional[date],
    b_start: Optional[date],
    b_end: Optional[date],
) -> bool:
    """True when the two closed intervals share at least one day.

    ``None`` ends mean "open ended"; a ``None`` *start* means "unbounded in the
    past", which callers should generally avoid — see the module docstring.
    """
    start_a = a_start or _MIN
    end_a = a_end or _MAX
    start_b = b_start or _MIN
    end_b = b_end or _MAX
    if end_a < start_a or end_b < start_b:
        # A reversed interval (bad data) covers nothing.
        return False
    return start_a <= end_b and start_b <= end_a


def period_covers(member: Member, period: Period) -> bool:
    """True when ``member``'s tenure overlaps ``period``."""
    if member.date_joining is None:
        return False
    if period.start is None:
        # A period without a start date cannot be intersected meaningfully.
        return False
    return intervals_overlap(
        member.date_joining, member.date_leaving, period.start, period.end
    )


def assign_periods(member: Member, periods: Iterable[Period]) -> List[Period]:
    """The legislative periods ``member``'s tenure covers, in ascending order.

    Returns an empty list when the member has no ``DateJoining``, or when no
    period intersects the tenure.
    """
    return sorted(
        (p for p in periods if period_covers(member, p)), key=lambda p: p.number
    )


def clip_to_period(
    member: Member, period: Period
) -> tuple[Optional[date], Optional[date]]:
    """The member's tenure restricted to one period: ``(start, end)``.

    Used by the ``period`` statement model, where each P39 statement covers a
    single legislature and so carries the *clipped* dates rather than the whole
    tenure's. The end stays ``None`` — i.e. the statement stays open — only
    when both the tenure and the period are open ended; a member still sitting
    in a period that has since closed gets that period's ``EndDate``.
    """
    start = member.date_joining
    if start is None or period.start is None:
        return (start, member.date_leaving)
    clipped_start = max(start, period.start)

    ends = [d for d in (member.date_leaving, period.end) if d is not None]
    clipped_end = min(ends) if ends else None
    return (clipped_start, clipped_end)


def current_period(periods: Sequence[Period], today: Optional[date] = None) -> Optional[Period]:
    """The period running on ``today`` (default: the real today).

    Falls back to the highest-numbered period when none matches — the running
    legislature usually has no ``EndDate``, but if the data ever dates it in the
    past we still want a sensible answer.
    """
    today = today or date.today()
    for period in sorted(periods, key=lambda p: p.number, reverse=True):
        if period.start is None:
            continue
        if period.start <= today and (period.end is None or today <= period.end):
            return period
    return max(periods, key=lambda p: p.number, default=None)


def period_by_number(periods: Iterable[Period], number: int) -> Optional[Period]:
    for period in periods:
        if period.number == number:
            return period
    return None


def coverage_report(
    members: Iterable[Member], periods: Sequence[Period]
) -> dict[int, set]:
    """``PersonNumber`` sets per period number, from interval overlap alone.

    This is the left-hand side of the validation described in the README: run
    it over all current members, then compare a period's set against the
    ``PersonNumber``s in one roll-call vote from that period
    (``parliament.get_period_attendance``). They should agree modulo absences;
    a systematic mismatch means this module's interval logic is wrong.
    """
    coverage: dict[int, set] = {}
    for member in members:
        for period in assign_periods(member, periods):
            coverage.setdefault(period.number, set()).add(member.person_number)
    return coverage
