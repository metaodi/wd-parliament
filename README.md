# wd-parliament

**A TODO list for Wikidata users, generated from the official Swiss parliament API.**

The members of the Swiss Federal Assembly are published by parlament.ch with
exact joining and leaving dates and a live `Active` flag. The matching
*position held* ([P39](https://www.wikidata.org/wiki/Property:P39)) statements
on **Wikidata** often lag behind: a member is elected and no statement appears,
a member leaves and the statement stays open, or the statement carries no
**start date** ([P580](https://www.wikidata.org/wiki/Property:P580)) at all —
which makes "who sits in the National Council *right now*" impossible to query.

`wd-parliament` compares the two and produces a prioritised **list of suggested
Wikidata edits**, plus a [QuickStatements](https://quickstatements.toolforge.org/)
file for the subset that can be applied mechanically.

It is a sibling of [`wd-squads`](https://github.com/metaodi/wd-squads), which
does the same job for football squads, and reuses its structure. But the
problem is meaningfully different in three ways:

|  | wd-squads | wd-parliament |
| --- | --- | --- |
| **Source** | scrapes Wikipedia wikitext | calls a typed OData API — no parsing layer at all |
| **Matching** | by name; refuses to guess between namesakes | joins on an **identifier** (P1307 ↔ `PersonNumber`); name matching is a fallback |
| **Authority** | explicitly heuristic | authoritative — so it can report that a statement *disagrees*, not just that one is missing |

That last difference is what justifies emitting QuickStatements at all, and it
enables a class of check wd-squads cannot make.

---

## ⚠️ Open verification steps

The **`Verify assumptions`** workflow was dispatched against live parlament.ch
on 2026-07-29 ([run 30477629765](https://github.com/metaodi/wd-parliament/actions/runs/30477629765)).
It settled steps 0, 1 and 3, and turned up one failure mode nobody had
predicted — step 0b, which is now the one that would do damage. A second
dispatch ([run 30481671076](https://github.com/metaodi/wd-parliament/actions/runs/30481671076))
confirmed the source read is fixed and produced the first measurements for
step 6. Steps 4 and 5 remain untouched, and 0c is the one to answer before any
bulk apply. Work through what is left before applying any QuickStatements.

### 0. ✅ The source read — *fixed: the council filter*

The first live run (2026-07-29, commit `897b2c2`) fetched **zero sitting
members** from parlament.ch, and did so silently: no exception, no error
recorded. With the member list empty, every Wikidata seat holder fell through
the diff's second pass and was reported as having left, so the run published
**2,234 confident and wrong** "this member has left" suggestions across the two
chambers.

Nothing reached QuickStatements — those suggestions carry no `qid_source` and
no leaving date, so `is_mechanical` rejected all of them, and
`suggestions.qs` correctly reads `0 of 2234 suggestions are mechanical`. The
safety rule did its job. The report did not.

The probe attributed it exactly:

```
rows returned by OData:            254
rows with Active true:             254
mapped to Member (any council):    254
after the council filter ['N', 'S']:  0

distinct CouncilAbbreviation values:  '', 'BR', 'NR', 'SR'
```

So the `Active` boolean was never the problem — the read works, and the config
was asking for a chamber code the service does not use. `config/parliament.yaml`
now filters on **`NR` / `SR`**, the German abbreviations, which is consistent
with the pipeline querying `Language=DE`. (`BR` is the Federal Council and the
blank is a row with no chamber; neither is in scope.)

Two changes made after the failed run stop it recurring silently:

- `app.process` raises when the member fetch comes back empty, so the Action
  fails before committing anything;
- `diff.compute_suggestions` skips the reverse walk entirely when there are no
  members, because "parlament.ch does not list this person" is not a claim you
  can make when parlament.ch has told you nothing.

The stale artifacts from that run are still committed on `main` and should be
regenerated once a full run succeeds.

### 0b. ✅ Null dates arrive as 1753-01-01 — *fixed, and it was the dangerous one*

The same probe output showed every sitting member carrying

```
DateJoining=2026-01-01, DateLeaving=1753-01-01
```

`1753-01-01` is SQL Server's `datetime` minimum. The OData service is backed by
one and sends it for "no date" instead of a null. Read literally it says the
member left in 1753, and that is **not** a harmlessly wrong number:

- `diff` raises `ADD_END_DATE` for every sitting member whose Wikidata P39 is
  open — which is the normal case — because the expected end is no longer
  `None`;
- `ADD_END_DATE` **is** in `MECHANICAL_KINDS`, it is not statement-ambiguous
  for the 97.2% of members with a single P39 for the seat, and P1307-matched
  members carry the right provenance. So it passes every gate in
  `is_mechanical` and renders as `P582|+1753-01-01T00:00:00Z/11`.

The P1307 rule that contained the step-0 failure does **not** contain this one:
these suggestions are exactly the kind it is designed to let through. It would
have been the first run to write wrong data rather than merely report it.

Second effect, silent rather than destructive: a tenure running from 2026 to
1753 is a reversed interval, which `period_overlap.intervals_overlap` correctly
refuses — so every sitting member would be assigned **no legislative periods**
and lose their P2937 qualifiers.

`parliament.NULL_DATE` now maps the sentinel (and anything at or below it) to
`None` at the mapping boundary, so nothing downstream ever sees it. The
fixtures were rewritten to carry the sentinel the way the service does, since
their previous `"DateLeaving": null` is the fiction that hid this.

### 0c. ⚠️ Still open: is `DateJoining` a tenure start or a year segment?

Parmelin's sitting row gives `DateJoining = 2026-01-01` — the current year, not
his 2016 start. His `MemberCouncilHistory` rows tell the same story: `BR`
segments broken at 2016–2018, 2019, 2020, 2021, 2022–2024, 2025. If sitting
`NR` / `SR` rows are segmented the same way, then `DateJoining` is the start of
a reporting period and **not** when the member took office — and
`ADD_MEMBERSHIP` and `ADD_START_DATE`, both mechanical, would write it to
Wikidata as P580.

His National Council history rows *do* carry real term dates (2003-12-01,
2007-12-03, 2011-12-05, 2015-11-30), so this may well be specific to Federal
Councillors. It is not yet known either way, because the probe only fetched
Parmelin, who now sits in neither chamber.

`scripts/verify_source.py` grew a section **A2** that answers it: it prints the
`DateJoining` spread per chamber and warns when every start is a 1 January.
Dispatch `Verify assumptions` again and read that section before letting any
`ADD_MEMBERSHIP` or `ADD_START_DATE` reach QuickStatements.

### 1. ✅ The P1307 assumption — *CONFIRMED*

The join strategy assumes Wikidata's
[P1307](https://www.wikidata.org/wiki/Property:P1307) "Swiss parliament ID"
holds `MemberCouncil.PersonNumber`. Read off the live service:

```
PersonNumber=1108, PersonIdCode=2621, FirstName='Guy', LastName='Parmelin'
```

against Wikidata's P1307 = 1108 for [Q121160](https://www.wikidata.org/wiki/Q121160).
It is `PersonNumber`, and the `PersonIdCode` alternative the scaffold could not
rule out is ruled out. `resolve.match_by_identifier` is comparing the right
fields; nothing to change.

Two incidental findings from the same probe: a former member appears in
**both** `MemberCouncil` and `MemberCouncilHistory`, and the rows come back in
several languages at once (`NR`/`CN` for the National Council, `BR`/`CF` for
the Federal Council) — which is why `get_members` pushes `Language=DE` down and
why the chamber codes in the config are the German ones.

The probe stays in the repo as a regression check. It reports one of three
verdicts, and only `CONFIRMED` exits 0:

| Verdict | Meaning |
| --- | --- |
| `CONFIRMED` | `PersonNumber == P1307`. The join is sound; nothing to change. |
| `CONTRADICTED` | Either `PersonIdCode` matches instead (the message says so, and which code to switch), or neither does. |
| `INCONCLUSIVE` | The person was not found, or the service could not be read at all. The two are reported differently — an unreachable service is a connectivity problem, not a finding. |

```bash
uv run python scripts/verify_source.py
```

### 2. Statement model — ✅ **settled: `tenure`**

Does Wikidata model these as **one P39 per tenure** (P580/P582 spanning it,
P2937 repeated per period) or **one P39 per legislative period** (each with its
own P2937 and dates)? Getting this backwards means emitting hundreds of
duplicate statements — the worst failure available to this tool.

Censused against live Wikidata on 2026-07-29. Of the **3,043** items carrying
both P1307 and a National Council P39:

| | |
| --- | ---: |
| exactly one P39 statement for the seat | **2,959 (97.2%)** |
| one statement carrying ≥2 P2937 terms → **tenure** | **156** |
| one statement per term (`statements == terms` > 1) → *period* | 6 |
| two or more statements for the seat | 84 (2.8%) |
| carrying **no** P2937 at all | 2,719 (89.4%) |

A per-period model would give every multi-term member several statements, and
almost nobody has them. `config/parliament.yaml` therefore ships
`statement_model: tenure`.

Note this contradicts what
[WikiProject every politician](https://www.wikidata.org/wiki/Wikidata:WikiProject_every_politician)'s
documentation implies (per-term data pages, and a
[P39 model](https://www.wikidata.org/wiki/Wikidata:WikiProject_every_politician/P39_model)
saying a repeated mandate gets its own statement). The documented convention
and the actual data disagree; the data wins, because that is what the tool has
to avoid duplicating.

Re-run the census before ever flipping this back:

```sparql
SELECT ?statements ?terms (COUNT(*) AS ?people) WHERE {
  {
    SELECT ?person
           (COUNT(DISTINCT ?statement) AS ?statements)
           (COUNT(DISTINCT ?term) AS ?terms)
    WHERE {
      ?person wdt:P1307 ?pid ;
              p:P39 ?statement .
      ?statement ps:P39 wd:Q18510612 .
      OPTIONAL { ?statement pq:P2937 ?term . }
    }
    GROUP BY ?person
  }
}
GROUP BY ?statements ?terms
ORDER BY DESC(?people)
```

Two consequences worth knowing before a first run:

- **P2937 is missing from ~89% of items.** Populating the `terms:` map will
  make `ADD_TERM` fire for most of the chamber, and `ADD_TERM` is mechanical
  under the P1307 rule — so `suggestions.qs` would become a bulk P2937 backfill.
  Legitimate, but decide deliberately.
- **2.8% of members hold several P39 statements for the same seat** (they left
  and returned). QuickStatements matches an existing statement by property +
  main value, which cannot tell those apart, so `is_mechanical` refuses
  qualifier-only commands for them. They stay in the report for a human.

### 3. ✅ The configured Q-IDs — *all resolve*

The position items are confirmed
([Q18510612](https://www.wikidata.org/wiki/Q18510612) National Council,
[Q18510613](https://www.wikidata.org/wiki/Q18510613) Council of States). The
**26 canton Q-IDs were assembled without live access** and only ZH, BE, LU, AG,
VD and GE had been checked against their item pages. The 2026-07-29 run checked
all 28: every one resolves, and every canton ID came back labelled
`Kanton <name>` with *instance of* `Kanton der Schweiz`. Nothing transposed.

Re-run it after any config change:

```bash
uv run python -m wd_parliament --verify-config
```

It prints each configured Q-ID with the label and *instance of* Wikidata
actually has for it, so a transposed ID is obvious. The `parl_groups`,
`parties` and `terms` maps ship **empty on purpose** — a wrong Q-ID there would
be attached as a qualifier to real statements, and the tool skips unknown
values rather than guessing.

Note that while `terms` is empty, no `ADD_TERM` suggestions are made, and under
`statement_model: period` **no qualifier-only QuickStatements are emitted
either** — without the P2937 term a command cannot say which of several
same-seat statements it means. That is deliberately fail-safe.

### 4. ⬜ Validate the period join — *not started*

```bash
uv run python -m wd_parliament --validate-periods 12345 23456
```

Give it one roll-call `IdVote` per period. It compares the `PersonNumber`s that
actually voted against the members the interval overlap assigned to that
period. They should agree modulo absences; anyone who **voted but was not
assigned** means the interval logic is wrong.

Worth doing after step 0c, not before: if `DateJoining` turns out to be a
reporting-year start, the period assignment is wrong for a reason this check
would report but not explain.

### 5. ⬜ Try one or two QuickStatements by hand — *not started*

Before any bulk apply, paste a single line into QuickStatements and confirm the
statement lands with its qualifiers and reference intact.

### 6. 🔶 Should the source be OpenParlData instead? — *measured, partly*

`swissparlpy` 1.0.0 ships a second backend, `openparldata`, reading
[api.openparldata.ch](https://api.openparldata.ch), and Wikidata has an
*OpenParlData ID* property, [P14527](https://www.wikidata.org/wiki/Property:P14527).
Either could replace the P1307 ↔ `PersonNumber` join the rest of this document
is built on. `scripts/verify_openparldata.py` measures whether they should.

**The data model** (established by the 2026-07-29 run, and *not* what the first
version of the probe assumed):

- a **body** is the *level* of parliament, not a chamber — the Federal Assembly
  is one body (`CHE` / "Schweiz") covering both councils;
- the **National Council and Council of States are groups**, and a person's
  seat is a `memberships` row pointing at one;
- so a tenure is reached by walking person → memberships → group. There is no
  chamber-shaped table to read.

Note a cantonal legislature carries exactly the same `council_legislative`
membership type as a federal seat, so the chamber's **name** is what
distinguishes them, not the type.

**What is measured.** P14527 is real and close to parity with P1307:

| | items | of them National Councillors |
| --- | ---: | ---: |
| P1307 Swiss parliament ID | 3,719 | **3,043** |
| P14527 OpenParlData ID | 4,277 | **3,025** |

P14527 is *broader overall* and 18 short among seat holders — near enough that
size alone decides nothing. The number that matters is how many seat holders
carry P14527 and **not** P1307, since two identifiers on the same people are
worth no more than one; `marginal_gain_query` measures exactly that.

`persons` also carries `wikidata_id` and `party_harmonized_wikidata_id`. Across
all 26,574 people in the API — every Swiss parliament at every level — those sit
at 16.2% and 65.6%. The party figure is the interesting one:
`config/parliament.yaml` ships `parties:` and `parl_groups:` **empty on
purpose**, because a wrong qualifier Q-ID is worse than none, and a
source-supplied one removes that hand-maintained map.

**What is still open, and it is the decisive one.** Do the chambers'
memberships carry `date_start` / `date_end`? This tool reconciles P39 with
P580/P582 and P2937, all of which come from a seat tenure with a start and an
end. The only seat membership the first run reached was *cantonal* — the probe
sampled the wrong Andrey, a Fribourg cantonal member rather than the National
Councillor — and it read:

```
type='council_legislative', group_name_de='Grosser Rat des Kantons Freiburg',
start=None, end=None
```

Undated. If the federal groups are the same, OpenParlData cannot source P39
however good its other fields are, because a membership with no start yields no
P580 and no period overlap. The reworked probe queries the chambers' groups
directly, so it measures the federal case rather than inferring from one
cantonal row.

**Recommendation, pending section B.** Add OpenParlData as a **second join path
and an enrichment source**; do not replace the OData read. `MemberCouncil` is a
seat-tenure table with `DateJoining`/`DateLeaving` on one row, the P1307 join is
confirmed, and both of that path's failure modes are now fixed and tested — the
cost is paid. If section B comes back fully dated, replacement becomes a real
option and the tenure dates would also settle step 0c independently.

Two cautions when reading the output. The backend logs unknown query parameters
and sends them anyway rather than rejecting them — `limit` is one, which is why
the first run pulled all 26,574 person records — and its warning does not
interpolate the table name, printing a literal `'{table}'`. And `wikidata_id`
is a different class of claim from P1307: `is_mechanical` gates on *Wikidata*
having asserted the identifier that established the match, so a Q-ID a third
party asserts about Wikidata needs its own decision rather than inheriting that
gate.

```bash
uv run python scripts/verify_openparldata.py
```

It is wired into the `Verify assumptions` workflow as an evaluation that
**never gates the job** — a "no" here is an answer about a design option, not a
broken pipeline.


---

## What it checks

| Suggestion | Priority | Trigger |
| --- | :---: | --- |
| **Add Swiss parliament ID** | 1 | Item matched by name but has no P1307. Highest leverage — it makes every future run exact. |
| **Review ended membership** | 1 | P39 closed with P582, but parlament.ch says `Active`. |
| **Add end date** | 2 | P39 open, but the member is no longer `Active`. |
| **Add membership** | 2 | Sitting member, no P39 for this council. |
| **Fix start date** | 2 | P580 disagrees with `DateJoining`. |
| **Add start date** | 3 | P39 open with no P580. |
| **Add parliamentary term** | 3 | P39 missing a P2937 for a period the tenure covers. |
| **Add qualifier** | 4 | Missing P768 electoral district or P4100 parliamentary group. |
| **Review party** | 4 | P102 missing or disagreeing with `PartyAbbreviation`. |
| **No Wikidata item** | 5 | No item found by P1307 or by name. |

Scope for v1: **both chambers, currently sitting members only** (~246 people).
Historic members are a later extension — `MemberCouncilHistory` has an identical
shape, so it is a table swap plus following `IdPredecessor` chains for members
who left and returned.

## How members are matched

1. **P1307 join** — `MemberCouncil.PersonNumber` against Wikidata's Swiss
   parliament ID. Exact. This is the only provenance QuickStatements are
   emitted from.
2. **Name + birth date fallback** — an exact label/alias match on a human who
   is a politician or already holds one of the seats, **with the birth date
   required to agree**. `MemberCouncil.DateOfBirth` upgrades wd-squads' "refuse
   to guess between namesakes" into "pick the one born on the right day"; a
   candidate whose known birth date contradicts the member's is rejected
   outright.

Members resolved by name are marked ⚠️ in every report and are **never**
written to `suggestions.qs`.

## Joining members to legislative periods (P2937)

`MemberCouncil` has no period field, and the OData metadata confirms there is
**no association** between `LegislativePeriod` and `MemberCouncil` — its only
associations are to `Session`, `Business` and `Vote`. The join is constructed:

- **Primary — interval overlap.** Fetch all ~52 `LegislativePeriod` rows in one
  request and intersect each period's `StartDate`/`EndDate` with the member's
  `DateJoining`/`DateLeaving`. Covers 100% of members in both chambers, needs no
  per-person requests, and is exactly what P2937 means. A tenure spanning
  several periods yields several P2937 values.
- **Cross-check — `Voting`.** The only table carrying both `PersonNumber` and
  `IdLegislativePeriod`, so it gives an empirical "this person actually sat in
  that period". Not a replacement: it *verifies*. Two known holes — Ständerat
  roll-call votes only exist from the 2010s, and a very short tenure may include
  no recorded vote. Never pull the whole table; `swissparlpy`'s README warns
  that unbounded `Voting` queries return 500s. `--validate-periods` fetches one
  vote at a time.
- **Rejected — `BusinessRole` → `Business`.** Only covers members who filed or
  were assigned business, and dates by *submission* rather than by membership.
  Weaker on both coverage and meaning.

Intervals are treated as **closed** on both sides, so a tenure ending exactly on
a period's `EndDate` is inside that period and does not leak into the next. A
member with no `DateJoining` is assigned **no** periods — treating an unknown
start as unbounded would silently emit ~52 wrong qualifiers, which is far worse
than emitting none.

`Session` (`StartDate`/`EndDate`/`LegislativePeriodNumber`) is a useful future
refinement: a mid-period joiner's real start is usually the first day of a
session, which would sanity-check `DateJoining` against `DateOath`.

## Architecture

```
config.load_config
  → parliament.get_members()          # swissparlpy, MemberCouncil, Active=True
  → parliament.get_periods()          # swissparlpy, LegislativePeriod (~52 rows)
  → period_overlap.assign_periods()   # pure: tenure × period intervals → P2937
  → wikidata.get_position_holders()   # SPARQL: P39 + qualifiers, P1307 index, P102
  → resolve.match_members()           # P1307 join, name+birth-date fallback
  → diff.compute_suggestions()        # pure
  → report.write_reports()            # reports/*.md, docs/index.html, docs/data.json
  → quickstatements.render()          # docs/suggestions.qs
```

- **`parliament.py`** — the only `swissparlpy` caller. Kept thin so everything
  downstream is pure and offline-testable.
- **`period_overlap.py`** — pure interval arithmetic.
- **`wikidata.py`** — three bounded SPARQL queries rather than one wide one, so
  repeatable qualifiers cannot blow up into a cartesian product.
- **`resolve.py`**, **`diff.py`**, **`quickstatements.py`** — pure.
- **`report.py`**, **`config.py`**, **`http_client.py`**, **`app.py`**,
  **`__main__.py`** — per the wd-squads patterns.

Pure logic is kept strictly separate from network code: the pure functions are
the only things the unit tests need.

## QuickStatements

`docs/suggestions.qs` holds V1 commands for the mechanically-applicable subset.
The safety rule lives in one testable predicate,
`quickstatements.is_mechanical`:

- the member was matched by **P1307**, never by name;
- the kind **adds** information — `FIX_START_DATE`, `REVIEW_ENDED` and
  `REVIEW_PARTY` all mean "an existing value looks wrong", and QuickStatements
  can only add, so applying them would create a second contradictory value
  rather than fix the first;
- every value the command needs is present;
- under `statement_model: period`, a qualifier-only command must also carry the
  P2937 term that says *which* of several same-seat statements it means.

Every line carries a reference: `S854` (reference URL) pointing at the member's
parlament.ch biography, and `S813` (retrieved) with the run date.

```
Q121160|P39|Q18510612|P580|+2015-12-07T00:00:00Z/11|P768|Q11943|S854|"https://www.parlament.ch/de/biografie/wd/1108"|S813|+2026-07-29T00:00:00Z/11
```

The file is pipe-separated. QuickStatements' paste box expects tabs:

```bash
tr '|' '\t' < docs/suggestions.qs
```

## Configuration

Everything that is *data* lives in
[`config/parliament.yaml`](config/parliament.yaml): the P39 position items, the
26 canton Q-IDs, the party / parliamentary-group mappings, the legislative-term
items for P2937, and the `statement_model` switch. Unknown values are skipped,
never guessed at.

> **User-Agent:** the Wikimedia APIs require a descriptive User-Agent with a
> contact URL/e-mail. The tool refuses to run with a placeholder one. The same
> `requests.Session` is handed to `SwissParlClient`, so parlament.ch sees it too.

## Running locally

```bash
uv sync --extra dev

# Full run
uv run python -m wd_parliament --config config/parliament.yaml

# Fast iteration: 20 members per chamber, debug logging
uv run python -m wd_parliament --config config/parliament.yaml --limit 20 --verbose
```

Watch the **P1307 hit rate** in the log and in the reports — a high rate
confirms the identifier join is doing the work; a low one means the name
fallback is carrying more than it should. A full run should see ~246 sitting
members, and a member you know is sitting should produce no *add membership*.

Outputs:

- `reports/README.md` — index of both chambers with match rates.
- `reports/<N|S>-<slug>.md` — per-chamber TODO, grouped by canton (or by
  parliamentary group; see `group_by`).
- `docs/index.html` — a self-contained dashboard.
- `docs/data.json` — the same data as JSON.
- `docs/suggestions.qs` — the QuickStatements file.

Network access to `ws.parlament.ch` and `query.wikidata.org` is required.

## Development

```bash
uv run --extra dev pytest -q      # offline; no network needed
```

The pure logic is covered by unit tests over committed fixtures, with no HTTP
mocking. **Note:** `tests/fixtures/*.json` were hand-built to the OData schema
rather than captured from the live service — see
[`tests/fixtures/README.md`](tests/fixtures/README.md), which explains why and
how to replace them with real captures.

## Running on GitHub Actions

- [`Verify assumptions`](.github/workflows/verify.yml) — **`workflow_dispatch`
  only**, `contents: read`. Runs `scripts/verify_source.py` and
  `--verify-config` and writes both results to the run summary. Generates
  nothing, commits nothing, publishes nothing. Run this **before** the update
  workflow. Both checks run even if the first fails, so one dispatch answers
  both questions.
- [`Update parliament TODO`](.github/workflows/update.yml) — weekly (Mon 06:00
  UTC) and on demand; regenerates the reports and commits them back
  (`contents: write`).
- [`Deploy dashboard to GitHub Pages`](.github/workflows/pages.yml) — publishes
  `docs/`, chained off the update workflow's completion because a
  `GITHUB_TOKEN` push does not itself fire a `push` event.
- [`Tests`](.github/workflows/tests.yml) — `uv run --extra dev pytest -q` on
  every push and PR.

To publish the dashboard: push to GitHub, then in **Settings → Pages** set the
source to **GitHub Actions**.

> **`workflow_dispatch` needs the workflow file on the default branch.** A
> workflow that only exists on a feature branch shows no "Run workflow" button.
> `Verify assumptions` therefore has to be merged to `main` before it can be
> dispatched — which is why it is a small, self-contained branch rather than
> part of the scaffold.

`reports/*.md`, `docs/index.html`, `docs/data.json` and `docs/suggestions.qs`
are **build artifacts** — generated by a run and by the weekly Action, committed
so the diffs are reviewable in git history and Pages can serve `docs/`, never
hand-edited. The versions currently committed are placeholders saying no run
has happened yet.

## License

MIT — see [LICENSE](LICENSE).
