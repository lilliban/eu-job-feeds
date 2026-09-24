"""Years-of-experience extraction, including the German and Italian phrasings."""

from __future__ import annotations

import pytest

from eu_job_feeds.normalize.experience import extract_years, parse_structured_range


class TestEnglish:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("3-5 years of experience in data engineering", (3, 5)),
            ("3 to 5 years of professional experience", (3, 5)),
            ("5+ years of experience", (5, None)),
            ("At least 4 years of relevant experience", (4, None)),
            ("Minimum of 7 years experience required", (7, None)),
            ("2 years of experience with Python", (2, None)),
        ],
    )
    def test_patterns(self, text: str, expected: tuple) -> None:
        assert extract_years(text) == expected

    def test_up_to_gives_a_maximum(self) -> None:
        assert extract_years("Up to 3 years of experience — this is a junior role") == (
            None, 3,
        )


class TestGerman:
    def test_mindestens(self) -> None:
        """The phrasing named in the brief."""
        assert extract_years("mindestens 3 Jahre Berufserfahrung") == (3, None)

    def test_abbreviated_mindestens(self) -> None:
        assert extract_years("Mind. 5 Jahre Erfahrung im Vertrieb") == (5, None)

    def test_range(self) -> None:
        assert extract_years("3 bis 5 Jahre Berufserfahrung") == (3, 5)


class TestItalian:
    def test_almeno(self) -> None:
        """The phrasing named in the brief."""
        assert extract_years("almeno 2 anni di esperienza") == (2, None)

    def test_plain(self) -> None:
        assert extract_years("5 anni di esperienza nel ruolo") == (5, None)

    def test_range(self) -> None:
        assert extract_years("3-5 anni di esperienza lavorativa") == (3, 5)


class TestOtherLanguages:
    def test_french(self) -> None:
        assert extract_years("au moins 3 ans d'expérience professionnelle") == (3, None)

    def test_spanish(self) -> None:
        assert extract_years("al menos 4 años de experiencia") == (4, None)

    def test_dutch(self) -> None:
        assert extract_years("minimaal 3 jaar werkervaring") == (3, None)


class TestRefusals:
    """Numbers of years that are not a requirement."""

    def test_company_age_is_not_a_requirement(self) -> None:
        assert extract_years("Founded 3 years ago, we now serve Europe") == (None, None)

    def test_past_performance_is_not_a_requirement(self) -> None:
        assert extract_years(
            "For the past 5 years we have doubled revenue annually"
        ) == (None, None)

    def test_years_without_an_experience_cue(self) -> None:
        """`a 5 year plan` is about the company, not the candidate."""
        assert extract_years("We are executing a 5 year plan") == (None, None)

    def test_implausible_values(self) -> None:
        assert extract_years("99 years of experience") == (None, None)
        assert extract_years("0 years of experience") == (None, None)

    def test_empty(self) -> None:
        assert extract_years(None) == (None, None)
        assert extract_years("") == (None, None)


class TestStructuredRange:
    """Personio ships `yearsOfExperience` directly; structured beats regex."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("7-10", (7, 10)),
            ("1-3", (1, 3)),
            ("5+", (5, None)),
            ("4", (4, None)),
            ("lt-1", (None, 1)),
            ("", (None, None)),
            (None, (None, None)),
            ("not specified", (None, None)),
            ("nonsense", (None, None)),
        ],
    )
    def test_parse(self, raw: str | None, expected: tuple) -> None:
        assert parse_structured_range(raw) == expected

    def test_reversed_range_is_normalised(self) -> None:
        assert parse_structured_range("10-7") == (7, 10)
