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
    assert cfg.group_by == "constituency"
    assert cfg.quickstatements is True


def test_the_shipped_config_loads():
    cfg = load_config("config/parliament.yaml")
    assert [b.council for b in cfg.bodies] == ["NR", "SR"]
    assert cfg.position_qids == ["Q18510612", "Q18510613"]
    assert len(cfg.constituencies) == 26
    assert cfg.constituency_qid("ZH") == "Q11943"


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
    cfg = load_config(write(tmp_path, MINIMAL + "constituencies:\n  ZH: Q11943\n  BE:\n"))
    assert cfg.constituencies == {"ZH": "Q11943"}
    assert cfg.constituency_qid("BE") is None


def test_a_malformed_constituency_qid_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="constituencies"):
        load_config(write(tmp_path, MINIMAL + "constituencies:\n  ZH: Zurich\n"))


def test_constituency_lookup_normalises_case_and_whitespace(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + "constituencies:\n  ZH: Q11943\n"))
    assert cfg.constituency_qid(" zh ") == "Q11943"
    assert cfg.constituency_qid(None) is None


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

    Wikidata asserts P14527, which is what ``is_mechanical`` can see. That its
    value equals the source's person id is the other question, and run 20
    answered it *no* — one record per person per body, so it identifies a
    record. A config joining on it must default to saying so rather than to
    silence.

    P13468 used to be this test's example and is no longer: run 28 measured it
    against the register that publishes it (591 of 638), which is what
    membership of VERIFIED_IDENTIFIER_PROPERTIES costs.
    """
    cfg = load_config(write(tmp_path, MINIMAL + "identifier_property: P14527\n"))
    assert cfg.identifier_verified is False


def test_claiming_an_unmeasured_property_is_verified_is_rejected(tmp_path):
    """The one claim ``is_mechanical`` writes real edits off the back of."""
    text = MINIMAL + "identifier_property: P14527\nidentifier_verified: true\n"
    with pytest.raises(ValueError, match="has not been measured"):
        load_config(write(tmp_path, text))


def test_a_measured_property_may_be_claimed_verified(tmp_path):
    """The other side of the same rule: measurement is what unlocks it."""
    text = MINIMAL + "identifier_property: P13468\nidentifier_verified: true\n"
    assert load_config(write(tmp_path, text)).identifier_verified is True


def test_an_unknown_identifier_property_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="identifier_property"):
        load_config(write(tmp_path, MINIMAL + "identifier_property: P9999999\n"))


def test_the_shipped_cantonal_config_joins_on_what_the_source_can_supply():
    """P13468 is the canton's own id and the property Wikidata uses for these
    people — but run 20 found its values in no column of OpenParlData, so the
    only joinable identifier here is the one that source *does* produce."""
    cfg = load_config("config/kantonsrat-zh.yaml")
    assert cfg.identifier_property == "P14527"
    assert cfg.identifier_verified is False
    assert cfg.quickstatements is False


def test_the_cantons_own_id_cannot_be_joined_on_from_openparldata(tmp_path):
    """Measured and falsified, so it is refused rather than documented.

    Run 20 compared P13468 against every column of the ZH person records: 0 of
    28 values are the person id and no column carries them. A join would
    compare two id spaces, and every ADD_IDENTIFIER would offer a number from
    the wrong one.
    """
    text = MINIMAL + """
source: openparldata
body_key: ZH
identifier_property: P13468
"""
    with pytest.raises(ValueError, match="no column"):
        load_config(write(tmp_path, text))


def test_only_measured_properties_are_verified():
    """P14527 lost its status to a single disagreement, and the reason
    generalises: OpenParlData holds one person record per person *per body*, so
    it identifies a record. Q131948095 carries 1411 where the ZH record is
    17436 — another parliament's id for the same human.

    P13468 joined on 2026-08-10 by the opposite route: run 28 measured its
    value against the register that publishes it, 591 of 638 (92.6%).
    """
    from wd_parliament.models import VERIFIED_IDENTIFIER_PROPERTIES

    assert VERIFIED_IDENTIFIER_PROPERTIES == frozenset({"P1307", "P13468"})
    assert "P14527" not in VERIFIED_IDENTIFIER_PROPERTIES


# --- the two identifiers a member has ---------------------------------------
def test_a_config_that_says_nothing_reports_only_the_join(tmp_path):
    """What the tool did before the key existed, unchanged."""
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.identifiers == ["P1307"]
    assert [c.property_id for c in cfg.identifier_checks] == ["P1307"]


def test_both_identifiers_are_read_and_the_join_comes_first(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + "identifiers: [P14527, P1307]\n"))
    assert [c.property_id for c in cfg.identifier_checks] == ["P1307", "P14527"]


def test_the_join_property_is_reported_even_if_the_list_omits_it(tmp_path):
    """It is the only one whose value the run holds, so dropping it would lose
    the highest-leverage edit in the report — a strange power for a config key
    that reads like an addition."""
    cfg = load_config(write(tmp_path, MINIMAL + "identifiers: [P14527]\n"))
    assert [c.property_id for c in cfg.identifier_checks] == ["P1307", "P14527"]


def test_an_unknown_identifier_property_is_an_error(tmp_path):
    """Not a skip, for the same reason person_data's is not: it selects a code
    path rather than supplying a value, and a typo would silently drop the
    second of a parliament's two identifiers."""
    with pytest.raises(ValueError, match="P999999"):
        load_config(write(tmp_path, MINIMAL + "identifiers: [P1307, P999999]\n"))


