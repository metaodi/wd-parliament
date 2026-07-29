"""wd-parliament: keep Wikidata's Swiss Federal Assembly memberships in sync.

The package reads the sitting members of the Nationalrat and Ständerat from the
official parlament.ch OData service (via ``swissparlpy``), compares them against
the *position held* (P39) statements on Wikidata, and produces a prioritised
TODO list of suggested Wikidata edits plus a QuickStatements file.

Unlike its sibling wd-squads, which matches football players by name against a
human-maintained Wikipedia list, this tool joins on an identifier — Wikidata's
P1307 "Swiss parliament ID" against ``MemberCouncil.PersonNumber`` — and reads
an authoritative source. That is what makes most of its findings facts rather
than heuristics, and what justifies emitting QuickStatements at all.
"""

__version__ = "0.1.0"
