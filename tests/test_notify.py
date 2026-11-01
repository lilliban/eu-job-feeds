"""The webhook that tells job_matcher about new postings faster than its own sync.

Inert by default (no `url` and no `JOB_MATCHER_WEBHOOK_URL` set), and a failure
must never look different from success to the caller — `run_update` awaits this
inside the same `try` that closes the HTTP client, so an exception here would
take the whole run down with it.
"""

from __future__ import annotations

from eu_job_feeds.models import JobPosting
from eu_job_feeds.notify import notify_new_postings

from .conftest import FakeClient

WEBHOOK = "https://job-matcher.example/webhooks/new-postings"


def posting(external_id: str = "1") -> JobPosting:
    return JobPosting(
        content_hash="abc123",
        title="Data Engineer",
        company_name="Acme",
        source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        source_board="greenhouse",
        external_id=external_id,
        first_seen_at="2026-09-24T00:00:00+00:00",
        last_seen_at="2026-09-24T00:00:00+00:00",
    )


class TestInertByDefault:
    async def test_no_url_configured_is_a_noop(self, monkeypatch) -> None:
        monkeypatch.delenv("JOB_MATCHER_WEBHOOK_URL", raising=False)
        client = FakeClient({})
        sent = await notify_new_postings(client, [posting()])
        assert sent is False
        assert client.posted == []

    async def test_no_new_postings_is_a_noop_even_with_a_url(self) -> None:
        client = FakeClient({})
        sent = await notify_new_postings(client, [], url=WEBHOOK)
        assert sent is False
        assert client.posted == []


class TestSending:
    async def test_a_configured_url_receives_the_postings(self) -> None:
        client = FakeClient({WEBHOOK: (200, "{}")})
        sent = await notify_new_postings(client, [posting("1"), posting("2")], url=WEBHOOK)
        assert sent is True
        assert len(client.posted) == 1
        url, payload = client.posted[0]
        assert url == WEBHOOK
        assert payload["count"] == 2
        assert [p["external_id"] for p in payload["postings"]] == ["1", "2"]

    async def test_the_environment_variable_is_used_when_no_url_is_passed(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("JOB_MATCHER_WEBHOOK_URL", WEBHOOK)
        client = FakeClient({WEBHOOK: (200, "{}")})
        sent = await notify_new_postings(client, [posting()])
        assert sent is True
        assert client.posted[0][0] == WEBHOOK


class TestFailureIsSwallowed:
    async def test_a_non_2xx_response_is_reported_as_not_sent(self) -> None:
        client = FakeClient({WEBHOOK: (500, "")})
        sent = await notify_new_postings(client, [posting()], url=WEBHOOK)
        assert sent is False

    async def test_a_transport_error_does_not_raise(self) -> None:
        class BrokenClient:
            async def post_json(self, url: str, json_body: object, **kwargs: object):
                raise ConnectionError("dns lookup failed")

        sent = await notify_new_postings(BrokenClient(), [posting()], url=WEBHOOK)
        assert sent is False
