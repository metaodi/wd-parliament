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

## ⚠️ What the live service actually returns — read before touching QuickStatements

See **Open verification steps** in the README. The `Verify assumptions`
workflow ran against live parlament.ch on 2026-07-29 and settled most of these.

Two facts about the source, both learned the hard way, both now enforced in
`parliament.py`. **Do not "simplify" either away:**

- **`CouncilAbbreviation` is `NR` / `SR`** (German, because the service is
  queried with `Language=DE`; the distinct values are `''`, `BR`, `NR`, `SR`,
  and French rows say `CN`). The config asked for `N` / `S` — from the OData
  docs, matching nothing — which is why the first live run fetched **zero**
  sitting members and published 2,234 wrong "this member has left" suggestions.
  Nothing reached QuickStatements; `is_mechanical` rejected all of them, the
  safety rule earning its keep.
- **"No date" is `1753-01-01`, not a null** — SQL Server's `datetime` minimum,
  on every sitting member's `DateLeaving`. `parliament.NULL_DATE` maps it (and
  anything below) to `None` at the mapping boundary. Left unmapped it reads as
  "left in 1753", which makes `diff` raise `ADD_END_DATE` for the whole
  chamber — and that kind **is** mechanical, so it would have reached
  QuickStatements as a P582 backfill. It also reverses every tenure interval,
  costing every P2937 qualifier silently. The P1307 provenance rule does not
  catch this class of error; only the mapping does.

- **`statement_model` is settled: `tenure`.** Censused against live Wikidata
  (2026-07-29): of 3,043 items with both P1307 and a National Council P39,
  97.2% have exactly one statement for the seat; 156 have one statement with
  ≥2 P2937 terms (tenure) against 6 with one statement per term (period). This
  **contradicts** WikiProject "every politician"'s documented per-term
  convention — the data wins, since duplicates are what the tool must avoid.
  Do not flip it back without re-running the census query in the README.
- **P1307 == `PersonNumber`: confirmed.** Parmelin's row reads
  `PersonNumber=1108, PersonIdCode=2621` against Wikidata's P1307 = 1108, so it
  is `PersonNumber` and not `PersonIdCode`. `resolve.match_by_identifier` is
  comparing the right fields.

**`DateJoining` is a mandate-*segment* start, not a tenure start — so P580 must
not be emitted from it, and `ADD_MEMBERSHIP` / `ADD_START_DATE` must not be
bulk-applied.** Measured (README step 0c): of 244 sitting members joined against
OpenParlData, 233 agree and 11 have `DateJoining` *later* than the legislature
opening. Bregy is the proof — `MemberCouncil` says 2025-09-16 while his
`MemberCouncilHistory` carries an active row from 2023-12-04. A2 corroborates:
200 National Councillors share only 16 distinct `DateJoining` values.

**Fixed** without changing source: `segments_from_rows` groups
`MemberCouncilHistory` into mandate segments (it must *not* de-duplicate on
`(person, council)` the way `members_from_rows` does — that is what loses the
tenure), `tenure_start` chains segments that are adjacent to within a day, and
`Member.start_date` (`tenure_start or date_joining`) is the **only** start
`diff` and `period_overlap` may read. `app.process` fetches the history once and
degrades to the raw field rather than aborting if it cannot.

A real break stops the chain, so someone who left and returned gets the return.

Re-run `scripts/compare_tenure_dates.py` after touching this. It prints **two**
verdicts off one join and they are not interchangeable: comparison 1 judges the
raw `DateJoining` against OpenParlData's *latest term* (step 0c as posed, kept
as the regression check for the finding), comparison 2 judges what the pipeline
actually emits — `Member.start_date` against `chained_start`, the same
adjacent-segment chaining applied to OpenParlData's per-term rows. **Comparison
2 is the one that says a bulk apply of P580 is safe.** Do not "simplify" it to
reuse `current_start`: comparing a chained tenure against a single term reports
every long-serving member as a disagreement.

Run 12 (2026-07-30) returned `CONFIRMED`, **244 of 244**: the chained tenure
start equals OpenParlData's independent per-term start for every sitting
member, so P580 may be applied in bulk. Step 4 came back clean in the same run
— 183 of 183 voting members assigned in the current legislature.

**A person is not a seat, and both new checks got that wrong first.** Run 11
had comparison 2 keying OpenParlData rows by Q-ID alone, so a member who moved
NR→SR chained their National Council years onto their Council of States seat —
all 22 "disagreements" were that. Step 4 scored the same 25 people as interval
failures. The rules that follow from it, and that must not be undone:
`seats_by_seat` is keyed by `(qid, council)`, and `validate_periods` classifies
a voter whose period ended before `start_date` as an earlier mandate. Anything
joining these two sources on a person alone reads a chamber change as a
contradiction.

