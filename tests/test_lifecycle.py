"""Posting lifecycle (trap 4).

A posting closes after **two consecutive complete reads** that did not return
it. The word carrying the weight is *complete*: a read that failed, timed out,
or covered only part of the catalogue must change nothing at all. Comparing the
archive against a partial list closes everything outside that list.
"""

from __future__ import annotations

from eu_job_feeds.lifecycle import MISSES_BEFORE_CLOSED, already_detailed_ids, merge
from eu_job_feeds.models import FetchOutcome, JobPosting, RawJob

COMPANY = "Acme"


def raw(external_id: str, title: str = "Data Analyst") -> RawJob:
    return RawJob(
        provider="greenhouse",
        external_id=external_id,
        title=title,
        source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        description_text="We are hiring.\nRequirements\n- SQL",
    )


def complete_read(*jobs: RawJob) -> FetchOutcome:
    return FetchOutcome(provider="greenhouse", slug="acme", complete=True, jobs=list(jobs))


def failed_read(error: str = "http 503") -> FetchOutcome:
    return FetchOutcome(provider="greenhouse", slug="acme", complete=False, error=error)


def first_run(*jobs: RawJob) -> list[JobPosting]:
    postings, _ = merge([], complete_read(*jobs), company_name=COMPANY)
    return postings


class TestFirstSighting:
    def test_new_postings_are_recorded_open(self) -> None:
        postings, stats = merge([], complete_read(raw("1"), raw("2")), company_name=COMPANY)
        assert stats.new == 2
        assert len(postings) == 2
        assert all(p.consecutive_misses == 0 and not p.is_closed for p in postings)

    def test_new_postings_list_matches_the_count(self) -> None:
        """A consumer notifying on new adverts needs the postings, not just `new`."""
        postings, stats = merge([], complete_read(raw("1"), raw("2")), company_name=COMPANY)
        assert {p.external_id for p in stats.new_postings} == {"1", "2"}
        assert stats.new_postings == [p for p in postings]

    def test_a_still_open_posting_is_not_in_the_new_list(self) -> None:
        stored = first_run(raw("1"))
        _, stats = merge(stored, complete_read(raw("1")), company_name=COMPANY)
        assert stats.new_postings == []

    def test_canonical_name_is_used_not_the_slug(self) -> None:
        postings = first_run(raw("1"))
        assert postings[0].company_name == COMPANY


class TestStillPresent:
    def test_seen_again_resets_nothing_and_keeps_first_seen(self) -> None:
        stored = first_run(raw("1"))
        stored[0].first_seen_at = "2020-01-01T00:00:00+00:00"

        postings, stats = merge(
            stored, complete_read(raw("1")), company_name=COMPANY, seen_at="2026-08-02T00:00:00+00:00"
        )
        assert stats.still_open == 1
        assert postings[0].first_seen_at == "2020-01-01T00:00:00+00:00"
        assert postings[0].last_seen_at == "2026-08-02T00:00:00+00:00"
        assert postings[0].consecutive_misses == 0

    def test_a_miss_then_a_hit_clears_the_counter(self) -> None:
        stored = first_run(raw("1"))
        stored, _ = merge(stored, complete_read(), company_name=COMPANY)
        assert stored[0].consecutive_misses == 1

        stored, _ = merge(stored, complete_read(raw("1")), company_name=COMPANY)
        assert stored[0].consecutive_misses == 0
        assert stored[0].is_closed is False


class TestClosing:
    def test_one_miss_does_not_close(self) -> None:
        """One read can be wrong; that is the whole reason the threshold is two."""
        stored = first_run(raw("1"))
        postings, stats = merge(stored, complete_read(), company_name=COMPANY)

        assert postings[0].consecutive_misses == 1
        assert postings[0].is_closed is False
        assert stats.newly_closed == 0

    def test_two_consecutive_misses_close(self) -> None:
        stored = first_run(raw("1"))
        stored, _ = merge(stored, complete_read(), company_name=COMPANY)
        postings, stats = merge(stored, complete_read(), company_name=COMPANY)

        assert postings[0].consecutive_misses == MISSES_BEFORE_CLOSED
        assert postings[0].is_closed is True
        assert stats.newly_closed == 1

    def test_closed_postings_lists_what_newly_closed_counts(self) -> None:
        """A consumer publishing the run's diff (`changes.py`) needs the
        postings themselves, not just a count — same reasoning as `new_postings`."""
        stored = first_run(raw("1"))
        stored, _ = merge(stored, complete_read(), company_name=COMPANY)
        _, stats = merge(stored, complete_read(), company_name=COMPANY)

        assert [p.external_id for p in stats.closed_postings] == ["1"]

    def test_closing_is_reported_only_once(self) -> None:
        stored = first_run(raw("1"))
        for _ in range(2):
            stored, _ = merge(stored, complete_read(), company_name=COMPANY)
        _, stats = merge(stored, complete_read(), company_name=COMPANY)
        assert stats.newly_closed == 0

    def test_a_closed_posting_reopens_if_it_comes_back(self) -> None:
        stored = first_run(raw("1"))
        for _ in range(2):
            stored, _ = merge(stored, complete_read(), company_name=COMPANY)
        assert stored[0].is_closed is True

        postings, stats = merge(stored, complete_read(raw("1")), company_name=COMPANY)
        assert postings[0].is_closed is False
        assert postings[0].consecutive_misses == 0
        assert stats.reopened == 1

    def test_closed_postings_are_kept_not_deleted(self) -> None:
        stored = first_run(raw("1"), raw("2"))
        for _ in range(2):
            stored, _ = merge(stored, complete_read(raw("1")), company_name=COMPANY)
        assert len(stored) == 2
        assert sum(1 for p in stored if p.is_closed) == 1


