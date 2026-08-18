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

**`wikidata_id` is not unique, and both scripts that join through it assumed
it was.** OpenParlData's person → `wikidata_id` → Q-ID join has no uniqueness
constraint: two person records naming one item pool their memberships under a
single key, and whatever reads "the latest row" then reads the *other* person's.
Run 17 reported Alfred Gehrig — who left in 1971 — against a leaving date of
2014 because of it. `verify_departures` and `compare_tenure_dates` both now
**skip** a Q-ID claimed by more than one record and say how many they skipped,
the same rule `resolve.match_by_identifier` applies to a P1307 claimed by two
items. Never arbitrate one: a source contradicting itself about who somebody is
cannot be resolved by picking a side. Any new join through that field needs the
same guard.

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
  Zürich has no P1307, so P14527 is the cantonal join despite reaching 0 of the
  180 sitting members — it is the only identifier OpenParlData can supply a
  value for. `scripts/verify_kantonsrat.py` measures it, P13468 and P1307 on
  every dispatch, values as well as coverage.

Runs 13 and 14 (2026-07-30) measured the cantonal source and it is sound: body
`ZH`, group **5077** `Kantonsrat Zürich`, 913 memberships, 912 with a
`begin_date`, `electoral_district_de/fr/it` on the *person* records. **The
position is `Q21518678`** "Mitglied des Zürcher Kantonsrat".

**The join is `P14527`, and `P13468` is the property that proves why coverage
is not a join.** P13468 "Zurich Kantonsrat and Regierungsrat member ID" is the
canton's own member id — the cantonal analogue of P1307, carried by 28 of the
35 linked ZH items. Run 20 (2026-08-04) then compared its *values*: **0 of 28**
equal OpenParlData's person id (Ruth Genner: 22518 against 9532), and the
column report found them in **no column** of the person record. An identifier
needs a value on **both** sides; P13468's lives in the canton's own dataset,
which this tool does not read. `config.load_config` **refuses**
`identifier_property: P13468` with `source: openparldata` — measured and
falsified, so it is refused rather than documented. Never re-pair them; the way
to P13468 is a source that supplies it.

Two more findings from the same run, both load-bearing:

- **P14527 identifies a person *record*, not a person.** 34 of 35 values are
  the ZH person id; `Q131948095` carries 1411 where this body's record is
  17436, because OpenParlData holds one record per person **per body**. So it
  misfires on exactly the members who also sat elsewhere — the federal bias in
  miniature — and it is out of `VERIFIED_IDENTIFIER_PROPERTIES`, which now
  holds P1307 alone. **Membership costs a measurement; losing it costs one
  disagreement.**
- **P14527 still ships as the join** because it is the only identifier this
  source can supply a value for, even though it matched **0 of the 180 sitting
  members** on the first real run — the 35 linked people are mostly members
  notable enough to have gone federal. **A coverage rate measured over a linked
  sample is not a coverage rate over the chamber**, the same sampling trap that
  put the National Council at the top of the position ranking one level up.

**The canton's Gever is the next candidate for P13468's value, and it is
unmeasured** (README step 10). `swissparlpy` PR #51 adds a backend for the CMI
CDWS API behind `kantonsrat.zh.ch` — `parlzhcdws.cmicloud.ch`, instance
`canton_zurich`, with a `MITGLIEDER` index. `scripts/verify_gever.py` asks
whether it publishes P13468, and it exists rather than an assumption because
**whose system a service is says nothing about which register's numbers it
publishes**: P13468's values live in the *Staatsarchiv's* KR-Daten database
(`wahlen.zh.ch/krdaten_staatsarchiv/`), and the Gever is the chamber's
business-management system. Two systems of one canton are two id spaces. Three
things about the probe that are deliberate: it reads the **raw CDWS XML**
rather than going through `GeverBackend`, because a column report taken through
a normaliser measures the normaliser; it searches **every field** for the
value, since "kept elsewhere" and "never heard of it" mean opposite things; and
it asks a second question that survives the first one's answer — the index
holds one row per person per *Gremium*, so the row key necessarily varies
within a person and only a field that does *not* could ever be joined on.
Nothing about it gates: no config here names that service.

