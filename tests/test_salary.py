"""Salary extraction.

Half of these assert that nothing is extracted. A wrong salary is worse than a
missing one — a consumer filtering on `salary_min >= 40000` silently drops the
right jobs and keeps the wrong ones, with no error anywhere.
"""

from __future__ import annotations

import pytest

from eu_job_feeds.normalize.salary import extract_salary, parse_amount


class TestParseAmount:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # European: dot groups thousands.
            ("35.000", 35_000),
            ("1.234.567", 1_234_567),
            ("45.000,50", 45_000.50),
            # Anglo-Saxon: comma groups thousands.
            ("35,000", 35_000),
            ("1,234,567", 1_234_567),
            ("45,000.50", 45_000.50),
            # Decimal comma with only two digits after it is not a thousands group.
            ("35,5", 35.5),
            ("35.5", 35.5),
            ("1500", 1500),
        ],
    )
    def test_separators(self, raw: str, expected: float) -> None:
        assert parse_amount(raw) == pytest.approx(expected)

    def test_k_suffix(self) -> None:
        assert parse_amount("45", k_suffix=True) == 45_000

    def test_space_grouped_thousands(self) -> None:
        assert parse_amount("35 000") == 35_000
        assert parse_amount("1 234 567") == 1_234_567


class TestEuropeanNotation:
    def test_ral_with_trailing_currency(self) -> None:
        """The notation named in the brief: currency last, dot for thousands."""
        assert extract_salary("RAL 35.000 - 45.000 EUR") == (35_000, 45_000, "EUR")

    def test_ral_without_space_before_currency(self) -> None:
        assert extract_salary("RAL 30.000-40.000EUR") == (30_000, 40_000, "EUR")

    def test_german_gehalt(self) -> None:
        assert extract_salary("Gehalt: 55.000 - 70.000 EUR pro Jahr") == (
            55_000, 70_000, "EUR",
        )

    def test_monthly_is_annualised(self) -> None:
        """`2.850 - 2.950 EUR al mese` is 34.200 - 35.400 a year."""
        assert extract_salary("Stipendio 2.850 - 2.950 EUR al mese") == (
            34_200, 35_400, "EUR",
        )


class TestAngloNotation:
    def test_symbol_prefix_range(self) -> None:
        assert extract_salary("Salary: €45,000 - €60,000") == (45_000, 60_000, "EUR")

    def test_k_shorthand(self) -> None:
        assert extract_salary("Compensation: $120k - $160k") == (120_000, 160_000, "USD")

    def test_up_to_gives_only_a_maximum(self) -> None:
        assert extract_salary("Salary up to £80,000") == (None, 80_000, "GBP")

    def test_from_gives_only_a_minimum(self) -> None:
        assert extract_salary("Salary from £50,000") == (50_000, None, "GBP")

    def test_single_value_fills_both_ends(self) -> None:
        assert extract_salary("The salary for this role is CHF 120,000") == (
            120_000, 120_000, "CHF",
        )


class TestRefusals:
    """Cases that must produce nothing."""

    def test_counting_customers_is_not_a_salary(self) -> None:
        """The example from the brief."""
        assert extract_salary("Serviamo tra 500 e 1000 clienti in Europa") == (
            None, None, None,
        )

    def test_large_counts_are_not_salaries(self) -> None:
        # Passes the magnitude test, fails on the noun that follows.
        assert extract_salary("Con oltre 50.000 utenti attivi") == (None, None, None)
        assert extract_salary("We serve 250,000 customers worldwide") == (
            None, None, None,
        )

    def test_no_currency_means_no_salary(self) -> None:
        """Numbers alone are unusable: 35.000 of what?"""
        assert extract_salary("RAL 35.000 - 45.000") == (None, None, None)

    def test_unsupported_currency_is_dropped_entirely(self) -> None:
        """SEK is real but outside the contract's four values.

        Reporting the numbers under a null or invented currency would be worse
        than reporting nothing.
        """
        assert extract_salary("Lön: 500 000 - 650 000 SEK per år") == (None, None, None)
        assert extract_salary("Wynagrodzenie 120000 - 180000 PLN") == (None, None, None)

    def test_hourly_rates_are_refused(self) -> None:
        """Annualising needs contracted hours, which the contract cannot record."""
        assert extract_salary("Rate: €45 per hour") == (None, None, None)
        assert extract_salary("Compenso 25 EUR all'ora") == (None, None, None)

    def test_daily_rates_are_refused(self) -> None:
        assert extract_salary("Day rate: £500 per day") == (None, None, None)

    def test_implausibly_small_amounts(self) -> None:
        assert extract_salary("A signing bonus of €500") == (None, None, None)

    def test_years_are_not_salaries(self) -> None:
        assert extract_salary("Founded in 2015, we have grown fast") == (
            None, None, None,
        )

    def test_empty_and_none(self) -> None:
        assert extract_salary(None) == (None, None, None)
        assert extract_salary("") == (None, None, None)


class TestOrdering:
    def test_reversed_range_is_normalised(self) -> None:
        assert extract_salary("Salary €60,000 - €45,000") == (45_000, 60_000, "EUR")
