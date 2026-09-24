"""Discovery and the negative cache (trap 5).

Remembering that a company has no board saves seven requests a run. Remembering
it forever means a company that adopts Greenhouse next month is never seen
again. Hence the 30-day expiry — and the rule that only a *definitive* answer is
ever written down.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eu_job_feeds.discovery import (
    NEGATIVE_TTL_DAYS,
    NegativeCache,
    candidate_slugs,
    discover,
    load_negative_cache,
    save_negative_cache,
)

NOW = datetime(2026, 8, 2, tzinfo=timezone.utc)


class TestNegativeCache:
    def test_unknown_slug_is_not_absent(self) -> None:
        assert NegativeCache().is_known_absent("greenhouse", "acme") is False

    def test_a_miss_is_remembered(self) -> None:
        cache = NegativeCache()
        cache.remember_absent("greenhouse", "acme", now=NOW)
        assert cache.is_known_absent("greenhouse", "acme", now=NOW) is True

    def test_the_memory_expires(self) -> None:
        """A company that adopts an ATS later must become findable again."""
        cache = NegativeCache()
        cache.remember_absent("greenhouse", "acme", now=NOW)

        just_inside = NOW + timedelta(days=NEGATIVE_TTL_DAYS - 1)
        assert cache.is_known_absent("greenhouse", "acme", now=just_inside) is True

        just_outside = NOW + timedelta(days=NEGATIVE_TTL_DAYS + 1)
        assert cache.is_known_absent("greenhouse", "acme", now=just_outside) is False

    def test_the_memory_is_per_provider(self) -> None:
        cache = NegativeCache()
        cache.remember_absent("greenhouse", "acme", now=NOW)
        assert cache.is_known_absent("lever", "acme", now=NOW) is False

    def test_finding_a_board_forgets_the_miss(self) -> None:
        cache = NegativeCache()
        cache.remember_absent("greenhouse", "acme", now=NOW)
        cache.forget("greenhouse", "acme")
        assert cache.is_known_absent("greenhouse", "acme", now=NOW) is False

    def test_expired_entries_are_purged(self) -> None:
        cache = NegativeCache()
        cache.remember_absent("greenhouse", "old", now=NOW - timedelta(days=60))
        cache.remember_absent("greenhouse", "fresh", now=NOW)

        assert cache.purge_expired(now=NOW) == 1
        assert "greenhouse:fresh" in cache.to_dict()["expires_at"]
        assert "greenhouse:old" not in cache.to_dict()["expires_at"]

    def test_an_unreadable_timestamp_counts_as_expired(self) -> None:
        """Re-probing costs one request; trusting an unreadable value costs a month."""
        cache = NegativeCache({"greenhouse:acme": "not-a-date"})
        assert cache.is_known_absent("greenhouse", "acme", now=NOW) is False

    def test_round_trip_through_disk(self, tmp_path) -> None:
        path = tmp_path / "negative.json"
        cache = NegativeCache()
        cache.remember_absent("greenhouse", "acme", now=NOW)
        save_negative_cache(cache, path)

        restored = load_negative_cache(path)
        assert restored.is_known_absent("greenhouse", "acme", now=NOW) is True

    def test_missing_and_corrupt_files_start_empty(self, tmp_path) -> None:
        assert load_negative_cache(tmp_path / "absent.json").to_dict()["expires_at"] == {}
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{not json", encoding="utf-8")
        assert load_negative_cache(corrupt).to_dict()["expires_at"] == {}


class TestCandidateSlugs:
    @pytest.mark.parametrize(
        "company,expected_first",
        [
            ("Datadog", "datadog"),
            ("Amazing Care", "amazingcare"),
            ("Bosch GmbH", "bosch"),
            ("Acme Inc.", "acme"),
        ],
    )
    def test_first_guess(self, company: str, expected_first: str) -> None:
        assert candidate_slugs(company)[0] == expected_first

    def test_hyphenated_variant_is_offered(self) -> None:
        assert "amazing-care" in candidate_slugs("Amazing Care")

    def test_no_duplicates(self) -> None:
        slugs = candidate_slugs("Datadog")
        assert len(slugs) == len(set(slugs))

    def test_empty_input(self) -> None:
        assert candidate_slugs("") == []
        assert candidate_slugs("   ") == []


class _StubConnector:
    """Returns a scripted probe result and counts how often it was asked."""

    def __init__(self, provider: str, result: str) -> None:
        self.provider = provider
        self._result = result
        self.calls = 0

    async def probe(self, client: object, slug: str):
        from eu_job_feeds.connectors.base import ProbeResult

        self.calls += 1
        return ProbeResult(self._result)


class TestDiscover:
    async def test_a_hit_is_returned_and_stops_further_slugs(self, monkeypatch) -> None:
        import eu_job_feeds.discovery as module

        stub = _StubConnector("greenhouse", "found")
        monkeypatch.setattr(module, "CONNECTORS", {"greenhouse": stub})

        hits = await discover(None, "Datadog", cache=NegativeCache())
        assert [(h.provider, h.slug) for h in hits] == [("greenhouse", "datadog")]
        assert stub.calls == 1

    async def test_a_miss_is_cached(self, monkeypatch) -> None:
        import eu_job_feeds.discovery as module

        monkeypatch.setattr(module, "CONNECTORS", {"greenhouse": _StubConnector("greenhouse", "not_found")})
        cache = NegativeCache()
        await discover(None, "Nobody", cache=cache, slugs=["nobody"])
        assert cache.is_known_absent("greenhouse", "nobody") is True

    async def test_a_cached_miss_is_not_re_probed(self, monkeypatch) -> None:
        import eu_job_feeds.discovery as module

        stub = _StubConnector("greenhouse", "not_found")
        monkeypatch.setattr(module, "CONNECTORS", {"greenhouse": stub})
        cache = NegativeCache()
        cache.remember_absent("greenhouse", "nobody")

        await discover(None, "Nobody", cache=cache, slugs=["nobody"])
        assert stub.calls == 0

    async def test_an_error_is_never_cached(self, monkeypatch) -> None:
        """A timeout or a 429 answered nothing.

        Recording it as absence turns a moment's outage into a month of silence.
        """
        import eu_job_feeds.discovery as module

        monkeypatch.setattr(module, "CONNECTORS", {"personio": _StubConnector("personio", "error")})
        cache = NegativeCache()
        hits = await discover(None, "Acme", cache=cache, slugs=["acme"])

        assert hits == []
        assert cache.is_known_absent("personio", "acme") is False

    async def test_ambiguous_is_surfaced_but_not_cached(self, monkeypatch) -> None:
        """SmartRecruiters cannot prove absence, so a human decides."""
        import eu_job_feeds.discovery as module

        monkeypatch.setattr(
            module, "CONNECTORS", {"smartrecruiters": _StubConnector("smartrecruiters", "ambiguous")}
        )
        cache = NegativeCache()
        hits = await discover(None, "Acme", cache=cache, slugs=["acme"])

        assert [h.result.value for h in hits] == ["ambiguous"]
        assert cache.is_known_absent("smartrecruiters", "acme") is False
