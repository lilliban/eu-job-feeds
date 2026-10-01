"""HTML handling, the requirements splitter, and the content hash."""

from __future__ import annotations

from eu_job_feeds.text import (
    clean_text,
    content_hash,
    html_to_text,
    split_description_requirements,
)


class TestHtmlToText:
    def test_strips_tags_and_keeps_structure(self) -> None:
        out = html_to_text("<p>First</p><ul><li>One</li><li>Two</li></ul>")
        assert "First" in out
        assert "- One" in out
        assert "- Two" in out

    def test_greenhouse_double_escaping(self) -> None:
        """Greenhouse sends `&lt;p&gt;`, not `<p>`; one unescape restores it."""
        out = html_to_text("&lt;p&gt;We are hiring a &lt;strong&gt;Data Analyst&lt;/strong&gt;&lt;/p&gt;")
        assert out == "We are hiring a Data Analyst"
        assert "<" not in out

    def test_entities_are_resolved(self) -> None:
        assert html_to_text("<p>R&amp;D &mdash; Paris</p>") == "R&D — Paris"

    def test_script_content_is_dropped(self) -> None:
        out = html_to_text("<p>Real</p><script>var x = 'tracking';</script>")
        assert "tracking" not in out
        assert "Real" in out

    def test_empty_inputs(self) -> None:
        assert html_to_text(None) is None
        assert html_to_text("") is None
        assert html_to_text("   ") is None

    def test_malformed_html_does_not_raise(self) -> None:
        assert html_to_text("<p>unclosed <b>bold") is not None


class TestSplitDescriptionRequirements:
    def test_splits_at_an_english_heading(self) -> None:
        body = (
            "We are a growing company looking for someone to join the platform team.\n"
            "You will work on our data pipeline every day.\n"
            "Requirements\n"
            "- Python\n- SQL\n"
        )
        description, requirements = split_description_requirements(body)
        assert "growing company" in description
        assert requirements.startswith("Requirements")
        assert "Python" in requirements
        assert "Requirements" not in description

    def test_typographic_apostrophe_heading(self) -> None:
        """Real adverts use U+2019 more often than the ASCII apostrophe.

        Matching only `'` silently left `requirements_raw` null on 12 of Ashby's
        18 affected postings before this was fixed.
        """
        body = (
            "Ramp is building the smart infrastructure for finance teams everywhere.\n"
            "This role sits in the platform group.\n"
            "What we’re looking for\n"
            "- 5 years of experience\n"
        )
        _, requirements = split_description_requirements(body)
        assert requirements is not None
        assert "5 years" in requirements

    def test_german_heading(self) -> None:
        body = (
            "Wir sind ein wachsendes Unternehmen mit Sitz in München und suchen Verstärkung.\n"
            "Dein Profil\n"
            "- Mindestens 3 Jahre Berufserfahrung\n"
        )
        description, requirements = split_description_requirements(body)
        assert "Dein Profil" in requirements
        assert "München" in description

    def test_italian_heading(self) -> None:
        body = (
            "Siamo un'azienda in crescita con sede a Milano e cerchiamo nuovi colleghi.\n"
            "Requisiti\n"
            "- Almeno 2 anni di esperienza\n"
        )
        description, requirements = split_description_requirements(body)
        assert "Requisiti" in requirements
        assert "Milano" in description

    def test_no_heading_keeps_the_body_whole(self) -> None:
        """Inventing a split point would corrupt both halves."""
        body = "A short advert with no section headings at all."
        description, requirements = split_description_requirements(body)
        assert description == body
        assert requirements is None

    def test_heading_at_the_very_start(self) -> None:
        """No real description half: keep the body rather than emit an empty one."""
        body = "Requirements\n- Python\n- SQL\n"
        description, requirements = split_description_requirements(body)
        assert description == body
        assert requirements is not None

    def test_empty(self) -> None:
        assert split_description_requirements(None) == (None, None)
        assert split_description_requirements("") == (None, None)


class TestContentHash:
    def test_is_deterministic(self) -> None:
        a = content_hash("Data Analyst", "Acme", "Python and SQL")
        b = content_hash("Data Analyst", "Acme", "Python and SQL")
        assert a == b
        assert len(a) == 64

    def test_ignores_case_and_surrounding_space(self) -> None:
        assert content_hash("Data Analyst", "Acme", "Python") == content_hash(
            "  DATA ANALYST  ", " acme ", "python"
        )

    def test_ignores_whitespace_only_edits(self) -> None:
        """A provider reflowing its HTML must not mint a new posting."""
        assert content_hash("Analyst", "Acme", "Python   and\n\n\nSQL") == content_hash(
            "Analyst", "Acme", "Python and\nSQL"
        )

    def test_differs_on_title(self) -> None:
        assert content_hash("Analyst", "Acme", "x") != content_hash(
            "Senior Analyst", "Acme", "x"
        )

    def test_differs_on_company(self) -> None:
        assert content_hash("Analyst", "Acme", "x") != content_hash(
            "Analyst", "Other", "x"
        )

    def test_only_the_first_500_characters_count(self) -> None:
        base = "x" * 500
        assert content_hash("T", "C", base + "AAA") == content_hash("T", "C", base + "BBB")

    def test_missing_requirements(self) -> None:
        assert content_hash("Analyst", "Acme", None) == content_hash("Analyst", "Acme", "")


class TestCleanText:
    def test_collapses_runs_of_space(self) -> None:
        assert clean_text("a    b\t\tc") == "a b c"

    def test_caps_blank_lines(self) -> None:
        assert clean_text("a\n\n\n\n\nb") == "a\n\nb"

    def test_empty(self) -> None:
        assert clean_text(None) is None
        assert clean_text("   ") is None
