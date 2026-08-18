"""Render the TODO list as Markdown files, a JSON dump and an HTML dashboard."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from jinja2 import Environment

from .models import KIND_LABEL, PRIORITY, Body, Suggestion

OTHER_GROUP = "Other"


@dataclass
class BodyResult:
    """Everything we learned about one chamber during a run."""

    body: Body
    suggestions: List[Suggestion] = field(default_factory=list)
    member_count: int = 0
    matched_by_identifier: int = 0
    matched_by_name: int = 0
    unmatched: int = 0
    wikidata_open: int = 0
    # Identifier matches rejected because the item they point at names somebody
    # else. Only ever non-zero under ``identifier_verified: false``, and then it
    # is the number that answers whether the property's values are the source's
    # person ids at all — which is why it is printed rather than merely logged.
    identifier_mismatches: int = 0
    error: Optional[str] = None

    @property
    def identifier_hit_rate(self) -> float:
        """Share of members joined exactly on P1307, as a percentage.

        The single most useful health number in a run: a high rate confirms the
        identifier join is doing the work, a low one means the name fallback is
        carrying more than it should.
        """
        if not self.member_count:
            return 0.0
        return self.matched_by_identifier / self.member_count * 100


def _slug(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text or "", flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", text) or "body"


def body_filename(body: Body) -> str:
    return f"{body.council}-{_slug(body.label)}.md"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def group_key(suggestion: Suggestion, group_by: str) -> str:
    """The constituency or parliamentary group a suggestion is filed under."""
    value = (
        suggestion.constituency if group_by == "constituency" else suggestion.parl_group
    )
    return value or OTHER_GROUP


def group_suggestions(
    suggestions: Sequence[Suggestion], group_by: str
) -> List[Tuple[str, List[Suggestion]]]:
    """Bucket suggestions by constituency (or group), "Other" always last."""
    buckets: Dict[str, List[Suggestion]] = {}
    for s in suggestions:
        buckets.setdefault(group_key(s, group_by), []).append(s)
    for items in buckets.values():
        items.sort(key=lambda s: (s.priority, s.member_label.casefold()))
    return sorted(buckets.items(), key=lambda kv: (kv[0] == OTHER_GROUP, kv[0].casefold()))


def by_kind(suggestions: Sequence[Suggestion]) -> List[Tuple[str, List[Suggestion]]]:
    buckets: Dict[str, List[Suggestion]] = {}
    for s in sorted(suggestions, key=lambda x: (x.priority, x.member_label.casefold())):
        buckets.setdefault(s.kind, []).append(s)
    return sorted(buckets.items(), key=lambda kv: PRIORITY.get(kv[0], 99))


# --- Markdown ---------------------------------------------------------------
def _markdown_member(s: Suggestion) -> str:
    if s.person_qid:
        name = f"**[{s.member_label}](https://www.wikidata.org/wiki/{s.person_qid})**"
    else:
        name = f"**{s.member_label}**"
    if s.person_number:
        bio = s.payload.get("biography")
        if bio:
            name += f" ([#{s.person_number}]({bio}))"
        else:
            name += f" (#{s.person_number})"
    if s.qid_source == "name":
        name += " ⚠️"
    return name


def _markdown_conflict_links(s: Suggestion) -> str:
    """Clickable links to the items claiming one identifier.

    A conflict is only actionable if the reader can open both items side by
    side, and the two Q-IDs are the whole content of the finding — for the
    orphan case there is no ``person_qid`` to link, because the point is that
    there is more than one.
    """
    qids = s.payload.get("duplicate_qids") or []
    if not qids:
        return ""
    links = ", ".join(f"[{q}](https://www.wikidata.org/wiki/{q})" for q in qids)
    return f" — items: {links}"


def _markdown_property_link(s: Suggestion) -> str:
    """The property page, for a finding whose whole content is a property.

    ``MISSING_IDENTIFIER`` has no value to offer — that is what distinguishes
    it from ``ADD_IDENTIFIER`` — so the property itself is the only thing the
    reader can be sent to.
    """
    url = s.links.get("property")
    if not url:
        return ""
    return f" — [{s.payload.get('property') or 'property'}]({url})"


def render_body_markdown(
    result: BodyResult,
    generated_at: str,
    group_by: str,
    source_name: str = "parlament.ch",
    identifier_property: str = "P1307",
    district_label: str = "Canton",
) -> str:
    """One chamber's Markdown report.

    The source and identifier are parameters rather than constants because the
    same renderer serves a cantonal chamber, where naming parlament.ch or P1307
    would send a reader to a service that has never heard of these members.
    """
    body = result.body
    lines = [
        f"# {body.label} — Wikidata TODO",
        "",
        f"Generated: {generated_at}",
        "",
        f"- Position item: [{body.position_qid}]"
        f"(https://www.wikidata.org/wiki/{body.position_qid})",
        f"- Sitting members ({source_name}): {result.member_count}",
        f"- Matched by {identifier_property}: {result.matched_by_identifier} "
        f"({result.identifier_hit_rate:.1f}%)",
        f"- Matched by name + birth date: {result.matched_by_name}",
        f"- Not matched at all: {result.unmatched}",
        f"- Open memberships on Wikidata: {result.wikidata_open}",
        f"- Suggested edits: {len(result.suggestions)}",
        "",
    ]

    # Only under ``identifier_verified: false``, and then it is the run's own
    # answer to the question the config could not: an identifier that matches
    # an item naming somebody else is two id spaces overlapping by accident,
    # and a large share of them means the join is on the wrong property.
    if result.identifier_mismatches:
        lines[-1] = (
            f"- ⚠️ {identifier_property} matches rejected because the item "
            f"names a different person: **{result.identifier_mismatches}** of "
            f"{result.identifier_mismatches + result.matched_by_identifier} "
            f"attempted. {identifier_property} is joined on unverified — a "
            "large share here means its values are not this source's person "
            "ids."
        )
        lines.append("")

    if result.error:
        lines += [f"> ⚠️ Could not fully process this chamber: {result.error}", ""]

    if not result.suggestions:
        lines += [f"✅ Nothing to do — {source_name} and Wikidata agree.", ""]
        return "\n".join(lines)

    lines += ["## By kind", ""]
    for kind, items in by_kind(result.suggestions):
        lines.append(f"- {KIND_LABEL.get(kind, kind)}: **{len(items)}**")
    lines.append("")

    label = district_label if group_by == "constituency" else "Parliamentary group"
    for name, items in group_suggestions(result.suggestions, group_by):
        lines.append(f"## {label}: {name} ({len(items)})")
        lines.append("")
        for kind, kind_items in by_kind(items):
            lines.append(f"### {KIND_LABEL.get(kind, kind)} ({len(kind_items)})")
            lines.append("")
            for s in kind_items:
                lines.append(
                    f"- {_markdown_member(s)} — {s.detail}"
                    f"{_markdown_conflict_links(s)}"
                    f"{_markdown_property_link(s)}"
                )
            lines.append("")
    return "\n".join(lines)


def render_index_markdown(
    results: Sequence[BodyResult],
    generated_at: str,
    quickstatements: int = 0,
    identifier_property: str = "P1307",
) -> str:
    total = sum(len(r.suggestions) for r in results)
    members = sum(r.member_count for r in results)
    lines = [
        "# wd-parliament — suggested Wikidata edits",
        "",
        f"Generated: {generated_at}",
        "",
        f"**{total} suggested edits** across **{members} sitting members** "
        f"in **{len(results)} chamber(s)**.",
        "",
        f"{quickstatements} of them are mechanical enough to be emitted as "
        "QuickStatements (see `../docs/suggestions.qs`).",
        "",
        f"| Chamber | Members | {identifier_property} match | By name | Unmatched "
        "| Suggestions | Report |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in sorted(results, key=lambda x: x.body.council):
        lines.append(
            f"| [{r.body.label}](https://www.wikidata.org/wiki/{r.body.position_qid}) "
            f"| {r.member_count} "
            f"| {r.matched_by_identifier} ({r.identifier_hit_rate:.0f}%) "
            f"| {r.matched_by_name} | {r.unmatched} | {len(r.suggestions)} "
            f"| [details]({body_filename(r.body)}) |"
        )
    lines.append("")

    totals: Dict[str, int] = {}
    for r in results:
        for s in r.suggestions:
            totals[s.kind] = totals.get(s.kind, 0) + 1
    if totals:
        lines += ["## Suggestions by kind", "", "| Kind | Priority | Count |",
                  "| --- | ---: | ---: |"]
        for kind, count in sorted(
            totals.items(), key=lambda kv: (PRIORITY.get(kv[0], 99), kv[0])
        ):
            lines.append(
                f"| {KIND_LABEL.get(kind, kind)} | {PRIORITY.get(kind, 99)} | {count} |"
            )
        lines.append("")
    return "\n".join(lines)


# --- JSON -------------------------------------------------------------------
def _json_date(value) -> Optional[str]:
    return value.isoformat() if isinstance(value, date) else value


def _json_payload(payload: dict) -> dict:
    return {k: _json_date(v) for k, v in payload.items()}


def to_json(
    results: Sequence[BodyResult], generated_at: str, quickstatements: int = 0
) -> dict:
    return {
        "generated": generated_at,
        "total_suggestions": sum(len(r.suggestions) for r in results),
        "total_members": sum(r.member_count for r in results),
        "quickstatements": quickstatements,
        "bodies": [
            {
                "council": r.body.council,
                "label": r.body.label,
                "position_qid": r.body.position_qid,
                "member_count": r.member_count,
                "matched_by_identifier": r.matched_by_identifier,
                "matched_by_name": r.matched_by_name,
                "unmatched": r.unmatched,
                "wikidata_open": r.wikidata_open,
                "identifier_mismatches": r.identifier_mismatches,
                "identifier_hit_rate": round(r.identifier_hit_rate, 1),
                "error": r.error,
                "suggestions": [
                    {
                        "kind": s.kind,
                        "priority": s.priority,
                        "member": s.member_label,
                        "person_qid": s.person_qid,
                        "person_number": s.person_number,
                        "qid_source": s.qid_source,
                        "constituency": s.constituency,
                        "parl_group": s.parl_group,
                        "detail": s.detail,
                        "links": s.links,
                        "payload": _json_payload(s.payload),
                    }
                    for s in r.suggestions
                ],
            }
            for r in results
        ],
    }


# --- HTML dashboard ---------------------------------------------------------
_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wd-parliament — Wikidata TODO</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1b1b1b; --muted:#666;
    --card:#f6f6f6; --border:#e2e2e2; --accent:#3366cc; --warn:#b45309; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14161a; --fg:#e8e8e8; --muted:#9aa0a6; --card:#1e2127;
      --border:#2c2f36; --accent:#7aa2f7; --warn:#fbbf24; }
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:var(--bg); color:var(--fg); line-height:1.5; }
  .wrap { max-width:1000px; margin:0 auto; padding:1.5rem 1rem 4rem; }
  h1 { margin:0 0 .25rem; font-size:1.6rem; }
  .sub { color:var(--muted); margin:0 0 1.5rem; }
  .totals { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1.5rem; }
  .stat { background:var(--card); border:1px solid var(--border); border-radius:10px;
    padding:.75rem 1rem; min-width:130px; }
  .stat b { font-size:1.5rem; display:block; }
  .stat span { color:var(--muted); font-size:.85rem; }
  .body-nav { display:flex; flex-wrap:wrap; gap:.5rem; margin-bottom:1.5rem; }
  .body-nav a { background:var(--card); border:1px solid var(--border);
    border-radius:999px; padding:.3rem .8rem; font-size:.9rem; }
  section.chamber { margin-bottom:2.5rem; }
  section.chamber > h2 { font-size:1.25rem; border-bottom:1px solid var(--border);
    padding-bottom:.3rem; scroll-margin-top:1rem; }
  .group { border:1px solid var(--border); border-radius:10px; margin-bottom:1rem;
    background:var(--card); overflow:hidden; }
  .group > summary { cursor:pointer; padding:.9rem 1rem; font-weight:600;
    display:flex; justify-content:space-between; align-items:center; gap:1rem; }
  .group > summary::-webkit-details-marker { display:none; }
  .count { background:var(--accent); color:#fff; border-radius:999px;
    padding:.1rem .6rem; font-size:.8rem; font-weight:600; }
  .count.zero { background:#3aa657; }
  .inner { padding:0 1rem 1rem; }
  .kind { margin:1rem 0 .3rem; font-size:.9rem; color:var(--muted);
    text-transform:uppercase; letter-spacing:.03em; }
  .prio { display:inline-block; min-width:1.2rem; text-align:center;
    border:1px solid var(--border); border-radius:4px; padding:0 .25rem;
    font-size:.75rem; margin-right:.35rem; }
  ul { margin:.2rem 0 .6rem; padding-left:1.2rem; }
  li { margin:.35rem 0; }
  a { color:var(--accent); text-decoration:none; }
  a:hover { text-decoration:underline; }
  .detail { color:var(--muted); }
  .byname { color:var(--warn); font-weight:600; }
  table { border-collapse:collapse; width:100%; margin:.5rem 0 1.5rem;
    display:block; overflow-x:auto; }
  th, td { border:1px solid var(--border); padding:.35rem .6rem; text-align:left; }
  th { background:var(--card); }
  td.num, th.num { text-align:right; }
  footer { color:var(--muted); font-size:.85rem; margin-top:2rem;
    border-top:1px solid var(--border); padding-top:1rem; }
  code { background:var(--card); padding:.05rem .3rem; border-radius:4px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>wd-parliament &mdash; suggested Wikidata edits</h1>
  <p class="sub">Sitting members of {{ source_name }}
     vs. <em>position held</em> (P39) statements on Wikidata.
     Generated {{ generated_at }}.</p>

  <div class="totals">
    <div class="stat"><b>{{ total }}</b><span>suggested edits</span></div>
    <div class="stat"><b>{{ members }}</b><span>sitting members</span></div>
    <div class="stat"><b>{{ hit_rate }}%</b><span>matched by {{ identifier_property }}</span></div>
    <div class="stat"><b>{{ quickstatements }}</b><span>QuickStatements</span></div>
  </div>

  <table>
    <thead><tr><th>Chamber</th><th class="num">Members</th>
      <th class="num">{{ identifier_property }}</th><th class="num">By name</th>
      <th class="num">Unmatched</th><th class="num">Suggestions</th></tr></thead>
    <tbody>
    {% for r in results %}
      <tr><td><a href="https://www.wikidata.org/wiki/{{ r.body.position_qid }}">{{ r.body.label }}</a></td>
        <td class="num">{{ r.member_count }}</td>
        <td class="num">{{ r.matched_by_identifier }}</td>
        <td class="num">{{ r.matched_by_name }}</td>
        <td class="num">{{ r.unmatched }}</td>
        <td class="num">{{ r.suggestions|length }}</td></tr>
    {% endfor %}
    </tbody>
  </table>

  <nav class="body-nav">
    {% for r in results %}
    <a href="#{{ r.slug }}">{{ r.body.label }} ({{ r.suggestions|length }})</a>
    {% endfor %}
  </nav>

  {% for r in results %}
  <section class="chamber">
    <h2 id="{{ r.slug }}">{{ r.body.label }}</h2>
    {% if r.error %}<p class="byname">Error: {{ r.error }}</p>{% endif %}
    {% if r.identifier_mismatches %}<p class="byname">⚠️ {{ r.identifier_mismatches }}
      {{ identifier_property }} match(es) rejected: the item they point at names
      a different person. The property is joined on unverified &mdash; a large
      share here means its values are not this source&rsquo;s person ids.</p>{% endif %}
    {% if not r.suggestions %}
      <p>✅ Nothing to do &mdash; {{ source_name }} and Wikidata agree.</p>
    {% else %}
      {% for name, items in r.groups %}
      <details class="group" {% if loop.first %}open{% endif %}>
        <summary>
          <span>{{ group_label }}: {{ name }}</span>
          <span class="count">{{ items|length }} to&nbsp;do</span>
        </summary>
        <div class="inner">
          {% for kind, kind_items in items|by_kind %}
          <div class="kind"><span class="prio">{{ priority[kind] }}</span>
            {{ kind_label[kind] }} ({{ kind_items|length }})</div>
          <ul>
            {% for s in kind_items %}
            <li>
              {% if s.person_qid %}<a href="https://www.wikidata.org/wiki/{{ s.person_qid }}">{{ s.member_label }}</a>
              {% else %}<strong>{{ s.member_label }}</strong>{% endif %}
              {% if s.payload.biography %}(<a href="{{ s.payload.biography }}">#{{ s.person_number }}</a>){% endif %}
              {% if s.qid_source == 'name' %}<span class="byname" title="matched by name, not by the identifier">&#9888;</span>{% endif %}
              <span class="detail">&mdash; {{ s.detail }}</span>
              {% if s.links.property %}
              <span class="detail">&mdash;
                <a href="{{ s.links.property }}">{{ s.payload.property }}</a>
              </span>
              {% endif %}
              {% if s.payload.duplicate_qids %}
              <span class="detail">&mdash; items:
                {% for q in s.payload.duplicate_qids %}<a href="https://www.wikidata.org/wiki/{{ q }}">{{ q }}</a>{% if not loop.last %}, {% endif %}{% endfor %}
              </span>
              {% endif %}
            </li>
            {% endfor %}
          </ul>
          {% endfor %}
        </div>
      </details>
      {% endfor %}
    {% endif %}
  </section>
  {% endfor %}

  <footer>
    Built by <a href="https://github.com/metaodi/wd-parliament">wd-parliament</a>
    from the official <a href="https://ws.parlament.ch/odata.svc/">parlament.ch
    OData service</a>. Members marked &#9888; were matched by name rather than by
    {{ identifier_property }} &mdash; check the identity before editing.
  </footer>
</div>
</body>
</html>
"""


