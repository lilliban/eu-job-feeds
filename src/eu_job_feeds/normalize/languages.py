"""Required-language detection.

Job descriptions mention languages constantly without requiring them ("our
English-language docs", "the German market"), so a bare language name is never
enough. A language counts only when a proficiency cue sits next to it, or when
the text uses a compound that can only mean a skill (`Deutschkenntnisse`).

Output is lowercase English language names, matching the contract's example
`["italian", "english"]`.
"""

from __future__ import annotations

import re

# Language name -> canonical output, across the languages the feeds are written in.
_LANGUAGE_NAMES: dict[str, str] = {
    "english": "english", "inglese": "english", "englisch": "english",
    "anglais": "english", "ingl[ée]s": "english", "engels": "english",
    "italian": "italian", "italiano": "italian", "italienisch": "italian",
    "italien": "italian", "italiaans": "italian",
    "german": "german", "tedesco": "german", "deutsch": "german",
    "allemand": "german", "alem[áa]n": "german", "duits": "german",
    "french": "french", "francese": "french", "franz[öo]sisch": "french",
    "fran[çc]ais": "french", "franc[ée]s": "french", "frans": "french",
    "spanish": "spanish", "spagnolo": "spanish", "spanisch": "spanish",
    "espagnol": "spanish", "espa[ñn]ol": "spanish", "spaans": "spanish",
    "portuguese": "portuguese", "portoghese": "portuguese",
    "portugiesisch": "portuguese", "portugais": "portuguese",
    "portugu[êe]s": "portuguese", "portugees": "portuguese",
    "dutch": "dutch", "olandese": "dutch", "niederl[äa]ndisch": "dutch",
    "n[ée]erlandais": "dutch", "neerland[ée]s": "dutch", "nederlands": "dutch",
    "flemish": "dutch", "vlaams": "dutch",
    "polish": "polish", "polacco": "polish", "polnisch": "polish",
    "polonais": "polish", "pools": "polish",
    "swedish": "swedish", "svedese": "swedish", "schwedisch": "swedish",
    "danish": "danish", "danese": "danish", "d[äa]nisch": "danish",
    "norwegian": "norwegian", "norvegese": "norwegian", "norwegisch": "norwegian",
    "finnish": "finnish", "finlandese": "finnish", "finnisch": "finnish",
    "czech": "czech", "ceco": "czech", "tschechisch": "czech",
    "romanian": "romanian", "rumeno": "romanian", "rum[äa]nisch": "romanian",
    "hungarian": "hungarian", "ungherese": "hungarian", "ungarisch": "hungarian",
    "greek": "greek", "greco": "greek", "griechisch": "greek",
    "turkish": "turkish", "turco": "turkish", "t[üu]rkisch": "turkish",
    "russian": "russian", "russo": "russian", "russisch": "russian",
    "arabic": "arabic", "arabo": "arabic", "arabisch": "arabic",
    "mandarin": "mandarin", "chinese": "mandarin", "cinese": "mandarin",
    "japanese": "japanese", "giapponese": "japanese",
    "hebrew": "hebrew", "ebraico": "hebrew",
    "ukrainian": "ukrainian", "bulgarian": "bulgarian", "croatian": "croatian",
    "slovak": "slovak", "slovenian": "slovenian", "catalan": "catalan",
}

# Proficiency words that make a nearby language name a requirement.
_CUE = (
    r"fluen\w*|proficien\w*|nativ\w*|mother\s?tongue|bilingual|"
    r"business[- ]level|working\s+knowledge|command\s+of|speak\w*|spoken|"
    r"written|verbal|conversational|advanced|intermediate|basic|"
    r"required|require\w*|must\s+have|skills?|knowledge|level|"
    r"madrelingua|fluent[ei]|conoscenz\w+|ottima\s+conoscenza|"
    r"parlat\w+|scritt\w+|livello|richiest\w+|buona\s+conoscenza|"
    r"kenntnis\w*|flie[bß]end|muttersprach\w*|verhandlungssicher\w*|"
    r"sprachkenntnis\w*|beherrsch\w*|"
    r"courant\w*|ma[îi]tris\w*|langue\s+maternelle|niveau|"
    r"dominio|nivel|lengua\s+materna|"
    r"vloeiend|beheersing|"
    r"c1|c2|b1|b2|a1|a2"
)
_CUE_RE = re.compile(_CUE, re.IGNORECASE)

# German/Dutch compounds that are self-evidently about language skill.
_COMPOUND = re.compile(
    r"\b(englisch|deutsch|franz[öo]sisch|spanisch|italienisch|niederl[äa]ndisch|"
    r"polnisch|russisch|portugiesisch)"
    r"(?:kenntnisse?|sprachkenntnisse?|niveau)\b",
    re.IGNORECASE,
)
_COMPOUND_TO_NAME = {
    "englisch": "english", "deutsch": "german", "französisch": "french",
    "franzosisch": "french", "spanisch": "spanish", "italienisch": "italian",
    "niederländisch": "dutch", "niederlandisch": "dutch", "polnisch": "polish",
    "russisch": "russian", "portugiesisch": "portuguese",
}

# A "Languages:" heading makes every language listed under it a requirement.
_SECTION = re.compile(
    r"(?:^|\n)\s*(?:languages?|lingue|sprachen|langues|idiomas|talen)\s*[:\-–]\s*(?P<body>[^\n]{0,200})",
    re.IGNORECASE,
)

_NAME_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_LANGUAGE_NAMES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Contexts where a language name is about the market, not the candidate.
_ANTIPATTERN = re.compile(
    r"(?:"
    r"(?:the\s+)?(?:english|german|french|spanish|italian|dutch|polish)\s+"
    r"(?:market|team|office|entity|law|version|site|website|documentation|docs|"
    r"customers?|clients?|users?|speaking\s+market)"
    r"|(?:this\s+(?:role|job|position)\s+is\s+posted\s+in)"
    r"|(?:job\s+description\s+in)"
    r")",
    re.IGNORECASE,
)


def _canonical(token: str) -> str | None:
    key = token.strip().lower()
    for pattern, name in _LANGUAGE_NAMES.items():
        if re.fullmatch(pattern, key, re.IGNORECASE):
            return name
    return None


def extract_languages(*texts: str | None) -> list[str]:
    """Return the required languages, sorted, deduplicated, possibly empty.

    An empty list means "not stated", not "none required" — the contract has no
    way to express the difference, and inventing one would break the consumer.
    """
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return []

    found: set[str] = set()

    for m in _COMPOUND.finditer(blob):
        name = _COMPOUND_TO_NAME.get(m.group(1).lower())
        if name:
            found.add(name)

    for m in _SECTION.finditer(blob):
        for token in _NAME_RE.finditer(m.group("body")):
            name = _canonical(token.group(0))
            if name:
                found.add(name)

    for m in _NAME_RE.finditer(blob):
        name = _canonical(m.group(0))
        if not name:
            continue
        window_start = max(0, m.start() - 60)
        window = blob[window_start : min(len(blob), m.end() + 60)]
        if _ANTIPATTERN.search(window):
            continue
        if _CUE_RE.search(window):
            found.add(name)

    return sorted(found)
