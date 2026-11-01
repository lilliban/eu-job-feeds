"""Years-of-experience extraction.

A bare number of years is meaningless (`founded 3 years ago`, `a 5 year plan`),
so a match only counts when an experience keyword sits next to it — either
introducing the phrase (`mindestens 3 Jahre`, `almeno 2 anni`) or following it
(`3 years of experience`, `5 anni di esperienza`).
"""

from __future__ import annotations

import re

# "year(s)" in the languages present in these feeds.
# `a[nñ]os?` covers año/años/ano/anos. Written as `an[ñn]os?` it required a
# literal "an" prefix and so matched none of them — Spanish adverts silently
# produced no years at all.
_YEAR = r"(?:years?|yrs?|anni|anno|jahre[ns]?|jahr|a[nñ]os?|ans?|jaar|jaren|let|lat)"

# Words meaning "at least" / "minimum".
_MIN_WORD = (
    r"(?:at\s+least|minimum(?:\s+of)?|min\.?|"
    r"almeno|minimo|come\s+minimo|"
    r"mindestens|mind\.?|zumindest|"
    r"au\s+moins|minimum|"
    r"al\s+menos|m[ií]nimo|"
    r"minimaal|ten\s+minste|minstens)"
)

# Words meaning "up to" / "maximum".
_MAX_WORD = r"(?:up\s+to|at\s+most|maximum(?:\s+of)?|max\.?|fino\s+a|bis\s+zu|h[oó]chstens|hasta|jusqu[’‘´' ]à|maximaal)"

# Something in the sentence must be about experience, not just about time.
_EXP_CUE = re.compile(
    r"(?:experience|experienc\w+|esperienz\w+|erfahrung\w*|berufserfahrung|"
    r"exp[ée]rience|experiencia|ervaring|"
    r"background|track\s+record|seniority|"
    r"working|worked|lavorativ\w+|professional|professionell\w*|"
    r"praxis|praktische|hands[- ]on)",
    re.IGNORECASE,
)

_RANGE = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s*(?:[-–—]|to|a|e|bis|und|[àa]|y|tot)\s*(\d{{1,2}})\s*\+?\s*{_YEAR}\b",
    re.IGNORECASE,
)
_PLUS = re.compile(rf"(?<!\d)(\d{{1,2}})\s*\+\s*{_YEAR}\b", re.IGNORECASE)
_MIN_PREFIX = re.compile(rf"{_MIN_WORD}\s+(?:von\s+|di\s+|de\s+)?(\d{{1,2}})\s*{_YEAR}\b", re.IGNORECASE)
_MAX_PREFIX = re.compile(rf"{_MAX_WORD}\s+(?:von\s+|di\s+|de\s+)?(\d{{1,2}})\s*{_YEAR}\b", re.IGNORECASE)
_PLAIN = re.compile(rf"(?<!\d)(\d{{1,2}})\s*{_YEAR}\b", re.IGNORECASE)

MAX_YEARS = 40


def _has_cue(text: str, start: int, end: int) -> bool:
    """True when an experience word sits within ~70 chars of the match."""
    return bool(_EXP_CUE.search(text[max(0, start - 70) : min(len(text), end + 70)]))


def _valid(*values: int | None) -> bool:
    return all(v is None or 0 < v <= MAX_YEARS for v in values)


def extract_years(text: str | None) -> tuple[int | None, int | None]:
    """Return `(min_years_exp, max_years_exp)`.

    The most specific pattern wins: an explicit range beats `N+`, which beats a
    `mindestens N` prefix, which beats a bare `N years`.
    """
    if not text:
        return None, None

    for m in _RANGE.finditer(text):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        if _valid(lo, hi) and _has_cue(text, m.start(), m.end()):
            return lo, hi

    for m in _PLUS.finditer(text):
        lo = int(m.group(1))
        if _valid(lo) and _has_cue(text, m.start(), m.end()):
            return lo, None

    for m in _MIN_PREFIX.finditer(text):
        lo = int(m.group(1))
        if _valid(lo) and _has_cue(text, m.start(), m.end()):
            return lo, None

    for m in _MAX_PREFIX.finditer(text):
        hi = int(m.group(1))
        if _valid(hi) and _has_cue(text, m.start(), m.end()):
            return None, hi

    for m in _PLAIN.finditer(text):
        val = int(m.group(1))
        if not (_valid(val) and _has_cue(text, m.start(), m.end())):
            continue
        # `for the past 3 years` and friends describe the company, not the ask.
        before = text[max(0, m.start() - 30) : m.start()].lower()
        if re.search(r"(?:past|last|ultim\w+|letzten|dernier\w*|for\s+the)\s*$", before):
            continue
        if re.search(r"^\s*ago\b", text[m.end() : m.end() + 8], re.IGNORECASE):
            continue
        return val, None

    return None, None


def parse_structured_range(value: str | None) -> tuple[int | None, int | None]:
    """Parse an ATS-supplied range such as Personio's `yearsOfExperience` = `7-10`.

    Structured data beats regex, so this runs before `extract_years`.
    """
    if not value:
        return None, None
    v = value.strip().lower()
    if not v or v in {"not specified", "none", "any"}:
        return None, None

    m = re.fullmatch(r"(\d{1,2})\s*[-–—]\s*(\d{1,2})", v)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return (lo, hi) if _valid(lo, hi) else (None, None)

    m = re.fullmatch(r"(\d{1,2})\s*\+", v)
    if m and _valid(int(m.group(1))):
        return int(m.group(1)), None

    m = re.fullmatch(r"lt-(\d{1,2})|<\s*(\d{1,2})", v)
    if m:
        hi = int(m.group(1) or m.group(2))
        return (None, hi) if _valid(hi) else (None, None)

    if v.isdigit() and _valid(int(v)):
        return int(v), None

    return None, None
