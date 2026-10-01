"""Location, contract type, work mode, languages, and the assembled posting."""

from __future__ import annotations

import pytest

from eu_job_feeds.models import RawJob
from eu_job_feeds.normalize import build_posting
from eu_job_feeds.normalize.classify import (
    infer_seniority,
    infer_work_mode,
    normalize_contract_type,
    normalize_work_mode,
)
from eu_job_feeds.normalize.languages import extract_languages
from eu_job_feeds.normalize.location import normalize_country_code, split_location


class TestCountryCode:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Italy", "IT"), ("Italia", "IT"), ("Deutschland", "DE"),
            ("Netherlands", "NL"), ("Schweiz", "CH"), ("España", "ES"),
            # Providers disagree on format: SmartRecruiters sends `pl`,
            # Ashby sends `USA`, Recruitee sends `NL`, Workable "United States".
            ("pl", "PL"), ("USA", "US"), ("NL", "NL"), ("United States", "US"),
            # UK is not an ISO code; GB is.
            ("UK", "GB"), ("United Kingdom", "GB"),
        ],
    )
    def test_known_countries(self, raw: str, expected: str) -> None:
        assert normalize_country_code(raw) == expected

    def test_unknown_returns_none(self) -> None:
        assert normalize_country_code("Atlantis") is None
        assert normalize_country_code("") is None
        assert normalize_country_code(None) is None


class TestSplitLocation:
    @pytest.mark.parametrize(
        "raw,city,country",
        [
            ("Milan, Italy", "Milan", "IT"),
            ("Berlin, Germany", "Berlin", "DE"),
            ("Utrecht, Utrecht, Netherlands", "Utrecht", "NL"),
            ("Paris, France", "Paris", "FR"),
            ("New York, New York, USA", "New York", "US"),
        ],
    )
    def test_city_and_country(self, raw: str, city: str, country: str) -> None:
        assert split_location(raw) == (city, country)

    def test_country_is_inferred_from_a_known_city(self) -> None:
        assert split_location("München") == ("München", "DE")
        assert split_location("Zurich") == ("Zurich", "CH")

    def test_a_country_alone_is_not_a_city(self) -> None:
        assert split_location("Germany") == (None, "DE")

    def test_non_places_yield_nothing(self) -> None:
        for value in ("Remote", "Anywhere", "EMEA", "Multiple Locations", "-"):
            assert split_location(value) == (None, None), value

    def test_empty(self) -> None:
        assert split_location(None) == (None, None)
        assert split_location("") == (None, None)


class TestContractType:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # The exact spellings the providers send.
            ("Full time", "full_time"),      # Greenhouse metadata
            ("FullTime", "full_time"),       # Ashby
            ("permanent", "full_time"),      # Lever / Personio
            ("fulltime_permanent", "full_time"),  # Recruitee
            ("Full-time", "full_time"),      # SmartRecruiters / Workable
            ("Part-time", "part_time"),
            ("Teilzeit", "part_time"),
            ("Contract", "contract"),
            ("Freelance", "contract"),
            ("befristet", "contract"),
            ("Internship", "internship"),
            ("Praktikum", "internship"),
            ("Werkstudent", "internship"),
            ("Tirocinio", "internship"),
        ],
    )
    def test_mapping(self, raw: str, expected: str) -> None:
        assert normalize_contract_type(raw) == expected

    def test_unknown_is_none_not_a_guess(self) -> None:
        assert normalize_contract_type("Whatever") is None
        assert normalize_contract_type(None) is None


class TestWorkMode:
    def test_provider_vocabularies(self) -> None:
        assert normalize_work_mode("Remote") == "remote"
        assert normalize_work_mode("Hybrid") == "hybrid"
        assert normalize_work_mode("Onsite") == "onsite"

    def test_unspecified_is_none(self) -> None:
        assert normalize_work_mode("Unspecified") is None
        assert normalize_work_mode(None) is None

    def test_hybrid_wins_over_remote_in_free_text(self) -> None:
        """`hybrid remote` and `remote/hybrid` are both common; hybrid is specific."""
        assert infer_work_mode("This is a hybrid remote role") == "hybrid"

    def test_inference_from_text(self) -> None:
        assert infer_work_mode("Fully remote position") == "remote"
        assert infer_work_mode("Lavoro in sede a Milano") == "onsite"
        assert infer_work_mode("A normal job description") is None


