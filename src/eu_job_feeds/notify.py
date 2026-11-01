"""Push a webhook when a run finds postings nobody has seen before.

`job_matcher` (the consumer of this dataset) already syncs the dataset itself
on its own 6h cadence and can find new postings by diffing `first_seen_at`.
This is a *faster* channel on top of that, not a replacement: a run that finds
new postings also POSTs them, so a consumer that wants to react immediately
does not have to wait for its next scheduled sync.

Inert by default. Without `JOB_MATCHER_WEBHOOK_URL` set, `notify_new_postings`
is a no-op — the collector must work identically whether or not a consumer has
configured a webhook. A failed POST is logged and swallowed, the same way a
provider failure is: this is a courtesy notification, and losing it must never
fail the run that produced good data.
"""

from __future__ import annotations

import logging
import os

from .http import RateLimitedClient
from .models import JobPosting, utcnow_iso

log = logging.getLogger(__name__)

#: Name of the environment variable carrying the webhook URL. Set from the
#: `JOB_MATCHER_WEBHOOK_URL` repository secret in `.github/workflows/update.yml`.
WEBHOOK_URL_ENV = "JOB_MATCHER_WEBHOOK_URL"


async def notify_new_postings(
    client: RateLimitedClient,
    postings: list[JobPosting],
    *,
    url: str | None = None,
) -> bool:
    """POST newly-seen postings to the configured webhook, if any.

    Returns True only when a webhook was configured, had postings to send, and
    the endpoint answered 2xx. Every other case — no URL configured, nothing
    new this run, a transport error, a non-2xx response — returns False
    without raising.
    """
    target = url if url is not None else os.environ.get(WEBHOOK_URL_ENV)
    if not target:
        return False
    if not postings:
        return False

    payload = {
        "generated_at": utcnow_iso(),
        "count": len(postings),
        "postings": [p.to_ordered_dict() for p in postings],
    }

    try:
        status, _ = await client.post_json(target, payload)
    except Exception as exc:  # a notification failure must not sink the run
        log.warning("webhook POST to %s failed: %r", target, exc)
        return False

    if not 200 <= status < 300:
        log.warning("webhook POST to %s answered http %s", target, status)
        return False

    log.info("notified %s of %d new posting(s)", target, len(postings))
    return True
