"""Contract type and work mode normalisation.

Both fields exist twice in the contract: `contract_type` keeps whatever the
company wrote, `contract_type_norm` is the closed vocabulary. The raw value is
never discarded, so a consumer that disagrees with a mapping can redo it.
"""

from __future__ import annotations

import re

# Values seen in real payloads, mapped to the contract vocabulary.
# Greenhouse metadata "Time Type", Ashby `employmentType`, Lever
# `categories.commitment`, Recruitee `employment_type_code`, SmartRecruiters
# `typeOfEmployment.label`, Workable `employment_type`, Personio `schedule`.
_CONTRACT_MAP: dict[str, str] = {
    # full time
    "full time": "full_time", "fulltime": "full_time", "full-time": "full_time",
    "full_time": "full_time", "permanent": "full_time", "regular": "full_time",
    "fulltime_permanent": "full_time", "unbefristet": "full_time",
    "festanstellung": "full_time", "vollzeit": "full_time",
    "tempo pieno": "full_time", "tempo indeterminato": "full_time",
    "indeterminato": "full_time", "temps plein": "full_time", "cdi": "full_time",
    "voltijd": "full_time", "jornada completa": "full_time",
    "employee": "full_time", "staff": "full_time",
    # part time
    "part time": "part_time", "parttime": "part_time", "part-time": "part_time",
    "part_time": "part_time", "fulltime_parttime": "part_time",
    "parttime_permanent": "part_time", "teilzeit": "part_time",
    "tempo parziale": "part_time", "temps partiel": "part_time",
    "deeltijd": "part_time", "media jornada": "part_time",
    # contract / temporary
    "contract": "contract", "contractor": "contract", "temporary": "contract",
    "temp": "contract", "freelance": "contract", "fixed term": "contract",
    "fixed-term": "contract", "fixed_term": "contract", "seasonal": "contract",
    "befristet": "contract", "zeitarbeit": "contract", "cdd": "contract",
    "determinato": "contract", "tempo determinato": "contract",
    "a contratto": "contract", "consultant": "contract", "interim": "contract",
    "partner": "contract", "vendor": "contract",
    # internship / apprenticeship
    "internship": "internship", "intern": "internship", "trainee": "internship",
    "apprentice": "internship", "apprenticeship": "internship",
    "praktikum": "internship", "praktikant": "internship",
    "werkstudent": "internship", "working student": "internship",
    "ausbildung": "internship", "tirocinio": "internship",
    "stage": "internship", "stagiaire": "internship", "stagista": "internship",
    "becario": "internship", "pr[aá]cticas": "internship",
    "student": "internship", "graduate": "internship", "co-op": "internship",
}

_CONTRACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:intern(?:ship)?|praktik\w+|tirocin\w+|stage|werkstudent|apprentice\w*)\b", re.I), "internship"),
    (re.compile(r"\b(?:part[\s_-]?time|teilzeit|tempo\s+parziale|temps\s+partiel|deeltijd)\b", re.I), "part_time"),
    (re.compile(r"\b(?:freelance|contractor|fixed[\s_-]?term|befristet|cdd|tempo\s+determinato|temporary)\b", re.I), "contract"),
    (re.compile(r"\b(?:full[\s_-]?time|vollzeit|tempo\s+pieno|temps\s+plein|voltijd|permanent|cdi)\b", re.I), "full_time"),
)


def normalize_contract_type(raw: str | None) -> str | None:
    """Map a company's own wording onto the closed vocabulary, or None."""
    if not raw:
        return None
    key = re.sub(r"[\s_-]+", " ", raw.strip().lower()).strip()
    if key in _CONTRACT_MAP:
        return _CONTRACT_MAP[key]
    if key.replace(" ", "") in _CONTRACT_MAP:
        return _CONTRACT_MAP[key.replace(" ", "")]
    for pattern, value in _CONTRACT_PATTERNS:
        if pattern.search(key):
            return value
    return None


_REMOTE_RE = re.compile(
    r"\b(?:fully\s+remote|100\s?%\s?remote|remote[\s-]?(?:first|only)|"
    r"work\s+from\s+home|telecommut\w*|telelavoro|"
    r"remote|remoto|homeoffice|home\s?office|"
    r"t[ée]l[ée]travail|teletrabajo|thuiswerk\w*)\b",
    re.IGNORECASE,
)
_HYBRID_RE = re.compile(
    r"\b(?:hybrid\w*|ibrido|ibrida|hybride|h[ií]brido|"
    r"(?:\d\s*(?:days?|giorni|tage|jours)\s+(?:a|per|pro|par)\s+week|in\s+office\s+\d\s*days?))\b",
    re.IGNORECASE,
)
_ONSITE_RE = re.compile(
    r"\b(?:on[\s-]?site|onsite|in[\s-]?office|in[\s-]?person|"
    r"vor\s?ort|presenza|in\s+sede|pr[ée]sentiel|op\s?kantoor|"
    r"no\s+remote|not\s+remote|office[\s-]?based)\b",
    re.IGNORECASE,
)


def normalize_work_mode(raw: str | None) -> str | None:
    """Map an ATS work-mode label (Ashby `workplaceType`, Lever `workplaceType`)."""
    if not raw:
        return None
    key = raw.strip().lower()
    if key in {"remote", "fully remote", "remoto"}:
        return "remote"
    if key in {"hybrid", "ibrido", "hybride"}:
        return "hybrid"
    if key in {"onsite", "on-site", "on site", "in office", "in-office", "office"}:
        return "onsite"
    if _HYBRID_RE.search(key):
        return "hybrid"
    if _ONSITE_RE.search(key):
        return "onsite"
    if _REMOTE_RE.search(key):
        return "remote"
    return None


def infer_work_mode(*texts: str | None) -> str | None:
    """Infer work mode from free text when the ATS did not say.

    Hybrid is checked before remote on purpose: `hybrid remote` and
    `remote/hybrid` are common, and hybrid is the more specific claim.
    """
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return None
    if _HYBRID_RE.search(blob):
        return "hybrid"
    if _REMOTE_RE.search(blob):
        return "remote"
    if _ONSITE_RE.search(blob):
        return "onsite"
    return None


_SENIORITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:intern(?:ship)?|praktikant\w*|tirocinante|stagista|werkstudent)\b", re.I), "Internship"),
    (re.compile(r"\b(?:principal|staff|distinguished|fellow)\b", re.I), "Principal"),
    (re.compile(r"\b(?:head\s+of|director|vp\b|vice\s+president|chief|c[teifo]o\b|leiter\w*)\b", re.I), "Executive"),
    (re.compile(r"\b(?:lead|leiter|responsabile|manager|teamlead)\b", re.I), "Lead"),
    (re.compile(r"\b(?:senior|sr\.?|senior[- ]level|erfahren\w*|esperto|confirm[ée])\b", re.I), "Senior"),
    (re.compile(r"\b(?:mid[- ]level|intermediate|regular|medior)\b", re.I), "Mid"),
    (re.compile(r"\b(?:junior|jr\.?|entry[- ]level|graduate|einsteiger|berufseinsteiger|d[ée]butant)\b", re.I), "Junior"),
)


def infer_seniority(title: str | None) -> str | None:
    """Derive a seniority label from the job title.

    Only used when the ATS provides no explicit level. Titles are short and the
    words above are unambiguous in them; the same regex over a full description
    would fire on anything.
    """
    if not title:
        return None
    for pattern, label in _SENIORITY_PATTERNS:
        if pattern.search(title):
            return label
    return None
