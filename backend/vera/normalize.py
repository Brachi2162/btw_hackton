"""Field normalization and validation (spec 5). Pure functions, no I/O.

Each `norm_*` returns a `Norm`: the normalized value (or None when the text
can't be normalized at all) and the failed checks as reason codes. A value
that normalizes but fails a check is kept: it goes to review, it is never
silently fixed (iron rule 1).
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

# Commitment numbers are text, not numbers: digits, sometimes English letters
# around them (Tamar: "can contain English letters"). Digits are required, so
# a word such as "BIOHAZARD" is never a commitment number.
DEFAULT_COMMITMENT_RE = r"^[A-Z]{0,3}\d{4,12}[A-Z]{0,2}$"
CENTRAL_RE = r"^\d{6}$"
# Hebrew/Latin letters, spaces, hyphen, apostrophe/geresh (ג', צ').
NAME_RE = re.compile(r"^[A-Za-zא-ת][A-Za-zא-ת '\-׳״\"]{0,29}$")
HEBREW_RE = re.compile(r"[א-ת]")
LATIN_RE = re.compile(r"[A-Za-z]")


# Invisible direction marks that come with copied Hebrew text.
_BIDI = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")

# What a reader writes instead of a value it couldn't read. It is not a
# value: the field stays empty and flagged (iron rule 1).
PLACEHOLDERS = {
    "לא קריא",
    "לא ברור",
    "לא ידוע",
    "לא רשום",
    "לא מופיע",
    "לא צוין",
    "אין",
    "ריק",
    "unknown",
    "illegible",
    "unreadable",
    "unclear",
    "n/a",
    "na",
    "none",
    "null",
    "?",
    "??",
    "???",
}


def is_placeholder(raw: str) -> bool:
    v = _BIDI.sub("", raw or "").strip(" .,:;|_-()[]\"'").lower()
    return v in PLACEHOLDERS


@dataclass
class Norm:
    value: str | None
    problems: list[str] = field(default_factory=list)  # reason codes
    rule: str = ""  # human-readable rule that failed, for the explanation


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def israeli_id_ok(nine: str) -> bool:
    total = 0
    for i, ch in enumerate(nine):
        d = int(ch) * (1 if i % 2 == 0 else 2)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def norm_id(raw: str) -> Norm:
    """Digits only; drop surplus leading zeros; pad to 9; check digit."""
    d = _digits(raw)
    if not d:
        return Norm(None, ["validation_failed"], "ספרות בלבד")
    if len(d) > 9:
        if set(d[: len(d) - 9]) != {"0"}:
            return Norm(d, ["validation_failed"], "עד 9 ספרות")
        d = d[-9:]
    d = d.zfill(9)
    if not d.strip("0"):  # all zeros passes the check digit, but is no ID
        return Norm(d, ["validation_failed"], "לא אפסים בלבד")
    if len(_digits(raw)) < 5:
        return Norm(d, ["validation_failed"], "לפחות 5 ספרות")
    if not israeli_id_ok(d):
        return Norm(d, ["checksum_failed"], "ספרת ביקורת")
    return Norm(d)


def norm_central(raw: str) -> Norm:
    # Screenshots: "947501-25.8" = central number + date; keep the number.
    m = re.match(r"^\s*(\d[\d\s]*\d)\s*[-–]\s*\d{1,2}[./]\d{1,2}", raw)
    d = _digits(m.group(1) if m else raw)
    if not d:
        return Norm(None, ["validation_failed"], "6 ספרות")
    if not re.match(CENTRAL_RE, d) or not d.strip("0"):
        return Norm(d, ["validation_failed"], "6 ספרות")
    return Norm(d)


def norm_commitment(raw: str, regex: str = DEFAULT_COMMITMENT_RE) -> Norm:
    """Text, never a number: keep leading zeros, upper-case the letter."""
    v = re.sub(r"[\s\-_/.]", "", _BIDI.sub("", raw)).upper()
    if not v:
        return Norm(None, ["validation_failed"], regex)
    if not re.match(regex, v):
        return Norm(v, ["validation_failed"], regex)
    return Norm(v)


def norm_name(raw: str) -> Norm:
    v = "".join(ch for ch in _BIDI.sub("", raw) if unicodedata.category(ch) != "Mn")
    v = re.sub(r"\s+", " ", v).strip(" .,:;|_-")
    if not v:
        return Norm(None, ["validation_failed"], "אותיות")
    if not NAME_RE.match(v):
        return Norm(v, ["validation_failed"], "אותיות, רווח, מקף, גרש; עד 30")
    # A name is written in one script; "Gec יצחק" is a misread of one.
    if HEBREW_RE.search(v) and LATIN_RE.search(v):
        return Norm(v, ["validation_failed"], "עברית או לטינית, לא שתיהן")
    return Norm(v)


_DATE = re.compile(r"(\d{1,2})\s*[./\-]\s*(\d{1,2})(?:\s*[./\-]\s*(\d{2,4}))?")
# Year first (2026-08-25), as a model or a system export may write it.
_ISO = re.compile(r"\b(\d{4})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{1,2})\b")


def parse_date(raw: str, ref: date) -> date | None:
    """Day-first Israeli dates: 25/08/2026, 27-08-2026, 27.8.26, 25.8.
    A missing year is the reference year, or the year before if that would
    put the date after the reference. Year-first dates are read as such."""
    iso = _ISO.search(raw)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    m = _DATE.search(raw)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
    if year is None:
        y = ref.year
    else:
        y = int(year)
        if y < 100:
            y += 2000
    try:
        d = date(y, month, day)
    except ValueError:
        return None
    if year is None and d > ref:
        try:
            d = date(y - 1, month, day)
        except ValueError:
            return None
    return d


def norm_date(raw: str, ref: date, max_age_days: int) -> Norm:
    d = parse_date(raw, ref)
    if d is None:
        return Norm(None, ["validation_failed"], "dd/mm/yyyy")
    iso = d.isoformat()
    if d > ref:
        return Norm(iso, ["date_out_of_range"], "לא בעתיד")
    if (ref - d).days > max_age_days:
        return Norm(iso, ["date_out_of_range"], f"עד {max_age_days} יום אחורה")
    return Norm(iso)


def _key(text: str) -> str:
    return re.sub(r"[\s\"'״׳.\-()]", "", text).lower()


class SenderIndex:
    """Map free text to a sender id through names and aliases (spec 3.5)."""

    def __init__(self, senders: list[dict]):
        self.by_id = {s["id"]: s for s in senders}
        pairs = []
        for s in senders:
            for alias in [s["name"], *s.get("aliases", [])]:
                if _key(alias):
                    pairs.append((_key(alias), s["id"]))
        # Longest alias first: "אסותא אשדוד" before "אסותא".
        self._aliases = sorted(pairs, key=lambda p: len(p[0]), reverse=True)

    def match(self, text: str) -> str | None:
        k = _key(text)
        if not k:
            return None
        if k in self.by_id:
            return k
        hits = {sid for alias, sid in self._aliases if alias in k or k in alias}
        if not hits:
            return None
        # The most specific institution wins over its parent (שניידר > כללית).
        children = {h for h in hits if self.by_id[h].get("parent") in hits}
        hits = (hits - {self.by_id[c]["parent"] for c in children}) or hits
        return sorted(hits)[0] if len(hits) == 1 else None

    def norm(self, raw: str) -> Norm:
        sid = self.match(raw)
        if sid is None:
            return Norm(None, ["validation_failed"], "רשימת השולחים")
        return Norm(sid)

    def name(self, sid: str) -> str:
        return self.by_id[sid]["name"]

    def has_children(self, sid: str) -> bool:
        return any(s.get("parent") == sid for s in self.by_id.values())

    def parent(self, sid: str) -> str | None:
        return self.by_id[sid].get("parent")
