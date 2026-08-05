"""Can the Gever of the canton of Zürich supply P13468, and what else?

README step 10. ``swissparlpy`` PR #51 adds a **Gever backend** — the CMI CDWS
API behind ``kantonsrat.zh.ch``, the Kantonsrat's own business-management
system — and it is the first candidate source for the ZH config that is the
*canton's own* rather than an aggregator's. The question this probe answers is
the one run 20 left open:

**P13468 "Zurich Kantonsrat and Regierungsrat member ID" is the right property
and, from OpenParlData, an impossible join.** 28 of 35 linked ZH items carry
it, **0 of 28** values equal OpenParlData's person id, and the probe then
searched *every* column of the person record and found them in **none**
(``verify_kantonsrat.py`` section C). An identifier needs a value on both
sides, so ``config.load_config`` refuses ``identifier_property: P13468`` with
``source: openparldata`` outright, and the way to that property was left as
"a source that supplies its value". Gever is a candidate for that source, and
this probe measures whether it is one — it does not assume so because the
system belongs to the canton. *Whose* register a service belongs to says
nothing about *which* register's numbers it publishes; that is the same
mistake as reading coverage as correctness.

Four sections, and only the third answers the question in the title:

**A. Reach.** Does the ``MITGLIEDER`` index answer at all, how many records
does it hold, and which fields does its XSD declare as searchable.

**B. The real column list.** Every field the service actually returns, with how
many records carry a value. Printed rather than inferred, because run 19 found
two adapter bugs (``party_name_de`` for ``party_de``, ``birthdate`` for
``birthday``) that were invisible through ``.get()`` — "no such column" and
"column full of nulls" are indistinguishable that way and mean opposite things.

**C. P13468.** Every item Wikidata gives the property to, matched to a Gever
member by name, and the value compared against **every field** of that member's
records. CONFIRMED names the field that holds it; CONTRADICTED means the value
appears nowhere, which settles Gever the way run 20 settled OpenParlData.

**D. What else it carries.** The columns that would feed the checks this tool
already has — P569, P106, P102, P768, P580/P582 — counted, plus the distinct
``gremium`` values, so the Kantonsrat's own seat row is *discovered* rather
than guessed at. Run 13's lesson: a name taken from a web search cost a whole
run (``Q19479543`` is a Wikimedia category, held by nobody).

**Nothing here gates anything.** It measures a source no config reads, so no
verdict it returns can change what a pipeline run does, let alone what reaches
``suggestions.qs``. Wiring it into a gate would be a category error, the same
one ``verify.yml`` already avoids for ``verify_kantonsrat``.

Two deliberate choices about *how* it reads:

- **it talks to the CDWS API directly rather than through
  ``GeverBackend``**, even once PR #51 ships. Section B is a claim about what
  the *service* returns, and a backend's normalisation (flattening, list
  wrapping, the 8-digit-string-to-datetime conversion PR #51 documents) sits
  between the service and any answer read through it. A column report taken
  through a normaliser measures the normaliser.
- **the identifier hunt walks every field of the record**, not a list of
  likely names, for the same reason ``discover_identifier_columns`` does: "the
  source keeps it elsewhere" and "the source has never heard of it" mean
  opposite things for a config, and only the second forbids the join.

    uv run python scripts/verify_gever.py
    uv run python scripts/verify_gever.py --limit 200 --verbose
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Make the ``src`` layout importable without installing the package.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wd_parliament.config import load_config  # noqa: E402
from wd_parliament.http_client import HttpClient  # noqa: E402
from wd_parliament.models import P_ZH_MEMBER_ID  # noqa: E402
from wd_parliament.wikidata import WikidataClient, qid_from_uri  # noqa: E402

CONFIRMED = "CONFIRMED"
CONTRADICTED = "CONTRADICTED"
INCONCLUSIVE = "INCONCLUSIVE"

# The canton's Gever, as ``swissparlpy`` PR #51 configures it
# (``swissparlpy/backends/gever_config.py``, instance ``canton_zurich``). The
# section prefix is part of the path: the indexes are spread over several
# CMI "Mandanten" and ``MITGLIEDER`` lives in ``parlzh2`` beside ``BEHOERDEN``,
# ``PARTEIEN`` and ``WAHLKREISE``.
GEVER_BASE = "https://parlzhcdws.cmicloud.ch"
MITGLIEDER_PATH = "/parlzh2/cdws/Index/MITGLIEDER"

# The API's own defaults, spelled out so a reader of the output knows what was
# asked. ``seq > 0`` is CQL for "everything"; the language is a full locale.
DEFAULT_QUERY = "seq > 0"
DEFAULT_LANG = "de-CH"
PAGE_SIZE = 500

# ``xsi:nil="true"`` marks an element as empty rather than absent — the same
# distinction ``parliament.NULL_DATE`` draws for the federal service, and the
# reason an empty field is reported as a column that exists.
NS_RE = re.compile(r"^\{[^}]*\}")
WORD_RE = re.compile(r"[^\w]+", re.UNICODE)

# What the pipeline would want from a source, and the Gever fields that could
# carry it. Candidates only: section B measures which of them exist, and a
# candidate that is absent is reported as absent rather than quietly skipped.
#
# ``geschlecht`` is listed under no property on purpose — P21 is not a check
# this tool has, and adding a column to a probe is not the same as adding a
# check to the diff.
FIELD_CANDIDATES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "P569": ("date of birth", ("geburtsdatum", "geburtsdatum_start")),
    "P106": ("occupation", ("beruf",)),
    "P102": (
        "political party",
        ("parteizugehoerigkeit_kurzname", "parteizugehoerigkeit_name"),
    ),
    "P768": ("electoral district", ("wahlkreis",)),
    "P580": ("start time", ("dauer_start",)),
    "P582": ("end time", ("dauer_end",)),
}

# Fields that identify the *row* rather than the person. Reported apart from
# everything else because the difference decides whether this source could ever
# be joined on at all: a per-membership key is not a person id, and the
# Mitglieder index holds one row per person **per Gremium** (a faction seat, a
# commission seat), which is how ``goifer``'s own example queries it.
ROW_KEY_FIELDS = ("obj_guid", "seq")


# --- pure: parsing the CDWS response ----------------------------------------
def strip_ns(tag: str) -> str:
    """``"{...}Mitglied"`` → ``"mitglied"``. Pure."""
    return NS_RE.sub("", tag or "").lower()


def _is_nil(elem: ET.Element) -> bool:
    for name, value in elem.attrib.items():
        if strip_ns(name) == "nil":
            return str(value).strip().lower() == "true"
    return False


def _add(record: Dict[str, Any], path: str, value: Any) -> None:
    """Record ``path``, turning a repeated element into a list. Pure.

    A field that occurs twice in one record is a list, and a field that occurs
    once is a scalar — the asymmetry PR #51's ``list_fields`` normalises away
    from the schema. It is kept here because this probe reads *values*, and
    :func:`record_values` flattens both shapes the same way.
    """
    if path not in record:
        record[path] = value
        return
    existing = record[path]
    if isinstance(existing, list):
        existing.append(value)
    else:
        record[path] = [existing, value]


def flatten_element(elem: ET.Element, prefix: str = "") -> Dict[str, Any]:
    """One record element → ``path -> value``, nesting joined with ``_``. Pure.

    Attributes are fields too (``OBJ_GUID``, ``SEQ``, ``IDX`` are attributes on
    the record element, not children), and a nil element yields ``None`` rather
    than disappearing: the column exists and is empty, which is not the same as
    the column not existing.
    """
    record: Dict[str, Any] = {}
    for name, value in elem.attrib.items():
        attr = strip_ns(name)
        if attr == "nil":
            continue
        _add(record, f"{prefix}_{attr}" if prefix else attr, value)
    for child in elem:
        path = f"{prefix}_{strip_ns(child.tag)}" if prefix else strip_ns(child.tag)
        if len(child):
            for key, value in flatten_element(child, path).items():
                _add(record, key, value)
        else:
            text = None if _is_nil(child) else (child.text or "").strip() or None
            _add(record, path, text)
            for name, value in child.attrib.items():
                attr = strip_ns(name)
                if attr != "nil":
                    _add(record, f"{path}_{attr}", value)
    return record


def parse_records(content: bytes) -> Tuple[List[Dict[str, Any]], int]:
    """A ``searchdetails`` response → ``(records, numHits)``. Pure.

    ``numHits`` is the total the service reports, not the number of records in
    this page — paging reads it to know when to stop.
    """
    root = ET.fromstring(content)
    try:
        total = int(str(root.attrib.get("numHits", "0")).strip() or 0)
    except ValueError:
        total = 0

    records: List[Dict[str, Any]] = []
    for hit in root.iter():
        if strip_ns(hit.tag) != "hit":
            continue
        for child in hit:
            if strip_ns(child.tag) == "snippet":
                continue
            if any(strip_ns(k) == "obj_guid" for k in child.attrib):
                records.append(flatten_element(child))
                break
    return records, total


def parse_search_fields(content: bytes) -> List[str]:
    """The XSD's ``searchfield`` names — what the API lets you *query* on. Pure.

    Not the same list as the fields a record carries, and the difference is
    worth printing: a source can return a value it cannot be filtered on.
    """
    root = ET.fromstring(content)
    names: List[str] = []
    for elem in root.iter():
        if strip_ns(elem.tag) != "searchfield":
            continue
        for key, value in elem.attrib.items():
            if strip_ns(key) == "name" and value:
                names.append(value)
    return list(dict.fromkeys(names))


# --- pure: the column report -------------------------------------------------
def record_values(record: Dict[str, Any]) -> Dict[str, List[str]]:
    """``path -> the non-empty values at it``, lists flattened. Pure."""
    out: Dict[str, List[str]] = {}
    for path, value in record.items():
        values = value if isinstance(value, list) else [value]
        texts = [str(v).strip() for v in values if v is not None and str(v).strip()]
        out[path] = texts
    return out


def column_report(
    records: Sequence[Dict[str, Any]],
) -> List[Tuple[str, int, Optional[str]]]:
    """``(column, records carrying a value, a sample)`` for every column. Pure.

    Every column *seen* is reported, including one that is empty in every
    record — that is the whole point of printing the list rather than probing
    for the columns one expects.
    """
    filled: Dict[str, int] = {}
    sample: Dict[str, str] = {}
    for record in records:
        for path, values in record_values(record).items():
            filled.setdefault(path, 0)
            if values:
                filled[path] += 1
                sample.setdefault(path, values[0])
    return [(c, filled[c], sample.get(c)) for c in sorted(filled)]


def distinct_values(
    records: Sequence[Dict[str, Any]], column: str
) -> List[Tuple[str, int]]:
    """The distinct values of a column, most common first. Pure."""
    counts: Dict[str, int] = {}
    for record in records:
        for value in record_values(record).get(column, []):
            counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


# --- pure: names ------------------------------------------------------------
def name_key(*parts: Optional[str]) -> str:
    """A name → a comparable key: folded, punctuation-free, order-free. Pure.

    ``"Rueff-Frenkel" + "Sonja"`` and the label ``"Sonja Rueff-Frenkel"`` have
    to meet, so the tokens are sorted rather than kept in order, and the hyphen
    is a separator like any other. Accents are folded because the two sides
    spell them independently.

    This is a *name* match, which this repo trusts for reporting and never for
    writing (``QID_FROM_NAME`` against ``QID_FROM_IDENTIFIER``). It is used
    here only to line two lists up for a value comparison — the finding is
    whether a number appears in a record, and a wrong pairing can only weaken
    that, never manufacture it.
    """
    text = " ".join(str(p) for p in parts if p)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    tokens = [t for t in WORD_RE.split(text.casefold()) if t]
    return " ".join(sorted(tokens))


def index_by_name(
    records: Sequence[Dict[str, Any]],
    name_field: str = "name",
    first_name_field: str = "vorname",
) -> Tuple[Dict[str, List[Dict[str, Any]]], int]:
    """Group records by person. Pure. Returns ``(index, rows with no name)``.

    Every row of a name key is kept: the Mitglieder index holds one row per
    Gremium, so a person's fields are spread across their rows and a value
    hunt that read only the first would miss the rest.
    """
    index: Dict[str, List[Dict[str, Any]]] = {}
    unnamed = 0
    for record in records:
        values = record_values(record)
        last = (values.get(name_field) or [""])[0]
        first = (values.get(first_name_field) or [""])[0]
        key = name_key(last, first)
        if not key:
            unnamed += 1
            continue
        index.setdefault(key, []).append(record)
    return index, unnamed


# --- pure: the identifier hunt ----------------------------------------------
def _norm_id(value: Any) -> str:
    """``'021984'`` and ``21984`` are the same identifier. Pure.

    The same int round-trip ``resolve._normalise_identifier`` applies to P1307,
    so a leading zero cannot read as a disagreement.
    """
    text = str(value).strip()
    try:
        return str(int(text))
    except (TypeError, ValueError):
        return text


def find_identifier_columns(
    index: Dict[str, List[Dict[str, Any]]],
    wanted: Dict[str, str],
) -> Tuple[Dict[str, int], int, int, List[str]]:
    """Which Gever field holds the identifier Wikidata carries? Pure.

    ``wanted`` maps a :func:`name_key` to the identifier value. Returns
    ``(column -> people it matched, people compared, names not in Gever,
    examples)``.

    An empty ``counts`` with a non-zero ``compared`` is the important answer
    and the one run 20 got from OpenParlData: the source carries the number
    nowhere, so no config can join on the property and no ``ADD_IDENTIFIER``
    could ever name the right value.

    A name several people share is skipped, not arbitrated — the rule
    ``resolve.match_by_identifier`` applies to a P1307 two items claim, and
    ``verify_departures`` / ``compare_tenure_dates`` to a doubly-claimed
    ``wikidata_id``. Here it would be worse than a wrong answer: it would
    report a number found on the *other* person's record as a match.
    """
    counts: Dict[str, int] = {}
    compared = 0
    missing = 0
    examples: List[str] = []
    for key, value in sorted(wanted.items()):
        rows = index.get(key)
        if not rows:
            missing += 1
            continue
        compared += 1
        target = _norm_id(value)
        if not target:
            continue
        hit: Set[str] = set()
        for record in rows:
            for column, values in record_values(record).items():
                if any(_norm_id(v) == target for v in values):
                    hit.add(column)
        for column in hit:
            counts[column] = counts.get(column, 0) + 1
        if not hit and len(examples) < 5:
            examples.append(f"{key}: {P_ZH_MEMBER_ID}={value!r} appears in no field")
    ordered = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return ordered, compared, missing, examples


def classify_identifier(
    counts: Dict[str, int], compared: int, matched_names: int
) -> Tuple[str, str]:
    """What the hunt means for the ZH config. Pure."""
    if not matched_names:
        return (
            INCONCLUSIVE,
            "No item carrying "
            f"{P_ZH_MEMBER_ID} could be matched to a Gever member by name, so "
            "nothing was compared. This is a fact about the overlap, not about "
            "the source: the property is mostly held by former members, and "
            "the Mitglieder index is queried here for sitting ones. Widen the "
            "query with --query before reading anything into it.",
        )
    if not counts:
        return (
            CONTRADICTED,
            f"No field of the Gever member record carries the {P_ZH_MEMBER_ID} "
            f"value for any of the {compared} people compared. Gever is the "
            "canton's own system and still does not publish the canton's own "
            "member id — the register behind that property is the "
            "Staatsarchiv's, a different dataset. So this source cannot supply "
            f"{P_ZH_MEMBER_ID} either, and the refusal in config.load_config "
            "stands for a Gever-sourced config as it does for an "
            "OpenParlData-sourced one.",
        )
    best, hits = next(iter(counts.items()))
    if hits == compared:
        return (
            CONFIRMED,
            f"Field {best!r} carries the {P_ZH_MEMBER_ID} value for all "
            f"{compared} people compared. This is the source the property was "
            "waiting on: a config reading Gever could join on "
            f"{P_ZH_MEMBER_ID} and emit ADD_IDENTIFIER with a real number. "
            "Before flipping identifier_verified, note that the join still has "
            "to reach the *chamber* rather than a linked sample — a coverage "
            "rate measured over the people Wikidata already links is not a "
            "coverage rate over the 180 seats.",
        )
    return (
        CONTRADICTED,
        f"Field {best!r} carries the value for {hits} of the {compared} people "
        "compared, and nothing carries it for the rest. A partial match is not "
        "an identifier: either the field is something else that collides "
        "numerically — the exact failure identifier_verified guards against — "
        "or the source holds the id for some people only. Read the per-person "
        "lines before treating it as a join.",
    )


# --- pure: what else the source carries --------------------------------------
def present_columns(
    seen: Iterable[str], candidates: Sequence[str]
) -> List[str]:
    """Which candidate columns the service really returns. Pure."""
    columns = set(seen)
    return [c for c in candidates if c in columns]


def field_coverage(
    records: Sequence[Dict[str, Any]], columns: Sequence[str]
) -> int:
    """How many records carry a value in any of these columns. Pure."""
    total = 0
    for record in records:
        values = record_values(record)
        if any(values.get(column) for column in columns):
            total += 1
    return total


def classify_row_key(
    index: Dict[str, List[Dict[str, Any]]], field: str = "obj_guid"
) -> Tuple[str, str, List[str]]:
    """Is the record key a person or a row? Pure.

    A person with several Gremium rows and one key would be identified by it;
    a person with several rows and several keys would not, and a source with no
    person-level key cannot be joined on an identifier at all — its own or
    anyone's. Reported apart from the P13468 question because it survives that
    question's answer.
    """
    lines: List[str] = []
    multi_row = 0
    multi_key = 0
    for key, rows in sorted(index.items()):
        if len(rows) < 2:
            continue
        multi_row += 1
        keys = {
            v
            for record in rows
            for v in record_values(record).get(field, [])
        }
        if len(keys) > 1:
            multi_key += 1
            if len(lines) < 5:
                lines.append(f"{key}: {len(rows)} row(s), {len(keys)} distinct {field}")
    if not multi_row:
        return (
            INCONCLUSIVE,
            f"No person has more than one row here, so whether {field!r} "
            "identifies the person or the row was not tested.",
            lines,
        )
    if multi_key:
        return (
            CONTRADICTED,
            f"{multi_key} of {multi_row} people with several rows carry several "
            f"distinct {field!r} values, so {field!r} identifies a *membership "
            "row*, not a person. This source has no person-level key of its "
            "own, which is a stronger obstacle than a missing property: a "
            "config reading it would have nothing to join on but names.",
            lines,
        )
    return (
        CONFIRMED,
        f"Every one of the {multi_row} people with several rows carries a "
        f"single {field!r}, so it is a person-level key. It is still not a "
        "*Wikidata* identifier — no property holds a Gever GUID — but it is "
        "what a Gever adapter would group rows by.",
        lines,
    )


# --- the SPARQL side ---------------------------------------------------------
def zh_member_id_query(language: str = "de") -> str:
    """Every item carrying P13468, with its value and label.

    Global rather than bounded by a position, for the reason
    ``wikidata.get_identifier_values`` is: the population that matters is
    "everyone Wikidata gives this property to", and narrowing it to seat
    holders would decide the answer before measuring it.
    """
    return f"""
