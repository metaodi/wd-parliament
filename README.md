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

**This project has been scaffolded but never run end to end against live
data.** The environment it was built in could reach neither `ws.parlament.ch`
nor `query.wikidata.org` — both were refused by the network egress policy.
Step 2 below has since been settled by running its query against live Wikidata;
step 1 has not. Work through the rest before applying any QuickStatements.

### 1. Confirm the P1307 assumption — *strong evidence, not verified directly*

The whole join strategy assumes Wikidata's
[P1307](https://www.wikidata.org/wiki/Property:P1307) "Swiss parliament ID"
holds `MemberCouncil.PersonNumber`.

What is confirmed: Guy Parmelin is
[Q121160](https://www.wikidata.org/wiki/Q121160) with **P1307 = 1108**, and his
parlament.ch biography is at `parlament.ch/en/biografie/guy-parmelin/`**`1108`**.
P1307's own documentation gives the URL pattern
`parlament.ch/[lang]/biografie/[name-slug]/[PersonNumber]`, i.e. the property
value *is* the `PersonNumber`.

What is **not** confirmed: nobody has fetched Parmelin's `MemberCouncil` row and
read `PersonNumber` back. Do that first:

```bash
uv run python scripts/verify_p1307.py
```

It searches **both** `MemberCouncil` and `MemberCouncilHistory` — Parmelin left
the National Council in 2015, and which table holds a former member is itself
something the probe establishes — then reports one of three verdicts:

| Verdict | Meaning |
| --- | --- |
| `CONFIRMED` | `PersonNumber == P1307`. The join is sound; nothing to change. |
| `CONTRADICTED` | Either `PersonIdCode` matches instead (the message says so, and which code to switch), or neither does. |
| `INCONCLUSIVE` | The person was not found, or the service could not be read at all. The two are reported differently — an unreachable service is a connectivity problem, not a finding. |

Only `CONFIRMED` exits 0. There is also a **`Verify assumptions`** workflow
(`workflow_dispatch` only) that runs this probe and `--verify-config` together
and writes both results to the run summary; see below.

If `PersonNumber` is not 1108, check `PersonIdCode`. If neither matches, the
join strategy falls back to name+birth date and this design needs revisiting —
say so rather than proceeding.

Corroborating but not conclusive: the step-2 census found **3,043** items
carrying both P1307 and a National Council P39, which is the right order of
magnitude for "every National Councillor with an identifier" and confirms
P1307 is genuinely the Swiss parliamentarian identifier. It does not
distinguish `PersonNumber` from `PersonIdCode`, which is why the check above
still has to be run.

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

### 3. Verify the configured Q-IDs

The position items are confirmed
([Q18510612](https://www.wikidata.org/wiki/Q18510612) National Council,
[Q18510613](https://www.wikidata.org/wiki/Q18510613) Council of States). The
**26 canton Q-IDs were assembled without live access** and only ZH, BE, LU, AG,
VD and GE were checked against their item pages. One command checks them all:

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

### 4. Validate the period join

```bash
uv run python -m wd_parliament --validate-periods 12345 23456
```

Give it one roll-call `IdVote` per period. It compares the `PersonNumber`s that
actually voted against the members the interval overlap assigned to that
period. They should agree modulo absences; anyone who **voted but was not
assigned** means the interval logic is wrong.

### 5. Try one or two QuickStatements by hand

Before any bulk apply, paste a single line into QuickStatements and confirm the
statement lands with its qualifiers and reference intact.

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
  only**, `contents: read`. Runs `scripts/verify_p1307.py` and
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
