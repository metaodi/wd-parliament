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

:meth:`WikidataClient.get_name_variants` answers the opposite question — given
items already identified, what *else* are they called? Aliases and the P1810
"subject named as" qualifier on the identifier statement, for a caller
comparing a label against a name in the source. It is bounded by the Q-IDs it
is handed and asks for nothing else.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

from .http_client import HttpClient
from .models import (
    NAME_FROM_ALIAS,
    NAME_FROM_NAMED_AS,
    P_PARLIAMENT_ID,
    P_SUBJECT_NAMED_AS,
    NameVariant,
    PersonMatch,
    PositionStatement,
    WikidataPerson,
)

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

# Q-IDs per name-variant query. Same URL-length constraint, but a Q-ID costs
# ~12 characters against a name's five language-tagged literals.
NAME_VARIANT_BATCH_SIZE = 200

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
    def identifier_query(
        language: str = "de", identifier_property: str = P_PARLIAMENT_ID
    ) -> str:
        """Every item carrying the join identifier, with its birth/death dates.

        The property is configuration because the join key is not the same at
        every level of government: federally it is P1307 against
        ``MemberCouncil.PersonNumber``, but **no cantonal parliament has a
        P1307**, and for the Kantonsrat Zürich the Wikidata-asserted identifier
        is P14527 (OpenParlData ID) — carried by 35 of the 35 members
        OpenParlData links, where federally it adds nobody.
        """
        return f"""
SELECT ?person ?personLabel ?parliamentId ?birth ?death WHERE {{
  ?person wdt:{identifier_property} ?parliamentId .
  OPTIONAL {{ ?person wdt:P569 ?birth . }}
  OPTIONAL {{ ?person wdt:P570 ?death . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},de,fr,it,en". }}
}}
""".strip()

    @staticmethod
    def position_query(
        position_qids: Sequence[str],
        language: str = "de",
        identifier_property: str = P_PARLIAMENT_ID,
    ) -> str:
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
  OPTIONAL {{ ?person wdt:{identifier_property} ?parliamentId . }}
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
        names: Sequence[str],
        position_qids: Sequence[str],
        language: str = "de",
        identifier_property: str = P_PARLIAMENT_ID,
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
  OPTIONAL {{ ?person wdt:{identifier_property} ?parliamentId . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},de,fr,it,en". }}
}}
""".strip()

    @staticmethod
    def name_variant_query(
        qids: Sequence[str],
        language: str = "de",
        identifier_property: str = P_PARLIAMENT_ID,
    ) -> str:
        """The names these items carry besides their label.

        Two kinds, and the query keeps them apart with a UNION rather than two
        OPTIONALs for the reason this module keeps its queries bounded
        everywhere else: an item with four aliases and a P1810 would otherwise
        come back as four rows carrying the same qualifier, and the "kinds
        multiply" bug is exactly what the three-query split exists to avoid.

        - ``skos:altLabel`` — Wikidata's "also known as", restricted to the
          label languages so a Cyrillic or Chinese transliteration of a Swiss
          politician cannot end up being compared against a German surname;
        - ``pq:P1810`` on the **identifier statement** — "subject named as",
          i.e. how the person is written *in the database the identifier points
          at*. That is the strongest name evidence available here, because it is
          a claim about this very source rather than about the person in
          general.

        The identifier property is a parameter for the same reason it is one on
        every other query: P1307 federally, P14527 for a cantonal parliament.
        """
        languages = ", ".join(
            f'"{lang}"' for lang in dict.fromkeys((language,) + LABEL_LANGUAGES)
        )
        return f"""
