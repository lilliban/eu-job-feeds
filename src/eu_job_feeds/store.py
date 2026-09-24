"""Reading and writing the published dataset.

One file per company, `data/{provider}/{slug}.json`. Not one big file: a single
document rewritten every few hours makes each commit a complete copy of the
whole dataset, and a year of that is a repository nobody wants to clone. With a
file per company and a stable ordering, a run touches only what actually
changed, and the diff is something a person can read.

"Stable ordering" is doing real work here. Postings are sorted by a fixed key
and every field is written in a fixed order, so a posting that did not change
serialises to identical bytes and does not appear in the diff at all.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import JobPosting, utcnow_iso

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
INDEX_PATH = DATA_DIR / "index.json"


def company_path(provider: str, slug: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / provider / f"{_safe_stem(slug)}.json"


def company_relpath(provider: str, slug: str) -> str:
    """Path recorded in `index.json`, relative to `data/`.

    Shares `_safe_stem` with `company_path` so the index can never point at a
    filename that was not the one written.
    """
    return f"{provider}/{_safe_stem(slug)}.json"


def _safe_stem(slug: str) -> str:
    """Make a slug safe as a filename without losing case.

    Case matters — SmartRecruiters' `BoschGroup` is not `boschgroup` — so this
    only replaces characters that a path cannot hold.
    """
    out = "".join(c if c.isalnum() or c in "-_." else "-" for c in slug)
    return out.strip("-.") or "unnamed"


def _sort_key(posting: JobPosting) -> tuple:
    """Deterministic order, independent of the order the provider returned.

    `content_hash` alone is not unique — the same role in three cities shares
    one — so the source URL breaks ties and keeps the order stable across runs.
    """
    return (posting.content_hash, posting.source_url)


def load_company(provider: str, slug: str, data_dir: Path = DATA_DIR) -> list[JobPosting]:
    """Read a company's stored postings. A missing or broken file reads as empty."""
    path = company_path(provider, slug, data_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("%s is unreadable (%s); treating as empty", path, exc)
        return []

    entries = raw.get("jobs") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return []

    postings: list[JobPosting] = []
    for entry in entries:
        try:
            postings.append(JobPosting.model_validate(entry))
        except Exception as exc:
            log.warning("%s: dropping unreadable posting (%s)", path, exc)
    return postings


def write_company(
    provider: str,
    slug: str,
    company_name: str,
    postings: list[JobPosting],
    *,
    data_dir: Path = DATA_DIR,
    updated_at: str | None = None,
) -> bool:
    """Write a company's file. Returns True when the bytes on disk changed.

    The caller uses the return value to decide whether anything is worth
    committing, so this must not rewrite a file whose content is unchanged.
    """
    path = company_path(provider, slug, data_dir)
    ordered = sorted(postings, key=_sort_key)

    document = {
        "company_name": company_name,
        "source_board": provider,
        "slug": slug,
        "job_count": len(ordered),
        "open_job_count": sum(1 for p in ordered if not p.is_closed),
        "updated_at": updated_at or utcnow_iso(),
        "jobs": [p.to_ordered_dict() for p in ordered],
    }
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"

    if path.exists():
        current = path.read_text(encoding="utf-8")
        # `updated_at` changes on every run by definition, so comparing the whole
        # document would report a change every time. The comparison ignores it.
        if _without_timestamp(current) == _without_timestamp(payload):
            return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return True


def _without_timestamp(document: str) -> str:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        return document
    if isinstance(parsed, dict):
        parsed.pop("updated_at", None)
    return json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)


def write_index(entries: list[dict], *, data_dir: Path = DATA_DIR) -> bool:
    """Write `data/index.json`: the catalogue of catalogues."""
    path = data_dir / "index.json"
    ordered = sorted(entries, key=lambda e: (e.get("source_board", ""), e.get("slug", "").lower()))
    document = {
        "generated_at": utcnow_iso(),
        "company_count": len(ordered),
        "job_count": sum(e.get("job_count", 0) for e in ordered),
        "open_job_count": sum(e.get("open_job_count", 0) for e in ordered),
        "companies": ordered,
    }
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"

    if path.exists():
        current = path.read_text(encoding="utf-8")
        if _without_generated_at(current) == _without_generated_at(payload):
            return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return True


def _without_generated_at(document: str) -> str:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        return document
    if isinstance(parsed, dict):
        parsed.pop("generated_at", None)
        for company in parsed.get("companies", []):
            if isinstance(company, dict):
                company.pop("updated_at", None)
    return json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)
