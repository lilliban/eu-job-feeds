"""Command line entry point.

    eu-job-feeds update              read every registered company
    eu-job-feeds update --only greenhouse:datadog
    eu-job-feeds discover "Some Company"
    eu-job-feeds probe greenhouse datadog
    eu-job-feeds validate            check the dataset against the contract
    eu-job-feeds stats               field coverage, honestly measured
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from .connectors import CONNECTORS, PROVIDERS
from .discovery import discover, load_negative_cache, save_negative_cache
from .http import RateLimitedClient
from .models import FIELD_ORDER, JobPosting
from .pipeline import run_update
from .registry import CompanyEntry, Registry, load_registry, save_registry
from .store import DATA_DIR, load_company

log = logging.getLogger("eu_job_feeds")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _filter_registry(registry: Registry, only: list[str] | None) -> Registry:
    """Narrow the registry to `provider` or `provider:slug` selectors."""
    if not only:
        return registry
    wanted = set(only)
    kept = [
        entry
        for entry in registry.companies
        if entry.provider in wanted or f"{entry.provider}:{entry.key}" in wanted
    ]
    if not kept:
        raise SystemExit(f"no registry entry matches {', '.join(only)}")
    return Registry(companies=kept)


async def _cmd_update(args: argparse.Namespace) -> int:
    registry = _filter_registry(load_registry(Path(args.registry)), args.only)
    summary = await run_update(
        registry, data_dir=Path(args.data_dir), concurrency=args.concurrency
    )

    report = summary.as_markdown()
    print(report)

    # GitHub Actions renders this on the job page.
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(report)

    # A provider failing is not a reason to fail the job: the point of the
    # `complete` flag is that a bad read changes nothing. It is reported, loudly.
    return 0


async def _cmd_discover(args: argparse.Namespace) -> int:
    cache = load_negative_cache(Path(args.cache))
    cache.purge_expired()
    registry = load_registry(Path(args.registry))

    async with RateLimitedClient() as client:
        for company in args.company:
            hits = await discover(
                client,
                company,
                cache=cache,
                slugs=args.slug or None,
                providers=args.provider or None,
            )
            if not hits:
                print(f"{company}: no public ATS board found")
                continue
            for hit in hits:
                marker = "?" if hit.result.value == "ambiguous" else "+"
                print(f"{marker} {company}: {hit.provider}/{hit.slug} ({hit.result.value})")
                if hit.result.value == "ambiguous":
                    # SmartRecruiters cannot prove a company exists. A person has
                    # to confirm before this becomes a registry entry.
                    print("    needs a human: this provider cannot confirm the slug")
                    continue
                if args.write:
                    added = registry.upsert(
                        CompanyEntry(
                            name=company,
                            provider=hit.provider,
                            slug=hit.slug,
                            source="discovered",
                        )
                    )
                    if added:
                        print(f"    added to {args.registry}")

    if args.write:
        save_registry(registry, Path(args.registry))
    save_negative_cache(cache, Path(args.cache))
    return 0


async def _cmd_probe(args: argparse.Namespace) -> int:
    connector = CONNECTORS.get(args.provider)
    if connector is None:
        raise SystemExit(f"unknown provider {args.provider!r}; known: {', '.join(CONNECTORS)}")
    async with RateLimitedClient() as client:
        result = await connector.probe(client, args.slug)
        print(f"{args.provider}/{args.slug}: {result.value}")
        if args.fetch:
            outcome = await connector.fetch(client, args.slug)
            print(
                f"  complete={outcome.complete} jobs={len(outcome.jobs)} "
                f"error={outcome.error}"
            )
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Re-parse every stored posting against the model. Non-zero on any failure."""
    data_dir = Path(args.data_dir)
    problems = 0
    checked = 0

    for provider_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()) if data_dir.exists() else []:
        for path in sorted(provider_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                print(f"{path}: not valid JSON ({exc})")
                problems += 1
                continue
            for entry in raw.get("jobs", []):
                checked += 1
                try:
                    JobPosting.model_validate(entry)
                except Exception as exc:
                    print(f"{path}: invalid posting ({exc})")
                    problems += 1
                    break
                missing = [f for f in ("content_hash", "title", "company_name", "source_url") if not entry.get(f)]
                if missing:
                    print(f"{path}: posting missing required values: {', '.join(missing)}")
                    problems += 1
                    break

    print(f"validated {checked} postings; {problems} problem(s)")
    return 1 if problems else 0


def _cmd_stats(args: argparse.Namespace) -> int:
    """Field coverage across the dataset, per provider.

    Prints what is actually there, so the README can be written from measurement
    rather than from hope.
    """
    data_dir = Path(args.data_dir)
    registry = load_registry(Path(args.registry))
    per_provider: dict[str, list[JobPosting]] = {}

    for entry in registry.companies:
        postings = load_company(entry.provider, entry.key, data_dir)
        if postings:
            per_provider.setdefault(entry.provider, []).extend(postings)

    if not per_provider:
        print("no data on disk yet; run `update` first")
        return 0

    fields = [f for f in FIELD_ORDER if f not in {"content_hash", "source_kind", "first_seen_at", "last_seen_at", "consecutive_misses", "is_closed", "external_id"}]
    width = max(len(f) for f in fields)
    for provider in sorted(per_provider):
        postings = per_provider[provider]
        print(f"\n{provider}  ({len(postings)} postings)")
        for field_name in fields:
            filled = sum(
                1 for p in postings if getattr(p, field_name) not in (None, "", [])
            )
            pct = 100 * filled // len(postings)
            bar = "#" * (pct // 5)
            print(f"  {field_name:<{width}}  {pct:3d}%  {bar}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eu-job-feeds", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--registry", default="registry/companies.yaml")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    sub = parser.add_subparsers(dest="command", required=True)

    update = sub.add_parser("update", help="read every registered company and write changes")
    update.add_argument(
        "--only",
        action="append",
        metavar="PROVIDER[:SLUG]",
        help="restrict to a provider or a single company; repeatable",
    )
    update.add_argument("--concurrency", type=int, default=6)

    disc = sub.add_parser("discover", help="find which board a company is on")
    disc.add_argument("company", nargs="+")
    disc.add_argument("--slug", action="append", help="try this exact slug instead of guessing")
    disc.add_argument("--provider", action="append", choices=list(CONNECTORS))
    disc.add_argument("--write", action="store_true", help="add hits to the registry")
    disc.add_argument("--cache", default="registry/negative.json")

    probe = sub.add_parser("probe", help="check one provider/slug pair")
    probe.add_argument("provider", choices=list(CONNECTORS))
    probe.add_argument("slug")
    probe.add_argument("--fetch", action="store_true", help="also fetch and count postings")

    sub.add_parser("validate", help="check the stored dataset against the contract")
    sub.add_parser("stats", help="field coverage per provider")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    if args.command == "update":
        return asyncio.run(_cmd_update(args))
    if args.command == "discover":
        return asyncio.run(_cmd_discover(args))
    if args.command == "probe":
        return asyncio.run(_cmd_probe(args))
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "stats":
        return _cmd_stats(args)
    raise SystemExit(f"unknown command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
