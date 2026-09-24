"""Connector parsing, against payloads captured from the live APIs.

Each fixture is a trimmed copy of a real response. The assertions encode what
each provider actually sends, including the parts that differ from its
documentation.
"""

from __future__ import annotations

import pytest

from eu_job_feeds.connectors import (
    AshbyConnector,
    GreenhouseConnector,
    LeverConnector,
    RecruiteeConnector,
    SmartRecruitersConnector,
    WorkableConnector,
    WorkdayConnector,
    WorkdayTarget,
)

from .conftest import FakeClient, load_fixture


class TestGreenhouse:
    async def test_parses_a_real_board(self) -> None:
        client = FakeClient({"boards-api.greenhouse.io": (200, load_fixture("greenhouse_datadog.json"))})
        outcome = await GreenhouseConnector().fetch(client, "datadog")

        assert outcome.complete is True
        assert len(outcome.jobs) == 3
        job = outcome.jobs[0]
        assert job.title
        assert job.source_url.startswith("https://")
        assert job.location
        assert job.department

    async def test_content_is_entity_escaped(self) -> None:
        """Greenhouse sends `&lt;p&gt;`, which must survive to readable text."""
        client = FakeClient({"boards-api.greenhouse.io": (200, load_fixture("greenhouse_datadog.json"))})
        outcome = await GreenhouseConnector().fetch(client, "datadog")
        assert "&lt;" in outcome.jobs[0].description_html

    async def test_missing_board_is_complete_and_empty(self) -> None:
        """404 is a real answer, so the lifecycle may act on it."""
        client = FakeClient({"boards-api.greenhouse.io": (404, '{"status":404,"error":"Job not found"}')})
        outcome = await GreenhouseConnector().fetch(client, "nope")
        assert outcome.complete is True
        assert outcome.jobs == []

    async def test_server_error_is_incomplete(self) -> None:
        client = FakeClient({"boards-api.greenhouse.io": (503, "")})
        outcome = await GreenhouseConnector().fetch(client, "datadog")
        assert outcome.complete is False

    async def test_unexpected_json_is_incomplete(self) -> None:
        """A 200 that is not the expected shape is not an empty board."""
        client = FakeClient({"boards-api.greenhouse.io": (200, '{"unexpected": true}')})
        outcome = await GreenhouseConnector().fetch(client, "datadog")
        assert outcome.complete is False


class TestLever:
    async def test_parses_a_real_board(self) -> None:
        client = FakeClient({"api.lever.co": (200, load_fixture("lever_spotify.json"))})
        outcome = await LeverConnector().fetch(client, "spotify")

        assert outcome.complete is True
        assert len(outcome.jobs) == 3
        job = outcome.jobs[0]
        assert job.title
        assert "jobs.lever.co" in job.source_url
        assert job.contract_type
        # `lists` holds the bulleted sections, which is where requirements live.
        assert job.description_html and len(job.description_html) > 200

    async def test_empty_array_is_a_live_board(self) -> None:
        """`plaid` answers `200 []`: the account exists and has no open roles."""
        client = FakeClient({"api.lever.co": (200, "[]")})
        outcome = await LeverConnector().fetch(client, "plaid")
        assert outcome.complete is True
        assert outcome.jobs == []
        assert (await LeverConnector().probe(client, "plaid")).value == "found"

    async def test_missing_board(self) -> None:
        client = FakeClient({"api.lever.co": (404, '{"ok":false,"error":"Document not found"}')})
        assert (await LeverConnector().probe(client, "nope")).value == "not_found"


class TestAshby:
    async def test_parses_a_real_board(self) -> None:
        client = FakeClient({"api.ashbyhq.com": (200, load_fixture("ashby_ramp.json"))})
        outcome = await AshbyConnector().fetch(client, "ramp")

        assert outcome.complete is True
        assert len(outcome.jobs) == 3
        job = outcome.jobs[0]
        assert job.description_text  # Ashby ships pre-rendered plain text
        assert job.contract_type
        assert job.work_mode_hint in {"remote", "hybrid", "onsite", None}

    async def test_plain_text_404_does_not_raise(self) -> None:
        """Ashby answers `404 text/plain`, so `.json()` would raise.

        The connector has to read the status before the body.
        """
        client = FakeClient({"api.ashbyhq.com": (404, "Not Found")})
        outcome = await AshbyConnector().fetch(client, "nope")
        assert outcome.complete is True
        assert outcome.jobs == []
        assert (await AshbyConnector().probe(client, "nope")).value == "not_found"


class TestRecruitee:
    async def test_parses_a_real_board(self) -> None:
        client = FakeClient({"recruitee.com": (200, load_fixture("recruitee_channable.json"))})
        outcome = await RecruiteeConnector().fetch(client, "channable")

        assert outcome.complete is True
        job = outcome.jobs[0]
        # The only provider that separates the two fields itself.
        assert job.description_html
        assert job.requirements_html
        assert job.city
        assert job.country_code == "NL"

    async def test_structured_salary_is_annualised(self) -> None:
        """Recruitee states the period, so no guessing is involved."""
        client = FakeClient({"recruitee.com": (200, load_fixture("recruitee_channable.json"))})
        outcome = await RecruiteeConnector().fetch(client, "channable")
        with_salary = [j for j in outcome.jobs if j.salary_min_hint]
        assert with_salary, "fixture should contain a salaried offer"
        job = with_salary[0]
        assert job.salary_currency_hint == "EUR"
        # Monthly figures in the low thousands become a plausible yearly total.
        assert 20_000 < job.salary_min_hint < 300_000

    async def test_timestamps_become_iso(self) -> None:
        """Recruitee writes `2026-04-21 15:51:44 UTC`."""
        client = FakeClient({"recruitee.com": (200, load_fixture("recruitee_channable.json"))})
        outcome = await RecruiteeConnector().fetch(client, "channable")
        assert "T" in outcome.jobs[0].posted_date
        assert outcome.jobs[0].posted_date.endswith("+00:00")