**Run 23 (2026-08-05) answered it: CONTRADICTED, 130 of 130.** Every P13468
value Wikidata holds, compared against every field of every row belonging to
that person, appears **nowhere** in Gever. So the canton's own business system
does not publish the canton's own member id, `config.load_config`'s refusal of
`identifier_property: P13468` stands for a Gever-sourced config too, and the
way to that property is still the Staatsarchiv's KR-Daten dataset. **Never
re-open this on the grounds that Gever belongs to the canton — that is the
inference the run falsified.** The same run makes Gever the richest *data*
source measured for the Kantonsrat (3,862 rows back to 1991; `dauer_start` /
`dauer_end` 3,862 of 3,862, party 3,735, district 3,716, occupation 3,596,
`Kantonsrat` 986 rows against OpenParlData's 913) — a separate finding, and one
that still cannot supply a *join*: no Wikidata property holds a Gever GUID, so
a Gever-sourced config would be name-matched throughout.

**A name is not a person, and `classify_row_key` learned it the way everything
else here did.** Run 23 found 14 of 748 multi-row names carrying two
`person_kontakt_obj_guid`s and the probe called all 14 a failure of the key.
Two opposite readings fit: one human recorded twice, or **two humans sharing a
name** — ordinary in a file spanning 35 years. It now asks the birth year:
disagreeing years mean namesakes and the key holds, agreeing years mean a split
person, and a missing year is **undecided** rather than assigned to whichever
reading is convenient. Do not collapse the third bucket into either of the
others.

Run 24 then split those 14 into 9 / 3 / 2 and showed the discriminator needed
one of its own: two "namesakes" rested on a `person_kontakt_geburtsjahr` of
**`'1'`**. A placeholder that reads as data is `1753-01-01` again, one canton
down — and reading it as a year takes the *strongest* conclusion from the
weakest evidence, in the direction that lets the key off. `plausible_year`
requires four digits in 1850..today and everything else is absence. **The
verdict does not turn on the recount**: 3 genuinely split people are enough for
CONTRADICTED, so `person_kontakt_obj_guid` is *nearly* a person key and not
one, and a Gever-sourced config would need its own duplicate handling rather
than inheriting `resolve`'s.

Run 22 (2026-08-05) answered the first two sections and **crashed on the
third**, and the crash is the more useful half. The index is real and rich —
3,862 rows back to 1991, 55 columns, the Kantonsrat's own seat among the
Gremien (`KR` / `Kantonsrat`), and **two GUIDs per record**: the row's
`OBJ_GUID` is the membership and `person_kontakt_obj_guid` is the person, so
the source does have a person-level key. But **the probe's field names were
copied from `goifer`'s normalised output while the probe reads raw XML**, and
the real record nests the person under `Person/Kontakt` — so every candidate
missed and section A printed "3,862 rows with no name", a line that reads as a
fact about the source and was a fact about the probe. `resolve_column` is the
guard: a candidate falls back to its **leaf segment**, shallowest match wins
(`name` must reach the person, not `…behoerdenmandat_name`, whose value is a
Gremium), and an unmatched leaf still resolves to `None` so "no such column"
stays sayable. Never widen it to substring matching — that would take away the
only answer section D exists to give. Two more things measured there: it is a
birth **year** (`person_kontakt_geburtsjahr`, `'1936'`), not a date, so it is
not a P569 value and cannot corroborate a name match; and the fixtures are now
built to the *service's* column list rather than a client's, which is the only
kind that could have caught this.

**A parliament has two identifiers and only one of them can be the join, so the
other has to be *reported* or it is never recorded at all.** Federally that is
P1307 (the join) beside P14527; cantonally P14527 (the join) beside P13468.
`config.identifiers` lists both and `diff._identifier_suggestions` raises one of
two kinds per property, differing in exactly one thing — whether the run has the
**value**. `ADD_IDENTIFIER` (priority 1) is the join property, whose value *is*
the source's person id. `MISSING_IDENTIFIER` (priority 5) is the other one, and
it deliberately offers **no number**: no source this pipeline reads publishes
those values (run 20 searched every column for P13468), and a number from the
wrong id space is the exact failure `identifier_verified` guards against.
Never "improve" it by supplying one from `enrich` — that module produces no
values by construction, and widening it is a decision, not a tidy-up. Three
rules hold this together:

- **the URL template belongs to the property** (`models.IDENTIFIER_PROPERTIES`),
  because the number printed beside a member is one particular property's value.
  Linking it through another property's template sends the reader to a page that
  cannot resolve it — which is what the ZH report did, an OpenParlData person id
  (P14527) linked to the Kantonsrat's member list. `biography_url` now defaults
  to the **join property's** record page; a config that overrides it owns the
  same correspondence.
- **`WikidataPerson.identifiers` must never reach `parliament_id`.** That field
  is read as "the source's person id" by `resolve.index_by_identifier`, by
  `_identifier_collisions` and by the reverse walk's bridge back to the source;
  another register's number landing there would be read as one.
- **`get_identifier_values` is global, not bounded by the seat**, because
  absence is the finding: a population narrowed to seat holders would report
  every item outside it as missing the property. It widens `people` with items
  that carry only that identifier — they hold no statements and no
  `parliament_id`, so every pass that walks by either ignores them.

**Provenance and value are two separate measurements**, and the config records
the second with `identifier_verified: false`. That flag is load-bearing, not
documentation:
`resolve.corroborates` then checks every identifier match against the item's own
name and birth date, `Member.identifier_mismatch_qids` records the rejects,
`report` prints the count, `diff` stamps `identifier_unverified` on every
suggestion and `is_mechanical` refuses it. The reason is the one failure an
exact join has that a missing one does not: **two id spaces that overlap
numerically match confidently and match the wrong people** — correct data
written onto somebody else's item, which no later run can detect. Never set
`identifier_verified: true` for a property outside
`models.VERIFIED_IDENTIFIER_PROPERTIES`; `config.load_config` refuses it, and
the way through is `verify_kantonsrat.py` section C reading CONFIRMED — 35 of
35, not 34 — first.
Section C also reports **which person column** does carry the values when the
person id does not — "the source keeps it elsewhere" and "the source has never
heard of it" mean opposite things for the config, and only the second forbids
the join outright.

Three traps paid for along the way, all now enforced in
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

**The ZH Wahlkreise now have Wikidata items** (class `Q141021240`), and
`config/kantonsrat-zh-krdaten.yaml` maps all 18 — derived from the class and
aligned on **the place both sides name**, never on a number. Run 31 aligned on
the number and was five-sixths wrong: the digits in an item's label are city
*quarter* numbers, so `Wahlkreis Stadt Zürich 3+9` is the register's **2nd**
district and keying on the 3 pushed `…7+8` onto `7. Wahlkreis (Dietikon)`,
which is not in the city. Filling the map also exposed that
`Config.constituency_qid` (then `canton_qid`, renamed once the config's
`cantons:`/`group_by: canton` keys became `constituencies:`/`group_by:
constituency` to stop implying every parliament this tool reads is
cantonal) upper-cased its lookup — right for `ZH`, and matching nothing for
a district name, so all 18 entries would have been silently dead. It folds
case and collapses whitespace on both sides now.

The history below is why the derivation is not shortcut:

**Before the items existed, the Bezirke were the tempting wrong answer.**
Run 30 (2026-08-11) searched for each of the register's 18 districts and every
candidate was the *place* it is named after — the municipality, the Bezirk, and
once a `Meilenstein`. Two returned nothing at all, and those two are exactly
the ones whose names are not a municipality (`Winterthur Stadt`, `Winterthur
Land`), which is the tell. **Never map a Wahlkreis to a Bezirk item**: the
register's own rows disprove the pairing without outside knowledge — six
Wahlkreise cover the city of Zürich and the city is one Bezirk. Twelve of the
eighteen share a name with a Bezirk, which is what makes it plausible rather
than obviously wrong, and it would put a false P768 on fifteen people at once.
Filling `constituencies:` for ZH needs items *created* on Wikidata first; that is a
modelling decision, not a lookup. The source side is otherwise ready —
`wahlkreis` is filled on 6,767 of 6,767 Einsitze and `ADD_QUALIFIER` is
mechanical, so the map is the only thing standing between here and ~180 P768
edits.

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
- **`get_link_conflicts` reports a data error in the *source*.** Two person
  records naming one `wikidata_id` is one item claimed as two people — the
  mirror image of `DUPLICATE_IDENTIFIER`, and the only finding in the report
  that is **not** repaired on Wikidata. Read only for collisions, never joined
  on: a Q-ID a third party asserts *about* Wikidata still needs its own
  `QID_FROM_*` constant. Keyed `(council, Q-ID)` so a chamber raises its own.
  `ParliamentClient` deliberately has no such method — parlament.ch asserts
  nothing about Wikidata — and `app.process` treats its absence as ordinary
  rather than as a degradation.
- **`get_periods` returns `[]` and `get_member_segments` returns `{}`, both on
  purpose.** No period table exists (so no P2937 is ever suggested), and the
  rows are per-tenure so `begin_date` is already the date P580 wants. Do not
  "fix" either by inventing data. `get_tenures` is *not* in that category and
  does return rows: it answers a different question — when did somebody hold
  this seat, including people who have left — off the ended `memberships` rows
  `get_members` filters away. Same cached fetch, role allowlist applied, no
  `today` filter.
- **user-facing strings are parameters.** `report` and `diff` take
  `source_name` / `identifier_property` / `district_label`; a cantonal report
  saying "parlament.ch" or "P1307" sends a reader to a service that has never
  heard of these members.
- **it ships `quickstatements: false` *and* `identifier_verified: false`,**
  which are two different gates. The first is an operator switch; the second is
  the claim that the identifier's value is the source's person id — measured at
  34 of 35 by run 20 — and it refuses every command on its own. `verify_kantonsrat`'s
  `compare_identifier_values` (now per-property) and
  `discover_identifier_columns` check it — the cantonal twin of the Parmelin
  check — and that must read CONFIRMED before either is flipped.

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

**A right config can still produce nothing, and the Gemeinderat der Stadt
Zürich is the case that proves it** (README step 12). `config/gemeinderat-zuerich.yaml`
is OpenParlData one level below the canton — body `261`, joined on P14527,
`statement_model: tenure`. Run 34 (2026-08-18) found body 261 holding **807
person records**, the chamber as a group under it at `id=8062, name_de='Gemeinderat'`
matched by exact name with `Büro des Gemeinderats` correctly rejected, and that
group holding **0 membership rows**. Body key right, group id right, group name
right, no seats — and `app.process` fails with "the source returned no sitting
members for GR". **Never read that message as a config error again**: the four
things a config controls were all correct, and the missing table is the
source's. `verify_kantonsrat.py` now answers it directly — when the chamber's
group holds no memberships, it walks a handful of the *body's people* and
prints the group ids their own rows point at. Walking people rather than
groups is the whole point: 156 groups is 156 requests to answer what five
people answer directly.

Three more rules the same run established, all now in that config:

- **`bodies` rows carry `body_key`, not `key`.** Reading `key` had the probe
  print "0 match key='261'" one line above the row whose `body_key` was 261,
  and then advise passing a different `--body-key`. `body_key_of` resolves it
  from the row, the same discipline as `BEGIN_FIELDS`.
- **the district keys are the source's own spelling.** The city writes `1 und
  2`, not `Wahlkreis Zürich 1+2`; the first draft used the latter and all nine
  entries were unreachable — the eighteen-entries-zero-lookups failure again,
  and one `_fold_key` cannot fix because these are different *words*. The same
  section found **17** distinct values across 807 person records: OpenParlData
  keeps the district on the **person**, so somebody who also sat cantonally or
  federally carries that seat's district here. The eight extras stay unmapped.
- **P14527 came back 68 of 68 and `identifier_verified` is still `false`.**
  Do not flip it on the strength of that run. Run 20's cantonal 34 of 35 failed
  on somebody who **also sat elsewhere** — the property identifies a person
  *record*, one per body — and a linked sample over-represents exactly the
  people it misfires on. A clean run on one body is evidence, not the
  retirement of a known failure mode.

**swissparlpy is pinned `>=2.1`, and the reason is the Gever backend.** 2.1.0
is the first release carrying `backends/gever.py` and `gever_config.py`, whose
`INSTANCES` holds **`city_zurich`** beside `canton_zurich` —
`www.gemeinderat-zuerich.ch` with `/api/kontakt`, `/api/behoerdenmandat`,
`/api/wahlkreis`, `/api/partei`, reachable as
`SwissParlClient(backend="gever_city_zurich")`. It is shaped differently from
the canton's single `MITGLIEDER` index: person and mandate are separate, and a
`behoerdenmandat` row is exactly the table OpenParlData is missing for body
261. `scripts/verify_gever_city.py` measures it (README step 13) and gates
nothing, like every other probe of a service no config names. Two things about
it that are not negotiable: it prints the **schema's** variable list beside the
columns the **records** carry and says the records win where they differ (run
22's "3,862 rows with no name" was a fact about the probe), and it matches the
chamber by **equality** — the city's `Stadtrat` is its nine-member executive
and sits in the same index.

And the thing to say out loud before writing a Gever enricher: under
`identifier_verified: false` **nothing this config produces is mechanical**, so
a second source has nothing left to withhold. A Gever cross-check would appear
in the report and change no output. Given that OpenParlData has no seats for
this body at all, the live question is whether Gever is this parliament's
*source* rather than its second opinion — and the join would be by **name**
throughout, since run 23 established no Wikidata property holds a Gever key,
with duplicate handling of its own rather than `resolve`'s (run 24: the
canton's person-level key is *nearly* a person key and not one).

Two facts from the same census shape the diff's behaviour:

- **89.4% of items carry no P2937 at all**, so populating `terms:` in the
  config turns `ADD_TERM` into a bulk backfill across most of the chamber.
- **2.8% of members hold several P39 statements for one seat** (left and
  returned). `diff` stamps `payload["ambiguous_statement"]` on those, and
  `quickstatements.is_mechanical` refuses qualifier-only commands for them,
  because QuickStatements matches on property + main value alone.

**The personal-data checks are the one class that compares presence, not
value** (README step 9): P19, P1321, P106, P102, P856 and P1971, listed in
`config.person_data` and defined once in `models.PERSON_DATA_CHECKS`. The
source publishes them as free text and the properties want items, so resolving
"Bern (BE)" to a Q-ID is the judgement the config's maps exist to keep out of
the code. Four rules hold them together and none is optional:

- **a suggestion needs the source to have a value *and* Wikidata to have no
  statement for the property at all.** The tool never says a recorded value is
  wrong here — that is `REVIEW_PARTY`'s job, and only for a mapped party.
- **`WikidataPerson.person_data_known` is not the same as an empty
  `properties`.** The fourth SPARQL query wraps its `VALUES ?prop` in an
  `OPTIONAL` precisely so a person carrying none of the properties still comes
  back; without that flag every name-matched item outside the query's
  population — no identifier, no seat — becomes a false positive.
- **`ADD_PERSON_DATA` is never mechanical**, and is refused twice: the kind is
  not in `MECHANICAL_KINDS`, and the payload carries no `position`. There is no
  Q-ID to render, so there is nothing to emit.
- **an unmeasured source column costs nothing**, and `verify_person_data.py`
  run 19 (2026-08-04) turned the guesses into measurements. **The two sources
  answer different halves of the list**, so the enabled checks are per-config:
  parlament.ch has `BirthPlace_City`/`_Canton` (245/246), `Citizenship`
  (244/246), `PartyName` (246/246) and `NumberOfChildren` (105/246) but **no
  occupation and no website column at all**; OpenParlData's ZH `persons` has
  `occupation_de` and `website_personal` and none of the other three. Never add
  `Mandates` / `AdditionalMandate` / `AdditionalActivity` as P106 candidates —
  that is the register of interests, not a living — and never read
  `website_parliament_url_de` as P856: it is the member's page on the chamber's
  site, not a website of the person.

**The same run found two bugs in the cantonal adapter that nothing else could
have.** `openparldata` was reading the party from `party_name_de` and the birth
date from `birthdate`; OpenParlData calls them **`party_de`** and
**`birthday`**, so every cantonal member had no party and no birth date. Both
failures are invisible from inside the pipeline — an unmapped party makes no
suggestion, and a missing birth date silently downgrades the name fallback to a
bare label match rather than breaking it. That is the argument for a probe that
prints the source's real column list instead of trusting `.get()`. The
fixtures now carry the live column names.

A zero in `NumberOfChildren` is treated as no value: in a nullable integer
column a zero and an unstated value are the same shape.

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
  → parliament.get_member_segments()  # MemberCouncilHistory -> the P580 start
  → parliament.get_tenures()          # the same rows -> dates for people who left
  → enrich.fetch()                    # OPTIONAL 2nd source: spans to contradict
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
  `diff.py`. `PERSON_DATA_CHECKS` is the single place the presence checks live
  — property, label, the `Member` field the source's value arrives in, and
  what a reader has to do with it; adding one means adding a source field for
  it, not just a line in a config. `IDENTIFIER_PROPERTIES` is the same shape for
  the identifier properties — id, label, the **URL template its values resolve
  through**, and the register they live in. That template is what makes a
  printed number clickable and correct; adding a property without one would
  leave the report linking a number through somebody else's template.
  `QID_FROM_IDENTIFIER` / `QID_FROM_NAME` record how a member's Q-ID
  was established, and that provenance is what gates QuickStatements. `Member`
  is "who sits today"; `Tenure` is "when did this person hold this seat", asked
  about people the members table does not contain, and keyed
  `(person_number, council)` for the same reason `seats_by_seat` is — a person
  is not a seat.
- **`config.py`** — loads/validates `config/parliament.yaml`. Enforces a real
  `user_agent` (rejects placeholders) and validates every Q-ID map.
  `identifier_verified` defaults from `models.VERIFIED_IDENTIFIER_PROPERTIES`
  and **may not be claimed true** for a property outside it: that claim is what
  `is_mechanical` writes edits off, so it costs a measurement, not an edit. A key
  mapped to a blank value is a deliberate "not known yet" and is dropped, not
  an error. `person_data` is the exception to that leniency: an unknown
  property id there is an **error**, because it selects a code path rather than
  supplying a value, and a typo would silently drop a check somebody asked for.
  `identifiers` is the same kind of key and the same kind of error: it lists the
  parliament's *two* identifier properties, and a typo would drop the one that
  is only ever reported. The join property is always included whatever the list
  says — it is the only one whose value the run holds. `biography_url_for`
  defaults to the join property's record page, so a config that says nothing
  cannot print a number linked through the wrong register.
- **`http_client.py`** — the single shared HTTP layer: required User-Agent,
  `request_delay` throttle, 429/5xx exponential backoff. Its `requests.Session`
  is also handed to `SwissParlClient`, so parlament.ch sees the same UA.
- **`parliament.py`** — the **only** `swissparlpy` caller. The row-mapping
  functions (`member_from_row`, `members_from_rows`, `period_from_row`,
  `periods_from_rows`) are module-level and pure, and that is how
  `get_members` is tested — by feeding fixture rows, never by mocking OData.
  The client is constructed lazily because building it fetches the metadata
  document over the network. `MemberCouncilHistory` is read **once per client**
  and answers two questions off the same rows: the tenure *start* P580 comes
  from (`tenure_start`), and the tenure *end* the reverse walk reports for
  departed members (`latest_tenure` / `tenures_from_segments`).
- **`period_overlap.py`** — pure interval arithmetic, and **the most
  consequential logic in the tool**: it decides a P2937 qualifier on every
  statement emitted. Both intervals are **closed**. A member with no
  `DateJoining` gets **no** periods — the fail-safe that stops an unknown start
  from silently meaning "all ~52 periods". Tested exhaustively.
- **`wikidata.py`** — SPARQL against WDQS. **Bounded queries**, not one wide
  one: P768/P4100/P2937/P102 are all repeatable, so a single SELECT would
  produce a cartesian product per statement. They are joined on the Q-ID in
  Python. A fourth query answers the personal-data checks and runs only when a
  config asks for them; `?prop` is bound from a `VALUES` list so each row is
  one (person, property) pair — a union, not a product.
  `get_identifier_values` re-asks the identifier query for a property the run
  reports but does **not** join on, and its answer goes to
  `WikidataPerson.identifiers`, never to `parliament_id` — that field means
  "the source's person id" to three separate passes. It is global rather than
  seat-bounded because absence is the finding. The P39 query deliberately fetches statements for **everyone** with
  those positions, not just P1307 holders, because the diff's second pass needs
  people who are *not* in the current-members set. Query builders are static
 methods so they are unit-testable. `get_name_variants` is **not** one of the
 three: it is asked on demand, by the step 8 probe only, for a Q-ID list the
 caller has already narrowed — the pipeline's own path is unchanged.
- **`resolve.py`** — the identifier join first (exact; P1307 federally, P14527
 for the Kantonsrat). Under `identifier_verified: false` every match must also
 be corroborated by the item's own birth date or label (`corroborates`), and
 the rejects are recorded and counted — that is the guard against two id
 spaces overlapping numerically, which is how an *exact* join matches the
 wrong person. An identifier claimed by two
  items is **skipped, logged and recorded on the member**
  (`Member.duplicate_identifier_qids`), never arbitrated. Recording it is what
  makes the conflict reportable: logging alone left the member looking merely
  unmatched, and an unmatched member draws "they may need a new item" — which
  would create a third duplicate. The name fallback rejects any candidate whose
  *known* birth date contradicts the member's, and never hands one Q-ID to two
  members.
- **`diff.py`** — pure. Seat checks compare *values*; `_person_data_suggestions`
  compares *presence* only, and is the one part of the diff that is about the
  person rather than the mandate (see above). `_identifier_suggestions` is the
  one place both identifier kinds are raised, and the *only* thing separating
  them is whether the run has the value: the join property gets
  `ADD_IDENTIFIER` with a number, every other configured property gets
  `MISSING_IDENTIFIER` with none. Never give the second one a value from
  anywhere — a number from another register is the failure mode, not the fix.
  `expected_statements` is the **single place** the
  `tenure` vs `period` statement model lives; the rest of the diff works off
  whatever it returns. Walks members → Wikidata, then Wikidata's open
  memberships → members (catching people Wikidata still lists as sitting).
  Sorted by priority then name. `DUPLICATE_IDENTIFIER` is raised from **two**
  places — once per conflicted sitting member, and once per identifier that
  several *seat-holding items* claim without any sitting member carrying it —
  deduplicated on the identifier value. The reverse walk skips items whose
  identifier belongs to a sitting member as well as items whose Q-ID does: a
  conflicted member has no Q-ID, so keying on that alone reports both claimants
  as departed, which is a wrong claim about somebody in office.
  That second walk is about people the
  *current-members* table does not contain, so `_departed_suggestion` reaches
  the source through the identifier **Wikidata** asserts — `config.biography_url`
  for the link, `Tenure` from the source's historic record for the dates it
  suggests. That bridge *is* the claim `identifier_verified` records, so under
  `false` the dates are withheld and the finding still reported: a wrong bridge
  does not fail, it prints another person's spell under this name. It stays **report-only** and is gated twice: no `qid_source`, and no
  `position` in the payload. Removing either would turn it into a P582 backfill
  across every open membership on Wikidata. `scripts/verify_departures.py`
  (README step 8) is the probe that would license removing them; **runs 16-18
  (2026-08-04) say not yet, and have found more wrong with the probe than with
  the data** — run 18 has the leaving dates agreeing **1,960 of 1,960**, and
  what is left is five identities one character apart (`Zünd`/`Zündt`), which a
  name comparison cannot settle and so reports as `near` without accepting.
  **Its identity check compares every name Wikidata gives the item, not the
  label alone**: aliases and the P1810 `subject named as` qualifier on the
  identifier statement, fetched by `WikidataClient.get_name_variants` (a fourth
  bounded query, asked only for the people section B judges, `UNION` rather than
  two `OPTIONAL`s so aliases and P1810 cannot multiply). The **strongest**
  reading wins, which is what makes the widening safe: an extra name can only
  move a row towards agreement, never produce the `CONTRADICTED` that blocks a
  bulk apply. It stays corroboration — an alias is asserted by whoever wrote the
  item — so which name settled a row is counted and printed, never folded into
  the total. Both gates have a test naming them. `_departed_suggestion` also
  stamps `ambiguous_statement` when the item holds several P39 for the seat (3
  of 1,969 in run 16) — that is a *separate* guard from the gates, and the one
  that survives them being removed.
- **`enrich.py`** — a **second** source, read only to contradict the first, and
  the one module that is not about Wikidata. Opt-in via `enrich:` in the
  config; absent, nothing changes. Bounded **structurally**: it produces no
  `Member`, so it cannot become a source, and a disagreement can only
  *withhold* — `diff` stamps `sources_disagree` and `is_mechanical` refuses.
  Three rules, each already paid for: keyed `(Q-ID, council)` never by Q-ID
  alone; a Q-ID several person records claim is skipped and reported, never
  arbitrated; and both sides chained by the same `MAX_SEGMENT_GAP_DAYS` rule,
  because a chained tenure against a single term reports every re-elected
  member as a disagreement. Silence is never a contradiction: only facts both
  sources state are compared.
- **`quickstatements.py`** — pure renderer. `is_mechanical` is the **one place**
  the safety rule lives; keep it that way. It gates on *provenance*
  (`qid_source`) and, since P13468, on the other half of the same claim: a
  suggestion stamped `identifier_unverified` is refused, because a property
  whose value has not been measured against the source's person id can match
  exactly and match somebody else. Review/correction kinds are excluded
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
  `--validate-periods`, `scripts/verify_departures.py`,
  `scripts/verify_kantonsrat.py`, `scripts/verify_person_data.py` and
  `scripts/verify_gever.py`, writes all nine to
  the run summary, and writes nothing to the repo. Keep it read-only: it is
  the diagnostic you run *before* trusting `update.yml`'s output. **Only the
  first two gate**; the other seven report without gating and are deliberately
  excluded from the job's pass/fail — do not wire their outcomes into the
  gate. The gate says whether the pipeline may run; `compare_tenure_dates` and
  `--validate-periods` answer whether a *bulk apply* is safe,
  `verify_departures` answers whether the departed members' report-only gates
  could be removed (its `INCONCLUSIVE` is the *expected* answer on tidy data —
  never wire it into a gate),
  `verify_kantonsrat` measures a parliament no config here processes, so it
  cannot bear on the federal run by construction, `verify_person_data`
  measures checks that are never mechanical, so no verdict it returns can
  change what reaches `suggestions.qs`, and `verify_gever` reads a service no
  config here names at all. The file must be on the
  default branch to appear in the dispatch UI, though a dispatch then runs the
  selected ref's version.
- `update.yml` — weekly (Mon 06:00 UTC) + manual; runs the pipeline and commits
  `reports/` and `docs/` back (`contents: write`).
- `pages.yml` — deploys `docs/` to Pages, chained off `update.yml`'s completion
  (a `GITHUB_TOKEN` push does not itself fire a `push` event).
