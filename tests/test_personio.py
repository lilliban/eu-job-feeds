"""Personio: the connector that must not trust its status code (trap 1).

An unknown slug does not 404. Personio redirects to `www.personio.com` and
answers with its marketing site. `tests/fixtures/personio_missing_slug.html` is
that real response, captured from `zzznotarealcompanyzzz.jobs.personio.de/xml`:
33 KB of HTML that a status-code check would happily accept.

If these tests ever fail open, every company in the world gets a Personio board.
"""

from __future__ import annotations

import pytest

from eu_job_feeds.connectors.personio import PersonioConnector

from .conftest import FakeClient, load_fixture

REAL_XML = "personio_real.xml"
MARKETING_HTML = "personio_missing_slug.html"


@pytest.fixture
def connector() -> PersonioConnector:
    return PersonioConnector()


class TestRootElementValidation:
    def test_real_feed_is_accepted(self, connector: PersonioConnector) -> None:
        assert connector._parse_root(load_fixture(REAL_XML)) is not None

    def test_marketing_page_is_rejected(self, connector: PersonioConnector) -> None:
        """The actual body Personio serves for a slug that does not exist."""
        body = load_fixture(MARKETING_HTML)
        assert len(body) > 10_000, "fixture should be the full marketing page"
        assert connector._parse_root(body) is None

    def test_wrong_root_element_is_rejected(self, connector: PersonioConnector) -> None:
        assert connector._parse_root("<?xml version='1.0'?><jobs><position/></jobs>") is None

    def test_empty_and_malformed(self, connector: PersonioConnector) -> None:
        assert connector._parse_root("") is None
        assert connector._parse_root("<workzag-jobs><unclosed>") is None

    def test_entity_declarations_are_refused(self, connector: PersonioConnector) -> None:
        """A job board has no reason to declare entities; expanding them is a risk."""
        bomb = (
            "<?xml version='1.0'?><!DOCTYPE workzag-jobs ["
            "<!ENTITY a 'aaaaaaaaaa'>]><workzag-jobs><position><id>1</id>"
            "<name>&a;</name></position></workzag-jobs>"
        )
        assert connector._parse_root(bomb) is None


class TestFetch:
    async def test_marketing_page_yields_no_jobs(self, connector: PersonioConnector) -> None:
        """A 200 full of marketing HTML means 'no board here', not 'a board'."""
        client = FakeClient({"jobs.personio.de": (200, load_fixture(MARKETING_HTML))})
        outcome = await connector.fetch(client, "zzznotarealcompanyzzz")
        assert outcome.jobs == []
        # Complete, because the question *was* answered: this slug has no board.
        assert outcome.complete is True

    async def test_probe_reports_not_found_for_the_marketing_page(
        self, connector: PersonioConnector
    ) -> None:
        client = FakeClient({"jobs.personio.de": (200, load_fixture(MARKETING_HTML))})
        result = await connector.probe(client, "zzznotarealcompanyzzz")
        assert result.value == "not_found"

    async def test_real_feed_is_parsed(self, connector: PersonioConnector) -> None:
        client = FakeClient({"jobs.personio.de": (200, load_fixture(REAL_XML))})
        outcome = await connector.fetch(client, "personio")
        assert outcome.complete is True
        assert len(outcome.jobs) == 1

        job = outcome.jobs[0]
        assert job.title == "Staff Software Engineer, Data Platform"
        assert job.external_id == "1834171"
        assert job.source_url == "https://personio.jobs.personio.de/job/1834171"
        assert job.department == "Product and Tech"
        # `yearsOfExperience` is `7-10`: structured data, so no regex involved.
        assert job.min_years_exp_hint == 7
        assert job.max_years_exp_hint == 10
        # Both offices are kept.
        assert "Munich" in job.location
        assert "Berlin" in job.location

    async def test_throttling_is_not_an_empty_board(
        self, connector: PersonioConnector
    ) -> None:
        """429 means the question went unanswered.

        Returning `complete` here would let the lifecycle start closing every
        posting the company has, purely because Personio was rate-limiting.
        """
        client = FakeClient({"jobs.personio.de": (429, load_fixture(MARKETING_HTML))})
        outcome = await connector.fetch(client, "personio")
        assert outcome.complete is False
        assert outcome.jobs == []

    async def test_throttling_probes_as_error_not_absent(
        self, connector: PersonioConnector
    ) -> None:
        """An ERROR is never written to the negative cache; NOT_FOUND would be."""
        client = FakeClient({"jobs.personio.de": (429, "")})
        assert (await connector.probe(client, "acme")).value == "error"

    async def test_clean_404_is_an_empty_board(self, connector: PersonioConnector) -> None:
        client = FakeClient({"jobs.personio.de": (404, "Not Found")})
        outcome = await connector.fetch(client, "acme")
        assert outcome.complete is True
        assert outcome.jobs == []
