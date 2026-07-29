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

from .models import MODEL_PERIOD, STATEMENT_MODELS, Body

DEFAULT_USER_AGENT = "wd-parliament/0.1 (+https://github.com/metaodi/wd-parliament)"
DEFAULT_BIOGRAPHY_URL = "https://www.parlament.ch/{language}/biografie/wd/{person_number}"

# Placeholder fragments that mark a User-Agent as not-yet-filled-in. The
# Wikimedia APIs require a real contact, so the tool refuses to run with one.
_PLACEHOLDER_AGENTS = ("example.com", "example.org", "you@", "your@", "changeme")


@dataclass
class Config:
    language: str = "de"  # parlament.ch content language, and label preference
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
    # Emitting QuickStatements is opt-out: the file is always written, but this
    # lets an operator turn it off entirely while the statement model is still
    # being confirmed against live Wikidata.
    quickstatements: bool = True

    @property
    def councils(self) -> List[str]:
        return [b.council for b in self.bodies]

    def body_for(self, council: str) -> Optional[Body]:
        for b in self.bodies:
            if b.council == council:
                return b
        return None

    def canton_qid(self, abbreviation: Optional[str]) -> Optional[str]:
        """The Q-ID for a canton abbreviation, or ``None`` when unmapped."""
        if not abbreviation:
            return None
        return self.cantons.get(abbreviation.strip().upper())

    def party_qid(self, abbreviation: Optional[str]) -> Optional[str]:
        if not abbreviation:
            return None
        return self.parties.get(abbreviation.strip())

    def parl_group_qid(self, abbreviation: Optional[str]) -> Optional[str]:
        if not abbreviation:
            return None
        return self.parl_groups.get(abbreviation.strip())

    def biography_url_for(self, person_number: int) -> str:
        return self.biography_url.format(
            language=self.language, person_number=person_number
        )

    @property
    def position_qids(self) -> List[str]:
        return [b.position_qid for b in self.bodies]


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
        bodies.append(
            Body(
                council=council,
                label=str(item.get("label") or council),
                position_qid=position,
                council_number=int(number) if number is not None else None,
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

    cfg = Config(
        language=str(data.get("language", "de")),
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
        quickstatements=bool(data.get("quickstatements", True)),
    )

    if not cfg.bodies:
        raise ValueError("Config must define at least one entry under 'bodies'.")
    if not cfg.user_agent or any(
        marker in cfg.user_agent.lower() for marker in _PLACEHOLDER_AGENTS
    ):
        raise ValueError(
            "Please set a descriptive 'user_agent' with a contact URL/e-mail; "
            "the Wikimedia APIs require it."
        )
    return cfg
