"""Tests for YAML config loading and validation."""

import pytest

from wd_parliament.config import load_config

MINIMAL = """
user_agent: "wd-parliament/0.1 (https://example.invalid/wd-parliament; me@somewhere.ch)"
bodies:
  - council: N
    label: Swiss National Council
    position: Q18510612
"""


def write(tmp_path, text):
    path = tmp_path / "parliament.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_config(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.councils == ["N"]
    assert cfg.body_for("N").position_qid == "Q18510612"
    assert cfg.body_for("S") is None
    assert cfg.statement_model == "period"  # the default
    assert cfg.group_by == "canton"
    assert cfg.quickstatements is True


def test_the_shipped_config_loads():
    cfg = load_config("config/parliament.yaml")
    assert [b.council for b in cfg.bodies] == ["NR", "SR"]
    assert cfg.position_qids == ["Q18510612", "Q18510613"]
    assert len(cfg.cantons) == 26
    assert cfg.canton_qid("ZH") == "Q11943"


def test_a_placeholder_user_agent_is_rejected(tmp_path):
    text = MINIMAL.replace("me@somewhere.ch", "you@example.com")
    with pytest.raises(ValueError, match="user_agent"):
        load_config(write(tmp_path, text))


def test_an_empty_user_agent_is_rejected(tmp_path):
    text = MINIMAL.replace(
        'user_agent: "wd-parliament/0.1 (https://example.invalid/wd-parliament; me@somewhere.ch)"',
        'user_agent: ""',
    )
    with pytest.raises(ValueError, match="user_agent"):
        load_config(write(tmp_path, text))


def test_a_config_without_bodies_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="bodies"):
        load_config(write(tmp_path, MINIMAL.split("bodies:")[0]))


def test_a_body_without_a_position_is_rejected(tmp_path):
    text = MINIMAL.replace("    position: Q18510612\n", "")
    with pytest.raises(ValueError, match="position"):
        load_config(write(tmp_path, text))


def test_a_non_qid_position_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="not a Q-ID"):
        load_config(write(tmp_path, MINIMAL.replace("Q18510612", "18510612")))


def test_an_unknown_statement_model_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="statement_model"):
        load_config(write(tmp_path, MINIMAL + "statement_model: yearly\n"))


def test_both_statement_models_are_accepted(tmp_path):
    for model in ("tenure", "period"):
        cfg = load_config(write(tmp_path, MINIMAL + f"statement_model: {model}\n"))
        assert cfg.statement_model == model


def test_an_unknown_group_by_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="group_by"):
        load_config(write(tmp_path, MINIMAL + "group_by: party\n"))


def test_a_blank_qid_is_treated_as_not_known_yet(tmp_path):
    """A deliberate 'not filled in' marker, not an error."""
    cfg = load_config(write(tmp_path, MINIMAL + "cantons:\n  ZH: Q11943\n  BE:\n"))
    assert cfg.cantons == {"ZH": "Q11943"}
    assert cfg.canton_qid("BE") is None


def test_a_malformed_canton_qid_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="cantons"):
        load_config(write(tmp_path, MINIMAL + "cantons:\n  ZH: Zurich\n"))


def test_canton_lookup_normalises_case_and_whitespace(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + "cantons:\n  ZH: Q11943\n"))
    assert cfg.canton_qid(" zh ") == "Q11943"
    assert cfg.canton_qid(None) is None


def test_terms_are_keyed_by_period_number(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + "terms:\n  51: Q51\n  52: Q52\n"))
    assert cfg.terms == {51: "Q51", 52: "Q52"}


def test_a_non_numeric_term_key_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="terms"):
        load_config(write(tmp_path, MINIMAL + "terms:\n  fifty: Q51\n"))


def test_a_malformed_term_qid_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="terms"):
        load_config(write(tmp_path, MINIMAL + "terms:\n  51: 51\n"))


def test_biography_url_substitution(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + "language: fr\n"))
    assert cfg.biography_url_for(1108) == "https://www.parlament.ch/fr/biografie/wd/1108"


def test_quickstatements_can_be_disabled(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + "quickstatements: false\n"))
    assert cfg.quickstatements is False


# --- the second source ------------------------------------------------------
def test_a_config_without_an_enrich_block_reads_one_source(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.enrich.enabled is False
    assert cfg.enrich.source == ""


def test_the_enrich_block_is_read(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + """
enrich:
  source: openparldata
  body_key: CHE
  groups:
    NR: Nationalrat
    SR: Ständerat
"""))
    assert cfg.enrich.enabled is True
    assert cfg.enrich.body_key == "CHE"
    assert cfg.enrich.groups == {"NR": "Nationalrat", "SR": "Ständerat"}
    assert cfg.enrich.source_name == "OpenParlData"


def test_an_unknown_enrich_source_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="enrich.source must be one of"):
        load_config(write(tmp_path, MINIMAL + "\nenrich:\n  source: wikipedia\n"))


def test_an_enrich_source_without_groups_is_rejected(tmp_path):
    """Without them there is no chamber to compare, and a substring match on
    'Nationalrat' would take a committee of the chamber for the chamber."""
    with pytest.raises(ValueError, match="needs 'groups'"):
        load_config(write(tmp_path, MINIMAL + "\nenrich:\n  source: openparldata\n"))


def test_a_source_cannot_corroborate_itself(tmp_path):
    with pytest.raises(ValueError, match="cannot corroborate itself"):
        load_config(write(tmp_path, MINIMAL + """
enrich:
  source: parlament
  groups:
    NR: Nationalrat
"""))


# --- the identifier property and its value ----------------------------------
def test_a_measured_property_is_verified_by_default(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.identifier_property == "P1307"
    assert cfg.identifier_verified is True


def test_an_unmeasured_property_defaults_to_unverified(tmp_path):
    """Provenance and value are two questions, and only the first is settled.

    Wikidata asserts P13468, which is what ``is_mechanical`` can see. That its
    value equals the source's person id is what nothing has measured, and a
    config joining on it must default to saying so rather than to silence.
    """
    cfg = load_config(write(tmp_path, MINIMAL + "identifier_property: P13468\n"))
    assert cfg.identifier_verified is False


def test_claiming_an_unmeasured_property_is_verified_is_rejected(tmp_path):
    """The one claim ``is_mechanical`` writes real edits off the back of."""
    text = MINIMAL + "identifier_property: P13468\nidentifier_verified: true\n"
    with pytest.raises(ValueError, match="has not been measured"):
        load_config(write(tmp_path, text))


def test_an_unknown_identifier_property_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="identifier_property"):
        load_config(write(tmp_path, MINIMAL + "identifier_property: P9999999\n"))


def test_the_shipped_cantonal_config_joins_on_the_cantons_own_id():
    cfg = load_config("config/kantonsrat-zh.yaml")
    assert cfg.identifier_property == "P13468"
    assert cfg.identifier_verified is False
    assert cfg.quickstatements is False
