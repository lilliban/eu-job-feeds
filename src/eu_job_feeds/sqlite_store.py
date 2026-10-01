"""SQLite state store: the durable postings archive, with full-text search.

Replaces one-JSON-file-per-company as the thing runs read and write between
each other — see docs/DECISIONS.md #18. The whole `postings` table is rebuilt
from scratch every run, the same reasoning as the old `store.write_company`
always rewriting its whole file: reconciling row-level diffs against postings
that can close and reopen is exactly the class of bug this project avoids by
staying simple.

The primary key is `(source_board, slug, row_key)`, not just `(source_board,
row_key)` the way `lifecycle.identity` keys a single company's postings.
While every company had its own file, the file boundary itself prevented two
companies on the same board from colliding on `external_id`. In one shared
table that boundary has to be explicit, or two Greenhouse orgs with
numerically-overlapping ids would silently overwrite each other's rows.
"""

from __future__ import annotations

import gzip
import json
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import FIELD_ORDER, JobPosting

log = logging.getLogger(__name__)

DB_PATH = Path("state/jobs.sqlite")

#: Nullable integer columns.
_INTEGER_FIELDS = {"min_years_exp", "max_years_exp", "salary_min", "salary_max"}
#: Integer columns that are never null (`is_closed` stored as 0/1).
_ZERO_DEFAULT_INT_FIELDS = {"consecutive_misses", "is_closed"}
#: Stored as a JSON-encoded string; SQLite has no array type.
_JSON_FIELDS = {"languages"}
#: Mirrors the fields `JobPosting` never leaves blank.
_NOT_NULL_FIELDS = {
    "content_hash", "title", "company_name", "source_url", "source_board",
    "source_kind", "external_id", "first_seen_at", "last_seen_at",
}

_FTS_COLUMNS = (
    "title", "company_name", "description", "requirements_raw", "location", "department", "city",
)


def _column_def(field_name: str) -> str:
    if field_name in _ZERO_DEFAULT_INT_FIELDS:
        return f"{field_name} INTEGER NOT NULL DEFAULT 0"
    if field_name in _INTEGER_FIELDS:
        return f"{field_name} INTEGER"
    sql_type = "TEXT NOT NULL" if field_name in _NOT_NULL_FIELDS else "TEXT"
    return f"{field_name} {sql_type}"


_SCHEMA = "\n".join(
    [
        "CREATE TABLE postings (",
        "    slug TEXT NOT NULL,",
        "    row_key TEXT NOT NULL,",
        *(f"    {_column_def(f)}," for f in FIELD_ORDER),
        "    PRIMARY KEY (source_board, slug, row_key)",
        ");",
        "CREATE INDEX idx_postings_board_slug ON postings(source_board, slug);",
        "CREATE VIRTUAL TABLE postings_fts USING fts5(",
        f"    {', '.join(_FTS_COLUMNS)},",
        "    content='postings', content_rowid='rowid'",
        ");",
    ]
)

_COLUMNS = ("slug", "row_key", *FIELD_ORDER)
#: `OR REPLACE`, not a plain `INSERT`: a connector can genuinely return the
#: same `external_id` twice within one fetch (observed on SmartRecruiters and
#: Workable — a pagination overlap or a company posting an identical advert
#: twice). The old JSON array tolerated that silently; the unique key here
#: would otherwise abort the whole run over one company's dirty data. The
#: last occurrence wins, so the job is counted once rather than not at all.
_INSERT_SQL = (
    f"INSERT OR REPLACE INTO postings ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)
_SELECT_SQL = f"SELECT slug, {', '.join(FIELD_ORDER)} FROM postings"


@dataclass
class CompanyPostings:
    provider: str
    slug: str
    postings: list[JobPosting]


def _row_key(posting: JobPosting) -> str:
    """Mirrors `lifecycle.stored_identity`'s fallback: id, or the URL when blank."""
    return posting.external_id or posting.source_url


def _to_row(slug: str, posting: JobPosting) -> tuple:
    data = posting.model_dump(mode="json")
    values: list[object] = [slug, _row_key(posting)]
    for field_name in FIELD_ORDER:
        value = data[field_name]
        if field_name in _JSON_FIELDS:
            value = json.dumps(value, ensure_ascii=False)
        elif field_name in _ZERO_DEFAULT_INT_FIELDS:
            value = int(value)
        values.append(value)
    return tuple(values)


def _from_row(row: tuple) -> tuple[str, JobPosting]:
    slug = row[0]
    data = dict(zip(FIELD_ORDER, row[1:]))
    for field_name in _JSON_FIELDS:
        data[field_name] = json.loads(data[field_name]) if data[field_name] else []
    data["is_closed"] = bool(data["is_closed"])
    return slug, JobPosting.model_validate(data)


def write_database(companies: list[CompanyPostings], db_path: Path = DB_PATH) -> None:
    """Rebuild the database from scratch and swap it in atomically."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(db_path.suffix + ".tmp")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(str(tmp_path))
    try:
        conn.executescript(_SCHEMA)
        conn.executemany(
            _INSERT_SQL,
            (
                _to_row(company.slug, posting)
                for company in companies
                for posting in company.postings
            ),
        )
        conn.execute("INSERT INTO postings_fts(postings_fts) VALUES ('rebuild')")
        conn.commit()
    finally:
        conn.close()

    tmp_path.replace(db_path)


def load_all(db_path: Path = DB_PATH) -> dict[tuple[str, str], list[JobPosting]]:
    """Prior postings keyed by `(source_board, slug)`.

    A missing, empty or corrupt file reads as empty — a fresh `latest` release,
    or the very first run, must proceed rather than crash. Mirrors the
    leniency the old `store.load_company` had for a missing/broken file.
    """
    if not db_path.exists():
        return {}
    result: dict[tuple[str, str], list[JobPosting]] = {}
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            for row in conn.execute(_SELECT_SQL):
                slug, posting = _from_row(row)
                result.setdefault((posting.source_board, slug), []).append(posting)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("%s is unreadable (%s); treating as empty", db_path, exc)
        return {}
    return result


def compress(db_path: Path, gz_path: Path | None = None) -> Path:
    gz_path = gz_path or db_path.with_suffix(db_path.suffix + ".gz")
    with open(db_path, "rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return gz_path


def decompress(gz_path: Path, db_path: Path | None = None) -> Path:
    db_path = db_path or gz_path.with_suffix("")
    with gzip.open(gz_path, "rb") as src, open(db_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return db_path