class TestWorkable:
    async def test_details_query_returns_bodies(self) -> None:
        client = FakeClient({"apply.workable.com": (200, load_fixture("workable_details.json"))})
        outcome = await WorkableConnector().fetch(client, "amazingcarecareers")

        assert outcome.complete is True
        assert len(outcome.jobs) == 3
        assert outcome.jobs[0].description_html
        assert client.calls[0].endswith("?details=true")

    async def test_iso_country_comes_from_locations(self) -> None:
        """Top-level `country` is a display name; `locations[].countryCode` is ISO."""
        client = FakeClient({"apply.workable.com": (200, load_fixture("workable_details.json"))})
        outcome = await WorkableConnector().fetch(client, "amazingcarecareers")
        assert outcome.jobs[0].country_code == "US"

    async def test_missing_account_is_a_clean_404(self) -> None:
        """Contrary to the brief, Workable does 404 an unknown account.

        The `200 {"jobs": []}` case means something else: a live account with no
        open roles. Workable is therefore disambiguable, unlike SmartRecruiters.
        """
        client = FakeClient({"apply.workable.com": (404, "Not Found")})
        assert (await WorkableConnector().probe(client, "nope")).value == "not_found"

    async def test_live_account_with_no_roles_is_found(self) -> None:
        client = FakeClient({"apply.workable.com": (200, '{"name":"Deliveroo","jobs":[]}')})
        assert (await WorkableConnector().probe(client, "deliveroo")).value == "found"


class TestSmartRecruiters:
    LIST = "postings?limit"
    DETAIL = "postings/"

    async def test_walks_the_list_and_fetches_details(self) -> None:
        detail = (
            '{"postingUrl":"https://jobs.smartrecruiters.com/x/1","jobAd":{"sections":'
            '{"jobDescription":{"text":"<p>Build things</p>"},'
            '"qualifications":{"text":"<p>5 years of Python</p>"}}}}'
        )
        client = FakeClient({
            self.DETAIL: (200, detail),
            self.LIST: (200, load_fixture("smartrecruiters_list.json")),
        })
        outcome = await SmartRecruitersConnector().fetch(client, "SmartRecruiters")

        assert outcome.complete is True
        assert len(outcome.jobs) == 3
        job = outcome.jobs[0]
        assert job.requirements_html == "<p>5 years of Python</p>"
        assert job.source_url == "https://jobs.smartrecruiters.com/x/1"

    async def test_already_detailed_postings_skip_their_detail_call(self) -> None:
        """Bosch has 4713 openings; re-reading every body each run is not viable."""
        client = FakeClient({self.LIST: (200, load_fixture("smartrecruiters_list.json"))})
        known = frozenset({"744000137613800", "744000137613801", "744000137613802"})
        raw = __import__("json").loads(load_fixture("smartrecruiters_list.json"))
        known = frozenset(str(e["id"]) for e in raw["content"])

        outcome = await SmartRecruitersConnector().fetch(
            client, "SmartRecruiters", already_detailed=known
        )
        assert outcome.complete is True
        assert all(self.DETAIL not in call or "limit" in call for call in client.calls)

    async def test_empty_list_can_never_prove_absence(self) -> None:
        """The trap: an unknown company and an idle one look identical."""
        client = FakeClient({self.LIST: (200, '{"offset":0,"limit":1,"totalFound":0,"content":[]}')})
        assert (await SmartRecruitersConnector().probe(client, "nope")).value == "ambiguous"

    async def test_short_read_is_incomplete(self) -> None:
        """`totalFound` says 50 but one page arrived: not a catalogue."""
        client = FakeClient({
            self.LIST: (200, '{"offset":0,"limit":100,"totalFound":50,"content":[]}')
        })
        outcome = await SmartRecruitersConnector().fetch(client, "acme")
        assert outcome.complete is False
        assert "50" in outcome.error


class TestWorkday:
    TARGET = WorkdayTarget("nvidia", "wd5", "NVIDIAExternalCareerSite")

    async def test_parses_list_and_detail(self) -> None:
        client = FakeClient({
            "/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs": (200, load_fixture("workday_list.json")),
            "/job/": (200, load_fixture("workday_detail.json")),
        })
        outcome = await WorkdayConnector().fetch(client, self.TARGET)

        assert outcome.complete is True
        assert outcome.slug == "nvidia"
        job = outcome.jobs[0]
        assert job.description_html
        # `postedOn` is relative text ("Posted Today"); `startDate` is the date.
        assert job.posted_date == "2026-08-02"
        assert job.source_url.startswith("https://nvidia.wd5.myworkdayjobs.com/")

    async def test_wrong_site_name_is_a_failure_not_an_empty_board(self) -> None:
        """422 means the tenant rejected the site name: a registry error."""
        client = FakeClient({"/wday/cxs/": (422, '{"errorCode":"HTTP_422"}')})
        outcome = await WorkdayConnector().fetch(client, self.TARGET)
        assert outcome.complete is False
        assert "422" in outcome.error

    async def test_unauthorised_site_is_a_failure(self) -> None:
        client = FakeClient({"/wday/cxs/": (401, '{"errorCode":"HTTP_401"}')})
        outcome = await WorkdayConnector().fetch(client, self.TARGET)
        assert outcome.complete is False
        assert outcome.jobs == []