SELECT ?person ?personLabel ?value WHERE {{
  ?person wdt:{P_ZH_MEMBER_ID} ?value .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},en". }}
}}
"""


def wanted_by_name(bindings: Sequence[dict]) -> Tuple[Dict[str, str], int, int]:
    """WDQS rows → ``name key -> identifier value``. Pure.

    Returns ``(wanted, items read, names dropped)``. A name two items claim is
    dropped rather than arbitrated: the comparison below reads a *value* off
    whichever record the name reaches, and pairing it with the wrong item's
    value would report a disagreement that is really an ambiguity.
    """
    values: Dict[str, List[str]] = {}
    read = 0
    for row in bindings:
        qid = qid_from_uri((row.get("person") or {}).get("value", ""))
        label = (row.get("personLabel") or {}).get("value", "")
        value = (row.get("value") or {}).get("value", "")
        if not qid or not value:
            continue
        read += 1
        key = name_key(label)
        # A label that is still a Q-ID means the item has no label in either
        # language asked for; there is no name to match on.
        if not key or key == qid.casefold():
            continue
        values.setdefault(key, []).append(value)
    wanted = {k: v[0] for k, v in values.items() if len(set(v)) == 1}
    return wanted, read, len(values) - len(wanted)


# --- network ----------------------------------------------------------------
def fetch_index(
    http: HttpClient,
    url: str,
    query: str = DEFAULT_QUERY,
    lang: str = DEFAULT_LANG,
    limit: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """Read an index, paging to ``limit`` records (0 = all)."""
    records: List[Dict[str, Any]] = []
    total = 0
    start = 1
    while True:
        content = _get(
            http,
            f"{url}/searchdetails",
            {"q": query, "l": lang, "m": PAGE_SIZE, "s": start},
        )
        page, total = parse_records(content)
        records.extend(page)
        start += PAGE_SIZE
        if not page or start > total or (limit and len(records) >= limit):
            break
    return (records[:limit] if limit else records), total


def _get(http: HttpClient, url: str, params: Dict[str, Any]) -> bytes:
    """GET through the shared client, which throttles and retries.

    ``HttpClient`` only parses JSON, and CDWS speaks XML — so the session is
    borrowed the same way ``parliament.py`` borrows it for OData, which is what
    keeps the User-Agent and the connection pool shared.
    """
    http._throttle()
    resp = http.session.get(url, params=params, timeout=http.timeout)
    resp.raise_for_status()
    return resp.content


def _verdict(name: str, verdict: str, detail: str) -> None:
    print(f"\n{name}: {verdict}")
    print(f"  {detail}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config/kantonsrat-zh.yaml")
    parser.add_argument(
        "--url",
        default=f"{GEVER_BASE}{MITGLIEDER_PATH}",
        help="The Gever MITGLIEDER index, as swissparlpy PR #51 configures it.",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help=(
            "CQL filter. The default asks for every record; the index holds "
            "one row per person per Gremium, including ended ones."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after this many records (0 = all). Paging is 500 per request.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every column and every distinct Gremium, not the head.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    http = HttpClient(user_agent=config.user_agent, request_delay=config.request_delay)

    # --- A. reach -----------------------------------------------------------
    print("=" * 70)
    print("A. Does the Gever MITGLIEDER index answer, and with how many rows?")
    print("=" * 70)
    print(f"index: {args.url}")
    try:
        schema = _get(http, f"{args.url}/schema", {})
    except Exception as exc:
        print(f"  ! could not read the schema: {exc}")
        print("\nConnectivity, not a finding. Nothing below was measured.")
        return 1

    search_fields = parse_search_fields(schema)
    print(f"searchable fields ({len(search_fields)}): {', '.join(search_fields)}")
    print("  (what the API can filter on — not the same list as the fields a")
    print("   record carries, which section B measures.)")

    try:
        records, total = fetch_index(
            http, args.url, query=args.query, limit=args.limit
        )
    except Exception as exc:
        print(f"  ! could not read the index: {exc}")
        print("\nConnectivity, not a finding. Nothing below was measured.")
        return 1

    print(f"\nquery {args.query!r}: {total} record(s), {len(records)} read")
    index, unnamed = index_by_name(records)
    print(f"distinct people by name: {len(index)}; rows with no name: {unnamed}")
    if not records:
        print("\nThe index answered with nothing. Nothing below was measured.")
        return 1

    # --- B. the real column list -------------------------------------------
    print()
    print("=" * 70)
    print("B. Which fields does the service actually return?")
    print("=" * 70)
    report = column_report(records)
    shown = report if args.verbose else report[:40]
    print(f"{len(report)} column(s) over {len(records)} record(s):")
    for column, filled, sample in shown:
        text = "" if sample is None else f"  e.g. {sample[:48]!r}"
        print(f"  {column:<38} {filled:>5}/{len(records)}{text}")
    if len(shown) < len(report):
        print(f"  ... {len(report) - len(shown)} more (--verbose for all)")

    # --- C. P13468 ----------------------------------------------------------
    print()
    print("=" * 70)
    print(f"C. Does Gever carry {P_ZH_MEMBER_ID}, the canton's own member id?")
    print("=" * 70)
    wikidata = WikidataClient(http, language=config.language)
    try:
        bindings = wikidata.run_query(zh_member_id_query(config.language))
    except Exception as exc:
        print(f"  ! could not read Wikidata: {exc}")
        bindings = []

    verdict, detail = INCONCLUSIVE, "Wikidata could not be read."
    if bindings:
        wanted, read, dropped = wanted_by_name(bindings)
        print(f"items carrying {P_ZH_MEMBER_ID}: {read}")
        print(f"  usable names: {len(wanted)}; dropped as ambiguous: {dropped}")
        counts, compared, missing, examples = find_identifier_columns(index, wanted)
        print(f"  matched to a Gever member: {compared}; not in this index: {missing}")
        if counts:
            print("  fields carrying the value:")
            for column, hits in list(counts.items())[:10]:
                print(f"    {column:<36} {hits}/{compared}")
        else:
            print("  fields carrying the value: none")
        for line in examples:
            print(f"    {line}")
        verdict, detail = classify_identifier(counts, compared, compared)
    _verdict(f"Verdict ({P_ZH_MEMBER_ID})", verdict, detail)

    print("\ndoes the record key identify a person or a Gremium row?")
    key_verdict, key_detail, key_lines = classify_row_key(index)
    for line in key_lines:
        print(f"  {line}")
    _verdict("Verdict (is the record key a person?)", key_verdict, key_detail)

    # --- D. what else -------------------------------------------------------
    print()
    print("=" * 70)
    print("D. What else could this source feed?")
    print("=" * 70)
    seen = [column for column, _, _ in report]
    for prop, (label, candidates) in FIELD_CANDIDATES.items():
        columns = present_columns(seen, candidates)
        if not columns:
            print(f"  {prop} {label:<20} no such column: {', '.join(candidates)}")
            continue
        covered = field_coverage(records, columns)
        print(
            f"  {prop} {label:<20} {covered:>5}/{len(records)} "
            f"via {', '.join(columns)}"
        )
    print()
    print("  row keys (identify the row, not the person):")
    for column in ROW_KEY_FIELDS:
        if column in seen:
            values = distinct_values(records, column)
            print(f"    {column:<36} {len(values)} distinct")

    gremien = distinct_values(records, "gremiumname") or distinct_values(
        records, "gremium"
    )
    print(f"\n  distinct Gremium values: {len(gremien)}")
    for value, count in (gremien if args.verbose else gremien[:15]):
        print(f"    {count:>5}  {value}")
    if not args.verbose and len(gremien) > 15:
        print(f"    ... {len(gremien) - 15} more (--verbose for all)")
    print(
        "\n  Which of these is the Kantonsrat's own seat is a question for a\n"
        "  reader of this list, not for a name guessed in advance — run 13\n"
        "  spent a whole run on a Q-ID that turned out to be a category."
    )

    print()
    print("=" * 70)
    print(f"{P_ZH_MEMBER_ID}: {verdict} | record key: {key_verdict}")
    print("Nothing here gates the pipeline: no config reads this source.")
    print("=" * 70)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
