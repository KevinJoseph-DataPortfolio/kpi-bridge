"""Analyse des valeurs : nombres (conventions), montants, pourcentages, dates. Représentations canoniques exactes."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

CURRENCY_SYMBOLS = {
    "€": "EUR",
    "$": "USD",
    "US$": "USD",
    "£": "GBP",
    "¥": "CNY",
    "元": "CNY",
    "₽": "RUB",
    "руб": "RUB",
    "руб.": "RUB",
    "р.": "RUB",
    "CHF": "CHF",
    "Fr.": "CHF",
}
ISO_CURRENCIES = {"EUR", "USD", "GBP", "CNY", "RUB", "CHF", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK", "PLN", "CZK", "MXN", "ARS", "COP", "CLP", "BRL", "INR", "RMB"}

_SYM = r"(?:US\$|\$|€|£|¥|元|₽|руб\.?|р\.|CHF|Fr\.|[A-Z]{3})"
NUMBER_RE = re.compile(
    rf"^(?P<neg1>[-−–+]|\()?\s*(?P<pre>{_SYM})?\s*(?P<neg2>[-−–])?\s*(?P<core>\d[\d\s.,'  ]*)\s*(?P<suf>%|{_SYM})?\s*(?P<close>\))?\s*(?P<neg3>-)?$"
)
DOT_RE = re.compile(r"^(\d{1,3}([,\s  ']\d{3})+|\d+)(\.\d+)?$")
COMMA_RE = re.compile(r"^(\d{1,3}([.\s  ']\d{3})+|\d+)(,\d+)?$")


class Parsed:
    __slots__ = ("value", "currency", "percent")

    def __init__(self, value: Decimal, currency: str | None, percent: bool) -> None:
        self.value = value
        self.currency = currency
        self.percent = percent


def split_number(text: str) -> tuple[str, str | None, bool, bool] | None:
    """Retourne (noyau, devise, pourcentage, négatif) ou None si la forme n'est pas numérique."""
    m = NUMBER_RE.match(text.strip())
    if not m:
        return None
    pre, suf = m.group("pre"), m.group("suf")
    currency = None
    percent = suf == "%"
    for tok in (pre, suf):
        if tok and tok != "%":
            code = CURRENCY_SYMBOLS.get(tok, tok if tok in ISO_CURRENCIES else None)
            if code is None:
                return None
            currency = "CNY" if code == "RMB" else code
    neg = bool(m.group("neg1") in ("-", "−", "–") or m.group("neg2") or m.group("neg3"))
    if m.group("neg1") == "(":
        if not m.group("close"):
            return None
        neg = True
    elif m.group("close"):
        return None
    core = m.group("core").strip().rstrip(".,")
    return core, currency, percent, neg


def parse_core(core: str, convention: str) -> Decimal | None:
    core = core.strip()
    try:
        if convention == "dot":
            if not DOT_RE.match(core):
                return None
            return Decimal(re.sub(r"[,\s  ']", "", core))
        if convention == "comma":
            if not COMMA_RE.match(core):
                return None
            return Decimal(re.sub(r"[.\s  ']", "", core).replace(",", "."))
    except InvalidOperation:
        return None
    return None


def parse_number(text: str, convention: str) -> Parsed | None:
    parts = split_number(text)
    if parts is None:
        return None
    core, currency, percent, neg = parts
    value = parse_core(core, convention)
    if value is None:
        return None
    return Parsed(-value if neg else value, currency, percent)


def infer_convention(values: list[str]) -> tuple[str | None, bool, int, list[str]]:
    """Retourne (convention, ambiguë, nb_valides, exemples ambigus)."""
    dot_ok = comma_ok = 0
    differ: list[str] = []
    numeric_like = 0
    for v in values:
        parts = split_number(v)
        if parts is None:
            continue
        numeric_like += 1
        d = parse_core(parts[0], "dot")
        c = parse_core(parts[0], "comma")
        dot_ok += d is not None
        comma_ok += c is not None
        if d is not None and c is not None and d != c:
            differ.append(v)
    if numeric_like == 0:
        return None, False, 0, []
    if dot_ok == comma_ok == numeric_like:
        if differ:
            return "dot", True, numeric_like, differ[:3]
        return "dot", False, numeric_like, []
    if dot_ok >= comma_ok:
        return "dot", False, dot_ok, []
    return "comma", False, comma_ok, []


