"""Posted-date normalisation.

Every connector already reads this from a structured ATS field; the only job
here is making the output shape (`YYYY-MM-DD`) consistent across providers
that disagree on whether to include a time component.
"""

from __future__ import annotations

from eu_job_feeds.normalize.dates import normalize_posted_date


class TestNormalizePostedDate:
    def test_a_bare_date_passes_through(self) -> None:
        """Workable's own shape already."""
        assert normalize_posted_date("2026-07-13") == "2026-07-13"

    def test_a_full_iso_datetime_is_truncated(self) -> None:
        """SmartRecruiters' `releasedDate` shape."""
        assert normalize_posted_date("2026-07-14T08:29:20.852Z") == "2026-07-14"

    def test_a_datetime_with_an_offset_is_truncated(self) -> None:
        """Lever's connector already converts its epoch-ms field to this shape."""
        assert normalize_posted_date("2026-07-20T14:03:19+00:00") == "2026-07-20"

    def test_none_stays_none(self) -> None:
        assert normalize_posted_date(None) is None

    def test_an_empty_string_becomes_none(self) -> None:
        assert normalize_posted_date("") is None

    def test_an_unparseable_value_becomes_none_not_a_guess(self) -> None:
        """A field whose contract promises YYYY-MM-DD is worse off holding
        something else than holding nothing (docs/DECISIONS.md #15)."""
        assert normalize_posted_date("Posted Today") is None
        assert normalize_posted_date("3 days ago") is None
