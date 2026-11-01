"""HTML to clean text, plus the content hash.

No external HTML parser: the ATS payloads are machine-generated fragments, not
arbitrary web pages, and a stdlib parser keeps the dependency list to three.
"""

from __future__ import annotations

import hashlib
import re
from html import unescape
from html.parser import HTMLParser

# Elements whose text content is never part of a job advert.
_DROP_CONTENT = {"script", "style", "head", "title"}

# Elements that imply a line break when they open or close.
_BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr", "header",
    "footer", "nav", "dl", "dt", "dd", "figure", "figcaption",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _DROP_CONTENT:
            self._suppress += 1
        elif tag in _BLOCK:
            self._parts.append("\n")
        if tag == "li":
            self._parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_CONTENT:
            self._suppress = max(0, self._suppress - 1)
        elif tag in _BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppress:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(value: str | None) -> str | None:
    """Turn an HTML fragment into readable plain text.

    Greenhouse double-escapes its `content` field (`&lt;p&gt;`), so unescape runs
    before parsing as well as inside it.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None

    # Greenhouse ships entity-escaped markup; one unescape turns it back into tags.
    if "&lt;" in raw and "<" not in raw:
        raw = unescape(raw)

    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
        out = parser.text()
    except Exception:
        # A malformed fragment should degrade to "tags stripped", never to a crash.
        out = re.sub(r"<[^>]+>", " ", raw)

    out = unescape(out)
    out = out.replace(" ", " ").replace("​", "")
    out = re.sub(r"[ \t\r\f\v]+", " ", out)
    out = re.sub(r" *\n *", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() or None


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace in an already-plain string."""
    if value is None:
        return None
    out = re.sub(r"[ \t]+", " ", value.replace(" ", " "))
    out = re.sub(r" *\n *", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() or None


# Headings that start the "what we want from you" half of an advert. Many
# providers (Greenhouse, Ashby, Lever, Workday) ship one undivided body, and
# `requirements_raw` would otherwise always be null — it is also what the
# content hash is defined over, so getting it right matters twice.
_REQ_HEADING = re.compile(
    r"^[\s\-*•]{0,4}(?:"
    r"requirements?|qualifications?|minimum\s+qualifications?|"
    r"basic\s+qualifications?|preferred\s+qualifications?|"
    r"who\s+you\s+are|what\s+you[’‘´']?(?:ll|\s*will)\s+need|what\s+you\s+need|"
    r"what\s+we[’‘´']?re\s+looking\s+for|what\s+you[’‘´']?(?:ll|\s*will)\s+bring|"
    # NVIDIA's Workday adverts use this heading throughout; without it every
    # Workday posting would have a null requirements_raw.
    r"what\s+we\s+need\s+to\s+see|what\s+we[’‘´']?d\s+like\s+to\s+see|"
    r"you\s+will\s+need|what\s+you[’‘´']?(?:ll|\s*will)\s+do\s+(?:and|&)\s+need|"
    r"your\s+profile|about\s+you|skills?(?:\s+(?:and|&)\s+experience)?|"
    r"must\s+haves?|you\s+have|we[’‘´']?re\s+looking\s+for|"
    r"dein\s+profil|ihr\s+profil|das\s+bringst\s+du\s+mit|anforderungen|"
    r"qualifikationen|deine\s+qualifikationen|was\s+du\s+mitbringst|"
    r"requisiti|il\s+tuo\s+profilo|competenze|chi\s+sei|cosa\s+cerchiamo|"
    r"profil\s+recherch[ée]|votre\s+profil|comp[ée]tences|"
    r"perfil|requisitos|lo\s+que\s+buscamos|"
    r"wie\s+ben\s+jij|jouw\s+profiel|functie-?eisen"
    r")\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def split_description_requirements(
    body: str | None,
) -> tuple[str | None, str | None]:
    """Split a single advert body into `(description, requirements)`.

    Splits at the first requirements-style heading. When there is no such
    heading the whole body stays the description and requirements is None —
    inventing a split point would corrupt both fields.
    """
    if not body:
        return None, None
    match = _REQ_HEADING.search(body)
    if match is None:
        return body, None

    description = body[: match.start()].strip()
    requirements = body[match.start() :].strip()
    # A heading in the first few characters means there is no real description
    # half; keep the body whole rather than emitting an empty description.
    if not description or len(description) < 40:
        return body, requirements or None
    return description or None, requirements or None


def _hash_normalise(value: str | None) -> str:
    """Collapse every run of whitespace to a single space.

    Stronger than `clean_text`, which preserves paragraph breaks because the
    published text should stay readable. For the hash the opposite is wanted: a
    company reflowing its advert's HTML — turning a blank line into a single
    newline and back — must not mint a new posting, so newlines and spaces are
    made indistinguishable before hashing.
    """
    return re.sub(r"\s+", " ", (value or "").replace(" ", " ")).strip().lower()


def content_hash(title: str, company: str, requirements: str | None) -> str:
    """sha256 of `title|company|first 500 chars of requirements`, lowercased+trimmed.

    This is the deduplication key in the consumer's contract. Whitespace is
    normalised first, and the 500-character window is taken after that, so the
    window covers the same words regardless of how the provider formatted them.
    """
    parts = [
        _hash_normalise(title),
        _hash_normalise(company),
        _hash_normalise(requirements)[:500].strip(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