# ---------------------------------------------------------------- dates
MONTHS = {
    # fr
    "janvier": 1, "janv": 1, "février": 2, "fevrier": 2, "févr": 2, "mars": 3, "avril": 4, "avr": 4, "mai": 5, "juin": 6,
    "juillet": 7, "juil": 7, "août": 8, "aout": 8, "septembre": 9, "sept": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    # en
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    # de
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "juni": 6, "juli": 7, "oktober": 10, "dezember": 12, "dez": 12,
    # es
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12, "ene": 1, "dic": 12,
    # ru (nominatif + génitif)
    "январь": 1, "января": 1, "февраль": 2, "февраля": 2, "март": 3, "марта": 3, "апрель": 4, "апреля": 4, "май": 5, "мая": 5,
    "июнь": 6, "июня": 6, "июль": 7, "июля": 7, "август": 8, "августа": 8, "сентябрь": 9, "сентября": 9, "октябрь": 10, "октября": 10,
    "ноябрь": 11, "ноября": 11, "декабрь": 12, "декабря": 12,
}

ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$")
YMD_SLASH_RE = re.compile(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})$")
DMY_LIKE_RE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
YM_RE = re.compile(r"^(\d{4})[-/](\d{1,2})$")
MY_RE = re.compile(r"^(\d{1,2})[/.](\d{4})$")
ZH_RE = re.compile(r"^(\d{4})年(\d{1,2})月(?:(\d{1,2})日)?$")
NAMED_RE = re.compile(r"^(?:(\d{1,2})\.?\s+)?([^\W\d_]+)\.?,?\s+(?:(\d{1,2}),?\s+)?(\d{4})$", re.UNICODE)


def _mk(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def _year(y: str) -> int:
    v = int(y)
    return 2000 + v if len(y) == 2 and v < 70 else (1900 + v if len(y) == 2 else v)


def date_shape(text: str) -> str | None:
    """Classe la forme d'une date : 'iso', 'ymd', 'dm_or_md', 'ym', 'zh', 'named' ou None."""
    t = text.strip()
    if ISO_RE.match(t):
        return "iso"
    if YMD_SLASH_RE.match(t):
        return "ymd"
    if DMY_LIKE_RE.match(t):
        return "dm_or_md"
    if YM_RE.match(t) or MY_RE.match(t):
        return "ym"
    if ZH_RE.match(t):
        return "zh"
    m = NAMED_RE.match(t)
    if m and m.group(2).lower() in MONTHS:
        return "named"
    return None


def parse_date(text: str, order: str | None) -> dt.date | None:
    """order ∈ {'DMY','MDY'} pour les formes jj/mm ambiguës."""
    t = text.strip()
    m = ISO_RE.match(t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = YMD_SLASH_RE.match(t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = DMY_LIKE_RE.match(t)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        if order == "MDY":
            return _mk(y, a, b)
        if order == "DMY":
            return _mk(y, b, a)
        return None
    m = YM_RE.match(t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), 1)
    m = MY_RE.match(t)
    if m:
        return _mk(int(m.group(2)), int(m.group(1)), 1)
    m = ZH_RE.match(t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    m = NAMED_RE.match(t)
    if m:
        month = MONTHS.get(m.group(2).lower())
        if month:
            day = int(m.group(1) or m.group(3) or 1)
            return _mk(int(m.group(4)), month, day)
    return None


def infer_date_order(values: list[str]) -> tuple[str | None, bool, list[str]]:
    """Pour les formes jj/mm ou mm/jj : retourne (ordre, ambigu, exemples)."""
    dmy_possible = mdy_possible = True
    ambiguous_examples: list[str] = []
    any_pair = False
    for v in values:
        m = DMY_LIKE_RE.match(v.strip())
        if not m:
            continue
        any_pair = True
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        if _mk(y, b, a) is None:
            dmy_possible = False
        if _mk(y, a, b) is None:
            mdy_possible = False
        if a != b and a <= 12 and b <= 12 and len(ambiguous_examples) < 3 and v.strip() not in ambiguous_examples:
            ambiguous_examples.append(v.strip())
    if not any_pair:
        return None, False, []
    if dmy_possible and mdy_possible:
        if ambiguous_examples:
            return "DMY", True, ambiguous_examples
        return "DMY", False, []
    if dmy_possible:
        return "DMY", False, []
    if mdy_possible:
        return "MDY", False, []
    return None, False, []


TRUE_WORDS = {"true", "vrai", "oui", "yes", "y", "ja", "sí", "si", "да", "是", "1"}
FALSE_WORDS = {"false", "faux", "non", "no", "n", "nein", "нет", "否", "0"}


def parse_bool(text: str) -> bool | None:
    t = text.strip().lower()
    if t in TRUE_WORDS:
        return True
    if t in FALSE_WORDS:
        return False
    return None


def has_leading_zero_id(text: str) -> bool:
    t = text.strip()
    return len(t) >= 2 and t.startswith("0") and t.isdigit()
