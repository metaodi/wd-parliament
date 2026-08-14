"""Load and validate the YAML configuration.

Everything that is *data* — the P39 position items, the 26 canton Q-IDs, the
party and parliamentary-group mappings — lives in ``config/parliament.yaml``
rather than in code, so a renamed party or a corrected Q-ID never needs a code
change. Unknown values are skipped, never guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .models import (
    IDENTIFIER_PROPERTIES,
    MODEL_PERIOD,
    PERSON_DATA_BY_PROPERTY,
    PERSON_DATA_PROPERTIES,
    P_PARLIAMENT_ID,
    P_ZH_MEMBER_ID,
    STATEMENT_MODELS,
    VERIFIED_IDENTIFIER_PROPERTIES,
    Body,
    IdentifierProperty,
    PersonDataCheck,
)

# Which source a config reads its members from. "parlament" is the parlament.ch
# OData service (the Federal Assembly); "openparldata" is api.openparldata.ch,
# which covers the cantons and cities as well.
SOURCE_PARLAMENT = "parlament"
SOURCE_OPENPARLDATA = "openparldata"
# The Staatsarchiv's KR-Daten register, read from the KRRR application's Excel
# export. The only cantonal source whose identifier join is *verified*: run 28
# (2026-08-10) found P13468's value in ``id_person_new`` for 591 of 638 people
# (92.6%). See ``krdaten.py``.
SOURCE_KRDATEN = "krdaten"
SOURCES = (SOURCE_PARLAMENT, SOURCE_OPENPARLDATA, SOURCE_KRDATEN)

DEFAULT_USER_AGENT = "wd-parliament/0.1 (+https://github.com/metaodi/wd-parliament)"
# Left unset, the biography link is the record page of the property the run
# joins on — which is what the number beside a member in the report *is*. See
# ``Config.biography_url_for``.
DEFAULT_BIOGRAPHY_URL = ""

# Placeholder fragments that mark a User-Agent as not-yet-filled-in. The
# Wikimedia APIs require a real contact, so the tool refuses to run with one.
_PLACEHOLDER_AGENTS = ("example.com", "example.org", "you@", "your@", "changeme")


@dataclass
class EnrichmentConfig:
    """A *second* source, read only to contradict the first.

    Opt-in and absent by default: a run reads one source unless a config says
    otherwise, and that stays the plain reading of every other module. What it
    can do is bounded by :mod:`enrich` — it produces no members, and a
    disagreement can only withhold a mechanical edit, never supply a value.
    """

    source: str = ""  # "" disables it
    body_key: str = ""
    # ``council`` -> the group name that **is** that chamber in the second
    # source. Matched by equality, so it must be exact; "Nationalrat" is the
    # chamber and "Büro NR" is not.
    groups: Dict[str, str] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.source and self.groups)

    @property
    def source_name(self) -> str:
        if self.source == SOURCE_OPENPARLDATA:
            return "OpenParlData"
        if self.source == SOURCE_KRDATEN:
            return "KR-Daten"
        return self.source


@dataclass
class Config:
    language: str = "de"  # source content language, and label preference
    source: str = SOURCE_PARLAMENT
    # OpenParlData only: the body (level of parliament) the chambers sit under.
    body_key: str = ""
    # The Wikidata property whose value equals the source's person identifier.
    # Federally P1307 == MemberCouncil.PersonNumber. Cantonally there is no
    # P1307 at all: the Kantonsrat is joined on P14527 (the OpenParlData ID),
    # which is the only identifier this source can actually supply a value for
    # — P13468, the canton's own member id, is the property Wikidata uses for
    # these people, but run 20 found its values in no column of OpenParlData.
    identifier_property: str = P_PARLIAMENT_ID
    # Has the property's **value** been shown to equal the source's person id?
    #
    # Provenance and value are two different questions, and only the first one
    # ``quickstatements.is_mechanical`` can see. A Wikidata-asserted identifier
    # whose value belongs to a *different* id space joins exactly and joins the
    # wrong people — the failure an unverified property can produce that an
    # unmatched member cannot. So a config naming a property outside
    # ``VERIFIED_IDENTIFIER_PROPERTIES`` must set this to false, which:
    #
    #   * makes ``resolve`` corroborate every identifier match against the
    #     item's own name and birth date, and count the ones it rejects;
    #   * stamps ``identifier_unverified`` on every suggestion, which
    #     ``is_mechanical`` refuses — nothing is written off an unproven join;
    #   * says so in the ADD_IDENTIFIER suggestion, whose value would be the
    #     wrong number if the id spaces differ.
    #
    # Flipping it to true is a measurement, not an opinion: run the probe named
    # in the config comments and read CONFIRMED first.
    identifier_verified: bool = True
    # Every identifier property this run reports as missing, the join property
    # included. A parliament has **two** identifiers worth recording on an item
    # and they are different things: its own member id (P1307 federally, P13468
    # for the Kantonsrat) and the OpenParlData id (P14527) of the record an
    # aggregator keeps about the same person. Whichever of them this run joins
    # on, the other is still a fact the item ought to carry, and only one of
    # them can be joined on at a time — so reporting is the only way the second
    # one ever gets recorded.
    #
    # Only ``identifier_property``'s value is in hand (it is the source's own
    # person id, which is what the join *means*); the rest are reported without
    # one, because no source this pipeline reads publishes them. That is the
    # whole difference between ``ADD_IDENTIFIER`` and ``MISSING_IDENTIFIER``.
    identifiers: List[str] = field(default_factory=list)
    user_agent: str = DEFAULT_USER_AGENT
    request_delay: float = 1.0
    # "tenure" (one P39 per continuous tenure) or "period" (one per legislature).
    # See the module docstring of ``diff`` and the README for why this matters.
    statement_model: str = MODEL_PERIOD
    group_by: str = "canton"  # "canton" or "group" — report grouping only
    biography_url: str = DEFAULT_BIOGRAPHY_URL
    bodies: List[Body] = field(default_factory=list)
    cantons: Dict[str, str] = field(default_factory=dict)  # "ZH" -> "Q11943"
    parties: Dict[str, str] = field(default_factory=dict)  # "SVP" -> "Q..."
    parl_groups: Dict[str, str] = field(default_factory=dict)  # "V" -> "Q..."
    # LegislativePeriodNumber -> the Q-ID of that legislature's Wikidata item,
    # i.e. the value a P2937 qualifier takes. Periods missing from this map
    # simply produce no P2937 suggestion; see ``diff._term_qids``.
    terms: Dict[int, str] = field(default_factory=dict)  # 52 -> "Q..."
    # Which personal-data properties to check for presence (README step 9).
    # A list of property ids from ``models.PERSON_DATA_PROPERTIES``; an empty
    # list turns the whole class of check off, including the SPARQL query it
    # needs. Defaults to all of them, which is what asking for the check means.
    person_data: List[str] = field(
        default_factory=lambda: list(PERSON_DATA_PROPERTIES)
    )
    # Emitting QuickStatements is opt-out: the file is always written, but this
    # lets an operator turn it off entirely while the statement model is still
    # being confirmed against live Wikidata.
    quickstatements: bool = True
    enrich: EnrichmentConfig = field(default_factory=EnrichmentConfig)

    @property
    def source_name(self) -> str:
        """What to call the source in user-facing text.

        The reports and suggestion texts name the source ("parlament.ch says
        they are still sitting"), and naming the wrong one is not cosmetic: it
        tells a reader to go and check a service that has never heard of this
        member.
        """
        if self.source == SOURCE_OPENPARLDATA:
            return "OpenParlData"
        if self.source == SOURCE_KRDATEN:
            return "the Staatsarchiv's KR-Daten register"
        return "parlament.ch"

    @property
    def district_label(self) -> str:
        """What the P768 value is *called* here.

        Federally the electoral district is the canton; for a cantonal chamber
        it is the Wahlkreis. Only affects headings.
        """
        return "Canton" if self.source == SOURCE_PARLAMENT else "Electoral district"

    @property
    def councils(self) -> List[str]:
        return [b.council for b in self.bodies]

    def body_for(self, council: str) -> Optional[Body]:
        for b in self.bodies:
            if b.council == council:
                return b
        return None

    def canton_qid(self, abbreviation: Optional[str]) -> Optional[str]:
        """The Q-ID for a canton or electoral district, or ``None``. 

        **The key is not always an abbreviation.** Federally it is ``ZH``, and
        upper-casing the lookup is what makes ``zh`` from the source match.
        Cantonally it is the district's whole name — ``9. Wahlkreis
        (Horgen)`` — and upper-casing that matches nothing at all, which was
        found the moment the ZH map was first filled in: eighteen entries, zero
        lookups, no error, and a run that would have reported "no districts to
        suggest" while holding all of them.

        So the comparison folds case and collapses whitespace on both sides.
        That keeps the federal behaviour exactly (``ZH`` still matches ``zh``)
        and makes the cantonal one work, including the register's untidy
        spacing (``'I      Zürich 1+2'``).
        """
        if not abbreviation:
            return None
        wanted = _fold_key(abbreviation)
        for key, qid in self.cantons.items():
            if _fold_key(key) == wanted:
                return qid
        return None

    def party_qid(self, abbreviation: Optional[str]) -> Optional[str]:
        if not abbreviation:
            return None
        return self.parties.get(abbreviation.strip())

    def parl_group_qid(self, abbreviation: Optional[str]) -> Optional[str]:
        if not abbreviation:
            return None
        return self.parl_groups.get(abbreviation.strip())

    @property
    def identifier_checks(self) -> List[IdentifierProperty]:
        """The identifier properties to report on, the join property first.

        The join property is always included, whatever ``identifiers`` says:
        it is the one whose value the run holds, so its ``ADD_IDENTIFIER`` is
        the highest-leverage edit in the report and dropping it silently would
        be a strange thing for a config key to be able to do.
        """
        wanted = [self.identifier_property] + [
            p for p in self.identifiers if p != self.identifier_property
        ]
        return [IDENTIFIER_PROPERTIES[p] for p in wanted if p in IDENTIFIER_PROPERTIES]

    @property
    def join_identifier(self) -> Optional[IdentifierProperty]:
        """The registry entry for the property this run joins on."""
        return IDENTIFIER_PROPERTIES.get(self.identifier_property)

    def identifier_url(self, property_id: str, value: object) -> Optional[str]:
        """The record page for one identifier value, or ``None`` if unknown."""
        prop = IDENTIFIER_PROPERTIES.get(property_id)
        if prop is None or value in (None, ""):
            return None
        return prop.url_for(value, self.language)

    def biography_url_for(self, person_number: int) -> str:
        """The source record for a person, as the reports link it.

        The number the reports print beside a member is the **join property's**
        value, so left unconfigured this is that property's record page — the
        one page guaranteed to know that number. A config may override it, and
        one that does is responsible for the same correspondence: the ZH report
        used to print an OpenParlData person id linked to the Kantonsrat's
        member list, which knows nothing about it.

        Both ``{person_number}`` and ``{id}`` name the value, so a template
        copied from :data:`~.models.IDENTIFIER_PROPERTIES` works as it stands.
        """
        template = self.biography_url
        if not template:
            prop = self.join_identifier
            if prop is None:  # pragma: no cover - load_config rejects these
                return ""
            template = prop.url_template
        return template.format(
            language=self.language, person_number=person_number, id=person_number
        )

    @property
    def position_qids(self) -> List[str]:
        return [b.position_qid for b in self.bodies]

    @property
    def person_data_checks(self) -> List[PersonDataCheck]:
        """The enabled personal-data checks, in the canonical order."""
        enabled = set(self.person_data)
        return [c for c in PERSON_DATA_BY_PROPERTY.values() if c.property_id in enabled]


def _fold_key(value: object) -> str:
    """A map key and a source value, made comparable. Pure.

    Case-folded with runs of whitespace collapsed, because the two sides are
    written by different hands: a config says ``9. Wahlkreis (Horgen)`` and a
    source may say ``9.  Wahlkreis  (Horgen)``, and a canton is ``ZH`` in one
    place and ``zh`` in another.
    """
    return " ".join(str(value or "").split()).casefold()


def _as_qid_map(raw: Optional[dict], what: str) -> Dict[str, str]:
    """Normalise a ``key: Qnnn`` mapping, dropping empty values.

    A key mapped to nothing (``ZH:`` with no value) is a deliberate "not known
    yet" marker and is skipped rather than treated as an error — the diff then
    simply makes no suggestion that would need it.
    """
    out: Dict[str, str] = {}
    for key, value in (raw or {}).items():
        if value is None or str(value).strip() == "":
            continue
        qid = str(value).strip()
        if not qid.startswith("Q") or not qid[1:].isdigit():
            raise ValueError(f"{what}: '{key}' maps to '{qid}', which is not a Q-ID.")
        out[str(key).strip()] = qid
    return out


def _as_term_map(raw: Optional[dict]) -> Dict[int, str]:
    """Normalise the ``LegislativePeriodNumber -> Q-ID`` map for P2937."""
    out: Dict[int, str] = {}
    for key, value in (raw or {}).items():
        if value is None or str(value).strip() == "":
            continue
        try:
            number = int(str(key).strip())
        except ValueError:
            raise ValueError(
                f"terms: '{key}' is not a legislative period number."
            ) from None
        qid = str(value).strip()
        if not qid.startswith("Q") or not qid[1:].isdigit():
            raise ValueError(f"terms: '{key}' maps to '{qid}', which is not a Q-ID.")
        out[number] = qid
    return out


def _as_identifiers(raw: object, identifier_property: str) -> List[str]:
    """Normalise the ``identifiers`` list of identifier property ids.

    Absent means "just the one this run joins on", which is what the tool did
    before the key existed. An unknown property is an **error** for the same
    reason it is in ``person_data``: it selects a code path rather than
    supplying a value, and a typo would silently drop a check somebody asked
    for — here, the second of a parliament's two identifiers, which is the only
    way that one ever gets reported at all.
    """
    if raw is None:
        return [identifier_property]
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise ValueError(
            "identifiers must be a list of property ids, e.g. [P1307, P14527]; "
            f"got {raw!r}."
        )
    out: List[str] = [identifier_property]
    for item in raw:
        prop = str(item).strip().upper()
        if prop not in IDENTIFIER_PROPERTIES:
            raise ValueError(
                f"identifiers: '{prop}' is not a known identifier property. "
                f"Known: {', '.join(sorted(IDENTIFIER_PROPERTIES))}. Adding one "
                "means adding an IdentifierProperty in models.py — with the URL "
                "template its values resolve through — not just a line here."
            )
        if prop not in out:
            out.append(prop)
    return out


def _as_person_data(raw: object) -> List[str]:
    """Normalise the ``person_data`` list of property ids.

    Absent means "all of them" — a config that says nothing about these checks
    gets them, because they need no per-parliament data to be safe. An explicit
    empty list means "none", which is how a config turns them off. An unknown
    property is an error rather than a skip: unlike a Q-ID map, where a missing
    key is a value nobody has established yet, a property id here is a *code*
    path, and a typo would silently drop a check the operator asked for.
    """
    if raw is None:
        return list(PERSON_DATA_PROPERTIES)
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise ValueError(
            "person_data must be a list of property ids, e.g. [P19, P106]; "
            f"got {raw!r}."
        )
    out: List[str] = []
    for item in raw:
        prop = str(item).strip().upper()
        if prop not in PERSON_DATA_BY_PROPERTY:
            raise ValueError(
                f"person_data: '{prop}' is not a supported personal-data "
                f"property. Supported: {', '.join(PERSON_DATA_PROPERTIES)}. "
                "Adding one means adding a PersonDataCheck in models.py and a "
                "source field for it, not just a line here."
            )
        if prop not in out:
            out.append(prop)
    return out


def load_config(path: str | Path) -> Config:
    """Read a YAML config file into a :class:`Config`."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    bodies: List[Body] = []
    for item in data.get("bodies") or []:
        if not isinstance(item, dict):
            raise ValueError("Each entry under 'bodies' must be a mapping.")
        council = str(item.get("council", "")).strip()
        position = str(item.get("position", "")).strip()
        if not council or not position:
            raise ValueError(
                "Each entry under 'bodies' needs a 'council' (N/S) and a "
                "'position' Q-ID."
            )
        if not position.startswith("Q") or not position[1:].isdigit():
            raise ValueError(f"bodies: '{position}' is not a Q-ID.")
        number = item.get("council_number")
        group_id = item.get("group_id")
        bodies.append(
            Body(
                council=council,
                label=str(item.get("label") or council),
                position_qid=position,
                council_number=int(number) if number is not None else None,
                group_id=int(group_id) if group_id is not None else None,
                group_name=(
                    str(item["group_name"]).strip()
                    if item.get("group_name")
                    else None
                ),
            )
        )

    statement_model = str(data.get("statement_model", MODEL_PERIOD)).strip()
    if statement_model not in STATEMENT_MODELS:
        raise ValueError(
            f"statement_model must be one of {', '.join(STATEMENT_MODELS)}; "
            f"got '{statement_model}'."
        )

    group_by = str(data.get("group_by", "canton")).strip()
    if group_by not in ("canton", "group"):
        raise ValueError(f"group_by must be 'canton' or 'group'; got '{group_by}'.")

    source = str(data.get("source", SOURCE_PARLAMENT)).strip()
    if source not in SOURCES:
        raise ValueError(
            f"source must be one of {', '.join(SOURCES)}; got '{source}'."
        )

    identifier_property = str(
        data.get("identifier_property", P_PARLIAMENT_ID)
    ).strip().upper()
    if identifier_property not in IDENTIFIER_PROPERTIES:
        raise ValueError(
            "identifier_property must be one of "
            f"{', '.join(sorted(IDENTIFIER_PROPERTIES))}; got "
            f"'{identifier_property}'. It is the property whose value equals "
            "the source's person id, and quickstatements.is_mechanical trusts "
            "a match made through it — so a property nothing on Wikidata "
            "asserts must not be added here at all, and one whose value has "
            "not been measured against the source's person id must be joined "
            "on with 'identifier_verified: false'."
        )

    # Default: true for a property whose value has been measured against the
    # source's person id, false for one that has not. Stating it explicitly is
    # allowed and is how a config records the day a probe confirms it; claiming
    # true for an unmeasured property is not, because that claim is exactly
    # what ``is_mechanical`` would write edits off the back of.
    # Measured and falsified, so it is refused rather than documented. Run 20
    # (2026-08-04) compared P13468 against every column of OpenParlData's ZH
    # person records: its values are the canton's own member ids and appear in
    # **no** column of this source. Joining on it here would compare a person id
    # to a different id space — matching nobody at best, and the wrong people at
    # worst — and every ADD_IDENTIFIER suggestion would offer a number that is
    # not this property's. The right source for it is the canton's own dataset
    # (README step 7); the wrong fix is to try this pairing again.
    if (
        identifier_property == P_ZH_MEMBER_ID
        and source == SOURCE_OPENPARLDATA
    ):
        raise ValueError(
            f"{P_ZH_MEMBER_ID} cannot be joined on from source "
            f"'{SOURCE_OPENPARLDATA}': its values are the canton of Zürich's "
            "own member ids, and run 20 found them in no column of that "
            "source's person records (0 of 28 compared). Join on a property "
            "this source supplies, or read the canton's dataset — see README "
            "step 7."
        )

    identifier_verified = bool(
        data.get(
            "identifier_verified",
            identifier_property in VERIFIED_IDENTIFIER_PROPERTIES,
        )
    )
    if identifier_verified and identifier_property not in VERIFIED_IDENTIFIER_PROPERTIES:
        raise ValueError(
            f"identifier_verified: true claims that {identifier_property}'s "
            "value equals the source's person id, which has not been measured. "
            "Run the probe (scripts/verify_kantonsrat.py section C for a "
            "cantonal config, scripts/verify_source.py section B federally), "
            "read CONFIRMED, and add the property to "
            "models.VERIFIED_IDENTIFIER_PROPERTIES in the same commit as the "
            "evidence."
        )

    enrich_raw = data.get("enrich") or {}
    if not isinstance(enrich_raw, dict):
        raise ValueError("'enrich' must be a mapping.")
    enrich_source = str(enrich_raw.get("source", "") or "").strip()
    if enrich_source and enrich_source not in SOURCES:
        raise ValueError(
            f"enrich.source must be one of {', '.join(SOURCES)}; "
            f"got '{enrich_source}'."
        )
    enrich = EnrichmentConfig(
        source=enrich_source,
        body_key=str(enrich_raw.get("body_key", "") or "").strip(),
        groups={
            str(k).strip(): str(v).strip()
            for k, v in (enrich_raw.get("groups") or {}).items()
            if str(v or "").strip()
        },
    )
    if enrich_source and not enrich.groups:
        raise ValueError(
            "enrich.source needs 'groups' mapping each council to the group "
            "name that IS that chamber in the second source (e.g. NR: "
            "Nationalrat). It is matched by exact name, because a substring "
            "match would take a committee of the chamber for the chamber."
        )

    cfg = Config(
        language=str(data.get("language", "de")),
        source=source,
        body_key=str(data.get("body_key", "") or "").strip(),
        identifier_property=identifier_property,
        identifier_verified=identifier_verified,
        identifiers=_as_identifiers(data.get("identifiers"), identifier_property),
        user_agent=str(data.get("user_agent", DEFAULT_USER_AGENT)),
        request_delay=float(data.get("request_delay", 1.0)),
        statement_model=statement_model,
        group_by=group_by,
        biography_url=str(data.get("biography_url", DEFAULT_BIOGRAPHY_URL)),
        bodies=bodies,
        cantons=_as_qid_map(data.get("cantons"), "cantons"),
        parties=_as_qid_map(data.get("parties"), "parties"),
        parl_groups=_as_qid_map(data.get("parl_groups"), "parl_groups"),
        terms=_as_term_map(data.get("terms")),
        person_data=_as_person_data(data.get("person_data")),
        quickstatements=bool(data.get("quickstatements", True)),
        enrich=enrich,
    )
    if enrich.enabled and enrich.source == cfg.source and enrich.body_key == cfg.body_key:
        raise ValueError(
            "enrich must name a *different* source from the one members are "
            "read from; a source cannot corroborate itself."
        )

    if not cfg.bodies:
        raise ValueError("Config must define at least one entry under 'bodies'.")
    if cfg.source == SOURCE_OPENPARLDATA and not cfg.body_key:
        raise ValueError(
            "source 'openparldata' needs a 'body_key' (the canton or city whose "
            "parliament this is, e.g. ZH). A body is the *level* of parliament "
            "there, and the chambers are groups under it."
        )
    if not cfg.user_agent or any(
        marker in cfg.user_agent.lower() for marker in _PLACEHOLDER_AGENTS
    ):
        raise ValueError(
            "Please set a descriptive 'user_agent' with a contact URL/e-mail; "
            "the Wikimedia APIs require it."
        )
    return cfg
