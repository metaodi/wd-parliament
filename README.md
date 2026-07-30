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
predicted — step 0b, which is now the one that would do damage. Later
dispatches confirmed the source read is fixed and settled step 6
([run 30494063489](https://github.com/metaodi/wd-parliament/actions/runs/30494063489)).

Step 0c is answered and fixed: `DateJoining` is a mandate-*segment* start, so
P580 now comes from `MemberCouncilHistory` instead.

**Step 4 is now runnable without a human first hunting roll-call numbers** —
`--validate-periods` discovers them — and the `Verify assumptions` workflow
runs it. **Step 5 is the only one left that no workflow can do for you:** it
needs a person to paste one line into QuickStatements and look at the result.

The tool is built against **swissparlpy 2.0.0**, which fixed the OpenParlData
defaults this repo's probes had to work around
([issue #52](https://github.com/metaodi/swissparlpy/issues/52)). The
work-arounds are kept where they are still meaningful as *choices* — `lang='de'`
pins the German columns the chamber names are matched in — and the probe's
section E re-measures the defaults on every dispatch rather than trusting a
changelog.

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

### 0c. ✅ `DateJoining` is a *segment* start — **fixed**: P580 comes from the history

**Answered against `MemberCouncil`.** `scripts/compare_tenure_dates.py` compared
all 246 sitting members against OpenParlData's per-term `begin_date`, joining
through Wikidata (`PersonNumber` →P1307→ Q-ID ←`wikidata_id`); 244 joined, 2
skipped:

```
agree exactly:                    233 (95.5%)
disagree:                         11
  of those, OData is a 1 January: 0
```

So the *reporting-year* shape this step feared is **not** what happens to
sitting NR/SR rows — Parmelin's `2026-01-01` is specific to Federal Councillors.
But every one of the 11 has `DateJoining` **later** than OpenParlData's
`2023-12-04`, the 52nd legislature's opening.

Philipp Bregy settles which source is right. `MemberCouncil` gives him
`DateJoining = 2025-09-16`; his `MemberCouncilHistory` rows read:

```
2019-03-04 → 2019-12-01   (NR, Active=False)
2019-12-02 → 2023-12-03   (NR, Active=False)
2023-12-04 → <sentinel>   (NR, Active=True)   <- the current legislature
```

He has held the seat since 2023-12-04 and the *history* says so. The
`2025-09-16` on the current row is a **later mandate segment**, not when he took
office. `verify_source.py` section A2 corroborates: 200 National Councillors
share just **16 distinct `DateJoining` values** (earliest 2023-12-04, latest
2026-06-01), which is the signature of most members carrying the legislature
start and a handful having been re-segmented at later session dates.

**Consequences.**

**The fix, and it did not need a change of source.** `MemberCouncilHistory`
already carries the right date, so P580 now comes from there:

- `parliament.segments_from_rows` groups the history into mandate segments per
  `(person, council)`. It deliberately does **not** de-duplicate the way
  `members_from_rows` does — collapsing `(person, council)` is exactly what
  loses the tenure. Only language duplicates are dropped, on the full
  `(person, council, joining, leaving)` tuple.
- `parliament.tenure_start` walks back from the newest segment while each one
  begins within a day of the previous ending, and returns where that chain
  starts. A legislature boundary is such a join (Bregy's 2019-12-01 /
  2019-12-02), so continuous re-election is one tenure. A **real** break stops
  the walk, so a member who left and returned gets the return — not their
  first-ever election.
- `models.Member.start_date` is `tenure_start or date_joining`, and it is the
  only start `diff` and `period_overlap` read. `date_joining` stays as the raw
  field it is.
- `app.process` fetches the history once and **degrades rather than aborting**
  if it cannot: a slightly-wrong P580 on a handful of members is a worse
  report, whereas no report is a worse outcome. It logs how many members had a
  later segment start, so the correction is visible in every run.

**And measure it, don't assume it.** Bregy proves the correction is right for
Bregy; nothing measured it across the chamber, which is a thin basis for a date
written mechanically. `compare_tenure_dates.py` therefore prints **two**
verdicts off one join:

1. *the raw field* — `DateJoining` against OpenParlData's latest term. This is
   0c as posed, and its `CONTRADICTED` (the 11 above) is the finding that moved
   P580 off the field. Kept as the regression check.
2. *what ships* — `Member.start_date` against the same segment chaining applied
   to OpenParlData's per-term rows, so both sides answer "since when, without a
   break". **This is the verdict to read before applying anything**: it is the
   only one that measures the date the tool would actually write.

Comparing 2 against the *latest term* rather than the chained run would report
every long-serving member as a disagreement — which is why they are two
functions (`chained_start` and `current_start`) and not one.

Re-run it after touching any of this.


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

### 4. 🔶 Validate the period join — *runnable now; dispatch it*

```bash
# discover one roll-call per recent period, or name them yourself
uv run python -m wd_parliament --validate-periods
uv run python -m wd_parliament --validate-periods 12345 23456
```

It compares the `PersonNumber`s that actually voted in a roll-call against the
members the interval overlap assigned to that period. They should agree modulo
absences; a sitting member who **voted but was not assigned** means the
interval logic is wrong.

This sat untouched because it needed `IdVote` numbers nobody had. It no longer
does: given none (or `auto`) the votes are discovered from the `Vote` table,
one per period, for the three most recent periods the overlap assigns anybody
to — the only periods where a list of *sitting* members can test anything. The
`Verify assumptions` workflow runs it as section 5, and takes a `vote_ids`
input if you would rather name them.

Two things the comparison now does that decide whether its answer means
anything at all:

- **only voters who are still sitting are compared.** The member list is the
  ~246 people in office today; every roll-call also contains people who have
  since left, and they are absent from that list entirely. Counting them as
  "not assigned" would report a mismatch that is purely an artefact — and one
  that grows the older the vote is. They are reported separately as
  `voted_but_no_longer_sitting`, which is an expected number, not a finding.
- **the step 0c tenure correction is applied first.** The overlap reads
  `Member.start_date`, so validating against uncorrected `DateJoining` values
  would be validating an interval the pipeline does not use.

It reports without gating the workflow: what it decides is the P2937 qualifier,
`terms:` in the config ships empty, and unknown values are skipped — so nothing
this check could falsify currently reaches QuickStatements at all. It gates a
bulk apply the day that map is filled in.

### 5. ⬜ Try one or two QuickStatements by hand — *not started, and only a human can*

Before any bulk apply, paste a single line into QuickStatements and confirm the
statement lands with its qualifiers and reference intact. This is the one
remaining step no workflow can carry out for you: it needs a Wikidata account,
and its whole point is that a person looks at what actually landed.

Do it against a fresh run's `docs/suggestions.qs`, not the committed file —
that one is still the failed run of 2026-07-29 and says `Statement model:
period`, which the config no longer does. Regenerate it with `Update
parliament TODO` first.

### 6. 🔶 Should the source be OpenParlData instead? — *viable; a real decision, not a dismissal*

`swissparlpy` 1.0.0 ships a second backend, `openparldata`, reading
[api.openparldata.ch](https://api.openparldata.ch), and Wikidata has an
*OpenParlData ID* property, [P14527](https://www.wikidata.org/wiki/Property:P14527).
`scripts/verify_openparldata.py` measures both.

**The data model.** A **body** is the *level* of parliament, not a chamber — the
Federal Assembly is one body, `CHE`. The **National Council and Council of
States are groups** (ids 1663 and 1664), and a seat is a `memberships` row
pointing at one. There is no chamber-shaped table. A cantonal legislature
carries exactly the same `council_legislative` membership type as a federal
seat, so the chamber's **name** is what distinguishes them, matched by
equality — a substring match picks up `Präsidium des Nationalrates` and
`Büro NR`.

**The seat tenure is there, and it is dated.** The columns are `begin_date` /
`end_date`:

| group | memberships | with `begin_date` | with `end_date` | open |
| --- | ---: | ---: | ---: | ---: |
| Nationalrat (1663) | 4,398 | **4,398** | 4,198 | **200** |
| Ständerat (1664) | 1,220 | **1,220** | 1,174 | **46** |

Every seat membership carries a start. The dates are real per-term spans going
back to 1853 — `2019-12-02 → 2023-12-03` is the 51st legislature exactly,
`2003-12-01 → 2007-12-02` the 47th. And the open-ended rows come to **200 for
the National Council and 46 for the Council of States** — both chambers' exact
sizes, which is a strong sign the data is current as well as correct.

Two things confirm this rather than one. The probe counts the populated column
itself, *and* asks the API to do the same filtering with `exclude_null`:

```
with a start:      4398
server-side check: CONFIRMED: The API reports 4398 of 4398 rows with a
                   non-null begin_date, agreeing with the client-side count.
```

The second does not depend on the probe having picked the right column name in
Python, which is exactly how an earlier run got this backwards.

So **OpenParlData can source P39**, including P580, P582 and — since the rows
are per-term — the P2937 qualifier. That is more than `MemberCouncil` offers
for free, and the history reaches far enough to make the "historic members"
extension possible.

**It also bears directly on step 0c.** `MemberCouncil` gives Parmelin
`DateJoining = 2026-01-01`, the current year rather than a tenure start.
OpenParlData gives spans that line up with legislature boundaries. If those
disagree for sitting members, OpenParlData is the more trustworthy of the two
and step 0c resolves in its favour. **Compare them before switching** — that
comparison is the next thing to do here, and it is not yet done.

**P14527 adds nobody.** It exists and is close to P1307 in size, but the
overlap is what matters:

| | items | of them National Councillors |
| --- | ---: | ---: |
| P1307 Swiss parliament ID | 3,719 | 3,043 |
| P14527 OpenParlData ID | 4,277 | 3,025 |
| reachable by **either** | | **3,043** |
| P14527 **without** P1307 | | **0** |

The union is exactly P1307's own count, so every seat holder with the new
identifier already carries the old one. A second join path would match nobody.
Keep the P1307 join regardless of what happens to the source.

**Enrichment, independent of the above.** Of the 3,686 federal members,
**3,685 carry a `wikidata_id`** and **3,219 (87.3%) a
`party_harmonized_wikidata_id`**. The party figure matters because
`config/parliament.yaml` ships `parties:` and `parl_groups:` **empty on
purpose** — a wrong qualifier Q-ID is worse than none — and this supplies them
from the source. Before using `wikidata_id` for *matching*, settle the
provenance question: `is_mechanical` gates on `QID_FROM_IDENTIFIER`, meaning
*Wikidata* asserted the identifier that established the match, and a Q-ID a
third party asserts about Wikidata is a different class of claim.

**A real inconsistency in the API**, worth knowing before building on it: the
seat is reachable from the **group** but not from the **person**. Walking
Gerhard Andrey (person 21709) returns 47 memberships — 14 of them dated,
including `Büro NR` and `Koordinationskonferenz` — but **none is his National
Council seat**, though group 1663 holds 4,398 such rows. So
`memberships?person_id=` and `memberships?group_id=` disagree about what a
membership is. Any rewrite must read the seat by group.

**How to query it**, learned the hard way. The first two were reported as
[swissparlpy #52](https://github.com/metaodi/swissparlpy/issues/52) and are
**fixed in 2.0.0**, which this project now requires; they are kept here because
the reasoning still applies and the probe still measures them:

- **`lang='de'` was load-bearing, and is now a deliberate choice.** swissparlpy
  1.0.0 hard-coded `lang='en'` with `lang_format='flat'`, and the English
  columns are null — so a table could read as *completely empty*: `bodies`
  returned 0 rows under the defaults and **1,405** with `lang='de'`. Neither
  `search='%'` nor `search_language='de'` changed anything; that was measured
  across seven combinations, not guessed. 2.0.0 sends no `lang` unless asked,
  and the probes still pass `lang='de'` explicitly — the chamber names they
  match are German, and pinning the language is what keeps the answer the same
  across library versions.
- **`search` works, but mind the scope and the casing.** 1.0.0 forced
  `search_scope='all'` and sent an empty `search=''` on every request; 2.0.0
  sends neither, so the API's own defaults apply — scope `metadata`, mode
  `partial`. Pass `search_scope='all'` explicitly for the full-text indexes.
  `exact` is **case-sensitive** in practice, despite the documentation calling
  it a case-insensitive exact match: `search='Nationalrat'` returns exactly 1
  group, `search='nationalrat'` returns 0. And `partial` is ILIKE substring
  matching, which is why `lastname=Andrey` alone matched *Pascal* Andrey, a
  Fribourg cantonal member, and made one run measure the wrong person.
- **Field filters need no such care** and are what this probe narrows with:
  `body_key=CHE` cuts 8,817 groups to 1,041, and `firstname=`/`lastname=`
  together find the right person.
- **`limit` is the page size, not a cap.** swissparlpy's response iterator
  follows `next_page` to exhaustion, so iterating returns everything that
  matches regardless. `len()` on a response is `meta.total_records` off the
  first page, so a count costs one request. Slicing (`response[0:1]`) loads
  only as far as the slice reaches — which is how `find_votes` samples one row
  from a large table.
- Unrecognised parameters are **logged and sent anyway** rather than rejected,
  so a mistyped filter silently does nothing; check the counts look plausible.
  Under 2.0.0 the warning names the table and no longer fires for documented
  query parameters, so one that does appear is worth reading.

`bodies` returning 0 rows was **not** an API bug — it was the `lang='en'`
default, as above.

```bash
uv run python scripts/verify_openparldata.py
```

Wired into the `Verify assumptions` workflow as an evaluation that **never
gates the job** — an answer about a design option must not turn the diagnostic
red.


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
  vote at a time, and finds them in `Vote` — the table of votes themselves,
  one row each — rather than making you supply `IdVote` numbers by hand.
  Only voters who are *still sitting* can be compared; see step 4.
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
