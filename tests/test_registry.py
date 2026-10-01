"""The registry: where the canonical company name lives (trap 2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from eu_job_feeds.registry import CompanyEntry, Registry, load_registry, save_registry


class TestCompanyEntry:
    def test_slug_providers_need_a_slug(self) -> None:
        with pytest.raises(ValidationError):
            CompanyEntry(name="Acme", provider="greenhouse")

    def test_workday_needs_its_three_parts(self) -> None:
        with pytest.raises(ValidationError):
            CompanyEntry(name="NVIDIA", provider="workday")

    def test_workday_key_is_the_tenant(self) -> None:
        entry = CompanyEntry(
            name="NVIDIA",
            provider="workday",
            workday={"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite"},
        )
        assert entry.key == "nvidia"
        assert entry.workday.to_target().wd == "wd5"

    def test_blank_names_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CompanyEntry(name="   ", provider="greenhouse", slug="acme")

    def test_slug_case_is_preserved(self) -> None:
        """`bosch` and `BoschGroup` are different companies to SmartRecruiters."""
        entry = CompanyEntry(name="Bosch Group", provider="smartrecruiters", slug="BoschGroup")
        assert entry.slug == "BoschGroup"
        assert entry.key == "BoschGroup"


class TestNameResolution:
    def test_the_registry_name_wins(self) -> None:
        """The whole point of trap 2: `datadog` must publish as `Datadog`."""
        registry = Registry(
            companies=[CompanyEntry(name="Datadog", provider="greenhouse", slug="datadog")]
        )
        assert registry.name_for("greenhouse", "datadog") == "Datadog"

    def test_unknown_board_falls_back_to_what_the_provider_said(self) -> None:
        registry = Registry()
        assert registry.name_for("greenhouse", "acme", fallback="Acme Corp") == "Acme Corp"

    def test_without_a_fallback_the_slug_is_returned_unchanged(self) -> None:
        """Deliberately not title-cased: `BoschGroup` must not become `Boschgroup`."""
        registry = Registry()
        assert registry.name_for("smartrecruiters", "BoschGroup") == "BoschGroup"


class TestUpsert:
    def test_a_new_entry_is_added(self) -> None:
        registry = Registry()
        assert registry.upsert(
            CompanyEntry(name="Acme", provider="greenhouse", slug="acme", source="discovered")
        ) is True
        assert len(registry.companies) == 1

    def test_a_curated_entry_is_never_overwritten(self) -> None:
        """A person chose that name; discovery does not get to change it."""
        registry = Registry(
            companies=[
                CompanyEntry(name="Bosch Group", provider="smartrecruiters", slug="BoschGroup")
            ]
        )
        registry.upsert(
            CompanyEntry(
                name="boschgroup", provider="smartrecruiters", slug="BoschGroup",
                source="discovered",
            )
        )
        assert registry.companies[0].name == "Bosch Group"

    def test_the_same_slug_on_two_providers_is_two_entries(self) -> None:
        registry = Registry()
        registry.upsert(CompanyEntry(name="Acme", provider="greenhouse", slug="acme"))
        registry.upsert(CompanyEntry(name="Acme", provider="lever", slug="acme"))
        assert len(registry.companies) == 2


class TestPersistence:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "companies.yaml"
        registry = Registry(
            companies=[
                CompanyEntry(name="Datadog", provider="greenhouse", slug="datadog"),
                CompanyEntry(
                    name="NVIDIA", provider="workday",
                    workday={"tenant": "nvidia", "wd": "wd5", "site": "Site"},
                ),
            ]
        )
        save_registry(registry, path)
        restored = load_registry(path)

        assert {c.name for c in restored.companies} == {"Datadog", "NVIDIA"}
        assert restored.find("workday", "nvidia").workday.site == "Site"

    def test_saving_is_sorted_and_stable(self, tmp_path: Path) -> None:
        path = tmp_path / "companies.yaml"
        registry = Registry(
            companies=[
                CompanyEntry(name="Zeta", provider="lever", slug="zeta"),
                CompanyEntry(name="Alpha", provider="greenhouse", slug="alpha"),
            ]
        )
        save_registry(registry, path)
        first = path.read_text(encoding="utf-8")
        save_registry(load_registry(path), path)
        assert path.read_text(encoding="utf-8") == first

    def test_missing_file_is_an_empty_registry(self, tmp_path: Path) -> None:
        assert load_registry(tmp_path / "nothing.yaml").companies == []

    def test_the_shipped_registry_is_valid(self) -> None:
        """Every entry in the repository's own registry must load."""
        path = Path(__file__).parent.parent / "registry" / "companies.yaml"
        if not path.exists():
            pytest.skip("no registry checked in")
        registry = load_registry(path)
        assert registry.companies
        for entry in registry.companies:
            assert entry.key, f"{entry.name} has no usable key"
            assert entry.name.strip() == entry.name

    def test_the_shipped_registry_names_the_awkward_slugs_properly(self) -> None:
        """The cases where capitalising the slug gives the wrong answer.

        For a one-word company `ramp` -> `Ramp` is both derived and correct, so
        a blanket "name must differ from the slug" rule proves nothing. These
        two are the entries where a derived name would actually be wrong.
        """
        path = Path(__file__).parent.parent / "registry" / "companies.yaml"
        if not path.exists():
            pytest.skip("no registry checked in")
        registry = load_registry(path)

        bosch = registry.find("smartrecruiters", "BoschGroup")
        if bosch:
            assert bosch.name == "Bosch Group"  # not "Boschgroup"

        workable = registry.find("workable", "amazingcarecareers")
        if workable:
            assert workable.name != "Amazingcarecareers"
            assert " " in workable.name
