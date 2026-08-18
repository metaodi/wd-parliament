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

## ✅ What has been verified

The **`Verify assumptions`** workflow is a read-only diagnostic: it asks the
questions below against live parlament.ch, OpenParlData and Wikidata, writes
everything to the run summary, and changes nothing in the repository. It has
been dispatched 22 times since 2026-07-29, and the numbers in this section are
from **[run 22](https://github.com/metaodi/wd-parliament/actions/runs/30988422645)
(2026-08-05)** unless another run is named — that dispatch ran every check
against the code as it now stands.

| # | Question | Verdict |
| --- | --- | --- |
| 0 | Can the sitting members be read at all? | ✅ **246** of 254 rows, after filtering `NR`/`SR` — 200 + 46, both chambers' exact sizes |
| 0b | Does the service send a null date? | ✅ no: `1753-01-01`, mapped to `None` at the boundary. No sitting member carries a leaving date |
| 0c | Where may P580 come from? | ✅ `MemberCouncilHistory`, chained — **244 of 244** agree with an independent source. Raw `DateJoining` disagrees on 11, which is why it is not used |
| 1 | Is P1307 `MemberCouncil.PersonNumber`? | ✅ **CONFIRMED** — Parmelin, `PersonNumber=1108` against P1307 `1108` |
| 2 | One P39 per tenure, or one per term? | ✅ **tenure** — 97.2% of items carry exactly one statement for the seat |
| 3 | Do the configured Q-IDs resolve? | ✅ all 28 — two positions, 26 cantons, every one labelled `Kanton …` |
| 4 | Does the period overlap match who actually voted? | ✅ **zero** sitting voters unassigned across three roll-calls; 183 of 183 in the 52nd |
| 8 | Do the departed members' leaving dates hold up? | ✅ **CONFIRMED** in all four sections — **1,959 of 1,959** dates agree exactly |
| 9 | Do the personal-data checks have a source? | ✅ four of six properties federally, two of three cantonally |
| 6 | Could OpenParlData be the source instead? | 🔶 measured and viable; the *decision* is open — [see below](#6--should-the-source-be-openparldata-instead--the-decision-not-the-data) |
| 7 | Could this be pointed at a cantonal parliament? | 🔶 it already is, report-only; three things remain unverified — [see below](#7--the-cantonal-extension--three-unverified-things) |
| 5 | Has a QuickStatement been tried by hand? | ⬜ **no**, and no workflow can do it — [see below](#5--paste-one-quickstatement-by-hand--not-started-and-nothing-to-paste-today) |

⚠️ **One operational hazard, unfixed.** Runs 13 and 14 failed their first gating
check on a parlament.ch timeout reading the full `MemberCouncil` table — `!
MemberCouncil: The server returned a timeout error`. It is intermittent rather
than an outage (the same query succeeded later in both jobs), but `HttpClient`
cannot retry it: it arrives as an OData error document rather than an HTTP
status. Re-dispatch and it usually passes.

### The source read, and the two field-level traps inside it

**The council filter is `NR` / `SR`.** The first live run (2026-07-29) fetched
**zero** sitting members and published **2,234 confident and wrong** "this
member has left" suggestions, because `config/parliament.yaml` asked for `N` /
`S` — codes the service does not use. The distinct values are `''`, `BR`, `NR`
and `SR` (German, because the pipeline queries `Language=DE`; French rows say
`CN` / `CF`). Nothing reached QuickStatements — `is_mechanical` rejected all
2,234 — so the safety rule earned its keep while the report did not. Two changes
stop it recurring silently: `app.process` raises when the member fetch comes
back empty, and `diff.compute_suggestions` skips the reverse walk entirely when
there are no members, because "parlament.ch does not list this person" is not a
claim you can make when parlament.ch has told you nothing.

**"No date" is `1753-01-01`, not a null** — SQL Server's `datetime` minimum, on
every sitting member's `DateLeaving`. This was the dangerous one. Read literally
it says the member left in 1753, which makes `diff` raise `ADD_END_DATE` for the
whole chamber — and that kind **is** mechanical, so it would have rendered as
`P582|+1753-01-01T00:00:00Z/11` and become the first run to write wrong data
rather than merely report it. It also reverses every tenure interval, which
`period_overlap` correctly refuses, silently costing every P2937 qualifier.
`parliament.NULL_DATE` maps the sentinel (and anything at or below it) to `None`
at the mapping boundary, and the fixtures now carry it the way the service does.

**`DateJoining` is a mandate *segment* start, so P580 does not come from it.**
Philipp Bregy settled it: `MemberCouncil` gives him `2025-09-16` while his
`MemberCouncilHistory` carries an active row from `2023-12-04`, the 52nd
legislature's opening. `parliament.segments_from_rows` groups the history into
mandate segments per `(person, council)`, `tenure_start` walks back through
segments that are adjacent to within a day, and `Member.start_date` is
`tenure_start or date_joining` — the only start `diff` and `period_overlap` may
read. A real break stops the chain, so somebody who left and returned gets the
return. `app.process` fetches the history once and degrades to the raw field
rather than aborting.

`scripts/compare_tenure_dates.py` prints **two** verdicts off one join and they
are not interchangeable:

| | What it compares | Run 22 |
| --- | --- | --- |
| 1. the raw field | `DateJoining` against OpenParlData's latest term | **CONTRADICTED**, 11 of 244 — the finding that moved P580 off the field, kept as its regression check |
| 2. what ships | `Member.start_date` against the same chaining applied to OpenParlData's per-term rows | **CONFIRMED**, 244 of 244 (100%) |

**Verdict 2 is the one that licenses a bulk apply of P580**, and two sources
sharing neither key nor publisher agree on every sitting member. Do not
"simplify" it to reuse the latest-term comparison: a chained tenure against a
single term reports every long-serving member as a disagreement — run 11 scored
22 that way, and all 22 were Council of States members whose National Council
years had been chained across the chamber change. Seat rows are keyed
`(Q-ID, council)` for that reason. *A person is not a seat.*

### The identifier join

Wikidata's [P1307](https://www.wikidata.org/wiki/Property:P1307) "Swiss
parliament ID" holds `MemberCouncil.PersonNumber`, not `PersonIdCode`:
Parmelin's row reads `PersonNumber=1108, PersonIdCode=2621` against
[Q121160](https://www.wikidata.org/wiki/Q121160)'s P1307 = 1108.
`resolve.match_by_identifier` is comparing the right fields. The probe stays as
a regression check and reports one of three verdicts, of which only `CONFIRMED`
exits 0:

| Verdict | Meaning |
| --- | --- |
| `CONFIRMED` | `PersonNumber == P1307`. The join is sound. |
| `CONTRADICTED` | Either `PersonIdCode` matches instead (the message says which to switch to), or neither does. |
| `INCONCLUSIVE` | The person was not found, or the service could not be read. Reported differently, because an unreachable service is a connectivity problem and not a finding. |

The same probe showed that a former member appears in **both** `MemberCouncil`
and `MemberCouncilHistory`, and that rows come back in several languages at once
— which is why `get_members` pushes `Language=DE` down and why the chamber codes
in the config are the German ones.

### The statement model: one P39 per tenure

Getting this backwards means emitting hundreds of duplicate statements, the
worst failure available to this tool. Censused against live Wikidata on
2026-07-29 over the **3,043** items carrying both P1307 and a National Council
P39:

| | |
| --- | ---: |
| exactly one P39 statement for the seat | **2,959 (97.2%)** |
| one statement carrying ≥2 P2937 terms → **tenure** | **156** |
| one statement per term (`statements == terms` > 1) → *period* | 6 |
| two or more statements for the seat | 84 (2.8%) |
| carrying **no** P2937 at all | 2,719 (89.4%) |

`config/parliament.yaml` ships `statement_model: tenure`. This **contradicts**
[WikiProject every politician](https://www.wikidata.org/wiki/Wikidata:WikiProject_every_politician)'s
documented per-term convention; the data wins, because duplicates are what the
tool has to avoid. Re-run the census before ever flipping it back:

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

Two consequences the diff depends on. **P2937 is missing from ~89% of items**,
so populating the `terms:` map turns `ADD_TERM` into a bulk P2937 backfill
across most of the chamber — legitimate, but a decision. And **2.8% hold several
P39 statements for one seat** (they left and returned); QuickStatements matches
an existing statement by property + main value, which cannot tell those apart,
so `diff` stamps `ambiguous_statement` and `is_mechanical` refuses qualifier-only
commands for them.

### The configured Q-IDs

All 28 resolve: the two position items
([Q18510612](https://www.wikidata.org/wiki/Q18510612) National Council,
[Q18510613](https://www.wikidata.org/wiki/Q18510613) Council of States) and the
26 cantons, every one of which came back labelled `Kanton <name>` with *instance
of* `Kanton der Schweiz`. Nothing transposed. `--verify-config` prints each
Q-ID with the label and *instance of* Wikidata actually has, so a transposed one
is obvious; re-run it after any config change.

The `parl_groups`, `parties` and `terms` maps ship **empty on purpose** — a
wrong Q-ID there would be attached as a qualifier to real statements, and the
tool skips unknown values rather than guessing. While `terms` is empty no
`ADD_TERM` suggestion is made, and under `statement_model: period` no
qualifier-only QuickStatements would be emitted either.

### The legislative-period join

`--validate-periods` compares the `PersonNumber`s that actually voted in a
roll-call against the members the interval overlap assigned to that period. Run
22, on the three most recent periods, from votes it discovered itself
(`16344`, `23315`, `31148`):

| period | assigned | voted | still sitting | earlier mandate | **not assigned** |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 52nd | 246 | 200 | 183 | 0 | **0** |
| 51st | 160 | 200 | 126 | 12 | **0** |
| 50th | 86 | 199 | 72 | 13 | **0** |

The 52nd is the strongest single result: 183 people demonstrably voted and the
overlap had assigned every one of them. Three rules make that answer mean
anything, and all three are load-bearing:

- **only voters who are still sitting are compared.** The member list is today's
  ~246 people; every roll-call also contains people who have left, and scoring
  those as "not assigned" is an artefact that grows with the age of the vote.
- **a vote cast before the member's current tenure is an earlier mandate**, not
  a mismatch — recognised by the period ending before `Member.start_date`, a
  bare date comparison rather than another call into `period_overlap`, so a bug
  in the function under test cannot excuse itself. Run 11 scored those 25 people
  as failures before this rule existed; every one was a chamber switch, the same
  cause as comparison 2's false disagreements.
- **the tenure correction is applied first**, because the overlap reads
  `Member.start_date`.

### The departed members' leaving dates

The diff's second pass finds people Wikidata still records as sitting whom the
source does not list. `scripts/verify_departures.py` asks whether the dates it
prints could be *applied*, and run 22 answered every section:

| Section | Verdict (run 22) |
| --- | --- |
| **A. Reach** | **CONFIRMED** — 1,968 of 1,983 resolve into `MemberCouncilHistory`, and every one has a closed tenure, i.e. a date to suggest |
| **B. Identity** | **CONFIRMED** — 1,957 surnames match the item's label exactly, 11 after folding a spelling difference, **0** contradicting and **0** unsettled |
| **C. Leaving dates** | **CONFIRMED** — **1,959 of 1,959** comparable dates agree exactly; 6 are absent from OpenParlData and 3 Q-IDs are claimed by two person records and skipped |
| **D. Which statement** | **CONFIRMED** — 3 items hold several P39 for the seat and the existing `ambiguous_statement` rule already refuses them; no statement starts on the wrong date |

That is a stronger result than step 0c's 244 of 244, on a population fifteen
times the size and one **defined by Wikidata rather than by the source** — the
set nobody had looked at. Four things had to be fixed in the probe's own
arithmetic to get there, and every `CONTRADICTED` it ever returned turned out to
be its own:

- **the identity check cried wolf 29 times**, all the same person spelt
  differently (`Börlin`/`Boerlin`, `Vonderweid`/`von der Weid`). `fold_name`
  folds umlauts, accents, particles, married names and doubled letters, and
  reports them as a **third bucket** — counted and printed, never silently
  merged, because a check that stops showing its work has stopped checking.
- **an item's label is not the whole of what Wikidata says a person is called.**
  Aliases and the P1810 *subject named as* qualifier on the P1307 statement are
  read too, by a fourth bounded query (`WikidataClient.get_name_variants`), asked
  only for the people section B judges. The **strongest reading wins**, which is
  what makes the widening safe: an extra name can only move a row towards
  agreement, never manufacture a `CONTRADICTED`. It stays corroboration — an
  alias is asserted by whoever wrote the item — so which name settled a row is
  counted and printed rather than folded into the total. In run 22 that closed
  the last 5 near misses: 20 rows were settled by a name the label does not
  carry (15 aliases, 5 P1810), including `Johann Zünd` → `Zündt` via the alias
  `Johannes Zündt`.
- **the one leaving-date disagreement was a Q-ID two people claimed.** Run 17
  reported Alfred Gehrig, who left in 1971, against a leaving date of 2014,
  because **nothing makes OpenParlData's `wikidata_id` unique** and two person
  records naming one item pool their memberships. A Q-ID claimed by more than
  one record is now skipped and reported, never arbitrated — the same rule
  `resolve.match_by_identifier` applies to a P1307 claimed by two items.
  `compare_tenure_dates.py` has the identical join and now the identical skip.
- **statement ambiguity is excludable, and was being read as a veto.** `diff`
  only stamped `ambiguous_statement` on sitting members; it stamps departed ones
  too, which is right whatever happens to the gates.

**The "no P580 anywhere" anomaly is real data, not a broken read.** All 1,968
open statements carry no start date, but the control line says **1,845 of 3,828**
statements for these seats (48.2%) do carry one. It is a selection effect: a
statement with a P580 and no P582 is what a *sitting* member's looks like, and
sitting members are excluded from this population by construction. Keep the
control line — from inside the subset the two are indistinguishable.

What this does **not** do is remove the gates; see
[step 8](#8--the-departed-members-report-only-gates--licensed-not-removed).

### The personal-data columns, on both sources

The presence checks (P19, P1321, P106, P102, P856, P1971) are safe however this
comes out — a source with nothing to say produces no suggestion — but "safe" is
not "useful". `scripts/verify_person_data.py` measured both halves. **The two
sources answer different halves of the list**, which is why the enabled checks
are per-config.

Federally, over the 246 sitting members, and what each check suggests against
Wikidata today:

| Property | Column | Source coverage | Suggestions |
| --- | --- | ---: | ---: |
| P19 place of birth | `BirthPlace_City` + `BirthPlace_Canton` | 245 (99.6%) | **23** |
| P1321 place of origin | `Citizenship` | 244 (99.2%) | **82** |
| P102 member of party | `PartyName` | 246 (100%) | **0** |
| P1971 number of children | `NumberOfChildren` | 105 (42.7%) | **101** |
| P106 occupation | — **no such column** | — | — |
| P856 official website | — **no such column** | — | — |

206 suggestions in total, none of them mechanical. Every member matched an item
and every one was reached by the personal-data query, so the zeroes are answers
rather than gaps; P102's zero is the good kind, and the check is kept listed
because it is what would notice a newly elected member without one.
`config/parliament.yaml` therefore ships `person_data: [P19, P1321, P102,
P1971]`, and `parliament.OCCUPATION_FIELDS` / `WEBSITE_FIELDS` stay as the hook
a future column would be read through.

Cantonally it inverts: ZH `persons` carries `occupation_de` (180 of 180) and
`party_de` (180 of 180) but no birthplace, no Bürgerort and no children.
`website_personal` **exists and is empty for all 180** — INCONCLUSIVE, not
CONTRADICTED, and the check costs nothing if the source fills it in.
`config/kantonsrat-zh.yaml` ships `person_data: [P102, P106, P856]`.

Two candidate columns are deliberately **not** read, and both would put a wrong
statement on a real person: `Mandates` / `AdditionalMandate` /
`AdditionalActivity` are the register of interests rather than a living (not
P106), and `website_parliament_url_de` is the member's page on the chamber's
site rather than a website *of the person* (not P856). A zero in
`NumberOfChildren` is treated as no value: in a nullable integer column a zero
and an unstated value are the same shape.

**The probe found more wrong with the adapter than with the checks.** Printing
the source's real column list showed `openparldata.py` reading the party from
`party_name_de` and the birth date from `birthdate`; OpenParlData calls them
**`party_de`** and **`birthday`**, so every cantonal member had been coming out
with no party and no birth date since the adapter was written. Neither failure
is visible from inside the pipeline — an unmapped party makes no suggestion, and
a missing birth date silently downgrades the name fallback from "pick the one
born on the right day" to a bare label match. That is the argument for a probe
that prints the source's columns instead of trusting `.get()`.

### What the two other sources carry

Measured for the design questions that are still open, and unchanged by run 22.

**OpenParlData**, federally: a **body** is the *level* of parliament (`CHE`), and
the chambers are **groups** (`Nationalrat` 1663, `Ständerat` 1664) matched by
name equality, because `Präsidium des Nationalrates` and `Büro NR` are not the
chamber. Every seat membership is dated — 4,398 of 4,398 for the National
Council and 1,220 of 1,220 for the Council of States, confirmed a second time by
asking the API to do the same filtering with `exclude_null` — with real per-term
spans back to 1853, and the open-ended rows come to exactly **200** and **46**.
3,685 of 3,686 federal members carry a `wikidata_id` and 3,219 (87.3%) a party
Q-ID. The seat is reachable from the **group** but not from the person: walking
Gerhard Andrey returns 47 memberships, none of them his National Council seat.

**The Kantonsrat Zürich**: body `ZH`, group **5077** `Kantonsrat Zürich`, 913
memberships all of `type_harmonized='council_legislative'`, 912 with a
`begin_date`. The position item is **`Q21518678`** "Mitglied des Zürcher
Kantonsrat" — 270 holders, 167 of them current, 93% of the chamber. The 18
Wahlkreise are on the *person* records as `electoral_district_de` (numbered and
untidily spaced, so `_tidy` before keying) and sum to exactly 180 members.
Memberships are **one row per tenure**, not per term as federally (913 rows over
834 people, ~40–50 new rows per election), so a ZH body is `statement_model:
tenure` and its P2937 would have to come from interval overlap.

Three traps paid for along the way, each now enforced in `verify_kantonsrat.py`:

- **`Q19479543` is `Kategorie:Kantonsrat (Zürich, Person)`**, a Wikimedia
  category held by nobody. It shipped as the probe's default off a web search
  and would have claimed people hold a category. It was also silently
  contagious: asking the identifier question *through* it made every count in
  section C read 0, which looks like "no coverage" and meant "no such seat".
  That is why the reach query mentions no position at all. **Never reinstate it.**
- **the derived ranking's top hit is not the answer.** Among the 35 linked
  members the most-held position is the *National Council* (26 of 35) — the
  sample skews to people notable enough to have gone federal. Q21518678 is
  second at 12. Read the list; do not take the max.
- **counting open membership rows is not counting members.** 186 open rows
  against 180 seats was four future starts, a `Gast` and one row with no role.
  Filtering to `Mitglied` then gave 177, because a presiding member has **no
  `Mitglied` row** — their `Präsidium` / `1.` / `2. Vizepräsidium` row *is* their
  seat. 177 + 3 = 180. `DEFAULT_SEAT_ROLES` is therefore an allowlist of
  seat-holding roles, and what protects the count is the probe printing every
  role it saw and refusing to call a total that is not 180 `CONFIRMED`.

**How to query swissparlpy's OpenParlData backend**, learned the hard way. The
first two were reported as
[swissparlpy #52](https://github.com/metaodi/swissparlpy/issues/52) and are
fixed in **2.0.0**, which this project requires; the reasoning still applies:

- **pass `lang='de'`.** 1.0.0 hard-coded `lang='en'` with `lang_format='flat'`
  and the English columns are null, so a table could read as *empty* — `bodies`
  gave 0 rows by default and 1,405 with `lang='de'`. 2.0.0 sends no `lang`
  unless asked (2,411 rows under the defaults, still 0 for `lang='en'`), and the
  probes keep passing it: the chamber names they match are German, and a pinned
  language is what makes an answer reproducible across library versions.
- **mind the search scope and the casing.** 2.0.0 no longer forces
  `search_scope='all'`, so the API's defaults apply (`metadata`, `partial`);
  pass `search_scope='all'` for the full-text indexes. `exact` is
  **case-sensitive** in practice — `'Nationalrat'` returns 1 group,
  `'nationalrat'` returns 0 — and `partial` is ILIKE substring matching, which
  is why `lastname=Andrey` alone matched *Pascal* Andrey and made one run
  measure the wrong person. Narrow with field filters instead (`body_key=CHE`
  cuts 8,817 groups to 1,041).
- **`limit` is a page size, not a cap.** The response iterator pages to
  exhaustion; `len()` is `meta.total_records` off the first page, so a count
  costs one request, and slicing loads only as far as the slice reaches.
- **unrecognised parameters are logged and sent anyway**, so a mistyped filter
  silently does nothing — check the counts look plausible.

### Re-running any of this

```bash
uv run python scripts/verify_source.py            # steps 0, 1
uv run python -m wd_parliament --verify-config    # step 3
uv run python scripts/compare_tenure_dates.py     # step 0c
uv run python -m wd_parliament --validate-periods # step 4
uv run python scripts/verify_departures.py        # step 8
uv run python scripts/verify_kantonsrat.py        # step 7
uv run python scripts/verify_person_data.py       # step 9
uv run python scripts/verify_openparldata.py      # step 6 — by hand only
```

All but the last are wired into `Verify assumptions`; only the first two gate
it. `verify_openparldata.py` was unwired once every one of its verdicts was
settled — it stays in the repo for the day the source question is reopened, and
its section E is what to run after a `swissparlpy` upgrade.

---

## ⚠️ Open verification steps

Four remain, and only the first is a measurement nobody has taken: step 5 blocks
the federal pipeline, step 6 is a decision rather than a measurement, step 7 is
the cantonal extension, and step 8 is a change the evidence licenses but nobody
has made.

### 5. ⬜ Paste one QuickStatement by hand — not started, and nothing to paste today

Before any bulk apply, a person has to paste a single line into QuickStatements
and confirm the statement lands with its qualifiers and reference intact. This
is the one step no workflow can carry out: it needs a Wikidata account, and its
whole point is that a human looks at what actually landed.

⚠️ **It has not been done, and today it cannot be**: the current run emits
**0 mechanical commands of 2,198 suggestions**. The federal seat data is in
good shape — 246 of 246 members matched on P1307, no member missing a P39, no
open statement to close — so what is left is 1,983 *departed* findings
(report-only by construction), 206 personal-data findings (never mechanical), 5
source link conflicts and 4 missing-identifier findings. A mechanical line will
appear the next time a member joins or leaves, or the day the `terms:` map is
populated.

That is also why this step comes **first**: the two changes that would produce
mechanical output at volume — filling in `terms:`, or removing the departed
members' gates — would each produce it in the thousands.

### 6. 🔶 Should the source be OpenParlData instead? — the decision, not the data

Every measurement is in (above); what is open is a choice. Where the two stand:

| | parlament.ch OData | OpenParlData |
| --- | --- | --- |
| tenure start | `MemberCouncilHistory` + `tenure_start` chaining | per-term `begin_date`, directly |
| P2937 term qualifier | constructed by interval overlap | implied by the per-term rows |
| historic members | same-shaped table, plus `IdPredecessor` chains | 5,618 rows back to 1853 |
| party / group Q-IDs | not carried; the config maps ship empty | 87.3% carry a party Q-ID |
| identifier join | `PersonNumber` ↔ P1307, confirmed | `wikidata_id` on 3,685 of 3,686 — but *asserted by a third party*, not by Wikidata |

The last row is what keeps the OData read where it is. `is_mechanical` gates on
`QID_FROM_IDENTIFIER`, meaning **Wikidata** asserted the identifier that
established the match; sourcing the Q-ID from OpenParlData would be a different
class of claim wearing the same provenance flag, and would need its own
`QID_FROM_*` constant and its own decision.

**And the gap this comparison was meant to expose has closed.** The two sources'
tenure starts agree on all 244 joinable sitting members, so the argument for
switching is no longer "the dates are better" — they are the same dates. What is
left is the shape of the work: OpenParlData hands you per-term rows, so P2937
falls out of the data instead of being constructed, and 3,686 historic members
come with it. Against that, the OData read is written, tested and verified end
to end, and the party/group Q-IDs can be taken from OpenParlData *without*
changing the source at all — which is the cheap half of the win, and is what the
`enrich:` block already does.

**P14527 adds nobody federally.** It reaches 3,027 seat holders against P1307's
3,042, and **every one of them already carries P1307** — the union is exactly
P1307's own count, so a second join path would match nobody new. Keep the P1307
join whatever happens to the source. (This inverts cantonally; see step 7.)

A reasonable reading of the evidence: **keep OData as the source, take
OpenParlData for enrichment**, and revisit if the historic-members extension
gets built, where per-term rows back to 1853 are worth more than they are today.

### 7. 🔶 The cantonal extension — three unverified things

The adapter exists and runs: `config/kantonsrat-zh.yaml` plus
`src/wd_parliament/openparldata.py` drive the same pipeline against the same
dataclasses, and `period_overlap`, `diff` and `quickstatements` cannot tell
which source produced a `Member`.

```bash
uv run python -m wd_parliament --config config/kantonsrat-zh.yaml \
  --reports-dir reports/kantonsrat-zh --docs-dir docs/kantonsrat-zh
```

It ships **report-only** (`quickstatements: false`) *and*
`identifier_verified: false`, which are two different gates: the first is an
operator switch, the second is a claim about the identifier's value that refuses
every command on its own. Three things stand between it and writing anything.

**1. The join's value is measured at 34 of 35, not 35 of 35.** P14527 identifies
a person *record*, not a person: OpenParlData holds one record per person per
body, so `Q131948095` carries `1411` where this body's record is `17436`. It
misfires on exactly the members who also sat elsewhere — the federal bias in
miniature — which is why it is out of `models.VERIFIED_IDENTIFIER_PROPERTIES`.
Under `identifier_verified: false` the run corroborates every identifier match
against the item's own name and birth date, records and counts the rejects,
stamps every suggestion `identifier_unverified`, and emits nothing. The reason
is the one failure an exact join has that a missing one does not: **two id
spaces that overlap numerically match confidently and match the wrong people**,
writing correct data onto somebody else's item, which no later run can detect.
When section C reads **CONFIRMED, 35 of 35**, put P14527 back into
`VERIFIED_IDENTIFIER_PROPERTIES`, flip the flag, and only then consider
QuickStatements — in that order, each step in the same commit as the output that
licenses it.

**2. P13468 is the right property and this source cannot supply it.**
[P13468](https://www.wikidata.org/wiki/Property:P13468) "Zurich Kantonsrat and
Regierungsrat member ID" is the canton's own member id — the cantonal analogue
of P1307 — and Wikidata carries it for 28 of the 35 linked ZH people. But **0 of
those 28 values equal OpenParlData's person id** (Ruth Genner: 22518 against
9532), and they appear in **no column** of the person record:

```
Does a P13468 value equal OpenParlData's person id?
  compared: 35 | value == person id: 0 | value != person id: 28 | no P13468: 7
    Q117716: P13468='22518' but person id=9532     (Ruth Genner)
    Q123979: P13468='22382' but person id=18999    (Ueli Maurer)

If P13468 is not the person id, which column is it?
  people compared: 28
  (no column of the person record carries these values)
```

**An identifier needs a value on both sides.** `config.load_config` therefore
**refuses** `identifier_property: P13468` with `source: openparldata` rather
than documenting the trap. Supplying it properly means reading the canton's own
dataset — opendata.swiss publishes *Kantonsratsmitglieder Kanton Zürich ab 1803*
(entry/exit dates, party, Wahlkreis: the direct `MemberCouncil` +
`MemberCouncilHistory` analogue) and the Kantonsrat's business system has an XML
web service. That is the *authoritative* source in the sense `diff` relies on;
OpenParlData is a harmonised aggregator of it. Note that "not the person id" and
"nowhere in the source" are different findings, and only the second forbids the
join.

The two identifiers are why the report names **both**: P14527 is the join and
carries a number, P13468 is reported as missing and deliberately carries none.
See [A member has two identifiers](#a-member-has-two-identifiers-and-only-one-of-them-can-be-the-join).

**3. Both Q-ID maps are empty, and neither can be filled from usage.** P768: the
source has all 18 Wahlkreise and **0 resolve** by exact label; the three values
already in use on the seat are `Kreis 4`, `Kreis 5` and `Kreis 11` — *city of
Zürich quarters* rather than cantonal electoral districts, so too few to be a
convention and not the right kind of thing. P2937: the qualifier appears on **no
statement** for the seat at all — INCONCLUSIVE, nothing to derive. Both maps
stay empty, which blocks nothing: an unmapped value makes no suggestion, while a
wrong one becomes a qualifier on real statements. Resolving the 18 district
items is a Wikidata-side job for a human.

**And a coverage gap that is not a fault.** Only **35 of 834** ZH person records
(4.2%, against 3,685 of 3,686 federally) carry a `wikidata_id` at all, and
P14527 matched **0 of the 180 sitting members** on the first real run — those 35
are people OpenParlData had *already linked*, which skews hard towards members
notable enough to have gone federal. **A coverage rate measured over a linked
sample is not a coverage rate over the chamber.** So the ZH report is dominated
by `NO_WIKIDATA_ITEM`: a worklist for *creating* items rather than for fixing
statements.

Three rules hold for any further cantonal work. **Never join a cantonal seat on
P1307** — it is the federal service, so it reaches only members who also sat in
Bern, and the people it misses are exactly those who never went federal: a bias
that reads as coverage rather than as a bug. **Never let OpenParlData's
`wikidata_id` inherit the P1307 gate** — a Q-ID a third party asserts *about*
Wikidata is a different class of claim. And **the Regierungsrat is not the
Kantonsrat**: `is_kantonsrat` requires a group name to *equal* one of the
chamber's spellings, because the cantonal executive is seven members and five
letters away.

Two things do **not** transfer from the federal runs, so nothing here inherits
their verdicts: step 4's roll-call cross-check has no cantonal equivalent unless
the canton publishes votes, and "P580 is safe to apply in bulk, 244 of 244" was
measured on federal members.

```bash
# checks Q21518678 and re-derives the candidates from the members
uv run python scripts/verify_kantonsrat.py

# another canton: body, seat count and seat roles are all parameters
uv run python scripts/verify_kantonsrat.py \
  --body-key BE --expect-seats 160 --position ''
```

### 8. 🔶 The departed members' report-only gates — licensed, not removed

Run 22 returned `CONFIRMED` in all four sections (above), which is the evidence
those gates were waiting for. Removing them is still a **deliberate act**, and
it has not been taken.

What the gates are: `diff._departed_suggestion` sets no `qid_source` and puts no
`position` in the payload, so `is_mechanical` refuses these suggestions twice
over. Remove both and the next run turns ~1,965 report-only findings into
mechanical P582 commands in one file — the largest single edit this tool could
make, and exactly the class of bulk edit the rest of this README is about not
making by accident.

So the order is: [step 5](#5--paste-one-quickstatement-by-hand--not-started-and-nothing-to-paste-today)
first, then remove the gates in a commit that says which run licensed it. Note
also that `INCONCLUSIVE` is the *expected* answer from this probe on tidy data —
its population is however many open memberships Wikidata has for people who have
gone, and a small one is good news about the data and no news about the
question. That is why it must never be wired into a gate.

### 10. ✅ Can the canton's own Gever supply P13468? — *no: 130 of 130 values appear in no field. It is a fine data source and not an identifier source*

Step 7 ended with P13468 as "the right property and the wrong join": 28 of 35
linked ZH items carry it, **0 of 28** values are OpenParlData's person id, and
run 20 found them in **no column** of the person record. What that step could
not say is where the values *do* live, beyond "the canton's own dataset".

[`swissparlpy` PR #51](https://github.com/metaodi/swissparlpy/pull/51) adds a
**Gever backend** — the CMI CDWS API behind `kantonsrat.zh.ch`
(`parlzhcdws.cmicloud.ch`, instance `canton_zurich`), with a `MITGLIEDER`
index beside `BEHOERDEN`, `PARTEIEN` and `WAHLKREISE`. It is the first
candidate source for the Kantonsrat that belongs to the *canton* rather than to
an aggregator, which makes it the obvious place to look — and exactly the
reason to measure rather than assume. **Whose system a service is says nothing
about which register's numbers it publishes.** P13468's formatter URL points at
`wahlen.zh.ch/krdaten_staatsarchiv/`, the Staatsarchiv's database of Kantonsrat
and Regierungsrat members; the Gever is the chamber's business-management
system. Two systems of one canton are still two id spaces, and reading one as
the other is the failure `identifier_verified` exists to stop.

`scripts/verify_gever.py` asks it, and the shape of the question is run 20's:

```bash
uv run python scripts/verify_gever.py
uv run python scripts/verify_gever.py --limit 200 --verbose
```

| Section | Question |
| --- | --- |
| A | does the `MITGLIEDER` index answer, with how many rows, and what can be *queried* on |
| B | which fields the service really returns — printed, not guessed at, for run 19's reason |
| C | every item Wikidata gives P13468, matched by name, its value compared against **every field** of that member's rows |
| D | what else it carries (P569, P106, P102, P768, P580/P582) and the distinct `Gremium` values, so the Kantonsrat's own seat row is discovered rather than named in advance |

| Verdict | Meaning |
| --- | --- |
| `CONFIRMED` | a field carries the P13468 value for every person compared. That is the source the property was waiting on, and a Gever-sourced config could join on it — after measuring coverage over the *chamber*, since a rate measured over the people Wikidata already links is not one |
| `CONTRADICTED` | the value appears in no field. Gever settles the way OpenParlData did, and `config.load_config`'s refusal stands for a Gever config too |
| `INCONCLUSIVE` | nobody could be matched — a fact about the overlap between the two lists, not about the source |

Section C asks a second question that survives the first one's answer: **is the
record key a person or a row?** The index holds one row per person per
*Gremium* (a faction seat, a commission seat), which is how `goifer` — the
client PR #51 is adapted from — queries it, so the row key necessarily varies
within a person. If *nothing* on the record is stable per person then the
source has no person-level key at all, which is a harder obstacle than a
missing property: no Wikidata property holds a Gever GUID either way, so a
config reading it would have nothing to join on but names.

#### The answer: run 23 (2026-08-05), CONTRADICTED at 130 of 130

```
items carrying P13468: 794
  usable names: 778; dropped as ambiguous: 16
  matched to a Gever member: 130; not in this index: 648
  fields carrying the value: none
    ackermann pia:  P13468='22236' appears in no field
    ackermann ruth: P13468='22145' appears in no field
```

**Gever does not publish P13468.** Not in a field under another name, not
nested somewhere unexpected — the probe compared each value against *every*
field of *every* row belonging to that person, and 130 of 130 came back empty.
Being the canton's own system is not the same as publishing the canton's own
member id: P13468's register is the **Staatsarchiv's** KR-Daten database, and
the Gever is the chamber's business-management system. Two systems of one
canton, two id spaces. `config.load_config`'s refusal of
`identifier_property: P13468` therefore stands for a Gever-sourced config
exactly as it does for an OpenParlData-sourced one, and the way to that
property is still the Staatsarchiv's own dataset.

**But as a *data* source for the Kantonsrat it is the best thing measured so
far** — better than OpenParlData on every axis except the identifier. Over all
3,862 rows:

| Property | Column | Coverage |
| --- | --- | ---: |
| P580 / P582 start & end | `dauer_start` / `dauer_end` | **3,862 / 3,862** |
| P102 member of party | `…parteizugehoerigkeit_kurzname` / `_name` | 3,735 |
| P768 electoral district | `person_kontakt_wahlkreis` | 3,716 |
| P106 occupation | `person_kontakt_beruf` | 3,596 |
| P569 date of birth | `person_kontakt_geburtsjahr` | 3,818 — **a year, not a date** |

`Kantonsrat` is the largest of 25 Gremien at **986 rows** (against
OpenParlData's 913 ZH memberships), so the seat itself is there beside the
commissions and factions. What it cannot offer is a *join*: no Wikidata
property holds a Gever GUID, so a Gever-sourced config would be name-matched
throughout — `QID_FROM_NAME`, which `is_mechanical` already refuses.

**And the key question needed a second question.** Run 23 found 14 of 748
multi-row names carrying two `person_kontakt_obj_guid`s, and the probe called
all 14 a failure of the key. Two readings fit that shape and they are
opposites: one human recorded twice, or **two humans who share a name** —
entirely ordinary in a file spanning 1991 to today (959 distinct person GUIDs).
`classify_row_key` now separates them by asking the birth year: disagreeing
years mean namesakes and the key is fine, agreeing years mean a genuinely split
person, and a missing year on either side is counted as **undecided** rather
than assigned to whichever reading is convenient.

**Run 24 split the 14 into 9 namesakes, 3 split people and 2 undecided** — and
then showed that even that was too generous. Two of its nine read:

```
brunner roland: 9 row(s), 2 distinct person_kontakt_obj_guid
                — namesakes (person_kontakt_geburtsjahr 1, 1952)
```

**`1` is not a birth year.** It is a placeholder that reads as data — the
cantonal cousin of `1753-01-01` in `DateLeaving` — and taking it as a
disagreement turns "we cannot tell" into "definitely two different people":
the strongest reading, from the weakest evidence, in the direction that
exonerates the key. `plausible_year` now requires a four-digit year from 1850
to the current one, and anything else counts as *absence*, which lands in the
undecided bucket where it belongs. Re-measured counts are pending the next
dispatch; the verdict itself does not turn on them, since **3 genuinely split
people are enough for CONTRADICTED on their own** — the row key `obj_guid`
answers the same way at 737 of 748, as a row key should.

So: `person_kontakt_obj_guid` is *nearly* a person key and not one. An adapter
grouping rows by it would silently split a handful of real people, and one
grouping by name would silently merge the namesakes. Neither is fatal for a
report-only source — but both are reasons a Gever-sourced config would need its
own duplicate handling rather than inheriting `resolve`'s.

#### What run 22 (2026-08-05) measured first

Sections A and B answered; **section C crashed on a bug in the probe**. What
that run established:

- **the index answers, and it is the whole history**: `q=seq>0` returns
  **3,862 rows**, with `dauer_start` as far back as 1991 and `dauer_end`
  `9999-12-31` for the open ones. 55 columns.
- **the Kantonsrat's own seat is in there** — `gremium` = `KR`,
  `gremiumname` = `Kantonsrat`, alongside the commissions and factions. The
  seat row does not have to be inferred from a faction.
- **the record is nested, not flat**: the person sits under `Person/Kontakt`
  (`person_kontakt_name`, `person_kontakt_beruf`,
  `person_kontakt_wahlkreis`, `person_kontakt_parteizugehoerigkeiten_…`),
  while the mandate's own fields (`dauer_*`, `funktion`, `gremium`) are at the
  top level.
- **a record carries two GUIDs.** Its own `OBJ_GUID` is the *membership row*
  (3,862 distinct over 3,862 rows); `person_kontakt_obj_guid` is the *person*
  (959 distinct, and the stem of `foto_id`). So the source does have a
  person-level key — a fact the first draft of this step guessed the other way.
- **it is a birth *year*, not a birth date**: `person_kontakt_geburtsjahr`
  (3,818 of 3,862) reads `'1936'`. Year precision is a different claim from a
  P569 date, and it cannot tell two namesakes apart the way the name
  fallback's birth-date check does.
- coverage of the rest, over all 3,862 rows: `person_kontakt_beruf` 3,596,
  `person_kontakt_geschlecht` 3,658, `dauer_start`/`dauer_end` 3,862.

**And the probe was wrong twice, in one instructive way and one dull one.**
The instructive one: its field names (`name`, `beruf`, `wahlkreis`) were copied
from `goifer`'s *normalised* example output while the probe reads raw XML, so
every one of them missed and section A reported "3,862 rows with no name" —
a line that reads as a fact about the source and was a fact about the probe.
That is the module's own warning arriving from the other side, and
`resolve_column` is the guard: candidates now fall back to the leaf segment, so
a path that gains or loses a level of nesting still resolves, and the report
prints which column each one reached. The dull one: `WikidataClient` takes no
`language` argument, which ended section C in a `TypeError`.

Wired into `Verify assumptions` as section 9; it **never gates**, for the
plainest reason in that file: no config here names this service.

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
  [run 20](#7--the-cantonal-extension--three-unverified-things):
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
  compared, the same rule the tool applies to unmapped constituencies and
  parties.

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

These suggestions are **report-only by construction**. `is_mechanical` refuses
them twice over: the member carries no `qid_source` (the identifier came from
Wikidata, not from a resolved member) and the payload carries no `position`.
`scripts/verify_departures.py` measures the historic table for departed members
the way step 1 measured `PersonNumber` for sitting ones, and run 22 returned
`CONFIRMED` on all four of its sections — so the gates now stand on a decision
rather than on missing evidence, and
[step 8](#8--the-departed-members-report-only-gates--licensed-not-removed) is
where removing them is worked out. Do not remove either one to "unlock" them:
a P582 backfill across everyone Wikidata records as sitting is exactly the
class of bulk edit the rest of this README is about not making by accident.

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
- `reports/<N|S>-<slug>.md` — per-chamber TODO, grouped by constituency (or
  by parliamentary group; see `group_by`).
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
  only**, `contents: read`. Runs the seven probes listed under
  [Re-running any of this](#re-running-any-of-this) and writes every result to
  the run summary. Generates nothing, commits nothing, publishes nothing. Run
  this **before** the update workflow. Every step runs even when an earlier one
  fails, so one dispatch answers all of the questions; only the first two —
  `verify_source.py` and `--verify-config` — decide the job's pass/fail.
- [`Update parliament TODO`](.github/workflows/update.yml) — weekly (Mon 06:00
  UTC) and on demand; regenerates the reports and commits them back
  (`contents: write`). It takes the config as a dispatch parameter and runs one
  parliament per run; the scheduled run does both, serialised, so a broken
  cantonal source can never stop the federal report being published. The federal
  outputs keep the top level (Pages serves `docs/index.html` and that URL should
  not move) and every other parliament gets a subdirectory named after its
  config.
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
hand-edited. Regenerate them by running the tool, not by editing the files.

## License

MIT — see [LICENSE](LICENSE).
