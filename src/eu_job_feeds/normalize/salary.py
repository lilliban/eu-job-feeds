"""Salary extraction from free text.

Deliberately conservative. Two rules do most of the work:

1. A number is only ever a salary if an explicit currency marker is attached to
   the range, or a salary keyword introduces it *and* a currency appears nearby.
   Without a currency the amount is unusable to the consumer anyway.
2. The value must land in a plausible annual band, and must not be followed by a
   counting noun. `tra 500 e 1000 clienti` fails both tests.

Only the four currencies in the published contract are emitted. If the text
quotes SEK or PLN the whole salary is dropped rather than reported under a
wrong or empty currency.
"""

from __future__ import annotations

import re

# Contract-allowed currencies. Anything else means "no salary" (see module docs).
ALLOWED_CURRENCIES = ("EUR", "USD", "GBP", "CHF")

_SYMBOLS = {
    "€": "EUR",
    "$": "USD",
    "us$": "USD",
    "£": "GBP",
    "chf": "CHF",
    "fr.": "CHF",
    "sfr": "CHF",
}
_CODES = {
    "eur": "EUR", "euro": "EUR", "euros": "EUR", "eu": None,
    "usd": "USD", "dollar": "USD", "dollars": "USD",
    "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "gbp.": "GBP",
    "chf": "CHF", "franc": "CHF", "francs": "CHF", "franken": "CHF",
    # Recognised so we can *refuse* rather than mislabel.
    "sek": "SEK", "nok": "NOK", "dkk": "DKK", "pln": "PLN",
    "czk": "CZK", "huf": "HUF", "ron": "RON", "bgn": "BGN",
}

# Words that introduce a salary, across the languages these feeds actually use.
_CUES = (
    r"ral|retribuzion\w*|stipendi\w*|compens\w*|salari\w*|salaire|sueldo|"
    r"gehalt\w*|verg[üu]tung|jahresgehalt|lohn|bruttogehalt|"
    r"salary|compensation|remuneration|base\s+pay|pay\s+range|wage|ote|"
    r"pay|package|bruto|brutto|budget\s+for\s+this\s+role"
)
_CUE_RE = re.compile(_CUES, re.IGNORECASE)

# A number that follows one of these is counting something, not paying anyone.
_COUNT_NOUNS = re.compile(
    r"^\W{0,3}(?:"
    r"client\w*|customer\w*|user\w*|utent\w*|employee\w*|dipendent\w*|"
    r"people|person\w*|persone|colleghi|colleagues|mitarbeiter\w*|kunden|"
    r"nutzer\w*|klanten|medewerker\w*|cliente\w*|empleado\w*|"
    r"collaborateur\w*|salari[ée]s|utilisateur\w*|"
    r"compan\w+|azienda|aziende|unternehmen|startups?|"
    r"seat\w*|student\w*|partner\w*|project\w*|progetti|merchant\w*|"
    r"transaction\w*|transazioni|order\w*|ordini|square\s?met|mq|sqm|"
    r"m2|m²|line\w*\s+of\s+code|righe|developer\w*|sviluppator\w*|"
    r"member\w*|membri|subscriber\w*|abbonati|download\w*|visitor\w*"
    r")\b",
    re.IGNORECASE,
)

_PERIOD_MONTH = re.compile(
    r"\b(?:per\s+month|/\s?month|monthly|a\s+month|p\.?m\.?\b|"
    r"al\s+mese|mensil\w*|/\s?mese|"
    r"pro\s+monat|monatlich\w*|im\s+monat|"
    r"par\s+mois|/\s?mois|mensuel\w*|"
    r"al\s+mes|mensual\w*|per\s+maand|maandelijks)",
    re.IGNORECASE,
)
_PERIOD_HOUR = re.compile(
    r"\b(?:per\s+hour|/\s?h(?:our|r)?\b|hourly|an\s+hour|"
    r"all[’‘´' ]ora|orari[oa]\b|pro\s+stunde|st[uü]ndlich|"
    r"par\s+heure|/\s?heure|por\s+hora|per\s+uur)",
    re.IGNORECASE,
)
_PERIOD_DAY = re.compile(
    r"\b(?:per\s+day|/\s?day|daily\s+rate|day\s+rate|al\s+giorno|"
    r"giornalier\w*|pro\s+tag|par\s+jour|tagess?atz)",
    re.IGNORECASE,
)

# Plausible *annual* gross salary, after any monthly conversion.
MIN_PLAUSIBLE = 8_000
MAX_PLAUSIBLE = 2_000_000

_NUM = r"\d{1,3}(?:[.,   ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_SYM = r"€|£|\$|US\$"
_CODE = r"EUR|USD|GBP|CHF|SEK|NOK|DKK|PLN|CZK|HUF|RON|BGN|euros?|dollars?|pounds?|francs?|franken"

# One money token: optional symbol/code before, number, optional k/K, optional code after.
_MONEY = re.compile(
    rf"(?P<pre>(?:{_SYM})\s?|(?:{_CODE})\s+)?"
    rf"(?P<num>{_NUM})"
    rf"\s?(?P<k>[kK])?"
    rf"(?:\s?(?P<post>(?:{_SYM})|(?:{_CODE})))?",
    re.IGNORECASE,
)