**OpenParlData is a live option, not a dead end** (README step 6). Its chambers
are *groups* (`Nationalrat` 1663, `Ständerat` 1664), matched by name equality
because `Präsidium des Nationalrates` and `Büro NR` are not the chamber. The
seat is a `memberships` row pointing at one, and **all 5,618 carry a
`begin_date`** — real per-term spans back to 1853, with 200 open-ended NR and
46 open-ended SR rows, both chambers' sizes exactly. So it *can* source P39 including P2937,
and it reaches far enough back for the historic-members extension. Whether to
switch turns on comparing its dates against `MemberCouncil.DateJoining` —
`scripts/compare_tenure_dates.py` does that, and the same comparison answers
step 0c.

Three things about it that cost a wrong answer each, and are now guarded:

- the columns are **`begin_date` / `end_date`**, not `date_start` / `date_end`
  (that is `speeches`). `classify_seat_memberships` resolves the column from
  the rows and returns INCONCLUSIVE — never CONTRADICTED — when it is absent,
  because "no such column" and "column full of nulls" are indistinguishable
  through `.get()` and mean opposite things;
- the seat is reachable from the **group**, not the person: walking a member
  returns committees and interest groups but not their own council seat;
- **pass `lang='de'`**: swissparlpy **1.0.0** hard-coded `lang='en'` with
  `lang_format='flat'`, and the English columns are null, so a table could read
  as *empty* — `bodies` gave 0 rows by default and 1,405 with `lang='de'`.
  **2.0.0 fixed that** (issue #52, filed off these runs) and sends no `lang`
  unless asked, but keep passing it: `CHAMBER_NAMES` matches German spellings,
  and a pinned language is what makes the probes reproducible. 2.0.0 also
  stopped forcing `search_scope='all'`, so the API's defaults (`metadata`,
  `partial`) now apply — pass `search_scope='all'` for the full-text indexes.
  Narrow with field filters (`body_key=CHE`, `group_id=`, `lastname=`); the
  `search` parameter works too, but its `exact` mode is case-sensitive in
  practice ('Nationalrat' → 1 row, 'nationalrat' → 0) and `CHAMBER_NAMES`
  holds lowercase spellings. `limit` is a page size, not a cap — the response
  iterator pages to exhaustion; `len()` is `meta.total_records`, and slicing
  loads only as far as the slice reaches;
- P14527 adds nobody — 0 National Councillors carry it without P1307 — so the
  P1307 join stays whatever happens to the source. **That is a fact about the
  *federal* overlap and it inverts cantonally** (README step 7): the Kantonsrat
  Zürich has no P1307, so P14527 may be the only Wikidata-asserted identifier
  that reaches those people. `scripts/verify_kantonsrat.py` measures it.

Runs 13 and 14 (2026-07-30) measured the cantonal source and it is sound: body
`ZH`, group **5077** `Kantonsrat Zürich`, 913 memberships, 912 with a
`begin_date`, `electoral_district_de/fr/it` on the *person* records. **The
position is `Q21518678`** "Mitglied des Zürcher Kantonsrat" and **P14527 is the
join** — 35 of the 35 linked ZH members carry it, so it needs no change to
`is_mechanical`. Three traps paid for along the way, all now enforced in
`verify_kantonsrat.py`:

- **`Q19479543` is `Kategorie:Kantonsrat (Zürich, Person)`** — a Wikimedia
  category, held by nobody. It shipped as the probe's default off a web search
  and as a P39 main value would have claimed people hold a category. **Never
  reinstate it.** A wrong position item is also silently contagious: it made
  every identifier count in section C read 0, which looks like "no coverage"
  and meant "no such seat". That is why the reach query mentions no position at
  all, and why section D *derives* candidates from the members OpenParlData
  links instead of trusting a name.
- **the derived ranking's top hit is not the answer.** Among those 35, the
  most-held position is the *National Council* (26 of 35) — the sample is
  cantonal members notable enough to have an item, which skews to people who
  went federal. Q21518678 is second at 12. Read the list; do not take the max.
- **counting open membership rows is not counting members.** The rows carry
  `role_name_de`, and 186 open against 180 seats was four future starts, a
  `Gast`, and one row with no role. Filtering to `Mitglied` then gave **177**,
  because a presiding member has **no `Mitglied` row** — their `Präsidium` /
  `1.` / `2. Vizepräsidium` row *is* their seat. 177 + 3 = 180. So
  `DEFAULT_SEAT_ROLES` is an allowlist of seat-holding roles; count **distinct
  people with an open, already-begun, seat-role membership** and print every
  role seen, because neither an allowlist nor a denylist self-corrects when the
  source adds a role — only the 180 check does.

