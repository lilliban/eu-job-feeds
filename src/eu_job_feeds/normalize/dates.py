"""Posted-date normalisation: every provider's own format, to one shape.

Every connector already reads `posted_date` from a structured field the ATS
itself provides — never derived from free text (`entry.get("first_published")`
on Greenhouse, `publishedAt` on Ashby, a converted epoch on Lever,
`company_name`-style structured fields elsewhere). What varies is the
provider's own format: Workable ships a bare date (`2026-07-13`), most others
a full ISO 8601 datetime (`2026-07-14T08:29:20.852Z`). The contract wants one
shape, `YYYY-MM-DD`, so a consumer filtering "postings from the last N days"
never has to parse more than one format itself.
"""

from __future__ import annotations

import re

_LEADING_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def normalize_posted_date(raw: str | None) -> str | None:
    """Truncate an ISO 8601 date or datetime to its date component.

    Every connector already hands this a string starting with `YYYY-MM-DD` —
    confirmed against real payloads from Greenhouse, Ashby, Lever, Recruitee,
    SmartRecruiters, Workable, Personio and Workday. A string that does not
    match is treated as unparseable and dropped to `None` rather than passed
    through: a field whose contract promises `YYYY-MM-DD` is worse off
    holding something else than holding nothing, same reasoning as the
    salary and experience extractors (see docs/DECISIONS.md #15).
    """
    if not raw:
        return None
    match = _LEADING_DATE.match(raw.strip())
    return match.group(1) if match else None