_RANGE_SEP = re.compile(
    r"^\s*(?:[-–—]|to|a\b|e\b|und\b|bis\b|y\b|et\b|tot\b|until|fino\s+a|\.\.\.?)\s*$",
    re.IGNORECASE,
)
_UP_TO = re.compile(
    r"(?:up\s+to|fino\s+a|bis\s+zu|hasta|jusqu[’‘´' ]à|max(?:imum)?\.?)\s*$",
    re.IGNORECASE,
)
_FROM = re.compile(
    r"(?:from|a\s+partire\s+da|ab\b|desde|[àa]\s+partir\s+de|starting\s+at|min(?:imum)?\.?)\s*$",
    re.IGNORECASE,
)


def parse_amount(raw: str, k_suffix: bool = False) -> float | None:
    """Parse a European or Anglo-Saxon formatted number.

    `35.000` and `35,000` are both 35000; `35.000,50` and `35,000.50` are both
    35000.5. The rule is positional, not locale-guessed: a separator followed by
    exactly three digits and not the last separator is a thousands separator.
    """
    s = raw.strip().replace(" ", " ").replace(" ", " ")
    if not s:
        return None

    # Space-separated thousands: only when every group after the first is 3 digits.
    if " " in s:
        parts = s.split(" ")
        if all(re.fullmatch(r"\d{3}(?:[.,]\d{1,2})?", p) for p in parts[1:]) and parts[0].isdigit():
            s = "".join(parts)
        else:
            s = parts[0]

    seps = [c for c in s if c in ".,"]
    if not seps:
        value = float(s)
    elif len(set(seps)) == 2:
        # Both separators present: the last one is the decimal point.
        dec = s[max(s.rfind("."), s.rfind(","))]
        thou = "," if dec == "." else "."
        value = float(s.replace(thou, "").replace(dec, "."))
    else:
        sep = seps[0]
        tail = s.rsplit(sep, 1)[1]
        if len(seps) > 1 or len(tail) == 3:
            # 1.234.567 or 35.000 -> grouping
            value = float(s.replace(sep, ""))
        else:
            value = float(s.replace(sep, "."))

    if k_suffix:
        value *= 1000
    return value


def _currency_from(token_pre: str | None, token_post: str | None) -> str | None:
    for raw in (token_post, token_pre):
        if not raw:
            continue
        key = raw.strip().lower()
        if key in _SYMBOLS:
            return _SYMBOLS[key]
        if key in _CODES:
            return _CODES[key]
    return None


def _scan_money(text: str) -> list[dict]:
    out: list[dict] = []
    for m in _MONEY.finditer(text):
        num = m.group("num")
        if not num:
            continue
        # A bare small integer with no currency marker at all is noise.
        if not (m.group("pre") or m.group("post") or m.group("k")):
            if len(num.replace(".", "").replace(",", "").replace(" ", "")) < 4:
                continue
        try:
            value = parse_amount(num, k_suffix=bool(m.group("k")))
        except ValueError:
            continue
        if value is None:
            continue
        out.append(
            {
                "value": value,
                "start": m.start(),
                "end": m.end(),
                "currency": _currency_from(m.group("pre"), m.group("post")),
            }
        )
    return out


def _nearby_currency(text: str, start: int, end: int) -> str | None:
    window = text[max(0, start - 40) : min(len(text), end + 40)]
    for sym, code in _SYMBOLS.items():
        if sym in window.lower():
            return code
    for m in re.finditer(_CODE, window, re.IGNORECASE):
        code = _CODES.get(m.group(0).lower())
        if code:
            return code
    return None


def extract_salary(text: str | None) -> tuple[int | None, int | None, str | None]:
    """Return `(salary_min, salary_max, currency)`, all None when unsure.

    Only the first defensible match in the text is used; job adverts that quote
    several figures are usually quoting equity or bonuses too, and picking among
    them would be guesswork.
    """
    if not text:
        return None, None, None

    tokens = _scan_money(text)
    if not tokens:
        return None, None, None

    for i, tok in enumerate(tokens):
        pair = None
        if i + 1 < len(tokens):
            gap = text[tok["end"] : tokens[i + 1]["start"]]
            if _RANGE_SEP.match(gap):
                pair = tokens[i + 1]

        start, end = tok["start"], (pair or tok)["end"]

        currency = tok["currency"] or (pair or {}).get("currency")
        if currency is None:
            currency = _nearby_currency(text, start, end)
        if currency is None or currency not in ALLOWED_CURRENCIES:
            # Unknown or unsupported currency -> report nothing (see module docs).
            continue

        before = text[max(0, start - 60) : start]
        after = text[end : end + 40]

        if _COUNT_NOUNS.match(after):
            continue
        if not (_CUE_RE.search(before) or tok["currency"] or (pair or {}).get("currency")):
            continue

        period_window = before + text[end : end + 30]
        if _PERIOD_HOUR.search(period_window) or _PERIOD_DAY.search(period_window):
            # Hourly and daily rates cannot be annualised without knowing the
            # contracted hours, and the contract has no period field to say so.
            continue
        factor = 12 if _PERIOD_MONTH.search(period_window) else 1

        lo = tok["value"] * factor
        hi = (pair["value"] * factor) if pair else lo
        if pair and hi < lo:
            lo, hi = hi, lo

        if not (MIN_PLAUSIBLE <= lo <= MAX_PLAUSIBLE and MIN_PLAUSIBLE <= hi <= MAX_PLAUSIBLE):
            continue

        if pair is None:
            if _UP_TO.search(before):
                return None, int(round(hi)), currency
            if _FROM.search(before):
                return int(round(lo)), None, currency
        return int(round(lo)), int(round(hi)), currency

    return None, None, None