def render_html(
    results: Sequence[BodyResult],
    generated_at: str,
    group_by: str = "constituency",
    quickstatements: int = 0,
    source_name: str = "parlament.ch",
    identifier_property: str = "P1307",
    district_label: str = "Canton",
) -> str:
    env = Environment(autoescape=True)
    env.filters["by_kind"] = by_kind
    template = env.from_string(_HTML_TEMPLATE)

    ordered = sorted(results, key=lambda r: r.body.council)
    views = []
    for r in ordered:
        views.append(
            _HtmlBody(
                body=r.body,
                suggestions=r.suggestions,
                member_count=r.member_count,
                matched_by_identifier=r.matched_by_identifier,
                matched_by_name=r.matched_by_name,
                unmatched=r.unmatched,
                identifier_mismatches=r.identifier_mismatches,
                error=r.error,
                slug=_slug(r.body.label),
                groups=group_suggestions(r.suggestions, group_by),
            )
        )

    members = sum(r.member_count for r in results)
    matched = sum(r.matched_by_identifier for r in results)
    return template.render(
        results=views,
        generated_at=generated_at,
        total=sum(len(r.suggestions) for r in results),
        members=members,
        quickstatements=quickstatements,
        hit_rate=round(matched / members * 100, 1) if members else 0.0,
        kind_label=KIND_LABEL,
        priority=PRIORITY,
        group_label=district_label if group_by == "constituency" else "Group",
        source_name=source_name,
        identifier_property=identifier_property,
    )


