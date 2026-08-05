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

Runs 11 and 12 (2026-07-30) settled the last two that a workflow can settle:

- **step 4 is done and clean.** The period overlap was cross-checked against
  three real roll-calls, discovered automatically; every sitting member who
  voted had been assigned that period. In the current legislature that is 183
  of 183, with nothing to explain away.
- **step 0c now has the measurement it was missing.** `DateJoining` is a
  mandate-*segment* start, so P580 comes from `MemberCouncilHistory` — and the
  date that comes out of it agrees with OpenParlData for **244 of 244** sitting
  members, two sources sharing neither key nor publisher. P580 may be applied
  in bulk.

**Step 5 is the only one left that blocks the federal pipeline, and no workflow
can do it:** a person has to paste one line into QuickStatements and look at
what landed. Step 7 — extending the tool to a *cantonal* parliament, the
Kantonsrat Zürich — is open too, but it gates nothing here: runs 13 and 14
settled the source, the position item and the identifier join, and what remains
is the adapter itself. Step 8 asks whether the *departed* members' leaving
dates could be applied mechanically; run 18 (2026-08-04) measured **1,960 of
1,960 agreeing exactly** with an independent source, and what keeps those
suggestions report-only is now five identities that are one character apart —
a morning's work on the biography pages, not a data problem. **Step 9 is done**:
run 19 (2026-08-04) measured which personal data each source actually carries
— four of six properties federally, three cantonally, 206 suggestions in the
federal report — and found two long-standing bugs in the *cantonal adapter*
along the way. It gates nothing, since none of those suggestions can ever be
mechanical.

⚠️ **Runs 13 and 14 both failed their first gating check on a parlament.ch
timeout** reading the full `MemberCouncil` table — `! MemberCouncil: The server
returned a timeout error`. It is intermittent rather than an outage: the *same*
query succeeded later in both jobs (section B of `verify_source.py`, and
`--validate-periods`). `HttpClient` retries 429/5xx, but this arrives as an
OData error document rather than an HTTP status, so nothing retries it.

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

**Run 12 answers it: `CONFIRMED`, 244 of 244, 100.0%.** Every sitting member's
tenure start, derived from `MemberCouncilHistory` by `tenure_start`, is exactly
the date OpenParlData gives for the run they are currently serving. Two sources
that share no key and no publisher agree on all 244. **P580 may be applied in
bulk.**

