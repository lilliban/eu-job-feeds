"""Merging a fresh read into the archive, and deciding what is closed.

The rule: a posting is closed once **two consecutive complete reads** of its
company's feed have not returned it. Two, not one, because a single read can be
partial or wrong.

The load-bearing word is *complete*. A miss may only be counted when the whole
catalogue was walked and the posting genuinely was not in it. Comparing the
archive against a partial page walk, a timed-out request, or — worst — a
filtered subset closes everything that fell outside the comparison. An
incomplete read therefore leaves the stored postings exactly as they were:
nothing is closed, nothing is counted, `last_seen_at` is not touched.

`FetchOutcome.complete` is set by the connectors and is the only thing consulted
here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .models import FetchOutcome, JobPosting, seen_stamp
from .normalize import build_posting

log = logging.getLogger(__name__)

#: Consecutive complete reads without a posting before it counts as closed.
MISSES_BEFORE_CLOSED = 2


@dataclass
class MergeStats:
    provider: str = ""
    slug: str = ""
    #: True when the read was ignored because the fetch did not complete.
    skipped: bool = False
    new: int = 0
    still_open: int = 0
    reopened: int = 0
    newly_closed: int = 0
    total: int = 0
    errors: list[str] = field(default_factory=list)
    #: The postings behind `new` — a consumer notifying on new adverts needs the
    #: postings themselves, not just a count.
    new_postings: list[JobPosting] = field(default_factory=list)


def identity(provider: str, external_id: str) -> str:
    """Stable key for a posting across runs.

    Not `content_hash`: the same role in three cities shares one hash by the
    contract's own definition, so keying on it would collapse those three into
    one and then close two of them on the next run. The provider's own id is
    what stays one-to-one with an advert.
    """
    return f"{provider}:{external_id}"


def stored_identity(posting: JobPosting) -> str:
    """Key of an archived posting.

    Falls back to `source_url` for postings written before `external_id` was
    stored — both are one-to-one with an advert, so the fallback keeps an older
    archive matching instead of treating every posting as new and closing it.
    """
    return identity(posting.source_board, posting.external_id or posting.source_url)


def merge(
    stored: list[JobPosting],
    outcome: FetchOutcome,
    *,
    company_name: str,
    seen_at: str | None = None,
) -> tuple[list[JobPosting], MergeStats]:
    """Fold a fetch result into the stored postings.

    Returns the postings to write and a summary of what moved.
    """
    stats = MergeStats(provider=outcome.provider, slug=outcome.slug, total=len(stored))

    if not outcome.complete:
        # Trap 4. Nothing about the catalogue was established, so nothing changes.
        stats.skipped = True
        if outcome.error:
            stats.errors.append(outcome.error)
        log.info(
            "%s/%s: incomplete read (%s); archive left untouched",
            outcome.provider, outcome.slug, outcome.error or "no reason given",
        )
        return list(stored), stats

    now = seen_at or seen_stamp()
    by_identity = {stored_identity(p): p for p in stored}
    fresh_ids: set[str] = set()
    result: list[JobPosting] = []

    for raw in outcome.jobs:
        key = identity(raw.provider, raw.external_id)
        fresh_ids.add(key)
        built = build_posting(raw, company_name=company_name, seen_at=now)
        previous = by_identity.get(key)

        if previous is None:
            stats.new += 1
            stats.new_postings.append(built)
            result.append(built)
            continue

        if previous.is_closed:
            stats.reopened += 1
        else:
            stats.still_open += 1

        # Carry forward what only the archive knows, and let everything else be
        # refreshed from the provider — a company may edit an advert in place.
        built.first_seen_at = previous.first_seen_at
        built.last_seen_at = now
        built.consecutive_misses = 0
        built.is_closed = False
        result.append(built)

    for key, previous in by_identity.items():
        if key in fresh_ids:
            continue
        # Absent from a complete read: this one counts.
        misses = previous.consecutive_misses + 1
        closed = misses >= MISSES_BEFORE_CLOSED
        if closed and not previous.is_closed:
            stats.newly_closed += 1
        result.append(
            previous.model_copy(update={"consecutive_misses": misses, "is_closed": closed})
        )

    stats.total = len(result)
    return result, stats


def already_detailed_ids(stored: list[JobPosting]) -> frozenset[str]:
    """External ids whose advert text is already stored.

    Passed to connectors that need one request per advert (SmartRecruiters,
    Workday) so a steady-state run re-fetches only the postings it has never
    read. A posting stored without text is deliberately excluded: it still needs
    its detail call.
    """
    return frozenset(
        p.external_id
        for p in stored
        if p.external_id and (p.description or p.requirements_raw)
    )