@dataclass
class _HtmlBody:
    """A :class:`BodyResult` pre-grouped for the template."""

    body: Body
    suggestions: List[Suggestion]
    member_count: int
    matched_by_identifier: int
    matched_by_name: int
    unmatched: int
    identifier_mismatches: int
    error: Optional[str]
    slug: str
    groups: List[Tuple[str, List[Suggestion]]]


# --- Orchestration ----------------------------------------------------------
def write_reports(
    results: Sequence[BodyResult],
    reports_dir: str | Path,
    docs_dir: str | Path,
    generated_at: Optional[str] = None,
    group_by: str = "constituency",
    quickstatements_text: Optional[str] = None,
    quickstatements_count: int = 0,
    source_name: str = "parlament.ch",
    identifier_property: str = "P1307",
    district_label: str = "Canton",
) -> None:
    generated_at = generated_at or now_iso()
    reports_dir = Path(reports_dir)
    docs_dir = Path(docs_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    (reports_dir / "README.md").write_text(
        render_index_markdown(
            results, generated_at, quickstatements_count, identifier_property
        ),
        encoding="utf-8",
    )
    for r in results:
        (reports_dir / body_filename(r.body)).write_text(
            render_body_markdown(
                r, generated_at, group_by, source_name, identifier_property,
                district_label,
            ),
            encoding="utf-8",
        )

    (docs_dir / "index.html").write_text(
        render_html(
            results, generated_at, group_by, quickstatements_count,
            source_name, identifier_property, district_label,
        ),
        encoding="utf-8",
    )
    (docs_dir / "data.json").write_text(
        json.dumps(
            to_json(results, generated_at, quickstatements_count),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if quickstatements_text is not None:
        (docs_dir / "suggestions.qs").write_text(quickstatements_text, encoding="utf-8")
