"""Command line entry point: ``python -m wd_parliament`` / ``wd-parliament``."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .app import run, validate_periods


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wd-parliament",
        description=(
            "Compare the sitting members of the Swiss Federal Assembly "
            "(parlament.ch) with 'position held' (P39) statements on Wikidata "
            "and generate a TODO list of suggested Wikidata edits."
        ),
    )
    parser.add_argument(
        "-c", "--config", default="config/parliament.yaml", help="Path to the YAML config."
    )
    parser.add_argument(
        "--reports-dir", default="reports", help="Directory for the Markdown reports."
    )
    parser.add_argument(
        "--docs-dir", default="docs", help="Directory for the dashboard, JSON and .qs."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N members per chamber (testing).",
    )
    parser.add_argument(
        "--validate-periods",
        metavar="IDVOTE",
        nargs="*",
        default=None,
        help=(
            "Instead of a normal run, cross-check the legislative-period "
            "overlap against roll-call votes (one IdVote per period) and print "
            "the comparison as JSON. Give no IdVote, or 'auto', to have them "
            "discovered from the Vote table for the most recent periods."
        ),
    )
    parser.add_argument(
        "--verify-config",
        action="store_true",
        help=(
            "Instead of a normal run, look up every Q-ID in the config on "
            "Wikidata and print what it actually is, so the hand-maintained "
            "canton/party/group/term maps can be checked."
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.verify_config:
        return _verify_config(args)

    # ``is not None`` rather than truthiness: an empty list is "--validate-periods
    # with no IdVote", which means discover them, not "do a normal run".
    if args.validate_periods is not None:
        return _validate(args)

    try:
        results = run(
            config_path=args.config,
            reports_dir=args.reports_dir,
            docs_dir=args.docs_dir,
            limit=args.limit,
        )
    except Exception as exc:  # pragma: no cover - top level guard
        logging.error("Run failed: %s", exc)
        return 1

    total = sum(len(r.suggestions) for r in results)
    members = sum(r.member_count for r in results)
    matched = sum(r.matched_by_identifier for r in results)
    rate = (matched / members * 100) if members else 0.0
    print(
        f"Done: {len(results)} chamber(s), {members} sitting members, "
        f"{total} suggested edits. P1307 hit rate: {rate:.1f}%."
    )
    return 0


def _verify_config(args) -> int:
    """Print what every Q-ID in the config actually is on Wikidata."""
    from .config import load_config
    from .http_client import HttpClient
    from .wikidata import WikidataClient

    try:
        config = load_config(args.config)
        http = HttpClient(
            user_agent=config.user_agent, request_delay=config.request_delay
        )
        wikidata = WikidataClient(http)

        sources = [
            ("position", {b.council: b.position_qid for b in config.bodies}),
            ("canton", config.cantons),
            ("parl_group", config.parl_groups),
            ("party", config.parties),
            ("term", {str(k): v for k, v in config.terms.items()}),
        ]
        all_qids = [qid for _, mapping in sources for qid in mapping.values()]
        if not all_qids:
            print("No Q-IDs configured.")
            return 0
        described = wikidata.describe_qids(all_qids, config.language)
    except Exception as exc:  # pragma: no cover - top level guard
        logging.error("Config verification failed: %s", exc)
        return 1

    unknown = 0
    for kind, mapping in sources:
        if not mapping:
            continue
        print(f"\n== {kind} ==")
        for key, qid in sorted(mapping.items()):
            info = described.get(qid, {})
            label = info.get("label")
            if not label:
                unknown += 1
                print(f"  {key:<6} {qid:<10} !! no such item / no label")
                continue
            instances = ", ".join(info.get("instance_of") or []) or "—"
            print(f"  {key:<6} {qid:<10} {label}  [{instances}]")

    if unknown:
        print(
            f"\n⚠️  {unknown} configured Q-ID(s) could not be resolved.",
            file=sys.stderr,
        )
        return 1
    print("\n✅ Every configured Q-ID resolves. Check the labels above match.")
    return 0


def parse_vote_ids(tokens) -> list:
    """The ``IdVote``s given on the command line. Pure.

    ``[]`` — from no tokens at all, or the literal ``auto`` — means "discover
    them". Anything that is neither a number nor ``auto`` raises, rather than
    being dropped: a silently ignored argument would run the check against a
    different set of votes than the caller asked for.
    """
    ids = []
    for token in tokens or []:
        if str(token).strip().lower() == "auto":
            continue
        try:
            ids.append(int(token))
        except (TypeError, ValueError):
            raise ValueError(
                f"--validate-periods takes IdVote numbers or 'auto', not {token!r}"
            )
    return ids


def _validate(args) -> int:
    """Run the period cross-check described in the README (step 4)."""
    from .config import load_config
    from .http_client import HttpClient
    from .parliament import ParliamentClient

    try:
        vote_ids = parse_vote_ids(args.validate_periods)
        config = load_config(args.config)
        http = HttpClient(
            user_agent=config.user_agent, request_delay=config.request_delay
        )
        parliament = ParliamentClient(session=http.session, language=config.language)
        report = validate_periods(parliament, config, vote_ids)
    except Exception as exc:  # pragma: no cover - top level guard
        logging.error("Validation failed: %s", exc)
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    if not report:
        print(
            "\n⚠️  No period could be compared: the votes named or discovered "
            "carry no attendance rows.",
            file=sys.stderr,
        )
        return 1

    departed = sum(v["voted_but_no_longer_sitting"] for v in report.values())
    if departed:
        print(
            f"\nℹ️  {departed} voter(s) are no longer sitting members and so "
            "cannot be checked — expected, and not a mismatch."
        )
    mismatched = sum(len(v["voted_but_not_assigned"]) for v in report.values())
    if mismatched:
        print(
            f"\n⚠️  {mismatched} sitting member(s) voted in a period the overlap "
            "did not assign them to — the interval logic needs a look.",
            file=sys.stderr,
        )
        return 1
    print(
        "\n✅ Every sitting member who voted was assigned to that period by the "
        "overlap."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
