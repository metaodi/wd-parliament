# Test fixtures

## Provenance — please read

`membercouncil.json` and `periods.json` were **hand-built against the OData
schema, not captured from the live service.** The environment this project was
scaffolded in had no network route to `ws.parlament.ch` (the egress policy
refused the CONNECT), so a real response could not be saved.

The field names, types and keys are exact: they were read from the
`$metadata` document that ships inside `swissparlpy` 1.0.0
(`tests/fixtures/metadata.xml` in that package), so the shape these fixtures
present to `parliament.member_from_row` / `period_from_row` is the shape the
real service presents. The legislative-period dates follow the real pattern
(a Swiss legislature opens on the first Monday of December after the October
election and runs four years). The **people are invented** — the names,
`PersonNumber`s and dates do not describe any real member of the Federal
Assembly, and must not be read as though they did.

### Two encodings corrected on 2026-07-29

The `Verify assumptions` workflow reached the live service for the first time,
and the fixtures were wrong about two things in a way that mattered. Both are
now as the service really sends them — **do not "tidy" either back**:

- **`CouncilAbbreviation` is `NR` / `SR`**, not `N` / `S`. The service's
  distinct values under `Language=DE` are `''`, `BR`, `NR`, `SR`. French rows
  carry `CN` instead, which is what the one French row here now shows and why
  the pipeline pushes `Language=DE` down.
- **"No date" is `1753-01-01T00:00:00`, never a null.** SQL Server's `datetime`
  minimum, sent for every sitting member's `DateLeaving`. The old fixtures used
  `null`, which is exactly why nothing caught that `diff` would raise a
  mechanical `ADD_END_DATE` — a P582 of 1753 — for the whole chamber. See
  `parliament.NULL_DATE`.

`PersonNumber` is also confirmed to be what P1307 holds (Parmelin: 1108, with
`PersonIdCode` 2621 as the near-miss), so the join key these fixtures exercise
is the right one.

This is fine for what the tests actually do: every test here exercises pure
logic — interval arithmetic, matching rules, suggestion generation, command
rendering — none of which depends on the data being historically true.

### Still to do

Replace both files with real captures once the service is reachable:

```bash
uv run python - <<'EOF'
import json, swissparlpy as spp
rows = [dict(r) for r in spp.get_data("MemberCouncil", Language="DE", Active=True)]
json.dump(rows[:40], open("tests/fixtures/membercouncil.json", "w"),
          indent=2, ensure_ascii=False, default=str)
periods = [dict(r) for r in spp.get_data("LegislativePeriod", Language="DE")]
json.dump(periods, open("tests/fixtures/periods.json", "w"),
          indent=2, ensure_ascii=False, default=str)
EOF
```

Keep the coverage the current fixtures were built for — both chambers, a
mid-period joiner, a departure, a single-day tenure, a member with no
`DateJoining`, a member with no `DateOfBirth`, a row with no `PersonNumber`,
a duplicate row in a second language, and a person repeated *within* one
language — and the tests will keep passing against the real data.

## Files

| File | What it holds |
| --- | --- |
| `membercouncil.json` | 13 `MemberCouncil` rows: 10 distinct people across both chambers, one repeated in German (an older mandate row) and once in French, one with a null `PersonNumber`. |
| `periods.json` | 10 `LegislativePeriod` rows: the 44th–52nd legislatures, plus the 52nd repeated in French. The 52nd has no `EndDate` — it is the running one. |
| `gever_mitglieder.xml` | 3 CMI CDWS `Hit`s from the canton of Zürich's `MITGLIEDER` index: two of them one invented person's two `Gremium` rows, under two different `OBJ_GUID`s. |
| `gever_mitglieder_schema.xml` | The XSD's `searchfield` annotation for the same index — what the API can be *filtered* on, which is a shorter list than what a record carries. |

### The two Gever fixtures

Also **hand-built, not captured** — the environment had no egress to
`parlzhcdws.cmicloud.ch` either. The *element names* are real: they are the
fields [`goifer`](https://github.com/metaodi/goifer)'s own pandas example
prints back from the live `canton_zurich` index, and `goifer` is the client
`swissparlpy` PR #51's Gever backend is adapted from. The people are invented.

What these fixtures must **not** be read as saying: no element here resembles a
Staatsarchiv member id, and that is a property of a file written to exercise
the parser, not a measurement of the service. Only
`scripts/verify_gever.py` against the live index can answer README step 10 —
which is the whole reason that probe prints the column list instead of
inspecting the fields it expects.
