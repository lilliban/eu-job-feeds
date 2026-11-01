"""Persistent Zefix snapshot and discovery queue.

Not in git — same reasoning as `state/jobs.sqlite` in Fase 1 (docs/DECISIONS.md
#18/#19): a weekly snapshot of ~543,000 companies is too large and too mutable
to commit. Published as `zefix.sqlite.gz` on the `latest` release, alongside
`jobs.sqlite.gz`.

`processed_at` is the discovery queue: NULL means "seen in the last Zefix
sync, never yet probed against an ATS provider". `zefix-discover` claims the
next batch by that column and stamps it, so a daily run always makes forward
progress through the backlog instead of reprocessing the same companies.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .zefix import ZefixCompany

DB_PATH = Path("state/zefix.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS zefix_companies (
    uid TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    legal_form TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_zefix_unprocessed ON zefix_companies(processed_at);
"""

_UPSERT_SQL = """
INSERT INTO zefix_companies (uid, legal_name, legal_form, first_seen_at, last_seen_at, processed_at)
VALUES (?, ?, ?, ?, ?, NULL)
ON CONFLICT(uid) DO UPDATE SET
    legal_name = excluded.legal_name,
    legal_form = excluded.legal_form,
    last_seen_at = excluded.last_seen_at
"""


@dataclass
class SyncStats:
    added: int = 0
    removed: int = 0
    unchanged: int = 0


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    return conn


def write_snapshot(
    companies: dict[str, ZefixCompany], db_path: Path = DB_PATH, *, at: str
) -> SyncStats:
    """Upsert this week's pull; a UID no longer present has left the register.

    An upsert, not a full rebuild like `sqlite_store.write_database`: this
    table's `processed_at` is state the pipeline itself writes (Fase 1's
    postings are always fully replaced from a fresh fetch, but here that would
    silently reset the discovery queue for every company still active — the
    `ON CONFLICT` clause deliberately leaves `processed_at` untouched.
    """
    conn = _connect(db_path)
    try:
        existing = {row[0] for row in conn.execute("SELECT uid FROM zefix_companies")}
        new_uids = set(companies)
        removed = existing - new_uids

        conn.executemany(
            "DELETE FROM zefix_companies WHERE uid = ?", [(uid,) for uid in removed]
        )
        conn.executemany(
            _UPSERT_SQL,
            [
                (c.uid, c.legal_name, c.legal_form, at, at)
                for c in companies.values()
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return SyncStats(
        added=len(new_uids - existing),
        removed=len(removed),
        unchanged=len(new_uids & existing),
    )


def next_batch(db_path: Path = DB_PATH, *, size: int) -> list[ZefixCompany]:
    """The next `size` companies never yet probed, oldest-UID-first."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT uid, legal_name, legal_form FROM zefix_companies "
            "WHERE processed_at IS NULL ORDER BY uid LIMIT ?",
            (size,),
        ).fetchall()
    finally:
        conn.close()
    return [ZefixCompany(uid=r[0], legal_name=r[1], legal_form=r[2]) for r in rows]


def mark_processed(db_path: Path, uids: list[str], *, at: str) -> None:
    if not uids:
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(
            "UPDATE zefix_companies SET processed_at = ? WHERE uid = ?",
            [(at, uid) for uid in uids],
        )
        conn.commit()
    finally:
        conn.close()


def queue_stats(db_path: Path = DB_PATH) -> dict[str, int]:
    if not db_path.exists():
        return {"total": 0, "processed": 0, "pending": 0}
    conn = sqlite3.connect(str(db_path))
    try:
        total = conn.execute("SELECT COUNT(*) FROM zefix_companies").fetchone()[0]
        processed = conn.execute(
            "SELECT COUNT(*) FROM zefix_companies WHERE processed_at IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    return {"total": total, "processed": processed, "pending": total - processed}