def test_a_string_instead_of_a_list_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="identifiers"):
        load_config(write(tmp_path, MINIMAL + "identifiers: P1307\n"))


def test_the_biography_link_defaults_to_the_join_propertys_record_page(tmp_path):
    """The number printed beside a member *is* the join property's value, so
    the page that resolves it is that property's. Linking it through anything
    else is how the Kantonsrat report came to point an OpenParlData person id
    at the chamber's member list."""
    text = MINIMAL + """
source: openparldata
body_key: ZH
identifier_property: P14527
identifier_verified: false
"""
    cfg = load_config(write(tmp_path, text))
    assert cfg.biography_url_for(18172) == "https://openparldata.ch/item/persons/18172"


def test_an_explicit_biography_url_still_wins(tmp_path):
    cfg = load_config(
        write(tmp_path, MINIMAL + 'biography_url: "https://example.org/{id}"\n')
    )
    assert cfg.biography_url_for(1108) == "https://example.org/1108"


def test_each_identifier_resolves_through_its_own_template(tmp_path):
    """Three registers, three URLs, and the values are not interchangeable."""
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.identifier_url("P1307", 1108) == (
        "https://www.parlament.ch/de/biografie/wd/1108"
    )
    assert cfg.identifier_url("P14527", 18172) == (
        "https://openparldata.ch/item/persons/18172"
    )
    assert cfg.identifier_url("P13468", 22518) == (
        "https://www.wahlen.zh.ch/krdaten_staatsarchiv/abfrage.php?id=22518"
    )
    assert cfg.identifier_url("P1307", None) is None


def test_the_shipped_configs_report_both_identifiers():
    """Federally the parliament's own id is the join and OpenParlData's is the
    extra; cantonally it is the other way round, because P13468's values are in
    no column OpenParlData has (run 20)."""
    federal = load_config("config/parliament.yaml")
    assert [c.property_id for c in federal.identifier_checks] == ["P1307", "P14527"]

    zurich = load_config("config/kantonsrat-zh.yaml")
    assert [c.property_id for c in zurich.identifier_checks] == ["P14527", "P13468"]
    assert zurich.biography_url_for(18172) == (
        "https://openparldata.ch/item/persons/18172"
    )


# --- the map key is not always an abbreviation ------------------------------
def test_a_district_name_key_is_found_despite_its_case(tmp_path):
    """Found the moment the ZH map was first filled: `constituency_qid`
    upper-cased the lookup, which is right for `ZH` and matches nothing for a
    whole district name. Eighteen entries, zero lookups, no error — a run
    that would have reported no districts while holding all of them.
    """
    text = MINIMAL + '''
constituencies:
  "9. Wahlkreis (Horgen)": Q141045940
'''
    cfg = load_config(write(tmp_path, text))
    assert cfg.constituency_qid("9. Wahlkreis (Horgen)") == "Q141045940"


def test_the_registers_untidy_spacing_still_finds_the_district(tmp_path):
    """The source writes `'I      Zürich 1+2'`; a config will not."""
    text = MINIMAL + '''
constituencies:
  "9. Wahlkreis (Horgen)": Q141045940
'''
    cfg = load_config(write(tmp_path, text))
    assert cfg.constituency_qid("9.  Wahlkreis   (Horgen)") == "Q141045940"


def test_a_canton_abbreviation_still_matches_either_case(tmp_path):
    """The federal behaviour the upper-casing existed for, unchanged."""
    cfg = load_config(write(tmp_path, MINIMAL + "\nconstituencies:\n  ZH: Q11943\n"))
    assert cfg.constituency_qid("zh") == cfg.constituency_qid("ZH") == "Q11943"


def test_an_unmapped_district_is_still_none(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + "\nconstituencies:\n  ZH: Q11943\n"))
    assert cfg.constituency_qid("9. Wahlkreis (Horgen)") is None
    assert cfg.constituency_qid("") is None
