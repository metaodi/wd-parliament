"""Query the Wikidata Query Service for P1307 holders and their P39 statements.

Three bounded queries rather than one wide one. Qualifiers (P768, P4100, P2937)
and P102 are all repeatable, so folding them into a single SELECT would produce
a cartesian product per statement; keeping them apart and joining on the Q-ID
in Python costs one extra request and keeps every result set predictable.

- :meth:`WikidataClient.get_identifier_index` — every item with P1307, plus its
  birth/death dates. This is the join key to ``MemberCouncil.PersonNumber``.
- :meth:`WikidataClient.get_position_statements` — every P39 statement for the
  configured position items *regardless of P1307*, with qualifiers. Fetching
  these unconditionally is what lets the diff walk Wikidata → parlament.ch and
  catch people Wikidata still lists as sitting.
- :meth:`WikidataClient.get_parties` — open P102 statements for the same people.

:meth:`WikidataClient.search_people` is the name-based fallback, restricted to
humans who are politicians or already hold one of the positions, and returning
birth dates so ``resolve`` can pick between namesakes.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

from .http_client import HttpClient
from .models import PersonMatch, PositionStatement, WikidataPerson

log = logging.getLogger(__name__)

WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
ENTITY_RE = re.compile(r"/entity/(Q\d+)$")

# "politician" — the occupation the name-search fallback is restricted to, on
# top of "already holds one of the configured positions".
POLITICIAN_OCCUPATION = "Q82955"

# Label languages a member's name is looked up in. "mul" is Wikidata's
# language-agnostic label, increasingly where a person's name lives.
LABEL_LANGUAGES = ("de", "fr", "it", "en", "mul")

# Names per name-search query; the query travels in the URL of a GET, so this
# keeps the request comfortably under the usual ~8 KB header limit.
SEARCH_BATCH_SIZE = 25

# A name needs at least two words to be worth searching — a single token is far
# more likely to collide with an unrelated person.
_SEARCHABLE_NAME_RE = re.compile(r"\S+\s+\S+")


def qid_from_uri(uri: str) -> Optional[str]:
    m = ENTITY_RE.search(uri or "")
    return m.group(1) if m else None


def date_value(binding: Optional[dict]) -> Optional[date]:
    """Extract a ``date`` from a WDQS time binding.

    WDQS returns e.g. ``"2021-07-01T00:00:00Z"``. Values with less than day
    precision (``"2021-00-00T00:00:00Z"``) and dates outside the proleptic
    Gregorian range Python can represent yield ``None`` rather than raising —
    a member whose Wikidata birth date is year-only simply cannot be used as a
    disambiguator, which is a fact about the data, not an error.
    """
    if not binding:
        return None
    value = binding.get("value", "")
    if not value:
        return None
    text = value.split("T", 1)[0].lstrip("+")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _literal(binding: Optional[dict]) -> Optional[str]:
    if not binding:
        return None
    value = binding.get("value")
    return value or None


def _escape_literal(text: str) -> str:
    """Escape a name for use inside a SPARQL double-quoted literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _chunks(items: Sequence[str], size: int) -> Iterator[List[str]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def _values_clause(qids: Iterable[str]) -> str:
    return " ".join(f"wd:{q}" for q in qids)


class WikidataClient:
    def __init__(self, http: HttpClient, endpoint: str = WDQS_ENDPOINT) -> None:
        self.http = http
        self.endpoint = endpoint

    def run_query(self, sparql: str) -> List[dict]:
        """Run a SPARQL SELECT and return the list of binding rows."""
        data = self.http.get_json(
            self.endpoint,
            params={"query": sparql, "format": "json"},
            accept="application/sparql-results+json",
        )
        return data.get("results", {}).get("bindings", [])

    # -- queries -------------------------------------------------------------
    @staticmethod
    def identifier_query(language: str = "de") -> str:
        """Every item carrying a Swiss parliament ID (P1307), ~3,600 rows."""
        return f"""
SELECT ?person ?personLabel ?parliamentId ?birth ?death WHERE {{
  ?person wdt:P1307 ?parliamentId .
  OPTIONAL {{ ?person wdt:P569 ?birth . }}
  OPTIONAL {{ ?person wdt:P570 ?death . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},de,fr,it,en". }}
}}
""".strip()

    @staticmethod
    def position_query(position_qids: Sequence[str], language: str = "de") -> str:
        """P39 statements for the configured seats, with their qualifiers.

        Walks the statement node (``p:``/``ps:``) rather than the truthy
        predicate so the qualifiers are reachable, and drops deprecated-rank
        statements to keep ``wdt:`` semantics.
        """
        values = _values_clause(position_qids)
        return f"""
SELECT ?person ?personLabel ?statement ?position ?start ?end
       ?district ?group ?term ?parliamentId WHERE {{
  VALUES ?position {{ {values} }}
  ?statement ps:P39 ?position ;
             wikibase:rank ?rank .
  ?person p:P39 ?statement .
  FILTER ( ?rank != wikibase:DeprecatedRank )
  OPTIONAL {{ ?statement pq:P580 ?start . }}
  OPTIONAL {{ ?statement pq:P582 ?end . }}
  OPTIONAL {{ ?statement pq:P768 ?district . }}
  OPTIONAL {{ ?statement pq:P4100 ?group . }}
  OPTIONAL {{ ?statement pq:P2937 ?term . }}
  OPTIONAL {{ ?person wdt:P1307 ?parliamentId . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},de,fr,it,en". }}
}}
""".strip()

    @staticmethod
    def party_query(position_qids: Sequence[str]) -> str:
        """Open P102 (member of political party) statements for seat holders."""
        values = _values_clause(position_qids)
        return f"""
SELECT ?person ?party WHERE {{
  VALUES ?position {{ {values} }}
  ?person wdt:P39 ?position ;
          p:P102 ?statement .
  ?statement ps:P102 ?party ;
             wikibase:rank ?rank .
  FILTER ( ?rank != wikibase:DeprecatedRank )
  FILTER NOT EXISTS {{ ?statement pq:P582 ?partyEnd . }}
}}
""".strip()

    @staticmethod
    def people_search_query(
        names: Sequence[str], position_qids: Sequence[str], language: str = "de"
    ) -> str:
        """Look each name up as a person's label or alias.

        Deliberately narrow: an **exact** label/alias match, on a human, who is
        a politician by occupation or already holds one of the configured
        seats. ``?birth`` comes back so ``resolve`` can require the birth date
        to agree before accepting the match.
        """
        values = " ".join(
            f'"{_escape_literal(name)}"@{lang}'
            for name in names
            for lang in LABEL_LANGUAGES
        )
        positions = _values_clause(position_qids)
        return f"""
SELECT DISTINCT ?name ?person ?personLabel ?birth ?hasPosition ?parliamentId WHERE {{
  VALUES ?name {{ {values} }}
  {{ ?person rdfs:label ?name . }} UNION {{ ?person skos:altLabel ?name . }}
  ?person wdt:P31 wd:Q5 .
  FILTER (
    EXISTS {{ ?person wdt:P106 wd:{POLITICIAN_OCCUPATION} }}
    || EXISTS {{ ?person wdt:P39 ?anyPosition }}
  )
  BIND ( EXISTS {{ VALUES ?seat {{ {positions} }} ?person wdt:P39 ?seat }} AS ?hasPosition )
  OPTIONAL {{ ?person wdt:P569 ?birth . }}
  OPTIONAL {{ ?person wdt:P1307 ?parliamentId . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},de,fr,it,en". }}
}}
""".strip()

    # -- fetching ------------------------------------------------------------
    def get_identifier_index(self, language: str = "de") -> Dict[str, WikidataPerson]:
        """Map Q-ID → :class:`WikidataPerson` for every item with a P1307."""
        people: Dict[str, WikidataPerson] = {}
        for row in self.run_query(self.identifier_query(language)):
            qid = qid_from_uri(row.get("person", {}).get("value", ""))
            if not qid:
                continue
            person = people.setdefault(qid, WikidataPerson(qid=qid))
            person.label = _literal(row.get("personLabel")) or person.label or qid
            person.parliament_id = _literal(row.get("parliamentId")) or person.parliament_id
            person.birth_date = person.birth_date or date_value(row.get("birth"))
            person.death_date = person.death_date or date_value(row.get("death"))
        log.info("Wikidata: %d items carry a Swiss parliament ID (P1307)", len(people))
        return people

    def get_position_holders(
        self, position_qids: Sequence[str], language: str = "de"
    ) -> Dict[str, WikidataPerson]:
        """Everyone holding one of ``position_qids``, with their P39 statements.

        Merges the P1307 index in, so the returned map is the single view of
        Wikidata the diff works from: people with an identifier but no seat,
        people with a seat but no identifier, and everyone in between.
        """
        people = self.get_identifier_index(language)
        statements: Dict[str, PositionStatement] = {}

        for row in self.run_query(self.position_query(position_qids, language)):
            qid = qid_from_uri(row.get("person", {}).get("value", ""))
            statement_uri = row.get("statement", {}).get("value", "")
            position = qid_from_uri(row.get("position", {}).get("value", ""))
            if not qid or not statement_uri or not position:
                continue

            person = people.setdefault(qid, WikidataPerson(qid=qid))
            person.label = _literal(row.get("personLabel")) or person.label or qid
            person.parliament_id = person.parliament_id or _literal(row.get("parliamentId"))

            statement = statements.get(statement_uri)
            if statement is None:
                statement = PositionStatement(
                    person_qid=qid,
                    statement_id=statement_uri,
                    position_qid=position,
                    person_label=person.label,
                    start=date_value(row.get("start")),
                    end=date_value(row.get("end")),
                )
                statements[statement_uri] = statement
                person.statements.append(statement)

            # Repeated qualifiers arrive as extra rows; collect them.
            for key, target in (
                ("district", statement.districts),
                ("group", statement.groups),
                ("term", statement.terms),
            ):
                value = qid_from_uri(row.get(key, {}).get("value", ""))
                if value and value not in target:
                    target.append(value)

        for row in self.run_query(self.party_query(position_qids)):
            qid = qid_from_uri(row.get("person", {}).get("value", ""))
            party = qid_from_uri(row.get("party", {}).get("value", ""))
            if not qid or not party:
                continue
            person = people.setdefault(qid, WikidataPerson(qid=qid))
            if party not in person.parties:
                person.parties.append(party)

        log.info(
            "Wikidata: %d P39 statements across %d people",
            len(statements),
            sum(1 for p in people.values() if p.statements),
        )
        return people

    def search_people(
        self,
        names: Sequence[str],
        position_qids: Sequence[str],
        language: str = "de",
    ) -> Dict[str, List[PersonMatch]]:
        """Map each searchable name to the Wikidata items carrying it.

        Names that match nothing (and names too generic to search) are simply
        absent. Picking a winner among several candidates is
        ``resolve.select_person_match``'s job — this only reports what exists.
        """
        unique = [
            name
            for name in dict.fromkeys(n.strip() for n in names if n and n.strip())
            if _SEARCHABLE_NAME_RE.fullmatch(name.strip())
        ]
        matches: Dict[str, List[PersonMatch]] = {}
        for batch in _chunks(unique, SEARCH_BATCH_SIZE):
            sparql = self.people_search_query(batch, position_qids, language)
            for row in self.run_query(sparql):
                qid = qid_from_uri(row.get("person", {}).get("value", ""))
                name = _literal(row.get("name"))
                if not qid or not name:
                    continue
                found = matches.setdefault(name, [])
                if any(m.qid == qid for m in found):
                    continue  # same item matched in several label languages
                found.append(
                    PersonMatch(
                        name=name,
                        qid=qid,
                        label=_literal(row.get("personLabel")) or qid,
                        birth_date=date_value(row.get("birth")),
                        has_position=row.get("hasPosition", {}).get("value") == "true",
                        parliament_id=_literal(row.get("parliamentId")),
                    )
                )
        return matches
