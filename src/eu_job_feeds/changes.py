"""The file of what changed this run: new and closed postings.

Published as a release asset alongside `jobs.sqlite` (docs/DECISIONS.md #18),
never committed to git. Overwritten unconditionally every run — there is no
diff trail beyond the last run, which matches "asset of the `latest` release,
replaced every run" as stated. Unlike `store.write_company`, there is no
stability short-circuit: this file is never git-diffed, so a no-op run simply
writes a valid document with empty arrays.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import JobPosting, utcnow_iso

CHANGES_PATH = Path("state/changes.json")


def _sort_key(posting: JobPosting) -> tuple:
    return (posting.content_hash, posting.source_url)


def write_changes(
    new: list[JobPosting],
    closed: list[JobPosting],
    path: Path = CHANGES_PATH,
) -> None:
    document = {
        "generated_at": utcnow_iso(),
        "new_count": len(new),
        "closed_count": len(closed),
        "new": [p.to_ordered_dict() for p in sorted(new, key=_sort_key)],
        "closed": [p.to_ordered_dict() for p in sorted(closed, key=_sort_key)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