SELECT ?person ?name ?origin WHERE {{
  VALUES ?person {{ {_values_clause(qids)} }}
  {{
    ?person skos:altLabel ?name .
    FILTER ( lang(?name) IN ( {languages} ) )
    BIND ( "{NAME_FROM_ALIAS}" AS ?origin )
  }} UNION {{
    ?person p:{identifier_property} ?idStatement .
    ?idStatement pq:{P_SUBJECT_NAMED_AS} ?name .
    BIND ( "{NAME_FROM_NAMED_AS}" AS ?origin )
  }}
}}
""".strip()

    # -- fetching ------------------------------------------------------------
    def get_identifier_index(
        self, language: str = "de", identifier_property: str = P_PARLIAMENT_ID
    ) -> Dict[str, WikidataPerson]:
        """Map Q-ID → :class:`WikidataPerson` for every item with the join id."""
        people: Dict[str, WikidataPerson] = {}
        for row in self.run_query(self.identifier_query(language, identifier_property)):
            qid = qid_from_uri(row.get("person", {}).get("value", ""))
            if not qid:
                continue
            person = people.setdefault(qid, WikidataPerson(qid=qid))
            person.label = _literal(row.get("personLabel")) or person.label or qid
            person.parliament_id = _literal(row.get("parliamentId")) or person.parliament_id
            person.birth_date = person.birth_date or date_value(row.get("birth"))
            person.death_date = person.death_date or date_value(row.get("death"))
        log.info(
            "Wikidata: %d item(s) carry %s", len(people), identifier_property
        )
        return people

    def get_position_holders(
        self,
        position_qids: Sequence[str],
        language: str = "de",
        identifier_property: str = P_PARLIAMENT_ID,
    ) -> Dict[str, WikidataPerson]:
        """Everyone holding one of ``position_qids``, with their P39 statements.

        Merges the P1307 index in, so the returned map is the single view of
        Wikidata the diff works from: people with an identifier but no seat,
        people with a seat but no identifier, and everyone in between.
        """
        people = self.get_identifier_index(language, identifier_property)
        statements: Dict[str, PositionStatement] = {}

        for row in self.run_query(
            self.position_query(position_qids, language, identifier_property)
        ):
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

    @staticmethod
    def label_query(qids: Sequence[str], language: str = "de") -> str:
        """Labels, descriptions and instance-of for a set of items."""
        return f"""
SELECT ?item ?itemLabel ?itemDescription ?instanceLabel WHERE {{
  VALUES ?item {{ {_values_clause(qids)} }}
  OPTIONAL {{ ?item wdt:P31 ?instance . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},de,fr,it,en". }}
}}
""".strip()

    def describe_qids(
        self, qids: Sequence[str], language: str = "de"
    ) -> Dict[str, Dict[str, object]]:
        """Look up what each Q-ID actually is, for ``--verify-config``.

        The config's canton, party, group and term maps are hand-maintained
        Q-IDs, and a wrong one would be attached as a qualifier to real
        statements. This turns "are these right?" into one query whose output a
        human can scan.
        """
        out: Dict[str, Dict[str, object]] = {}
        unique = list(dict.fromkeys(q for q in qids if q))
        for batch in _chunks(unique, 100):
            for row in self.run_query(self.label_query(batch, language)):
                qid = qid_from_uri(row.get("item", {}).get("value", ""))
                if not qid:
                    continue
                entry = out.setdefault(qid, {"label": None, "description": None, "instance_of": []})
                entry["label"] = _literal(row.get("itemLabel")) or entry["label"]
                entry["description"] = (
                    _literal(row.get("itemDescription")) or entry["description"]
                )
                instance = _literal(row.get("instanceLabel"))
                instances = entry["instance_of"]
                assert isinstance(instances, list)
                if instance and instance not in instances:
                    instances.append(instance)
        for qid in unique:
            out.setdefault(qid, {"label": None, "description": None, "instance_of": []})
        return out

    def get_name_variants(
        self,
        qids: Sequence[str],
        language: str = "de",
        identifier_property: str = P_PARLIAMENT_ID,
    ) -> Dict[str, List[NameVariant]]:
        """Map Q-ID → the aliases and P1810 values those items carry.

        Bounded by the caller's list: this answers "what else is this person
        called?" for a population somebody has already narrowed down, not for
        Wikidata at large. Items with no variant are simply absent from the
        result, which is a fact about them and not a failure — a caller must
        treat a missing entry as "nothing more to compare", never as a
        mismatch.

        Read-only by construction. Nothing in this tool ever writes P1810 or an
        alias; both are read to corroborate that an identifier reached the
        person the item is about.
        """
        out: Dict[str, List[NameVariant]] = {}
        unique = list(dict.fromkeys(q for q in qids if q))
        for batch in _chunks(unique, NAME_VARIANT_BATCH_SIZE):
            sparql = self.name_variant_query(batch, language, identifier_property)
            for row in self.run_query(sparql):
                qid = qid_from_uri(row.get("person", {}).get("value", ""))
                name = _literal(row.get("name"))
                origin = _literal(row.get("origin"))
                if not qid or not name or not origin:
                    continue
                found = out.setdefault(qid, [])
                if any(v.name == name and v.origin == origin for v in found):
                    continue
                found.append(
                    NameVariant(
                        name=name,
                        origin=origin,
                        language=row.get("name", {}).get("xml:lang") or None,
                    )
                )
        log.info(
            "Wikidata: %d of %d item(s) carry an alias or a %s value",
            len(out),
            len(unique),
            P_SUBJECT_NAMED_AS,
        )
        return out

    def search_people(
        self,
        names: Sequence[str],
        position_qids: Sequence[str],
        language: str = "de",
        identifier_property: str = P_PARLIAMENT_ID,
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
            sparql = self.people_search_query(
                batch, position_qids, language, identifier_property
            )
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
