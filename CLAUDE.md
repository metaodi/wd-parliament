# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## What this project does

`wd-parliament` compares the sitting members of the Swiss Federal Assembly —
read from the official parlament.ch OData service via `swissparlpy` — against
*position held* (P39) statements on **Wikidata**, and generates a prioritised
TODO list of suggested Wikidata edits plus a QuickStatements file. It runs
unattended via GitHub Actions on a weekly schedule and commits refreshed
reports plus an HTML dashboard.

It is a sibling of [`wd-squads`](https://github.com/metaodi/wd-squads) and
reuses its structure, but differs in three ways that matter for any change you
make here:

1. **The source is a typed API, not scraped wikitext.** There is no parsing
   layer, and there should never be one.
2. **People are joined on an identifier**, not a name: Wikidata's P1307 "Swiss
   parliament ID" against `MemberCouncil.PersonNumber`. Name matching is a
   fallback, and it requires the birth date to agree.
3. **The source is authoritative**, so the tool can assert that a statement
   *disagrees* (`FIX_START_DATE`, `REVIEW_ENDED`, `REVIEW_PARTY`), not merely
   that one is missing. That is also what justifies emitting QuickStatements.

## ⚠️ Unresolved at scaffold time — read before touching QuickStatements

The project has **never been run end to end against live data**; the
environment it was built in could reach neither `ws.parlament.ch` nor
`query.wikidata.org`. See **Open verification steps** in the README.

- **`statement_model` is settled: `tenure`.** Censused against live Wikidata
  (2026-07-29): of 3,043 items with both P1307 and a National Council P39,
  97.2% have exactly one statement for the seat; 156 have one statement with
  ≥2 P2937 terms (tenure) against 6 with one statement per term (period). This
  **contradicts** WikiProject "every politician"'s documented per-term
  convention — the data wins, since duplicates are what the tool must avoid.
  Do not flip it back without re-running the census query in the README.
- **P1307 == `PersonNumber`** is still unverified directly. Strongly supported
  (Q121160 / P1307 = 1108 matches Parmelin's biography URL; the property's URL
  pattern is built from `PersonNumber`; the census found 3,043 National
  Councillors carrying it) but nobody has read `PersonNumber` off an actual
  `MemberCouncil` row, and none of that distinguishes it from `PersonIdCode`.

Two facts from the same census shape the diff's behaviour:

- **89.4% of items carry no P2937 at all**, so populating `terms:` in the
  config turns `ADD_TERM` into a bulk backfill across most of the chamber.
- **2.8% of members hold several P39 statements for one seat** (left and
  returned). `diff` stamps `payload["ambiguous_statement"]` on those, and
  `quickstatements.is_mechanical` refuses qualifier-only commands for them,
  because QuickStatements matches on property + main value alone.

Do not remove the warnings in the README, `config/parliament.yaml` or
`tests/fixtures/README.md` until the corresponding step has actually been
carried out.

Likewise, `tests/fixtures/*.json` are **hand-built to the OData schema**, not
captured. The field names and types are exact (read from the `$metadata`
document inside `swissparlpy` 1.0.0); the people are invented.

## Commands

```bash
# Tests (offline; no network needed) — this is exactly what CI runs
uv run --extra dev pytest -q

# A single file / test
uv run --extra dev pytest tests/test_period_overlap.py
uv run --extra dev pytest tests/test_diff.py::test_missing_electoral_district -q

# Full pipeline run (hits ws.parlament.ch and query.wikidata.org)
uv run python -m wd_parliament --config config/parliament.yaml

# Fast iteration
uv run python -m wd_parliament --config config/parliament.yaml --limit 20 --verbose

# Check every Q-ID in the config against Wikidata
uv run python -m wd_parliament --verify-config

# Cross-check the period join against roll-call attendance (one IdVote per period)
uv run python -m wd_parliament --validate-periods 12345 23456
```

There is **no linter or formatter configured** — do not invent one.

## Architecture

A straight line, wired in `app.py::run` → `app.py::process`:

```
config.load_config
  → parliament.get_periods()          # LegislativePeriod (~52 rows), one request
  → parliament.get_members()          # MemberCouncil, Active=True, ~246 rows
  → wikidata.get_position_holders()   # 3 SPARQL queries, merged into one map
  → resolve.resolve_members()         # P1307 join, then the name fallback
  → for each chamber: diff.compute_suggestions()
  → quickstatements.render_file() + report.write_reports()
```

Members and the Wikidata view are fetched **once for all chambers**, not per
chamber: a member can move between councils, and the Wikidata query is the
expensive one.

### Module responsibilities (`src/wd_parliament/`)

- **`models.py`** — shared dataclasses plus the **suggestion taxonomy**:
  `KIND_*` constants, `PRIORITY` sort weights (lower = more urgent) and
  `KIND_LABEL` human strings. Adding a kind means touching all three maps plus
  `diff.py`. `QID_FROM_IDENTIFIER` / `QID_FROM_NAME` record how a member's Q-ID
  was established, and that provenance is what gates QuickStatements.
- **`config.py`** — loads/validates `config/parliament.yaml`. Enforces a real
  `user_agent` (rejects placeholders) and validates every Q-ID map. A key
  mapped to a blank value is a deliberate "not known yet" and is dropped, not
  an error.
- **`http_client.py`** — the single shared HTTP layer: required User-Agent,
  `request_delay` throttle, 429/5xx exponential backoff. Its `requests.Session`
  is also handed to `SwissParlClient`, so parlament.ch sees the same UA.
- **`parliament.py`** — the **only** `swissparlpy` caller. The row-mapping
  functions (`member_from_row`, `members_from_rows`, `period_from_row`,
  `periods_from_rows`) are module-level and pure, and that is how
  `get_members` is tested — by feeding fixture rows, never by mocking OData.
  The client is constructed lazily because building it fetches the metadata
  document over the network.
- **`period_overlap.py`** — pure interval arithmetic, and **the most
  consequential logic in the tool**: it decides a P2937 qualifier on every
  statement emitted. Both intervals are **closed**. A member with no
  `DateJoining` gets **no** periods — the fail-safe that stops an unknown start
  from silently meaning "all ~52 periods". Tested exhaustively.
- **`wikidata.py`** — SPARQL against WDQS. **Three bounded queries**, not one
  wide one: P768/P4100/P2937/P102 are all repeatable, so a single SELECT would
  produce a cartesian product per statement. They are joined on the Q-ID in
  Python. The P39 query deliberately fetches statements for **everyone** with
  those positions, not just P1307 holders, because the diff's second pass needs
  people who are *not* in the current-members set. Query builders are static
  methods so they are unit-testable.
- **`resolve.py`** — the P1307 join first (exact). An identifier claimed by two
  items is **skipped and logged**, not arbitrated. The name fallback rejects any
  candidate whose *known* birth date contradicts the member's, and never hands
  one Q-ID to two members.
- **`diff.py`** — pure. `expected_statements` is the **single place** the
  `tenure` vs `period` statement model lives; the rest of the diff works off
  whatever it returns. Walks members → Wikidata, then Wikidata's open
  memberships → members (catching people Wikidata still lists as sitting; those
  carry no leaving date, so they are report-only). Sorted by priority then name.
- **`quickstatements.py`** — pure renderer. `is_mechanical` is the **one place**
  the safety rule lives; keep it that way. Review/correction kinds are excluded
  because QuickStatements can only add, so applying them would create a second
  contradictory value.
- **`report.py`** — per-chamber Markdown, an index, `docs/data.json` and the
  inline Jinja2 `_HTML_TEMPLATE` dashboard. Grouped by canton or parliamentary
  group (wd-squads' `_group_by_league` equivalent); `BodyResult` is its
  `TeamResult`. Surfaces the **P1307 hit rate**, the run's key health number.
- **`app.py`** / **`__main__.py`** — orchestration and CLI. `process` catches
  per-chamber exceptions and records them on `BodyResult.error` so one broken
  chamber never aborts the run.

## Conventions

- **Pure vs. network code is kept separate on purpose.** The pure functions are
  the only things the tests exercise. Keep new logic pure and testable the same
  way; do not reach for HTTP mocking.
- Tests import `wd_parliament` without installing it: `tests/conftest.py`
  prepends `src/` to `sys.path` and exposes `FIXTURES` plus row fixtures.
- `from __future__ import annotations` throughout; dataclasses with `Optional`
  fields are the norm. Dates are `datetime.date`, converted at the
  `parliament.py` boundary.
- **Skip unknown values rather than guessing at them.** An unmapped canton,
  party, group or term produces no suggestion — that rule is load-bearing given
  the config ships partly unverified.

## Generated files

`reports/*.md`, `docs/index.html`, `docs/data.json` and `docs/suggestions.qs`
are **build artifacts** produced by a run and by the `Update parliament TODO`
Action. Do not hand-edit them; regenerate by running the tool. They are
committed so the Markdown diffs are reviewable in git history and Pages can
serve `docs/`. The versions currently committed are placeholders stating that
no run has happened yet.

## GitHub Actions

- `tests.yml` — `uv run --extra dev pytest -q` on every push/PR.
- `verify.yml` — `workflow_dispatch` only, `contents: read`. Runs
  `scripts/verify_p1307.py` and `--verify-config`, writes both to the run
  summary, and writes nothing to the repo. Keep it read-only: it is the
  diagnostic you run *before* trusting `update.yml`'s output. Note
  `workflow_dispatch` requires the file to be on the default branch.
- `update.yml` — weekly (Mon 06:00 UTC) + manual; runs the pipeline and commits
  `reports/` and `docs/` back (`contents: write`).
- `pages.yml` — deploys `docs/` to Pages, chained off `update.yml`'s completion
  (a `GITHUB_TOKEN` push does not itself fire a `push` event).
