"""URL construction for every provider.

A wrong URL does not raise: it 404s, and a 404 is how most of these connectors
say "this company has no board". A typo in a base URL would therefore look
exactly like every company having quietly disappeared.
"""

from __future__ import annotations

import pytest

from eu_job_feeds.connectors import (
    AshbyConnector,
    GreenhouseConnector,
    LeverConnector,
    PersonioConnector,
    RecruiteeConnector,
    SmartRecruitersConnector,
    WorkableConnector,
    WorkdayTarget,
)


class TestListUrls:
    """Each asserted URL was fetched successfully against the live API."""

    def test_greenhouse(self) -> None:
        assert GreenhouseConnector().list_url("datadog") == (
            "https://boards-api.greenhouse.io/v1/boards/datadog/jobs?content=true"
        )

    def test_lever(self) -> None:
        assert LeverConnector().list_url("spotify") == (
            "https://api.lever.co/v0/postings/spotify?mode=json"
        )

    def test_ashby(self) -> None:
        assert AshbyConnector().list_url("ramp") == (
            "https://api.ashbyhq.com/posting-api/job-board/ramp"
        )

    def test_recruitee_puts_the_slug_in_the_subdomain(self) -> None:
        assert RecruiteeConnector().list_url("channable") == (
            "https://channable.recruitee.com/api/offers/"
        )

    def test_workable_requests_details(self) -> None:
        """Without `?details=true` the response carries no advert text at all."""
        url = WorkableConnector().list_url("amazingcarecareers")
        assert url == "https://apply.workable.com/api/v1/widget/accounts/amazingcarecareers?details=true"
        assert "details=true" in url

    def test_personio_puts_the_slug_in_the_subdomain(self) -> None:
        assert PersonioConnector().list_url("personio") == (
            "https://personio.jobs.personio.de/xml"
        )


class TestSmartRecruitersUrls:
    def test_first_page(self) -> None:
        assert SmartRecruitersConnector().list_url("SmartRecruiters") == (
            "https://api.smartrecruiters.com/v1/companies/SmartRecruiters/postings?limit=100&offset=0"
        )

    def test_offset_is_carried(self) -> None:
        assert "offset=100" in SmartRecruitersConnector().list_url("BoschGroup", 100)

    def test_slug_case_is_preserved(self) -> None:
        """`bosch` returns 0 postings, `BoschGroup` returns 4713.

        The identifier is case-sensitive and the API reports the difference as an
        empty list, not an error — lower-casing a slug loses a whole company in
        silence.
        """
        url = SmartRecruitersConnector().list_url("BoschGroup")
        assert "BoschGroup" in url
        assert "boschgroup" not in url

    def test_detail_url(self) -> None:
        assert SmartRecruitersConnector().detail_url("SmartRecruiters", "744000137613800") == (
            "https://api.smartrecruiters.com/v1/companies/SmartRecruiters/postings/744000137613800"
        )


class TestPersonioJobUrl:
    def test_job_url(self) -> None:
        assert PersonioConnector().job_url("personio", "1834171") == (
            "https://personio.jobs.personio.de/job/1834171"
        )


class TestWorkdayTarget:
    """Workday's identity is three parts, none derivable from the others."""

    def test_list_url(self) -> None:
        target = WorkdayTarget("nvidia", "wd5", "NVIDIAExternalCareerSite")
        assert target.list_url() == (
            "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
        )

    def test_tenant_appears_twice(self) -> None:
        """Once in the hostname and once in the path — dropping either 404s."""
        url = WorkdayTarget("nvidia", "wd5", "SiteName").list_url()
        assert url.count("nvidia") == 2

    def test_detail_url_joins_the_external_path(self) -> None:
        target = WorkdayTarget("nvidia", "wd5", "NVIDIAExternalCareerSite")
        assert target.detail_url("/job/Israel-Yokneam/Engineer_JR2021705") == (
            "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/"
            "NVIDIAExternalCareerSite/job/Israel-Yokneam/Engineer_JR2021705"
        )

    def test_public_url_omits_the_cxs_prefix(self) -> None:
        """The applicant-facing URL is not the API URL."""
        target = WorkdayTarget("nvidia", "wd5", "NVIDIAExternalCareerSite")
        public = target.public_url("/job/Israel-Yokneam/Engineer_JR2021705")
        assert public == (
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
            "/job/Israel-Yokneam/Engineer_JR2021705"
        )
        assert "/wday/cxs/" not in public

    def test_datacentre_must_be_well_formed(self) -> None:
        WorkdayTarget("nvidia", "wd1", "Site")
        WorkdayTarget("nvidia", "wd12", "Site")
        with pytest.raises(ValueError):
            WorkdayTarget("nvidia", "5", "Site")
        with pytest.raises(ValueError):
            WorkdayTarget("nvidia", "", "Site")

    def test_slug_is_the_tenant(self) -> None:
        assert WorkdayTarget("nvidia", "wd5", "Site").slug == "nvidia"


class TestSlugsThatNeedCare:
    def test_hyphenated_slug(self) -> None:
        assert "my-company" in GreenhouseConnector().list_url("my-company")

    def test_numeric_slug(self) -> None:
        assert "1password" in LeverConnector().list_url("1password")