Run 15 closed the two Q-ID maps, and both answers are "leave it empty":
**P768** appears on 3 of 270 statements for the seat and those three are
`Kreis 4/5/11`, *city of Zürich quarters* rather than any of the 18 Wahlkreise
— too few to be a convention and the wrong kind of thing, so never paste them.
**P2937** appears on none at all. The source side is fine either way: all 18
districts are on the person records as `electoral_district_de` (numbered and
untidily spaced — `'I      Zürich 1+2'` — so `_tidy` before keying), and the
start dates cluster on the four-yearly election dates. Note ZH memberships are
**one row per tenure**, not per term as federally (913 rows / 834 people, ~40–50
new rows per election), so a ZH body is `statement_model: tenure` and its P2937
must come from interval overlap.

**The cantonal adapter now exists**: `openparldata.py` beside `parliament.py`,
selected by `config.source` in `app.build_source`, joined on
`config.identifier_property` (P14527 for ZH, P1307 federally) and configured by
`config/kantonsrat-zh.yaml`. Four things about it that are load-bearing:

- **`Member.active` is computed, not read.** The source has no usable flag, and
  `is_seat_row` — open, already begun, in a seat role — is the entire
  difference between 186 rows and 180 members.
- **`get_periods` returns `[]` and `get_member_segments` returns `{}`, both on
  purpose.** No period table exists (so no P2937 is ever suggested), and the
  rows are per-tenure so `begin_date` is already the date P580 wants. Do not
  "fix" either by inventing data.
- **user-facing strings are parameters.** `report` and `diff` take
  `source_name` / `identifier_property` / `district_label`; a cantonal report
  saying "parlament.ch" or "P1307" sends a reader to a service that has never
  heard of these members.
- **it ships `quickstatements: false`.** P14527 coverage is proven; P14527's
  *value* being the person id is not. `verify_kantonsrat`'s
  `compare_identifier_values` checks it — the cantonal twin of the Parmelin
  check — and that must read CONFIRMED before anything is emitted.

Three more rules follow from step 7 and hold before any cantonal code is written.
**Never join a cantonal seat on P1307** — it is the federal service, so it
reaches only members who also sat in Bern, and the people it misses are exactly
those who never went federal: a bias that reads as coverage rather than as a
bug. **Never let OpenParlData's `wikidata_id` inherit the P1307 gate** — a Q-ID
a third party asserts *about* Wikidata is a different class of claim and needs
its own `QID_FROM_*` constant. And **the Regierungsrat is not the Kantonsrat**:
`is_kantonsrat` matches a group name by equality for the same reason
`chamber_of` does, except the row it must not match is a seven-member cantonal
*executive* rather than a committee.

For enrichment it is unambiguously good: 3,685/3,686 federal members carry a
`wikidata_id` and 87.3% a party Q-ID, which would fill the deliberately-empty
`parties` / `parl_groups` maps.

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

# Cross-check the period join against roll-call attendance. With no IdVote (or
# 'auto') the votes are discovered from the Vote table for the most recent
# periods; naming them explicitly takes exactly those.
uv run python -m wd_parliament --validate-periods
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
  chamber never aborts the run. `validate_periods` (README step 4) compares the
  overlap against roll-call attendance and has **two rules that make its answer
  mean anything**: it applies the same `apply_tenure_starts` correction
  `process` does, because the overlap reads `Member.start_date`; and it counts
  only voters who are **still sitting** as comparable, because the member list
  is today's ~246 people and every roll-call also contains people who have left
  — scoring those as "not assigned" is a false mismatch that grows with the age
  of the vote.

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
  `scripts/verify_source.py`, `--verify-config`,
  `scripts/verify_openparldata.py`, `scripts/compare_tenure_dates.py`,
  `--validate-periods` and `scripts/verify_kantonsrat.py`, writes all six to
  the run summary, and writes nothing to the repo. Keep it read-only: it is
  the diagnostic you run *before* trusting `update.yml`'s output. **Only the
  first two gate**; the other four report without gating and are deliberately
  excluded from the job's pass/fail — do not wire their outcomes into the
  gate. The gate says whether the pipeline may run; `compare_tenure_dates` and
  `--validate-periods` answer whether a *bulk apply* is safe, and
  `verify_kantonsrat` measures a parliament no config here processes, so it
  cannot bear on the federal run by construction. The file must be on the
  default branch to appear in the dispatch UI, though a dispatch then runs the
  selected ref's version.
- `update.yml` — weekly (Mon 06:00 UTC) + manual; runs the pipeline and commits
  `reports/` and `docs/` back (`contents: write`).
- `pages.yml` — deploys `docs/` to Pages, chained off `update.yml`'s completion
  (a `GITHUB_TOKEN` push does not itself fire a `push` event).