Getting there took one wrong answer, and it is the one worth remembering.
**Run 11 ([30538886383](https://github.com/metaodi/wd-parliament/actions/runs/30538886383))
returned `CONTRADICTED`, 22 of 244 — and every one of the 22 was a *Council of
States* member:**

```
4116 Gössi Petra (SR):  tenure start 2023-12-04, OpenParlData 2011-12-05
806  Graf Maya (SR):    tenure start 2019-12-04, OpenParlData 2001-06-05
4189 Burkart Thierry (SR): tenure start 2019-12-02, OpenParlData 2015-11-30
```

Those OpenParlData dates are when they entered the **National Council**. The
probe was keying its seat rows by Q-ID alone, so a member who moved chambers
had an NR row ending 2019-12-01 and an SR row starting 2019-12-02 — which chain
straight across the change into one run. The tool models **one P39 per seat**,
so the comparison has to: seat rows are now keyed by `(Q-ID, council)`.

The lesson generalises, and step 4 walked into the same wall on the same 25
people: *a person is not a seat.* Any check that joins these two sources on a
person alone will read a chamber change as a contradiction. With the key fixed,
the 22 disappeared and comparison 2 went to 100%.

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

### 4. ✅ Validate the period join — *done: the overlap agrees with who voted*

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

Three things the comparison does that decide whether its answer means anything
at all:

- **only voters who are still sitting are compared.** The member list is the
  ~246 people in office today; every roll-call also contains people who have
  since left, and they are absent from that list entirely. Counting them as
  "not assigned" would report a mismatch that is purely an artefact — and one
  that grows the older the vote is. They are reported separately as
  `voted_but_no_longer_sitting`, which is an expected number, not a finding.
- **a vote cast before the member's current tenure is an earlier mandate, not
  a mismatch.** The tool models one P39 per *seat*, so a member who moved from
  the National Council to the Council of States has a Council of States tenure
  that rightly begins at the move — their earlier votes belong to a different
  statement. Reported as `voted_before_their_current_tenure`, recognised by the
  period ending before `Member.start_date`. That is a bare date comparison and
  **not** another call into `period_overlap`, so a bug in the function under
  test cannot excuse itself.
- **the step 0c tenure correction is applied first.** The overlap reads
  `Member.start_date`, so validating against uncorrected `DateJoining` values
  would be validating an interval the pipeline does not use.

**Run 12 ([30539278018](https://github.com/metaodi/wd-parliament/actions/runs/30539278018))
answers it**, on the three most recent periods, from votes it discovered
itself (`16344`, `23315`, `31148`):

| period | assigned | voted | still sitting | earlier mandate | **not assigned** |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 52nd | 246 | 200 | 183 | 0 | **0** |
| 51st | 160 | 200 | 126 | 12 | **0** |
| 50th | 86 | 199 | 72 | 13 | **0** |

```
✅ Every sitting member who voted was assigned to that period by the overlap.
```

The 52nd is the strongest single result: **183 people demonstrably voted, and
the interval overlap had assigned every one of them** — no absences to explain
away, no reclassification.

Run 11 scored the 25 in the older periods as failures before the earlier-mandate
rule existed. Every one was a chamber switch: Gössi, Graf, Burkart and the rest
voted in the 50th and 51st as *National* Councillors, and their Council of
States tenure rightly begins later. That the same 25 people were also
comparison 2's false disagreements is what identified the shared cause.

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

`swissparlpy` ships a second backend, `openparldata`, reading
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

**It bore directly on step 0c, and that comparison has now been made.**
`scripts/compare_tenure_dates.py` joined both sources through Wikidata: 11 of
244 sitting members had `MemberCouncil.DateJoining` *later* than OpenParlData's
legislature start, and reading Bregy's `MemberCouncilHistory` settled which was
right — OpenParlData's. So on the date that matters most, **OpenParlData was
the more trustworthy of the two, and parlament.ch only matched it once a second
table and a chaining rule were added.**

That is the honest summary of where the two stand:

| | parlament.ch OData | OpenParlData |
| --- | --- | --- |
| tenure start | `MemberCouncilHistory` + `tenure_start` chaining | per-term `begin_date`, directly |
| P2937 term qualifier | constructed by interval overlap (step 4) | implied by the per-term rows |
| historic members | same-shaped table, plus `IdPredecessor` chains | 5,618 rows back to 1853 |
| party / group Q-IDs | not carried; the config maps ship empty | 87.3% carry a party Q-ID |
| identifier join | `PersonNumber` ↔ P1307, confirmed | `wikidata_id` on 3,685 of 3,686 — but *asserted by a third party*, not by Wikidata |

The last row is the one that keeps the OData read where it is for now:
`is_mechanical` gates on `QID_FROM_IDENTIFIER`, meaning **Wikidata** asserted
the identifier that established the match. Sourcing the Q-ID from OpenParlData
instead would be a different class of claim wearing the same provenance flag,
and it would have to be given its own.

**And the gap the comparison was meant to expose has closed.** Run 12 put the
two sources' tenure starts side by side for all 244 joinable sitting members
and they agree **100%**. So the argument for switching is no longer "the dates
are better"; they are the same dates. What is left is the shape of the work:
OpenParlData hands you per-term rows, so P2937 falls out of the data instead of
being constructed by interval overlap, and 3,686 historic members come with it.
Against that, the OData read is written, tested and now verified end to end,
and the party/group Q-IDs OpenParlData would supply can be taken from it
*without* changing the source at all — that is the cheap half of the win.

A reasonable reading of the evidence: **keep OData as the source, take
OpenParlData for enrichment**, and revisit if the historic-members extension
gets built, where per-term rows back to 1853 are worth more than they are
today.

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

### 7. 🔶 Could this be pointed at a cantonal parliament? — *yes, on P14527; the canton's own id is the right property and this source cannot supply it*

The **Kantonsrat Zürich**, as the first case. `scripts/verify_kantonsrat.py`
measures it. Runs 13–15 (2026-07-30) settled every question an adapter needs
answered, and the two that came back "no" are both the harmless kind — a map
left empty makes no suggestion:

| | Verdict |
| --- | --- |
| Kantonsrat located as a group | **YES** — body `ZH`, group **5077**, `name_de='Kantonsrat Zürich'` |
| Seat memberships dated | **CONFIRMED** — 912 of 913 carry a `begin_date` |
| Seats currently held == 180 | **CONFIRMED** — `186 open → 182 begun → 180 in a seat role` |
| A Wikidata-asserted identifier | **CONFIRMED** — **35 of 35** linked members carry P14527… |
| …and it reaches the *chamber* | **CONTRADICTED** by the first real run — **0 of the 180 sitting members** matched on it |
| …and its value is the person id | **CONTRADICTED**, 34 of 35 (run 20) — the odd one is another body's record for the same human |
| The canton's own id, P13468 | **CONTRADICTED as a join from this source** (run 20) — 28 of 35 items carry it, **0 of 28** values are the person id, and **no column** of OpenParlData holds them |
| The position item | **CONFIRMED** — `Q21518678`, held by 270 items, **167 currently (93% of 180)** |
| P768 Wahlkreis map | **CONTRADICTED** — 18 districts in the source, **0 resolvable** |
| P2937 term map | **INCONCLUSIVE** — the qualifier is used on **no** statement for the seat |

**P14527 was the cantonal join, and measuring it over the wrong population is
what made it look like one.** Of the 35 ZH members OpenParlData links to
Wikidata, **all 35** carry P14527 and 27 also carry P1307 — CONFIRMED, and the
federal finding inverted exactly as predicted. Then the first real run joined
**0 of the 180 sitting members** on it and fell through to the name search for
138 of them.

Nothing about the 35 was wrong; the population was. Those 35 are the people
OpenParlData *had already linked to an item*, which skews hard towards members
notable enough to have gone federal — the same bias that put the National
Council at the top of the position ranking below. **A coverage rate measured
over a linked sample is not a coverage rate over the chamber**, and this is the
second time that distinction has cost a wrong answer in the same section.

**P13468 "Zurich Kantonsrat and Regierungsrat member ID" is the right property
and the wrong join, and run 20 (2026-08-04) is how that was settled in one
dispatch.** It is the id the canton itself assigns — the cantonal analogue of
P1307 in a way an aggregator's id never was — and Wikidata carries it for **28
of the 35** linked ZH people. Then the value comparison:

```
Does a P13468 value equal OpenParlData's person id?
  compared: 35 | value == person id: 0 | value != person id: 28 | no P13468: 7
    Q117716: P13468='22518' but person id=9532     (Ruth Genner)
    Q123979: P13468='22382' but person id=18999    (Ueli Maurer)

If P13468 is not the person id, which column is it?
  people compared: 28
  (no column of the person record carries these values)
```

**An identifier needs a value on both sides.** P13468 identifies these people
in the canton's *own* dataset, which this tool does not read, and OpenParlData
carries it nowhere — so joining on it would compare a person id against a
different id space, and every `ADD_IDENTIFIER` suggestion would offer a number
that is not this property's. `config.load_config` now **refuses** the pairing
outright rather than documenting it, because it is the plausible mistake
somebody will try again. Supplying P13468 properly means reading the canton's
dataset (the alternative source at the end of this section), not renaming a key.

That is also the answer to "why measure the column?": *not* the person id and
*nowhere in the source* are different findings, and only the second forbids the
join.

**The same run cost P14527 its verified status, for a structural reason worth
keeping.** 34 of its 35 values are the ZH person id; the one that is not —
`Q131948095: P14527='1411' but person id=17436` — is another body's record for
the same human, because **OpenParlData holds one person record per person per
body**. So P14527 identifies a *record*, not a person, and it misfires on
exactly the members who also sat elsewhere: the federal bias in miniature. It
is still the join here, since it is the only identifier this source can supply
a value for, but `config/kantonsrat-zh.yaml` ships `identifier_verified: false`
— which makes the run corroborate every identifier match against the item's own
name and birth date, count the rejections in the report, stamp every suggestion
`identifier_unverified` so nothing can be emitted, and say in each
ADD_IDENTIFIER suggestion that the number it offers is unconfirmed.

One caveat that survives all of this: 35 links is 35 of **834** ZH person
records (4.2%, against 3,685 of 3,686 federally), so the vast majority of
cantonal members have no Wikidata item at all — the report is dominated by
`NO_WIKIDATA_ITEM`, a worklist for *creating* items.

**The source side works.** 46 groups under body `ZH`, the chamber found by
exact name, 913 memberships all of `type_harmonized='council_legislative'`,
dated with `begin_date`/`end_date`, 727 of them closed. The electoral district
is there too, on the *person* records: `electoral_district_de/fr/it`. So
OpenParlData can source a cantonal P39, P580, P582 and P768.

**186 open memberships against 180 seats, and none of the six was a vacancy.**
The funnel run 14 printed:

```
open (no end_date):    186
of those, begun by 2026-07-30: 182  (4 start later)
roles among those:      (none)=1, 1. Vizepräsidium=1, 2. Vizepräsidium=1,
                        Gast=1, Mitglied=177, Präsidium=1
```

Four rows start on `2026-08-17` — real, correctly open, not sitting yet. One is
a `Gast` and one carries no role. The remaining **177 `Mitglied` + 3 presiding
officers = 180**, exactly.

That last part corrected the correction. Run 13's rule filtered to `Mitglied`
and got **177**, because the presidium rows are *not* second rows for people who
also hold a plain membership — **a presiding member has no `Mitglied` row at
all, so their presidium row is their seat.** So `DEFAULT_SEAT_ROLES` is an
allowlist of roles that hold a seat. Neither polarity is self-correcting when
the source adds a role, so what actually protects the count is the probe
printing every role it saw and refusing to call a total that is not 180
CONFIRMED.

**`Q19479543` was `Kategorie:Kantonsrat (Zürich, Person)`** — instance of
Wikimedia category, held by nobody. It shipped as the probe's default on the
strength of a web search, and as a P39 main value it would have written
hundreds of statements claiming people hold a Wikimedia category. Section D
caught it because it *counts holders* instead of reading a label.

Rather than guess again, section D now **derives** the position: it asks which
P39 positions the members OpenParlData links actually hold, ranked. Run 14:

```
  26 x Q18510612    Mitglied des schweizerischen Nationalrats     (74% of sample)
  12 x Q21518678    Mitglied des Zürcher Kantonsrat               (34% of sample)
   7 x Q98502244    Mitglied des Regierungsrats Zürich            (20% of sample)
```

**Q21518678 is the seat.** Note the top of that ranking is the *National
Council* — the sample is cantonal members notable enough to have a Wikidata
item, which skews hard towards people who later went federal. So the discovery
output names its top hit a candidate rather than an answer, and a human picks
from the list. (The Regierungsrat appearing third is the executive-vs-legislature
trap, visible in the data.)

The category item also invalidated section C on run 13: it asked its identifier
question *through* that position, so every count came back 0 — which reads as
"cantonal members carry no identifier" and only meant "nobody holds that item".
The reach query now asks about the **people** directly and mentions no position
at all, which is why run 14 could answer it.

**The adapter is built.** `config/kantonsrat-zh.yaml` plus
`src/wd_parliament/openparldata.py` run the same pipeline against the same
dataclasses — `period_overlap`, `diff` and `quickstatements` cannot tell which
source produced a `Member`:

```bash
uv run python -m wd_parliament --config config/kantonsrat-zh.yaml \
  --reports-dir reports/kantonsrat-zh --docs-dir docs/kantonsrat-zh
```

`Update parliament TODO` takes the config as a dispatch parameter and runs one
parliament per run; the scheduled run does both, serialised, so a broken
cantonal source can never stop the federal report from being published. The
federal outputs keep the top level (Pages serves `docs/index.html` and that URL
should not move) and each other parliament gets a subdirectory named after its
config.

**It ships report-only** (`quickstatements: false`), for two independent
reasons. Only 35 of 834 ZH people have a Wikidata item at all, so the report is
chiefly a worklist for *creating* them rather than for fixing statements. And
the join's value link is measured at 34 of 35 rather than 35 of 35, for the
record/person reason above. Section C compares them on every dispatch. When it
says CONFIRMED, put P14527 back into `models.VERIFIED_IDENTIFIER_PROPERTIES`,
flip `identifier_verified`, and only then consider QuickStatements — in that
order, and each step in the same commit as the output that licenses it.

The rest of this section is design that the measurements have not changed.

**Most of the pipeline is already parliament-agnostic**, which is the fact that
makes this worth measuring at all. `period_overlap.py` is closed-interval
arithmetic over dates. `diff.py` works off `Body`/`Member`/`Period`/`Config`.
`quickstatements.py` never names P1307 — `is_mechanical` gates on
`qid_source == QID_FROM_IDENTIFIER`, and P39/P580/P582/P768/P4100/P2937 are the
same properties for a cantonal seat. `resolve.py` joins `Member.person_number`
against `WikidataPerson.parliament_id`, whatever filled those in. What is
federal is confined to three places: `parliament.py` (the OData tables),
`wdt:P1307` hard-coded in two SPARQL builders in `wikidata.py`, and the config.

So the extension is a **source adapter plus an identifier decision**, and the
second is the hard half:

**There is no cantonal P1307.** `is_mechanical`'s whole claim is that *Wikidata
itself* asserted the identifier that established the match. Three candidates,
not equivalent — and run 14 settled it in favour of the first:

| Candidate | Provenance | What it would cost |
| --- | --- | --- |
| **P13468** Zurich Kantonsrat and Regierungsrat member ID | Wikidata-asserted, and the canton's *own* id | a source that can supply its value — OpenParlData cannot (run 20), so this needs the canton's dataset |
| **P14527** OpenParlData ID | Wikidata-asserted, per person *record* | nothing to the gate, and it is what ships — but it matched **0 of the 180 sitting members**, so the report is carried by the name fallback |
| OpenParlData's `wikidata_id` field | a third party asserting a Q-ID *about* Wikidata | its own `QID_FROM_*` constant and its own decision in `is_mechanical`; it must not inherit the P1307 gate |
| name matching only | none | `is_mechanical` already refuses it → report-only, `quickstatements: false` |

This **inverts** step 6's finding. There, P14527 added nobody: 0 National
Councillors carry it without P1307. That is a fact about the *federal* overlap.
Cantonally it is the whole join — measured at 35 of 35.

Do not use P1307 as the cantonal join under any circumstances. It is the
*federal* service and reaches only the members who also sat in Bern — the
people it misses are precisely those who never went federal, which is a bias
that looks like coverage rather than like a bug.

**The seat-count check is the one that catches a bad source read**, and run 13
proved it by catching one. Section B asks whether the chamber currently holds
exactly **180** people — the cantonal version of the federal probe's strongest
signal, where the National Council's open memberships came to exactly 200 and
the Council of States' to 46. A count *near* 180 is reported as CONTRADICTED,
not waved through: 179 means a vacancy or an unopened row and 181 an unclosed
one, and both change who the diff thinks is sitting. Getting that wrong
federally is what published 2,234 bad suggestions.

Zurich also sharpens the matching discipline step 6 learned. There, a substring
match made `Präsidium des Nationalrates` — a committee of eight — read as the
National Council. Here the same mistake is worse: the **Regierungsrat** is the
cantonal *executive*, seven members, five letters from the legislature.
`is_kantonsrat` requires a name to **equal** one of the chamber's spellings, and
`tests/test_verify_kantonsrat.py` pins that down.

Two more things the extension needs, both smaller:

- **P768: the source has all 18 Wahlkreise; Wikidata has no practice to copy.**
  The names are on the **person** records (`electoral_district_de`), numbered
  and untidy — `'XVII Bülach'`, `'I      Zürich 1+2'` — and the 18 distinct
  values over the 180 current members sum to exactly 180 members. But P768
  appears on only **3 of 270** statements for the seat, and those three are
  `Kreis 4`, `Kreis 5` and `Kreis 11` — *city of Zürich quarters*, not cantonal
  electoral districts. Three statements are not a convention, and they are not
  even the right kind of thing, so **the map stays empty**: an unmapped district
  makes no suggestion, while a wrong one becomes a qualifier on real statements.
  Resolving the 18 items is a Wikidata-side job for a human, not something to
  infer.
- **P2937: the qualifier is used on no statement for the seat at all.** Nothing
  can be derived from usage, so that map stays empty too — the federal default.
  Section F does show the source-side evidence, and it is clean: the start dates
  cluster on the election dates, four years apart (`1991-05-06 ×53`,
  `1995-04-02 ×41`, `1999-05-31 ×51`, `2003-05-19 ×41`, `2007-05-21 ×47`,
  `2011-05-09 ×49`, `2015-05-18 ×40`, `2019-05-06 ×43`), so a term list is a
  handful of rows whenever someone wants one.
- **`statement_model` for a ZH body is `tenure`, not `period`** — correcting
  what this section said before run 15 measured it. The clusters above are the
  giveaway: 913 memberships across 834 people is ~1.1 rows each, and each
  election contributes only ~40–50 new rows rather than 180. So OpenParlData
  gives ZH **one row per continuous tenure**, not the per-term rows it gives
  federally, and P2937 would have to be constructed by interval overlap against
  a term list exactly as `period_overlap.py` already does. That is convenient
  rather than costly — the module transfers unchanged — but it does mean
  `statement_model` still needs to move onto `Body` (it is global in
  `config.py`), since two parliaments need not share one convention.

And two things that **do not transfer**, so nothing here inherits their
verdicts: step 4's roll-call cross-check has no cantonal equivalent unless the
canton publishes votes, and run 12's "P580 is safe to apply in bulk, 244 of 244"
was measured on federal members.

```bash
# checks Q21518678 and re-derives the candidates from the members
uv run python scripts/verify_kantonsrat.py

# another canton: body, seat count and seat roles are all parameters
uv run python scripts/verify_kantonsrat.py \
  --body-key BE --expect-seats 160 --position ''
```

Wired into `Verify assumptions` as section 7, and like step 6 it **never gates
the job** — for the plainest reason of the non-gating checks: it measures
a parliament no config here processes, so nothing it finds can make the federal
pipeline more or less safe to run.

The alternative source, if OpenParlData turns out not to carry what section B
needs: the canton's own service. opendata.swiss publishes *Kantonsratsmitglieder
Kanton Zürich ab 1803* (members with entry/exit dates, party and Wahlkreis — the
direct `MemberCouncil` + `MemberCouncilHistory` analogue) and an XML web service
for the Kantonsrat's business system. That is the *authoritative* source in the
sense `diff` relies on; OpenParlData is a harmonised aggregator of it.

### 8. 🔶 May the departed members' P582 be applied in bulk? — *the dates agree 1,960/1,960; five identities are one character apart*

The diff's second pass finds people Wikidata still records as sitting whom the
source does not list, and the report now names the leaving date to add (see
[The one suggestion that is about somebody the source does not
list](#the-one-suggestion-that-is-about-somebody-the-source-does-not-list)).
Applying those dates mechanically is a *different* question from printing them,
and it is the one nothing has answered. Three things about that population are
unmeasured, and each is enough on its own:

- **it is defined by Wikidata, not by the source.** Every other check in this
  file starts from the ~246 people parlament.ch lists. This one starts from
  whatever Wikidata has left open, which is precisely the set nobody has looked
  at. `ADD_END_DATE` is mechanical, so ungating it writes P582 across all of
  them unread.
- **the person number comes from Wikidata's identifier value.** Step 1
  confirmed P1307 == `PersonNumber` for a *sitting* member (Parmelin, two
  identifiers compared directly). Nothing has checked it for somebody who left
  in 1987, and the failure mode is a date written to another person's item.
- **the date comes from a table read only for people still in office.**
  `MemberCouncilHistory` is where P580's tenure start comes from — and step 0c
  is the standing proof that "it is in the history" can mean something other
  than it looks like.

`scripts/verify_departures.py` measures all three plus a fourth nobody would
think of until QuickStatements refused: **which statement the P582 would
close.** An item with two P39 statements for one seat cannot be targeted by
property + main value at all, and an open statement whose P580 is not this
tenure's start is most likely about an earlier spell — closing it with this
tenure's end puts a wrong span on a real statement out of two individually
correct dates.

```bash
uv run python scripts/verify_departures.py

# single-source run: section C then says INCONCLUSIVE by construction
uv run python scripts/verify_departures.py --no-openparldata
```

It cross-checks the leaving date against OpenParlData through the same join
step 0c uses — `PersonNumber` →P1307→ Q-ID ←`wikidata_id`— keyed by
`(Q-ID, council)` and never by Q-ID alone, because a member who moved NR→SR
reads as a contradiction if the two chambers are pooled.

**Run 16 (2026-08-04)** measured it for the first time. Of 3,727 items holding
one of the two seats, **1,986** are reported as departed — an open P39 whom
parlament.ch does not list — and the population turns out to be far more
tractable than the gates assumed:

**Run 18 (2026-08-04)** is the current answer, after two rounds of fixing the
probe rather than the data:

| | Verdict |
| --- | --- |
| Reach | **CONFIRMED** — 1,968 of the population resolve into `MemberCouncilHistory` and **every one** has a closed tenure, i.e. a date to suggest |
| Identity | **INCONCLUSIVE** — no identifier reaches a *different* surname; 5 of 1,968 are one character apart and a name comparison cannot settle those |
| Leaving dates | **CONFIRMED** — **1,960 of 1,960 comparable dates agree exactly (100.0%)**; 7 are absent from OpenParlData and 1 Q-ID is claimed by two person records and skipped |
| Which statement | **CONFIRMED** — 3 people hold several P39 for the seat and the existing `ambiguous_statement` rule already refuses them; none starts on the wrong date |

**The dates are settled.** Two sources sharing neither key nor publisher agree
on the leaving date of every one of 1,960 people the current-members table has
never heard of. That is a stronger result than step 0c's 244 of 244, on a
population fifteen times the size.

**What is left is five names**, all nineteenth-century, all one letter apart:

| Wikidata | parlament.ch | |
| --- | --- | --- |
| `Johann Zünd` | `Zündt` | a trailing consonant |
| `Maurice Despland` | `Desplands` | a trailing s |
| `Camille Desfayes` | `Défayes` | an s inside |
| `Hans Wunderly-von Muralt` | `Wunderli` | y for i |
| `Jeannot de Crousaz` | `Decrousnaz` | an inserted n |

They are almost certainly the same people, and the probe deliberately does not
say so: a name spelt two ways and a wrong person one letter away look identical
from here. They are reported as **near misses** — a bucket that accepts
nothing, so widening it cannot cost safety; the worst it can do is move a row
from "wrong person" to "check this one". Somebody settling those five on the
biography pages is what stands between step 8 and a `CONFIRMED`.

#### Wikidata already answers some of those five, and the check was not asking

An item's **label is not the whole of what Wikidata says a person is called**,
and until now section B compared the label alone. Two other assertions live on
the same item, both about spelling and both ignored:

- an **alias** ("also known as"). `Johann Zünd` carries `Johannes Zündt` as an
  alias — the item itself records the second spelling;
- **P1810 `subject named as`** as a qualifier on the P1307 statement. That one
  is not a spelling in general but a claim about *this source*: "in the
  parlament.ch council-member database this person is named `Johannes Zündt`".
  Where it exists it settles the question outright, because it is Wikidata
  stating which record the identifier points at and under what name.

Both are now read, by a fourth bounded query
(`WikidataClient.get_name_variants`), asked only for the people section B
actually judges. Every name the item carries is compared and the **strongest
reading wins**, so the extra names can only move a row *towards* agreement:
they cannot manufacture the `CONTRADICTED` that would block a bulk apply, and
the worst they can do is settle a row the probe already had the answer to. What
they cannot do is turn corroboration into proof — an alias is asserted by
whoever wrote the item, exactly like the label — so which name settled a row is
counted and printed rather than folded into the total:

```
  items carrying another name:    2
    settled by an alias:          1
    settled by P1810 'named as':  1

  settled by a name the label does not carry — read them:
    Q9 'Johann Zünd' (NR) -> #2126 'Zündt' via P1810 'Johannes Zündt' (exact)
```

An unsettled row now also says whether there was anything else to check
(`[no alias or P1810 to check]` against `[also checked: alias '…']`), because
the two mean different things: the first is somebody not having recorded the
source's spelling yet — fixable on Wikidata by adding the P1810, which both
records the finding and settles the probe — while the second is the sources
genuinely spelling the person differently everywhere.

⚠️ **Not yet re-measured against live Wikidata.** The change is covered by
tests, but how many of the five it settles is a question only a dispatch of
`Verify assumptions` can answer. Until run 19 reports, the table above stands.

The main pipeline's name fallback (`resolve.py`) already searched aliases —
`people_search_query` matches `rdfs:label` UNION `skos:altLabel` — so this
closes the gap between what the pipeline matches on and what the probe checks.

⚠️ Even then, `CONFIRMED` licenses *considering* the removal of the gates, not
the removal itself. Step 5 — pasting one line by hand — comes first.

The single dissenting row was reported as `#2126 Alfred Gehrig (NR)`,
parlament.ch 1971-11-28 against OpenParlData 2014-05-31. **It was not a
disagreement at all — it was this probe's join, and checking it by hand is what
found that out.** OpenParlData's `Nationalrat` memberships for Gehrig carry
exactly the parlament.ch date; the 2014 row belongs to somebody else.

The join runs person → `wikidata_id` → Q-ID, and **nothing makes
`wikidata_id` unique**. Two person records naming the same item pool their
memberships under one key, after which `chained_end` answers with whichever of
the two has the later row. A Q-ID claimed by more than one record is now
**skipped and reported**, never arbitrated — the same rule
`resolve.match_by_identifier` applies to a P1307 claimed by two items, and for
the same reason: a source contradicting itself about who somebody is cannot be
resolved by picking a side. Every disagreement now prints the row count and the
OpenParlData person id(s) behind it, which is what would have shown this at a
glance instead of costing a manual lookup.

**`compare_tenure_dates.py` has the identical join and was exposed to the
identical bug**; the same skip is now applied there. Its recorded 244-of-244
verdict was measured over sitting members only, where the collision did not
bite — but that was luck, not design, and that comparison licenses a *bulk*
apply of P580.

**Four things the first runs got wrong about *themselves*, all now fixed.**
Runs 16-18 found more wrong with this probe's arithmetic than with the data —
every CONTRADICTED it has ever returned except the current one turned out to be
its own, and it took a human checking a row by hand to catch the worst. That is
the expected shape of a first measurement, and the reason its verdicts are read
rather than wired into a gate:

- **the identity check cried wolf 29 times.** All 29 "wrong person" hits were
  the same person spelt differently: `Börlin`/`Boerlin`, `Ettlin`/`Etlin`,
  `Bremi`/`Bremi-Forrer`, `Vonderweid`/`von der Weid`,
  `Patocchi`/`Pattocchi`. `fold_name` now folds umlauts, accents, particles,
  married names and doubled letters, and reports them as a **third bucket** —
  counted and printed, never silently merged, because a check that stops
  showing its work has stopped checking. 29 false alarms would have buried the
  one real mismatch nobody would then look for.
- **statement ambiguity is excludable, and was being read as a veto.** Three
  people out of 1,969 is three people skipped by a rule that already exists —
  except it did not exist for *these* suggestions: `diff` only stamped
  `ambiguous_statement` on sitting members. It now stamps departed ones too,
  which is right whatever happens to the gates.
- **the one leaving-date disagreement was a Q-ID two people claimed**, as above.
  A probe whose single finding is its own join is a probe that has not yet
  measured anything; the fix is a skip, and the diagnostic that would have
  caught it — printing the person ids behind a row — is now always on. With it
  skipped, the remaining 1,960 agree **exactly**.
- **and it cried wolf five more times**, at one character rather than five.
  `Zünd`/`Zündt` is not evidence of a wrong person, and calling it one would
  have repeated the first mistake in miniature. Those are now `near` misses:
  reported, listed, and **not accepted** — the section returns INCONCLUSIVE
  rather than either CONFIRMED or CONTRADICTED, because "unsettled" is what
  they are. And it was asking a narrower question than Wikidata answers: the
  aliases and the P1810 qualifier are now read too, see above.

**The "no P580 anywhere" anomaly is settled, and it is real data.** Every one
of the 1,968 open statements carries no start date — but the control run 18
added says **1,844 of 3,829** statements for these seats (48.2%) do carry a
P580, so the field is being read perfectly well. It is a selection effect: a
statement with a P580 and no P582 is exactly what a *sitting* member's looks
like, and sitting members are excluded from this population by construction.
What is left is the undated bulk imports. Keep the control line: it is the only
thing that distinguishes this from a broken read, and the two are
indistinguishable from inside the subset.

Wired into `Verify assumptions` as section 6, and it **never gates the job**
for a third distinct reason: the suggestions it measures are report-only *by
construction*. `diff._departed_suggestion` sets no `qid_source` and puts no
`position` in the payload, so `is_mechanical` refuses them twice over and
nothing this probe could falsify reaches `suggestions.qs` today. What a
`CONFIRMED` would license is removing those two gates — a deliberate act, after
step 5, not something a green run should imply.

**`INCONCLUSIVE` is the expected answer on tidy data.** The population is
however many open memberships Wikidata has for people who have gone; a small
one is good news about the data and no news about the question. That is exactly
why this must never be wired into a gate. (Federally the population is 1,986,
so this is not the federal run's situation — it is what a cantonal or
well-maintained chamber would return.)


### 9. ✅ Do the personal-data checks have a source? — *measured: four of six federally, three cantonally, and it found two adapter bugs*

The checks described under [The suggestions that are about the person rather
than the seat](#the-suggestions-that-are-about-the-person-rather-than-the-seat)
are safe however this comes out — a source with nothing to say produces no
suggestion — but "safe" is not "useful". **Run 19 (2026-08-04)** measured both
halves.

**Federally, four of the six have a column and two do not.** Coverage over the
246 sitting members, and what each check would suggest against Wikidata today:

| Property | Column | Source coverage | Suggestions |
| --- | --- | ---: | ---: |
| P19 place of birth | `BirthPlace_City` + `BirthPlace_Canton` | 245 (99.6%) | **23** |
| P1321 place of origin | `Citizenship` | 244 (99.2%) | **82** |
| P102 member of party | `PartyName` | 246 (100%) | **0** |
| P1971 number of children | `NumberOfChildren` | 105 (42.7%) | **101** |
| P106 occupation | — **no such column** | — | — |
| P856 official website | — **no such column** | — | — |

206 suggestions in total, none of them mechanical. Every one of the 246 members
matched an item and every one was reached by the personal-data query, so the
zeroes are real answers rather than gaps. P102's zero is the good kind — every
matched item already carries one — and the check is kept listed because it is
what would notice a newly elected member without one.

`config/parliament.yaml` therefore ships `person_data: [P19, P1321, P102,
P1971]`. `parliament.OCCUPATION_FIELDS` / `WEBSITE_FIELDS` stay as the hook a
future column would be read through. `Mandates`, `AdditionalMandate` and
`AdditionalActivity` are deliberately **not** candidates for P106: they are the
register of interests, and filing a board seat as an occupation would put a
wrong statement on a real person.

**Cantonally it inverts, exactly as the identifier did.** The ZH `persons`
table *does* carry `occupation_de` and `website_personal` — so P106 and P856
are answerable there and not here — and carries no birthplace, no Bürgerort and
no children. `config/kantonsrat-zh.yaml` ships `person_data: [P102, P106,
P856]`. Note `website_parliament_url_de` is the member's page on the chamber's
own site rather than a website *of the person*, and is deliberately not read as
P856.

**And the probe found more wrong with the adapter than with the checks.**
Printing the real column list showed `openparldata.py` reading the party from
`party_name_de` and the birth date from `birthdate`, neither of which
OpenParlData uses — the real names are **`party_de`** and **`birthday`**. So
every cantonal member had been coming out with no party and no birth date since
the adapter was written. Neither failure was visible from inside the pipeline:
an unmapped party makes no suggestion (`parties:` ships empty), and a missing
birth date silently downgrades the name fallback from "pick the one born on the
right day" to a bare label match. Both names are fixed, the wrong ones kept as
trailing aliases, and `tests/fixtures/zh_persons.json` now carries the columns
the API really returns.

Two smaller findings from the same listing: `MemberCouncil` has a `Nationality`
column that is not in the fixtures — a country rather than a Bürgerort, so a
possible P27 source and a separate check nobody has asked for — and 13 of the
138 matched ZH members were *not* reached by the personal-data query, which is
precisely the population `person_data_known` exists to keep quiet about.

**Re-run it after touching any of this**, and know the volume before adding to
`person_data:`. These are coverage checks across a whole chamber rather than
exceptions: P1971 alone is 41% of the National Council and the Council of
States.

```bash
uv run python scripts/verify_person_data.py
uv run python scripts/verify_person_data.py --config config/kantonsrat-zh.yaml

# section A only — no SPARQL, so no suggestion counts
uv run python scripts/verify_person_data.py --skip-wikidata
```

Section A prints every column the source returns, then per property: the
columns found, how many sitting members carry a value, and an example.
Section B runs the real comparison and counts what would come out. The three
verdicts are the ones this repo has learned to keep apart:

| Verdict | Meaning |
| --- | --- |
| `CONFIRMED` | the column exists and is filled in; the count says how often |
| `CONTRADICTED` | **no** candidate column exists. The check is dead weight in `person_data:` — drop it, or read the column listing and add the real name |
| `INCONCLUSIVE` | the column exists but is empty for every sitting member — a fact about today's chamber, not about the schema. Also what an unreadable column listing returns, because "no such column" and "could not look" mean different things |

Wired into `Verify assumptions` as section 8, and it **never gates the job** —
the strongest of this file's several reasons for that: `ADD_PERSON_DATA` is not
in `MECHANICAL_KINDS` at all, so nothing this probe measures can reach
`suggestions.qs` under any verdict.

One thing run 19 got wrong about *itself*, now fixed: a check with **no source
column** printed "nothing to do — every matched item already records it",
which is the message for the opposite situation. A zero has two causes — the
source said nothing, or Wikidata already has it — and reading the first as the
second is how a missing column comes to look like a well-maintained property.
`volume_note` now takes the source coverage and says which it is.

---

## What it checks

| Suggestion | Priority | Trigger |
| --- | :---: | --- |
| **Add the source's identifier** | 1 | Item matched by name but has no P1307 (P14527 for the Kantonsrat). Highest leverage — it makes every future run exact. |
| **Missing the other identifier** | 5 | The item lacks the *other* of the parliament's two identifiers — the one this run does not join on and has no value for. See below. |
| **One identifier, several items** | 1 | Two or more Wikidata items claim the same P1307. A contradiction in Wikidata's own data — see below. |
| **One item, several source records** | 1 | Two or more *source* records point at one Wikidata item. The mirror image, and the only finding here that is fixed in the source rather than on Wikidata. |
| **The two sources disagree** | 2 | parlament.ch and OpenParlData describe the same seat differently. Withholds the mechanical edit for that member — see below. |
| **Review ended membership** | 1 | P39 closed with P582, but parlament.ch says `Active`. |
| **Add end date** | 2 | P39 open, but the member is no longer `Active`. |
| **Add membership** | 2 | Sitting member, no P39 for this council. |
| **Fix start date** | 2 | P580 disagrees with `DateJoining`. |
| **Add start date** | 3 | P39 open with no P580. |
| **Add parliamentary term** | 3 | P39 missing a P2937 for a period the tenure covers. |
| **Add qualifier** | 4 | Missing P768 electoral district or P4100 parliamentary group. |
| **Review party** | 4 | P102 missing or disagreeing with `PartyAbbreviation`. |
| **No Wikidata item** | 5 | No item found by the identifier or by name. |
| **Add personal data** | 6 | The source publishes a fact about the person (P19, P1321, P106, P102, P856, P1971) and the item records **no** statement for it. See below. |

Scope for v1: **both chambers, currently sitting members only** (~246 people).
Historic members are a later extension — `MemberCouncilHistory` has an identical
shape, so it is a table swap plus following `IdPredecessor` chains for members
who left and returned.

### A member has two identifiers, and only one of them can be the join

Every parliament here is described by **two** Wikidata identifiers, and they
name records in different registers:

| | the parliament's own member id | the OpenParlData id |
| --- | --- | --- |
| Federal Assembly | **P1307** Swiss parliament ID | **P14527** OpenParlData ID |
| Kantonsrat Zürich | **P13468** Zurich Kantonsrat and Regierungsrat member ID | **P14527** OpenParlData ID |
| resolves through | `parlament.ch/{lang}/biografie/wd/{id}` · `wahlen.zh.ch/krdaten_staatsarchiv/abfrage.php?id={id}` | `openparldata.ch/item/persons/{id}` |

A run can only join on one of them — the one whose value its source publishes —
so the other was never mentioned anywhere, and an item missing it stayed missing
it. The `identifiers:` key in the config lists both, and the run reports each one
it does not find. The two findings differ in exactly one thing, **whether the run
has the value**:

- **`ADD_IDENTIFIER`** (priority 1) is the join property. Its value *is* the
  source's person id — that equality is what the join means — so the suggestion
  carries a number to paste.
- **`MISSING_IDENTIFIER`** (priority 5) is the other one. No source this
  pipeline reads publishes its values (measured for P13468 in
  [run 20](#7--could-this-be-pointed-at-a-cantonal-parliament--yes-on-p14527-the-cantons-own-id-is-the-right-property-and-this-source-cannot-supply-it):
  they appear in **no column** of OpenParlData's person records), so the finding
  is the absence itself and the reader looks the person up in that register.
  Offering a number here would mean offering one from the wrong id space, which
  is the failure `identifier_verified` exists to prevent — correct data written
  onto somebody else's item.

Neither is ever mechanical: both kinds are outside
`quickstatements.MECHANICAL_KINDS` and neither payload carries a `position`.

The **URL template belongs to the property**, in
`models.IDENTIFIER_PROPERTIES`, and that is not decoration. The number printed
beside a member in a report is one particular property's value, so linking it
through any other property's template sends the reader to a page that has never
heard of it — which is what the Kantonsrat report did until now: an OpenParlData
person id (P14527) linked to the chamber's member list. `biography_url` now
defaults to the **join property's** record page, and a config overriding it owns
that same correspondence.

### The one suggestion that is about Wikidata contradicting itself

P1307 should identify one person. When two items claim the same value, the
identifier join **refuses to arbitrate** — picking one would attach every
subsequent edit to a coin flip. That refusal used to be a log line and nothing
more, which made the conflict worse than invisible:

- the member came out of the join unmatched, so the run's headline health
  number (the P1307 hit rate) counted them as a coverage gap rather than a
  data conflict;
- and an unmatched member draws **"no Wikidata item was found… they may need a
  new item"** — the one piece of advice guaranteed to make things worse, since
  acting on it would create a third duplicate.

Both are fixed. `resolve.match_by_identifier` records the claiming items on the
member, `diff` raises `DUPLICATE_IDENTIFIER` at priority 1 and suppresses the
"create an item" advice for that member, and the report links every claimant so
the two can be opened side by side. A second pass raises the same conflict
between items about people who have **left**, which no sitting member's number
would ever surface.

It is report-only, and not for want of evidence — this is the strongest finding
the tool makes. Every repair (merging two items, or removing an identifier from
one) is destructive in a way QuickStatements cannot express, and *which* item is
the real person is exactly the judgement the join declined to make.

One consequence worth knowing: a member whose identifier is duplicated has no
Q-ID, so the reverse walk below cannot recognise them by item and would report
**both** claimants as having left — a confident, wrong claim about somebody
sitting today. That pass therefore also skips any item whose identifier belongs
to a sitting member, not just any item whose Q-ID does.

### The suggestions that are about the person rather than the seat

Everything above is about a **mandate**: a P39 statement, its dates, its
qualifiers — compared *by value*, because parlament.ch is authoritative about
them and that is what licenses saying a statement disagrees.

The personal-data checks are the exception, and they are deliberately weaker.
The source publishes facts *about the member* alongside the mandate, and
Wikidata has properties for them:

| Property | Source (federal / cantonal) | Value |
| --- | --- | --- |
| [P19](https://www.wikidata.org/wiki/Property:P19) place of birth | `BirthPlace_City` + `BirthPlace_Canton` / — | "Bern (BE)" |
| [P1321](https://www.wikidata.org/wiki/Property:P1321) place of origin | `Citizenship`, the *Bürgerort* / — | "Zürich (ZH), Chur (GR)" |
| [P106](https://www.wikidata.org/wiki/Property:P106) occupation | — / `occupation_de` | "Architektin" |
| [P102](https://www.wikidata.org/wiki/Property:P102) member of political party | `PartyName` / `party_de` | "Schweizerische Volkspartei" |
| [P856](https://www.wikidata.org/wiki/Property:P856) official website | — / `website_personal` | "https://example.ch/anna" |
| [P1971](https://www.wikidata.org/wiki/Property:P1971) number of children | `NumberOfChildren` / — | 2 |

The two sources answer **different** halves of that list, which is why the
enabled checks are per-config: parlament.ch has a birthplace, a Bürgerort and a
number of children but no occupation and no website, and OpenParlData's ZH
records have exactly the reverse (step 9).

**They compare presence, not value.** The source gives free text and the
property wants an item; matching "Bern (BE)" to
[Q70](https://www.wikidata.org/wiki/Q70) is a judgement, and it is the same
judgement the config's Q-ID maps exist to keep out of the code. So a suggestion
is raised only when the source has a value **and the item carries no statement
for that property at all**, and the report quotes the source's own string for a
human to resolve. The tool never says a recorded place of birth is wrong.

Three consequences follow, and all three are load-bearing:

- **Nothing here is ever mechanical.** `ADD_PERSON_DATA` is not in
  `MECHANICAL_KINDS`, and its payload carries no `position`, so
  `is_mechanical` refuses it twice. There is no Q-ID to render, so there is
  nothing to emit — relaxing one rule elsewhere cannot turn these into edits.
- **An item nobody queried produces nothing.** `WikidataPerson.person_data_known`
  is what separates "we asked and it carries none of them" from "we never
  asked". A name-matched item with neither the identifier nor a seat falls
  outside the query's population, and without that flag every one of them would
  become a false positive.
- **A source that cannot answer produces nothing.** An absent column, an empty
  one, a member the source has no birthplace for — each yields no value and no
  suggestion. That is what makes the unmeasured column names safe (step 9).

**P102 is the one that overlaps.** *Review party* compares the value, but only
for a party in the `parties:` map — and both configs ship that map empty, so it
never fires today. The presence check is what notices a member with no P102
whatsoever; when a party *is* mapped, it stands aside so one gap is not filed
twice.

They sit at **priority 6**, below every seat finding, because there is
potentially one per member per property and the question this tool exists to
answer is who sits today. Turn any of them off — or all of them — with
`person_data:` in the config.

### Reading a second source, to be contradicted by it

parlament.ch is authoritative, and that is precisely why a claim it makes alone
cannot be checked: P580 is emitted **mechanically** from it, so a wrong value
reaches Wikidata unreviewed. OpenParlData carries the same seats, harmonised
from the same official record but assembled independently — so the federal run
now reads it too, as a `enrich:` block in
[`config/parliament.yaml`](config/parliament.yaml).

The join has no shared key and goes through Wikidata, exactly as step 0c's
probe does:

```
MemberCouncil.PersonNumber ──P1307──▶ Q-ID ◀──wikidata_id── OpenParlData
```

**What the second source may do is deliberately bounded**, and the bounds are
structural rather than a matter of discipline — `enrich.py` produces no
`Member` at all:

- **it supplies nothing.** The member list, every date and every emitted value
  still come from parlament.ch alone. Enrichment can only *withhold*.
- **a disagreement withholds the edit.** `SOURCES_DISAGREE` is raised and
  `sources_disagree` is stamped on every other suggestion for that member, so
  `is_mechanical` refuses them. Which side is right is not decided and does not
  need to be: a value two sources dispute is exactly the value not to write
  unreviewed.
- **it never says who is right.** OpenParlData is not authoritative about the
  Federal Assembly. The suggestion is a prompt to read the biography page, not
  a correction.
- **silence is not disagreement.** Only facts *both* sources state are
  compared, the same rule the tool applies to unmapped cantons and parties.

Three traps, each already paid for by a wrong answer elsewhere in this file and
each now enforced in `enrich.py`:

- **keyed `(Q-ID, council)`, never by Q-ID alone.** An NR→SR mover's two rows
  chain into one span across a chamber change if pooled; run 11 scored 22
  members that way.
- **a Q-ID claimed by several person records is skipped**, never arbitrated,
  and reported (below). Run 17's phantom came from exactly that.
- **both sides chained by the same rule.** Comparing a chained tenure against a
  single term reports every re-elected member as a disagreement.

Run 12 measured the two sources agreeing on **244 of 244** sitting members, so
a long list here means something changed — not that they always differ. Losing
the cross-check degrades the run to a single-source one and says so in the log;
deleting the `enrich:` block turns it off entirely, and nothing else depends
on it.

### …and its mirror image, which is not a Wikidata edit at all

Some sources assert a Wikidata link of their own: OpenParlData's person records
carry a `wikidata_id`. When two of them name the same item, one item is being
claimed as two people — the same conflict as above, pointing the other way.

Nothing about it is repaired on Wikidata, and the report says so in as many
words. It is raised because **a link like this silently corrupts anything
joined through it**, which is exactly how it was found: run 17 of the
departures probe reported Alfred Gehrig, who left in 1971, against a leaving
date of 2014, because a second person record named his item and the two sets of
memberships pooled under one key. Both the Q-ID and the source record ids go
into the report and into `docs/data.json`, so the finding can be handed to the
source's maintainers as it stands.

Only a source that asserts such links can produce any: `parlament.ch` says
nothing about Wikidata, so `ParliamentClient` has no `get_link_conflicts` at
all and the federal run raises none. That is the ordinary case, not a
degradation.

The two probes that join through the field — `verify_departures.py` and
`compare_tenure_dates.py` — **skip** a Q-ID claimed twice rather than
arbitrating, which is what keeps their verdicts honest; this suggestion is how
the same fact reaches somebody who can fix it.

### The one suggestion that is about somebody the source does not list

**Add end date** fires twice, from opposite directions, and the second is the
odd one out. Walking Wikidata's open memberships back finds people the source
does not list as sitting at all — they have left, and the source's
*current-members* table has therefore never heard of them. There is no `Member`
to read anything off, so both of the things that suggestion needs come from
elsewhere:

- **the link to the source's database** — built from the identifier *Wikidata
  itself* asserts (P1307 federally, P14527 cantonally) through the
  `biography_url` template, which defaults to that very property's record page
  and can be overridden per config: a cantonal report pointing at parlament.ch
  sends a reader to a service that has never heard of these members, and one
  pointing an OpenParlData person id at the Kantonsrat's member list sends them
  to a page that cannot resolve the number either;
- **the start and end date to add** — from the source's *historic* record,
  which the pipeline already reads: `MemberCouncilHistory` federally (the same
  rows P580's tenure start comes from, so it costs no extra request) and the
  ended `memberships` rows in OpenParlData. The end date is the newest
  segment's `DateLeaving`; the start is the chained tenure start, the same one
  `Member.start_date` uses.

Both degrade rather than guess: an item with no identifier value gets no link
and no dates, a source that cannot answer leaves the report saying the date has
to be looked up by hand, and a tenure the source has not closed offers no end
date at all.

These suggestions are **report-only by construction**, and stay that way until
[step 8](#8--may-the-departed-members-p582-be-applied-in-bulk--not-measured-the-reason-those-suggestions-are-report-only)
comes back `CONFIRMED` — that probe (`scripts/verify_departures.py`) measures
the historic table for departed members the way step 1 measured `PersonNumber`
for sitting ones. `is_mechanical` refuses them twice over: the member carries
no `qid_source` (the identifier came from Wikidata, not from a resolved member)
and the payload carries no `position`. Do not remove either gate to "unlock"
them — a P582 backfill across everyone Wikidata records as sitting is exactly
the class of bulk edit the rest of this README is about not making by accident.

## How members are matched

1. **Identifier join** — the source's person id against the Wikidata property
   the config names (`identifier_property`): P1307 "Swiss parliament ID"
   against `MemberCouncil.PersonNumber` federally, P14527 "OpenParlData ID"
   for the Kantonsrat. Exact. This is the only provenance QuickStatements are
   emitted from.

   Exactly one property can be the join, because only one of them has a value
   on both sides. The parliament's *other* identifier is still reported when an
   item lacks it — see
   [A member has two identifiers](#a-member-has-two-identifiers-and-only-one-of-them-can-be-the-join)
   — and `config.identifiers` is the list of both. It never joins on anything,
   and `WikidataPerson.identifiers` is deliberately kept out of
   `parliament_id`: that field is read as *the source's person id* by the join
   and by both duplicate checks, and another register's number landing there
   would be read as one.

   Two things have to be true of that property, and they are measured
   separately — run 20 found a property that passes the first and fails the
   second (P13468) and one that passes both only 34 times in 35 (P14527).
   **Wikidata must assert it** — that is what `is_mechanical` gates on. And its
   **value must be the source's person id** — that is what `verify_source.py`
   section B showed for P1307 (Parmelin: `PersonNumber` 1108 == P1307 1108) and
   what `verify_kantonsrat.py` section C asks for the cantonal candidates. A config joining on a property whose value has not
   been measured says so with `identifier_verified: false`, and then the run
   corroborates every identifier match against the item's own name and birth
   date, counts the ones it rejects, and emits nothing mechanically. That
   guard exists because an unverified identifier fails in the one direction a
   missing one cannot: two id spaces that overlap numerically match
   *exactly*, and match the wrong people.
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
