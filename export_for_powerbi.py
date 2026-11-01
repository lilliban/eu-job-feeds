"""One-off export: flatten jobs.sqlite into a CSV that Power BI can load
directly (Get Data > Text/CSV), no ODBC driver needed.

Adds a `category` column via a simple keyword heuristic on the title — the
dataset has no real job-category field yet, so this is an approximation, not
a ground truth. Run again any time to refresh the CSV after a new `update`.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path

DB_PATH = Path("state/jobs.sqlite")
OUT_PATH = Path("state/postings_for_powerbi.csv")

_DATA_AI_KEYWORDS = re.compile(
    r"data scientist|data engineer|data analy|machine learning|\bml\b|"
    r"artificial intelligence|\bai\b|business intelligence|\bbi\b|analytics",
    re.IGNORECASE,
)
_SOFTWARE_KEYWORDS = re.compile(
    r"software engineer|developer|full.?stack|backend|frontend|devops|"
    r"cloud engineer|site reliability|\bsre\b|platform engineer|architect",
    re.IGNORECASE,
)
_OTHER_TECH_KEYWORDS = re.compile(
    r"security|infrastructure|product manager|qa engineer|test engineer|"
    r"\bit\b|network engineer|database administrator|\bdba\b",
    re.IGNORECASE,
)


def categorize(title: str) -> str:
    if _DATA_AI_KEYWORDS.search(title):
        return "Data/AI/BI"
    if _SOFTWARE_KEYWORDS.search(title):
        return "Software Engineering"
    if _OTHER_TECH_KEYWORDS.search(title):
        return "Other Tech"
    return "Non-Tech"


#: Real columns read from the database, in this order.
DB_COLUMNS = [
    "company_name", "source_board", "title", "seniority",
    "department", "contract_type_norm", "work_mode", "city", "country_code",
    "salary_min", "salary_max", "salary_currency", "posted_date",
    "first_seen_at", "last_seen_at", "is_closed", "source_url",
]
#: `category` is computed, not stored — inserted right after `title`.
OUT_COLUMNS = DB_COLUMNS[:3] + ["category"] + DB_COLUMNS[3:]


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(f"SELECT {', '.join(DB_COLUMNS)} FROM postings").fetchall()
    conn.close()

    title_idx = DB_COLUMNS.index("title")
    with open(OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(OUT_COLUMNS)
        for row in rows:
            category = categorize(row[title_idx] or "")
            writer.writerow(list(row[:3]) + [category] + list(row[3:]))

    print(f"wrote {len(rows)} rows to {OUT_PATH}")


if __name__ == "__main__":
    main()