class TestIncompleteReadsChangeNothing:
    """The error the brief describes: closing everything outside a partial read."""

    def test_a_failed_read_does_not_count_a_miss(self) -> None:
        stored = first_run(raw("1"), raw("2"))
        postings, stats = merge(stored, failed_read(), company_name=COMPANY)

        assert stats.skipped is True
        assert all(p.consecutive_misses == 0 for p in postings)
        assert all(not p.is_closed for p in postings)

    def test_repeated_failures_never_close_anything(self) -> None:
        """Two failed reads look like two misses if `complete` is not checked."""
        stored = first_run(raw("1"))
        for _ in range(10):
            stored, _ = merge(stored, failed_read(), company_name=COMPANY)

        assert stored[0].consecutive_misses == 0
        assert stored[0].is_closed is False

    def test_a_partial_read_is_not_a_catalogue(self) -> None:
        """A connector that walked half the pages reports complete=False.

        Trusting the postings it did return would close the other half.
        """
        stored = first_run(raw("1"), raw("2"), raw("3"))
        partial = FetchOutcome(
            provider="greenhouse",
            slug="acme",
            complete=False,
            jobs=[raw("1")],
            error="got 1 of 3 postings",
        )
        postings, stats = merge(stored, partial, company_name=COMPANY)

        assert stats.skipped is True
        assert len(postings) == 3
        assert all(p.consecutive_misses == 0 for p in postings)

    def test_the_archive_is_returned_untouched(self) -> None:
        stored = first_run(raw("1"))
        before = stored[0].model_dump()
        postings, _ = merge(stored, failed_read(), company_name=COMPANY)
        assert postings[0].model_dump() == before

    def test_error_is_carried_into_the_stats(self) -> None:
        stored = first_run(raw("1"))
        _, stats = merge(stored, failed_read("http 503"), company_name=COMPANY)
        assert stats.errors == ["http 503"]


class TestEmptyButComplete:
    def test_an_empty_complete_read_does_count(self) -> None:
        """`200 []` from Lever is a real answer: the board exists and is empty."""
        stored = first_run(raw("1"))
        postings, stats = merge(stored, complete_read(), company_name=COMPANY)
        assert stats.skipped is False
        assert postings[0].consecutive_misses == 1


class TestIdentity:
    def test_the_same_role_in_two_cities_stays_two_postings(self) -> None:
        """These share a `content_hash` by the contract's own definition.

        Keying the lifecycle on the hash would merge them and then close one.
        """
        a = raw("1", "Account Executive")
        b = raw("2", "Account Executive")
        postings, stats = merge([], complete_read(a, b), company_name=COMPANY)

        assert stats.new == 2
        assert len(postings) == 2
        assert postings[0].content_hash == postings[1].content_hash
        assert postings[0].external_id != postings[1].external_id

    def test_postings_survive_a_changed_url(self) -> None:
        """Matching is on the provider id, which outlives a URL change."""
        stored = first_run(raw("1"))
        moved = raw("1")
        moved.source_url = "https://careers.acme.com/new-path/1"

        postings, stats = merge(stored, complete_read(moved), company_name=COMPANY)
        assert stats.new == 0
        assert stats.still_open == 1
        assert postings[0].source_url.endswith("/new-path/1")


class TestAlreadyDetailed:
    def test_postings_with_text_are_reported(self) -> None:
        stored = first_run(raw("1"))
        assert already_detailed_ids(stored) == frozenset({"1"})

    def test_a_posting_without_text_still_needs_its_detail_call(self) -> None:
        stored = first_run(raw("1"))
        stored[0].description = None
        stored[0].requirements_raw = None
        assert already_detailed_ids(stored) == frozenset()
