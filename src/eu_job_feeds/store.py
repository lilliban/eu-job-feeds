"""Aggregate stats written to git: `data/index.json`.

The postings themselves live in `state/jobs.sqlite` (see `sqlite_store.py`
and docs/DECISIONS.md #18), not in git. This module only writes the
lightweight per-company summary and dataset-wide counters that stay in
version control.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import utcnow_iso

DATA_DIR = Path("data")
INDEX_PATH = DATA_DIR / "index.json"


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