class TestSeniority:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Senior Data Engineer", "Senior"),
            ("Junior Developer", "Junior"),
            ("Staff Software Engineer", "Principal"),
            ("Engineering Manager", "Lead"),
            ("Head of Marketing", "Executive"),
            ("Praktikant Marketing", "Internship"),
        ],
    )
    def test_from_title(self, title: str, expected: str) -> None:
        assert infer_seniority(title) == expected

    def test_plain_title_has_no_level(self) -> None:
        assert infer_seniority("Data Engineer") is None


class TestLanguages:
    def test_proficiency_phrases(self) -> None:
        assert extract_languages("Fluent English and Italian required") == [
            "english", "italian",
        ]

    def test_german_compounds(self) -> None:
        """`Deutschkenntnisse` can only mean a language skill."""
        assert "german" in extract_languages("Sehr gute Deutschkenntnisse erforderlich")

    def test_italian_phrasing(self) -> None:
        assert "english" in extract_languages("Ottima conoscenza della lingua inglese")

    def test_a_languages_section(self) -> None:
        result = extract_languages("Languages: English, French")
        assert "english" in result and "french" in result

    def test_market_mentions_are_not_requirements(self) -> None:
        """`the German market` says nothing about the candidate."""
        assert extract_languages("You will grow the German market for us") == []

    def test_bare_mentions_are_not_requirements(self) -> None:
        assert extract_languages("Our documentation is in English") == []

    def test_output_is_sorted_and_deduplicated(self) -> None:
        result = extract_languages("Fluent English. Written English required.")
        assert result == ["english"]

    def test_empty(self) -> None:
        assert extract_languages(None) == []
        assert extract_languages("") == []


class TestBuildPosting:
    def base_raw(self, **kwargs: object) -> RawJob:
        defaults = dict(
            provider="greenhouse",
            external_id="1",
            title="Senior Data Analyst",
            source_url="https://example.com/jobs/1",
        )
        defaults.update(kwargs)
        return RawJob(**defaults)

    def test_company_name_comes_from_the_caller_not_the_slug(self) -> None:
        """Trap 2: the registry decides the name, never the connector."""
        posting = build_posting(self.base_raw(), company_name="Datadog")
        assert posting.company_name == "Datadog"

    def test_undivided_body_is_split(self) -> None:
        raw = self.base_raw(
            description_text=(
                "We are a growing company and we would like you to join the team here.\n"
                "Requirements\n- 5 years of experience with SQL\n"
            )
        )
        posting = build_posting(raw, company_name="Acme")
        assert posting.requirements_raw is not None
        assert "SQL" in posting.requirements_raw
        assert "Requirements" not in posting.description

    def test_structured_hints_beat_regex(self) -> None:
        """Provider JSON is tier one; regex over text is only the fallback."""
        raw = self.base_raw(
            work_mode_hint="hybrid",
            description_text="This is a fully remote position",
            min_years_exp_hint=7,
            max_years_exp_hint=10,
        )
        posting = build_posting(raw, company_name="Acme")
        assert posting.work_mode == "hybrid"
        assert (posting.min_years_exp, posting.max_years_exp) == (7, 10)

    def test_salary_without_a_currency_is_dropped(self) -> None:
        raw = self.base_raw(salary_min_hint=50_000, salary_currency_hint=None)
        posting = build_posting(raw, company_name="Acme")
        assert posting.salary_min is None
        assert posting.salary_currency is None

    def test_lifecycle_fields_start_clean(self) -> None:
        posting = build_posting(self.base_raw(), company_name="Acme")
        assert posting.consecutive_misses == 0
        assert posting.is_closed is False
        assert posting.first_seen_at == posting.last_seen_at

    def test_every_contract_field_is_present(self) -> None:
        """Renaming or dropping a field would break the consumer silently."""
        posting = build_posting(self.base_raw(), company_name="Acme")
        produced = posting.to_ordered_dict()
        required = {
            "content_hash", "title", "company_name", "source_url", "source_board",
            "source_kind", "description", "requirements_raw", "location",
            "contract_type", "contract_type_norm", "work_mode", "seniority",
            "department", "min_years_exp", "max_years_exp", "languages", "city",
            "country_code", "salary_min", "salary_max", "salary_currency",
            "posted_date", "first_seen_at", "last_seen_at", "consecutive_misses",
            "is_closed",
        }
        assert required <= set(produced)

    def test_source_kind_is_always_ats(self) -> None:
        assert build_posting(self.base_raw(), company_name="Acme").source_kind == "ats"
