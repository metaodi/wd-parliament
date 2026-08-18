"""Tests for the pure decisions in ``scripts/verify_kantonsrat.py``.

The API calls are network code and are left alone, as in
``test_verify_openparldata.py``. What is worth pinning down is what the probe
*concludes* — and here that is three things, each of which decides whether a
cantonal adapter should be written at all:

- ``is_kantonsrat``, because matching the wrong group means measuring the wrong
  body of people. Zurich makes this sharper than the federal case: the
  **Regierungsrat** is the cantonal *executive*, and a loose match would report
  a seven-member government as a 180-seat legislature;
- ``classify_seat_count``, the cantonal version of the signal that made the
  federal read trustworthy — the National Council's open memberships came to
  exactly 200 and the Council of States' to exactly 46;
- ``classify_wikidata_reach``, which decides whether the cantonal tool may emit
  QuickStatements at all. The Kantonsrat has no P1307, so the answer turns on
  whether an identifier **Wikidata itself asserts** reaches these people.

The fixtures follow the model the 2026-07-29 run established: a body is the
level of parliament, the chamber is a group under it, and a seat is a
membership pointing at that group with ``begin_date`` / ``end_date``.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_kantonsrat import (  # noqa: E402
    CONFIRMED,
    body_key_of,
    CONTRADICTED,
    INCONCLUSIVE,
    OPENPARLDATA_ID,
    SWISS_PARLIAMENT_ID,
    ZH_MEMBER_ID,
    classify_identifier_column,
    classify_position_item,
    classify_qualifier_readiness,
    classify_seat_count,
    classify_wikidata_reach,
    compare_identifier_values,
    date_clusters,
    discover_identifier_columns,
    district_fields,
    distinct_values,
    find_kantonsrat_group,
    DEFAULT_SEAT_ROLES,
    identifier_reach_query,
    identifier_values_query,
    is_kantonsrat,
    kantonsrat_candidates,
    position_candidates_query,
    qualifier_usage_query,
    reconcile_values,
    render_qid_yaml,
    seat_holder_ids,
    seat_holders,
    seat_reach_query,
    split_numbered_label,
    summarise_position_candidates,
    summarise_qualifier_usage,
    trace_memberships,
)

TODAY = date(2026, 7, 30)


def group(id=1, name="Kantonsrat", body_key="ZH"):
    return {"id": id, "body_key": body_key, "name_de": name}


def seat(start="2023-05-08", end=None, person_id=1, district=None, role="Mitglied"):
    """One seat membership as the live API shapes it."""
    row = {
        "type_harmonized": "council_legislative",
        "group_name_de": "Kantonsrat Zürich",
        "role_name_de": role,
        "person_id": person_id,
        "begin_date": start,
        "end_date": end,
    }
    if district is not None:
        row["electoral_district"] = district
    return row


def seats(open_count, closed_count=0):
    """``open_count`` sitting members plus ``closed_count`` who have left."""
    rows = [seat(person_id=i) for i in range(open_count)]
    rows += [
        seat(person_id=1000 + i, end="2023-05-07") for i in range(closed_count)
    ]
    return rows


def binding(qid, label, holders):
    return {
        "position": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "positionLabel": {"value": label},
        "holders": {"value": str(holders)},
    }


# --- A. locating the chamber -------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "Kantonsrat",
        "Kantonsrat Zürich",
        "Zürcher Kantonsrat",
        "Kantonsrat des Kantons Zürich",
        "Cantonal Council of Zürich",
    ],
)
def test_a_the_chamber_is_recognised_under_its_spellings(name):
    assert is_kantonsrat({"name_de": name}) is True


@pytest.mark.parametrize(
    "name",
    [
        # The dangerous one: the cantonal *executive*, seven members, and five
        # letters away from the legislature. A substring match on "rat" — or a
        # careless addition to KANTONSRAT_NAMES — turns the government into the
        # parliament, and every one of the 180 seats would then be wrong.
        "Regierungsrat",
        "Regierungsrat des Kantons Zürich",
        # Organs *of* the chamber: the Zurich analogues of the federal probe's
        # "Präsidium des Nationalrates", a committee of eight that a substring
        # match once reported as the National Council.
        "Büro des Kantonsrates",
        "Geschäftsleitung des Kantonsrates",
        "Präsidium des Kantonsrates",
        "Parlamentsdienste",
        "Aufsichtskommission des Kantonsrates",
        # A parliamentary group is not the chamber either.
        "SVP-Fraktion",
        # Another canton's legislature, in case the body filter is ever dropped.
        "Grosser Rat des Kantons Freiburg",
        "Nationalrat",
    ],
)
def test_a_neither_the_executive_nor_an_organ_of_the_chamber_matches(name):
    assert is_kantonsrat({"name_de": name}) is False


def test_a_a_name_that_merely_mentions_the_chamber_is_reported_as_a_near_miss():
    """Rejecting a row must not make it invisible.

    Being strict costs the case of a chamber named with a suffix, so the rows
    the matcher declines are surfaced. That is what lets "not found" be told
    apart from "found under a name this function did not expect" — the
    distinction the federal probe added only after getting it wrong.
    """
    rows = [group(1, "Büro des Kantonsrates"), group(2, "Regierungsrat")]
    near = kantonsrat_candidates(rows)
    assert [r["id"] for r in near] == [1]


def test_a_the_chamber_is_found_among_other_groups():
    rows = [group(1, "Regierungsrat"), group(2, "Kantonsrat"), group(3, "SVP-Fraktion")]
    found, lines = find_kantonsrat_group(rows)
    assert found is not None and found["id"] == 2
    assert any("Kantonsrat: id=2" in line for line in lines)


def test_a_not_found_is_a_real_answer_and_says_so_loudly():
    found, lines = find_kantonsrat_group([group(1, "Büro des Kantonsrates")])
    assert found is None
    text = "\n".join(lines)
    assert "NOT FOUND" in text
    assert "near miss" in text


def test_a_an_empty_group_table_points_at_the_body_key_not_the_names():
    found, lines = find_kantonsrat_group([])
    assert found is None
    assert "body key" in "\n".join(lines)


def test_a_nothing_mentioning_the_chamber_blames_the_body_key():
    """No near misses at all is a different diagnosis from a spelling problem."""
    found, lines = find_kantonsrat_group([group(1, "Regierungsrat")])
    assert found is None
    assert "body key" in "\n".join(lines)


def test_a_a_failed_match_lists_the_groups_it_did_see():
    """"Not found" has to hand over the names, not just the diagnosis.

    The Gemeinderat run is why: body 261 read 807 person records, so the body
    key was plainly right, and the configured group matched zero memberships.
    The one thing needed was the list of group names that body really has —
    which the probe held and did not print.
    """
    found, lines = find_kantonsrat_group(
        [group(1, "Regierungsrat"), group(2, "Stadtrat")]
    )
    assert found is None
    text = "\n".join(lines)
    assert "Regierungsrat" in text and "Stadtrat" in text


def test_a_another_parliament_is_matched_under_its_own_names():
    """The same measurement, pointed at a chamber this file was not written for."""
    rows = [group(1, "Stadtrat"), group(2, "Gemeinderat"), group(3, "SP-Fraktion")]
    found, lines = find_kantonsrat_group(rows, ["Gemeinderat"], "Gemeinderat")
    assert found is not None and found["id"] == 2
    assert any("Gemeinderat: id=2" in line for line in lines)


def test_a_naming_another_chamber_does_not_relax_the_equality_rule():
    """The executive of a city is as dangerous as the executive of a canton.

    ``Stadtrat`` is the city's nine-member government, and the Gemeinderat's
    own organs are not the Gemeinderat either. Passing a name must not turn the
    match into a substring one.
    """
    names = ["Gemeinderat"]
    for name in (
        "Stadtrat",
        "Büro des Gemeinderates",
        "Geschäftsleitung des Gemeinderates",
        "Präsidium des Gemeinderates",
        "Kantonsrat",
    ):
        assert is_kantonsrat({"name_de": name}, names) is False


def test_a_the_near_miss_needle_follows_the_chamber_it_was_given():
    """A near miss is "this chamber under another name", never another chamber."""
    rows = [
        group(1, "Büro des Gemeinderates"),
        group(2, "Kantonsrat Zürich"),
    ]
    near = kantonsrat_candidates(rows, ["Gemeinderat"])
    assert [r["id"] for r in near] == [1]


# --- B. is the chamber the right size? --------------------------------------
def test_b_exactly_180_seat_holders_confirms_the_data_is_current():
    verdict, detail, _ = classify_seat_count(
        seats(180, closed_count=900), today=TODAY
    )
    assert verdict == CONFIRMED
    assert "180" in detail


def test_b_a_near_miss_on_the_seat_count_is_contradicted_not_waved_through():
    """179 and 181 are the interesting failures, not rounding.

    One means a vacancy or a member the source has not opened a row for, the
    other a row that should have been closed. Both change who the diff thinks
    is sitting, which is exactly the failure that published 2,234 wrong
    suggestions federally.
    """
    for count in (179, 181):
        verdict, _, _ = classify_seat_count(seats(count), today=TODAY)
        assert verdict == CONTRADICTED, count


def test_b_all_seats_closed_means_this_is_not_the_sitting_chamber():
    verdict, detail, _ = classify_seat_count(seats(0, closed_count=200), today=TODAY)
    assert verdict == CONTRADICTED
    assert "holds nobody today" in detail


# The three corrections run 13 forced. The live group returned 186 open
# memberships against 180 seats, and none of the difference was vacancies.
def test_b_a_member_who_has_not_taken_office_yet_is_not_a_sitting_member():
    """Run 13: several open rows began 2026-08-17, eighteen days out.

    They are real and correctly open, but a member who starts next month is
    not sitting today, and counting them inflates the chamber.
    """
    rows = seats(180) + [seat(person_id=900 + i, start="2026-08-17") for i in range(6)]
    assert classify_seat_count(rows, today=TODAY)[0] == CONFIRMED
    # ...and on a date after they take office, they are members and the
    # chamber is over-full, which is a finding rather than a rounding error.
    assert classify_seat_count(rows, today=date(2026, 9, 1))[0] == CONTRADICTED


def test_b_a_presiding_officer_holds_their_seat_through_the_presidium_row():
    """Run 14's correction, and it went the opposite way from run 13's.

    Filtering to ``Mitglied`` alone gave 177 against 180 seats. The presidium
    rows are **not** second rows for people who also hold a plain membership —
    a presiding member has no ``Mitglied`` row at all, so their presidium row
    *is* their seat. 177 + 3 presiding officers = 180.
    """
    rows = seats(177) + [
        seat(person_id=900, role="Präsidium"),
        seat(person_id=901, role="1. Vizepräsidium"),
        seat(person_id=902, role="2. Vizepräsidium"),
    ]
    assert classify_seat_count(rows, today=TODAY, seat_roles=DEFAULT_SEAT_ROLES)[0] == (
        CONFIRMED
    )
    # Excluding them — run 13's rule — is three seats short.
    assert classify_seat_count(rows, today=TODAY, seat_roles=["Mitglied"])[0] == (
        CONTRADICTED
    )


def test_b_a_guest_is_not_a_member():
    rows = seats(180) + [seat(person_id=901, role="Gast")]
    assert classify_seat_count(
        rows, today=TODAY, seat_roles=DEFAULT_SEAT_ROLES
    )[0] == CONFIRMED
    # Without the role filter the guest counts, and the probe says so rather
    # than quietly absorbing them.
    assert classify_seat_count(rows, today=TODAY)[0] == CONTRADICTED


def test_b_a_member_holding_two_rows_is_still_one_person():
    """Whatever the roles, the count is of people."""
    rows = seats(180) + [seat(person_id=5, role="Präsidium")]
    assert classify_seat_count(
        rows, today=TODAY, seat_roles=DEFAULT_SEAT_ROLES
    )[0] == CONFIRMED


def test_b_the_funnel_is_reported_at_every_stage():
    """A drop from 186 to 180 must be visible, not inferred.

    These are run 14's live numbers: 186 open, 4 starting later, and roles
    Mitglied=177, Präsidium/1./2. Vizepräsidium=1 each, Gast=1, none=1.
    """
    rows = (
        seats(177)
        + [seat(person_id=900 + i, start="2026-08-17") for i in range(4)]
        + [
            seat(person_id=910, role="Präsidium"),
            seat(person_id=911, role="1. Vizepräsidium"),
            seat(person_id=912, role="2. Vizepräsidium"),
            seat(person_id=913, role="Gast"),
            seat(person_id=914, role=None),
        ]
    )
    count, lines = seat_holders(rows, today=TODAY, seat_roles=DEFAULT_SEAT_ROLES)
    text = "\n".join(lines)
    assert count == 180
    assert "open (no end_date):    186" in text
    assert "4 start later" in text
    assert "Gast=1" in text
    assert "Mitglied=177" in text
    assert "distinct people:        180" in text


def test_b_an_unrecognised_role_stays_visible_in_the_breakdown():
    """The allowlist can only be wrong in silence if the roles are not printed.

    Neither an allowlist nor a denylist is self-correcting when the source adds
    a role, so what protects the count is the breakdown plus the refusal to
    call a wrong total CONFIRMED.
    """
    rows = seats(180) + [seat(person_id=999, role="Interimspräsidium")]
    verdict, _, lines = classify_seat_count(
        rows, today=TODAY, seat_roles=DEFAULT_SEAT_ROLES
    )
    assert verdict == CONFIRMED  # the new role is not a seat under this list...
    assert "Interimspräsidium=1" in "\n".join(lines)  # ...but it is visible


def test_b_reaching_the_size_only_after_narrowing_is_stated_in_the_verdict():
    rows = seats(180) + [seat(person_id=901, role="Gast")]
    _, detail, _ = classify_seat_count(
        rows, today=TODAY, seat_roles=DEFAULT_SEAT_ROLES
    )
    assert "raw open count is 181" in detail


def test_b_without_a_person_column_rows_are_counted_and_the_funnel_says_so():
    rows = [
        {"role_name_de": "Mitglied", "begin_date": "2023-05-08", "end_date": None}
        for _ in range(180)
    ]
    count, lines = seat_holders(rows, today=TODAY)
    assert count == 180
    assert any("no person column" in line for line in lines)


def test_b_a_missing_end_column_is_inconclusive_never_contradicted():
    """"No such column" and "column full of nulls" are opposite findings.

    They look identical through ``.get()``. Assuming the second is what made an
    earlier probe report all 5,618 federal seat memberships as undated when the
    column is ``begin_date``, so a column that is absent must never produce a
    verdict about the data.
    """
    rows = [{"type_harmonized": "council_legislative", "person_id": i} for i in range(180)]
    verdict, detail, _ = classify_seat_count(rows, today=TODAY)
    assert verdict == INCONCLUSIVE
    assert "END_FIELDS" in detail


def test_b_no_rows_is_inconclusive():
    verdict, _, _ = classify_seat_count([], today=TODAY)
    assert verdict == INCONCLUSIVE


def test_b_the_expected_size_is_a_parameter_not_a_constant():
    """So the probe generalises to the other 25 cantons without an edit."""
    assert classify_seat_count(seats(100), expected=100, today=TODAY)[0] == CONFIRMED
    assert classify_seat_count(seats(100), expected=180, today=TODAY)[0] == CONTRADICTED


# --- C. may the cantonal tool ever write? -----------------------------------
def test_c_no_wikidata_asserted_identifier_means_report_only():
    verdict, detail = classify_wikidata_reach(
        holders=400, with_opd_id=0, with_federal_id=0, with_either=0
    )
    assert verdict == CONTRADICTED
    assert "quickstatements: false" in detail


def test_c_the_federal_id_alone_must_not_be_used_as_the_cantonal_join():
    """P1307 is the *federal* service and says nothing about a cantonal seat.

    It reaches only the members who also sat in Bern, so joining on it would
    silently exclude precisely the people who never went federal — a bias that
    looks like coverage rather than like a bug.
    """
    verdict, detail = classify_wikidata_reach(
        holders=400, with_opd_id=0, with_federal_id=120, with_either=120
    )
    assert verdict == CONTRADICTED
    assert SWISS_PARLIAMENT_ID in detail
    assert "must not be used" in detail


def test_c_good_p14527_coverage_needs_no_change_to_the_safety_rule():
    """The federal finding inverts here, and that is the point of the section.

    ``verify_openparldata`` found P14527 adds nobody federally — every National
    Councillor carrying it already had P1307. Cantonally it may be the only
    Wikidata-asserted identifier there is, and because Wikidata asserts it, it
    slots into ``is_mechanical`` unchanged.
    """
    verdict, detail = classify_wikidata_reach(
        holders=400, with_opd_id=300, with_federal_id=90, with_either=310
    )
    assert verdict == CONFIRMED
    assert OPENPARLDATA_ID in detail
    assert "is_mechanical" in detail


def test_c_the_cantons_own_id_is_reported_as_wikidatas_side_only():
    """Coverage is what this classifier can see, and only half the question.

    Run 20: P13468 reaches 28 of the 35 linked people — and **0 of 28** of its
    values are OpenParlData's person id, in no column of that source. An
    identifier needs a value on both sides, so a high count here must not read
    as "join on this".
    """
    verdict, detail = classify_wikidata_reach(
        holders=400, with_opd_id=300, with_federal_id=90, with_either=340,
        with_zh_id=250,
    )
    assert verdict == CONFIRMED
    assert ZH_MEMBER_ID in detail
    assert "Wikidata's** side only" in detail
    assert "not for this source" in detail


def test_c_thin_coverage_still_confirms_but_promises_less():
    verdict, detail = classify_wikidata_reach(
        holders=400, with_opd_id=40, with_federal_id=10, with_either=45
    )
    assert verdict == CONFIRMED
    assert "report-only" in detail


def test_c_no_seat_holders_blames_the_position_item_not_the_coverage():
    verdict, detail = classify_wikidata_reach(
        holders=0, with_opd_id=0, with_federal_id=0, with_either=0
    )
    assert verdict == INCONCLUSIVE
    assert "position item" in detail


# --- D. is the position Q-ID the seat? --------------------------------------
def test_d_an_item_nobody_holds_is_not_the_position():
    verdict, detail = classify_position_item(holders=0, open_holders=0)
    assert verdict == CONTRADICTED
    assert "category" in detail


def test_d_more_current_holders_than_seats_falsifies_the_item():
    """A position cannot be currently held by more people than there are seats."""
    verdict, _ = classify_position_item(holders=5000, open_holders=181)
    assert verdict == CONTRADICTED


def test_d_a_plausible_count_confirms_and_incomplete_coverage_does_not_falsify():
    """Wikidata's gap makes the open count low, not the item wrong.

    Under-coverage is the tool's whole reason to exist, so only the upper bound
    can falsify here.
    """
    assert classify_position_item(holders=420, open_holders=180)[0] == CONFIRMED
    assert classify_position_item(holders=420, open_holders=61)[0] == CONFIRMED


def test_d_held_by_people_but_by_nobody_currently_is_inconclusive():
    verdict, _ = classify_position_item(holders=300, open_holders=0)
    assert verdict == INCONCLUSIVE


def test_d_the_category_item_run_13_disproved_is_caught_by_the_holder_count():
    """Q19479543 is 'Kategorie:Kantonsrat (Zürich, Person)', held by nobody.

    It shipped as this probe's default on the strength of a search, and as a
    P39 main value it would have claimed people hold a Wikimedia category.
    Counting holders is what caught it — the label alone read plausibly.
    """
    verdict, detail = classify_position_item(holders=0, open_holders=0)
    assert verdict == CONTRADICTED
    assert "category" in detail


# --- D. discovering the position rather than guessing it again --------------
def test_d_candidates_are_ranked_by_how_many_known_members_hold_them():
    rows = [
        binding("Q123", "Mitglied des Kantonsrats Zürich", 28),
        binding("Q18510612", "Mitglied des Nationalrats", 9),
    ]
    lines, top = summarise_position_candidates(rows, sample_size=35)
    assert top == "Q123"
    assert any("Q123" in line and "28" in line for line in lines)


def test_d_the_top_candidate_is_offered_as_a_candidate_not_an_answer():
    """A federal seat can outrank the cantonal one in a small sample."""
    _, top = summarise_position_candidates([binding("Q18510612", "NR", 3)], 4)
    assert top == "Q18510612"
    lines, _ = summarise_position_candidates([binding("Q18510612", "NR", 3)], 4)
    assert any("not" in line and "answer" in line for line in lines)


def test_d_no_candidates_is_a_reported_answer_not_a_crash():
    lines, top = summarise_position_candidates([], sample_size=0)
    assert top is None
    assert lines


def test_d_the_discovery_query_asks_about_the_given_people():
    sparql = position_candidates_query(["Q117716", "Q117154"])
    assert "wd:Q117716" in sparql and "wd:Q117154" in sparql
    assert "GROUP BY" in sparql
    assert "DeprecatedRank" in sparql


def test_the_identifier_reach_query_does_not_depend_on_the_position_item():
    """Run 13's section C returned 0 for everything because the P39 was wrong.

    Asking about the people directly cannot fail that way, so this pins down
    that the query mentions no position at all.
    """
    sparql = identifier_reach_query(["Q117716"])
    assert "wd:Q117716" in sparql
    assert f"wdt:{OPENPARLDATA_ID}" in sparql
    assert "P39" not in sparql


# --- E. the Wahlkreis map for P768 ------------------------------------------
def test_e_district_columns_are_reported_not_guessed():
    rows = [seat(district="Bezirk Winterthur")]
    assert district_fields(rows) == ["electoral_district"]


def test_e_no_district_column_is_an_empty_list_not_a_guess():
    assert district_fields([seat()]) == []


def test_e_only_current_members_supply_the_district_names():
    """Zurich redrew its districts for 2007.

    Taking the names off all 834 person records would include districts that
    no longer exist, and the count could never be 18. The ids come from the
    same funnel section B counts, so the two cannot describe different people.
    """
    rows = seats(180) + [seat(person_id=1001, end="2006-04-30")]
    ids = seat_holder_ids(rows, today=TODAY, seat_roles=DEFAULT_SEAT_ROLES)
    assert len(ids) == 180
    assert 1001 not in ids


def test_e_a_name_is_matched_to_a_qid_only_by_exact_label():
    """A substring match is how a plausible-but-wrong item gets in."""
    mapping, unmatched, _, _ = reconcile_values(
        {"Winterthur-Stadt": 12, "Bezirk Horgen": 9},
        {"Q1": "Winterthur-Stadt", "Q2": "Horgen"},
    )
    assert mapping == {"Winterthur-Stadt": "Q1"}
    assert unmatched == ["Bezirk Horgen"]


# Run 15's live district names, which arrive numbered and untidily spaced.
@pytest.mark.parametrize(
    "value,expected",
    [
        ("XVII Bülach", ("XVII", "Bülach")),
        ("I      Zürich 1+2", ("I", "Zürich 1+2")),
        ("XIV Winterthur Stadt", ("XIV", "Winterthur Stadt")),
        ("Uster", (None, "Uster")),
    ],
)
def test_e_a_numbered_district_splits_into_numeral_and_name(value, expected):
    assert split_numbered_label(value) == expected


def test_e_the_numeral_is_stripped_to_match_but_kept_in_the_key():
    """No Wikidata item is labelled 'XVII Bülach'...

    ...but the key has to stay the value the adapter will look up, so the
    numeral is dropped for the comparison only.
    """
    mapping, _, _, _ = reconcile_values({"XVII Bülach": 18}, {"Q1": "Bülach"})
    assert mapping == {"XVII Bülach": "Q1"}


def test_e_erratic_whitespace_never_reaches_the_config_key():
    """Run 15 returned 'I      Zürich 1+2' with six spaces."""
    mapping, unmatched, _, _ = reconcile_values(
        {"I      Zürich 1+2": 5}, {"Q1": "Zürich 1+2"}
    )
    assert mapping == {"I Zürich 1+2": "Q1"}
    assert unmatched == []


def test_e_an_unmatched_name_is_reported_rather_than_guessed_at():
    _, _, _, lines = reconcile_values({"Bezirk Uster": 7}, {})
    text = "\n".join(lines)
    assert "resolve by hand" in text
    assert "Bezirk Uster" in text


def test_e_qids_in_use_that_match_no_source_value_are_surfaced():
    _, _, unused, lines = reconcile_values({"Uster": 7}, {"Q1": "Uster", "Q9": "Bülach"})
    assert unused == {"Q9": "Bülach"}
    assert "matching no source value" in "\n".join(lines)


# --- E. is there a practice to follow at all? -------------------------------
def test_e_a_qualifier_nobody_uses_leaves_the_map_empty():
    """Run 15: P2937 appears on no statement for the seat.

    Nothing can be derived from usage, so the honest outcome is the federal
    default — an empty map, which makes no suggestion.
    """
    verdict, detail = classify_qualifier_readiness("P2937", {}, 0, 18, 270)
    assert verdict == INCONCLUSIVE
    assert "no practice here to follow" in detail


def test_e_three_statements_are_not_a_convention_to_infer_from():
    """The live P768 case, and the reason it must not become the map.

    Run 15 found P768 on 3 of 270 statements, naming 'Kreis 4', 'Kreis 5' and
    'Kreis 11' — city-of-Zürich quarters, not any of the 18 Wahlkreise. Too
    few to be a convention and the wrong kind of thing besides.
    """
    used = {"Q870098": "Kreis 11", "Q460885": "Kreis 5", "Q677133": "Kreis 4"}
    verdict, detail = classify_qualifier_readiness("P768", used, 0, 18, 270)
    assert verdict == CONTRADICTED
    assert "Leave the map empty" in detail


def test_e_a_partial_match_confirms_only_what_resolved():
    verdict, detail = classify_qualifier_readiness("P768", {"Q1": "Uster"}, 1, 18, 270)
    assert verdict == CONFIRMED
    assert "leave the rest unmapped" in detail


def test_e_no_statements_for_the_seat_measures_nothing():
    verdict, _ = classify_qualifier_readiness("P768", {}, 0, 18, 0)
    assert verdict == INCONCLUSIVE


def test_e_only_resolved_names_reach_the_paste_block():
    lines = render_qid_yaml({"Uster": "Q1", "Dietikon": "Q2"}, "districts")
    text = "\n".join(lines)
    assert "districts:" in text
    assert '"Dietikon":' in text and "Q2" in text
    assert "Bülach" not in text


def test_e_nothing_resolved_yields_no_pasteable_block():
    assert "districts:" not in "\n".join(render_qid_yaml({}, "districts"))


def test_e_the_qualifier_usage_query_asks_one_property_at_a_time():
    """P768, P4100 and P2937 are all repeatable.

    Asking for them in one SELECT would produce a cartesian product per
    statement — the reason wikidata.py runs three bounded queries.
    """
    sparql = qualifier_usage_query("Q21518678", "P768")
    assert "pq:P768" in sparql
    assert "pq:P2937" not in sparql and "pq:P4100" not in sparql
    assert "ps:P39 wd:Q21518678" in sparql


def test_e_values_in_use_are_ranked_and_labelled():
    used, lines = summarise_qualifier_usage(
        [
            {
                "value": {"value": "http://www.wikidata.org/entity/Q1"},
                "valueLabel": {"value": "Winterthur-Stadt"},
                "uses": {"value": "12"},
            }
        ]
    )
    assert used == {"Q1": "Winterthur-Stadt"}
    assert "Winterthur-Stadt" in "\n".join(lines)


def test_e_a_qualifier_nobody_uses_says_so_instead_of_returning_nothing():
    used, lines = summarise_qualifier_usage([])
    assert used == {}
    assert "not used on any statement" in "\n".join(lines)


# --- F. legislature terms for P2937 -----------------------------------------
def test_f_start_dates_cluster_at_legislature_boundaries():
    """The federal A2 reasoning: 200 National Councillors, 16 distinct starts.

    Most members start when the legislature does, so a term boundary stands
    out as a large cluster without needing a periods table to read.
    """
    rows = (
        [seat(person_id=i, start="2023-05-08") for i in range(170)]
        + [seat(person_id=200 + i, start="2019-05-13") for i in range(8)]
        + [seat(person_id=300, start="2024-11-04")]
    )
    clusters = date_clusters(rows, "begin_date")
    assert clusters[0] == ("2023-05-08", 170)
    assert clusters[1] == ("2019-05-13", 8)


def test_f_distinct_values_counts_occurrences_and_skips_blanks():
    rows = [seat(district="Uster"), seat(district="Uster"), seat(district=None)]
    assert distinct_values(rows, "electoral_district") == {"Uster": 2}


# --- the SPARQL --------------------------------------------------------------
def test_the_reach_query_asks_about_the_configured_position_and_both_properties():
    sparql = seat_reach_query("Q19479543")
    assert "wd:Q19479543" in sparql
    assert f"wdt:{OPENPARLDATA_ID}" in sparql
    assert f"wdt:{SWISS_PARLIAMENT_ID}" in sparql
    assert "DeprecatedRank" in sparql


def test_the_open_count_rewalks_the_statement_rather_than_reusing_the_bound_one():
    """A member who left and returned has a closed statement *and* an open one.

    Testing the single statement the store happened to bind would make the
    answer depend on which. The query binds its own ``?openStatement``, and
    that is what this pins down.
    """
    sparql = seat_reach_query("Q19479543")
    assert "?openStatement" in sparql
    assert "FILTER NOT EXISTS { ?openStatement pq:P582 ?anyEnd . }" in sparql


# --- C. is the identifier's VALUE the person id? ----------------------------
def _binding(qid, value):
    return {
        "person": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "value": {"value": str(value)},
    }


def test_c_matching_values_confirm_the_cantonal_join():
    """The federal check, cantonally: Parmelin's PersonNumber 1108 == P1307 1108.

    Coverage is not correctness — 35 of 35 carrying P14527 says nothing about
    whether the value is the person id the source gave.
    """
    verdict, detail, _ = compare_identifier_values(
        {"Q117716": 9532, "Q117154": 21905},
        [_binding("Q117716", 9532), _binding("Q117154", 21905)],
    )
    assert verdict == CONFIRMED
    assert "identifier_verified: false" in detail


def test_c_the_property_compared_is_named_in_the_finding():
    """Two candidates now, and only one of them can be the person id.

    P13468 is the canton's own member id and P14527 an aggregator's, so a
    finding that does not say which one it is about cannot settle a config.
    """
    _, detail, lines = compare_identifier_values(
        {"Q1": 9532}, [_binding("Q1", 777)], ZH_MEMBER_ID
    )
    assert ZH_MEMBER_ID in detail
    assert any(ZH_MEMBER_ID in line for line in lines)
    assert OPENPARLDATA_ID not in detail


def test_c_leading_zeros_and_ints_are_the_same_identifier():
    verdict, _, _ = compare_identifier_values(
        {"Q1": 9532}, [_binding("Q1", "09532")]
    )
    assert verdict == CONFIRMED


def test_c_one_disagreement_is_enough_to_refuse_the_join():
    """No tolerance here: is_mechanical writes real edits off this claim."""
    verdict, detail, lines = compare_identifier_values(
        {"Q1": 9532, "Q2": 21905},
        [_binding("Q1", 9532), _binding("Q2", 777)],
    )
    assert verdict == CONTRADICTED
    assert "wrong people" in detail
    assert any("Q2" in line for line in lines)


def test_c_nothing_to_compare_leaves_the_join_unverified():
    verdict, detail, _ = compare_identifier_values({}, [])
    assert verdict == INCONCLUSIVE
    assert "Keep quickstatements off" in detail


def test_c_the_value_query_asks_only_about_the_given_people():
    sparql = identifier_values_query(["Q117716"])
    assert "wd:Q117716" in sparql
    assert f"wdt:{OPENPARLDATA_ID}" in sparql
    assert "P39" not in sparql


def test_c_the_value_query_can_ask_about_either_candidate():
    sparql = identifier_values_query(["Q117716"], ZH_MEMBER_ID)
    assert f"wdt:{ZH_MEMBER_ID}" in sparql
    assert OPENPARLDATA_ID not in sparql


# --- C. where does the source keep the identifier? --------------------------
def _person(pid, qid, **extra):
    row = {"id": pid, "wikidata_id": qid, "lastname": "Muster"}
    row.update(extra)
    return row


def test_c_the_column_report_finds_the_person_id_when_that_is_the_value():
    counts, compared = discover_identifier_columns(
        [_person(9532, "Q1"), _person(21905, "Q2")],
        {"Q1": "9532", "Q2": "21905"},
    )
    assert compared == 2
    assert counts["id"] == 2
    assert classify_identifier_column(counts, compared)[0] == CONFIRMED


def test_c_the_column_report_names_the_column_that_does_carry_the_values():
    """"Not the person id" is half an answer; this is the other half.

    If the source keeps the canton's id under another name, the config can
    join on that instead — and the two id spaces being different means the
    members matched so far were matched by accident.
    """
    counts, compared = discover_identifier_columns(
        [_person(9532, "Q1", source_id=22243), _person(21905, "Q2", source_id=20314)],
        {"Q1": "22243", "Q2": "20314"},
    )
    verdict, detail = classify_identifier_column(counts, compared)
    assert counts == {"source_id": 2}
    assert verdict == CONTRADICTED
    assert "source_id" in detail


def test_c_no_column_at_all_means_this_source_cannot_supply_the_identifier():
    """The finding that would forbid the join outright.

    An identifier that exists on Wikidata and nowhere in the source cannot be
    joined on, and — worse — must not be *suggested*: the number the report
    would offer is the source's own person id, from a different id space.
    """
    counts, compared = discover_identifier_columns(
        [_person(9532, "Q1"), _person(21905, "Q2")],
        {"Q1": "22243", "Q2": "20314"},
    )
    verdict, detail = classify_identifier_column(counts, compared)
    assert counts == {}
    assert verdict == CONTRADICTED
    assert "must not suggest adding it" in detail


def test_c_an_unlinked_source_says_nothing_about_the_column():
    counts, compared = discover_identifier_columns([_person(9532, "Q1")], {})
    assert (counts, compared) == ({}, 0)
    assert classify_identifier_column(counts, compared)[0] == INCONCLUSIVE


# --- A. the body key, and where the memberships are -------------------------
def test_a_the_body_key_is_read_from_the_column_that_carries_it():
    """`bodies` rows say `body_key`, not `key`.

    Reading only `key` had the probe print "0 match key='261'" and advise a
    different --body-key, one line above a row whose body_key was exactly 261.
    """
    assert body_key_of({"id": 322, "body_key": "261", "name_de": "Zürich"}) == "261"
    assert body_key_of({"id": 275, "key": "ZH"}) == "ZH"
    assert body_key_of({"id": 1, "name_de": "Zürich"}) == ""


class _TraceApi:
    """Serves `memberships` filtered by person_id, and nothing else."""

    def __init__(self, rows):
        self.rows = rows
        self.asked = []

    def get_data(self, table, **params):
        assert table == "memberships"
        self.asked.append(params["person_id"])
        return [r for r in self.rows if r["person_id"] == params["person_id"]]


def test_b_the_trace_names_the_groups_the_rows_actually_point_at():
    """The finding is a group id nobody configured, so it has to be printed."""
    api = _TraceApi(
        [
            {"person_id": 1, "group_id": 9001, "role_name_de": "Mitglied",
             "begin_date": "2022-05-01", "end_date": None},
            {"person_id": 2, "group_id": 9001, "role_name_de": "Mitglied",
             "begin_date": "2022-05-01", "end_date": None},
        ]
    )
    groups = [{"id": 8062, "name_de": "Gemeinderat"},
              {"id": 9001, "name_de": "Gemeinderat 2022-2026"}]
    lines = trace_memberships(api, [{"id": 1}, {"id": 2}], groups)
    text = "\n".join(lines)
    assert "9001" in text and "Gemeinderat 2022-2026" in text
    assert "8062" not in text


def test_b_no_membership_rows_at_all_is_named_as_a_gap_in_the_source():
    """"The config is wrong" and "the source has no such rows" are opposite
    diagnoses, and only the second one no config can fix."""
    api = _TraceApi([])
    lines = trace_memberships(api, [{"id": 1}, {"id": 2}], [])
    assert "SOURCE" in "\n".join(lines)


def test_b_the_trace_walks_a_handful_of_people_not_the_whole_body():
    """One request each, against a body that can hold hundreds of records."""
    api = _TraceApi([])
    trace_memberships(api, [{"id": i} for i in range(50)], [], limit=3)
    assert api.asked == [0, 1, 2]


def test_a_chamber_named_by_several_groups_is_reported_as_ambiguous():
    """Picking the first by row order is silent arbitration.

    Run 34: body 261 holds both `Gemeinderat` (id 8062) and `Gemeinderat
    Zürich` (id 465), and the one row order picks has ZERO membership rows.
    An empty group looks exactly like a group whose members all agree with
    Wikidata, so the second match has to be printed.
    """
    rows = [
        group(8062, "Gemeinderat", body_key="261"),
        group(465, "Gemeinderat Zürich", body_key="261"),
    ]
    found, lines = find_kantonsrat_group(
        rows, ["Gemeinderat", "Gemeinderat Zürich"], "Gemeinderat"
    )
    assert found is not None and found["id"] == 8062
    text = "\n".join(lines)
    assert "ALSO" in text and "465" in text
    assert "2 groups name this chamber" in text


def test_one_matching_group_says_nothing_about_ambiguity():
    """The note must not fire on the ordinary case, or it stops being read."""
    _, lines = find_kantonsrat_group([group(5077, "Kantonsrat Zürich")])
    assert "ALSO" not in "\n".join(lines)
