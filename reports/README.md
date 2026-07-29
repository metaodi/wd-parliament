# wd-parliament — suggested Wikidata edits

> **No run has been performed yet.**
>
> This file, the per-chamber reports beside it, and everything under `docs/`
> are **build artifacts**. They are produced by
> `python -m wd_parliament --config config/parliament.yaml` and by the
> [`Update parliament TODO`](../.github/workflows/update.yml) workflow, which
> commits a refreshed set every Monday. Do not hand-edit them.
>
> The first scheduled (or manually dispatched) run replaces this placeholder
> with the real index: a table of both chambers, their P1307 match rates, and
> a link to each chamber's detailed TODO list.

Before that first run, work through **Open verification steps** in the
[project README](../README.md#open-verification-steps) — the P1307 assumption
and the statement model both need confirming against live data, and the
canton Q-IDs in `config/parliament.yaml` need checking with
`python -m wd_parliament --verify-config`.
