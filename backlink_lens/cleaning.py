"""Turn a raw backlink export into clean, de-duplicated rows, with a change log and flagged rows."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .columns import Mapping
from .readers import Table

NULL_WORDS = {"", "-", "—", "–", "n/a", "na", "none", "null", "nan", "unknown", "?"}
SUSPICIOUS_TLDS = {"xyz", "top", "click", "icu", "buzz", "work", "loan", "win", "gq", "cf", "tk", "ml", "ga", "zip"}
SPAM_WORDS = ("casino", "pills", "payday", "viagra", "betting", "free-seo", "buy-links", "cheap-", "replica")
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "ref", "mc_cid", "mc_eid")
DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d %b %Y", "%b %d, %Y", "%d-%m-%Y", "%Y/%m/%d")


@dataclass
class Row:
    index: int  # 1-based row number in the source file (excluding the header)
    referring_domain: str = ""
    referring_page: str = ""
    domain_rating: float | None = None
    referring_domains: int | None = None
    target_url: str = ""
    anchor: str = ""
    first_seen: str = ""
    link_type: str = ""


@dataclass
class Change:
    row: int
    field: str
    before: str
    after: str
    reason: str


@dataclass
class Flag:
    row: int
    domain: str
    code: str
    message: str
    severity: str  # "warn" | "info"


@dataclass
class CleanResult:
    name: str
    competitor: str
    rows: list[Row]
    changes: list[Change] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    source_rows: int = 0
    dropped_empty: int = 0
    duplicates: int = 0

    @property
    def flagged_rows(self) -> int:
        return len({f.row for f in self.flags})

    def summary(self) -> dict[str, object]:
        return {
            "name": self.name,
            "competitor": self.competitor,
            "source_rows": self.source_rows,
            "clean_rows": len(self.rows),
            "dropped_empty": self.dropped_empty,
            "duplicates": self.duplicates,
            "changes": len(self.changes),
            "flags": len(self.flags),
            "flagged_rows": self.flagged_rows,
        }

    def flags_as_dicts(self) -> list[dict[str, object]]:
        return [asdict(f) for f in self.flags]


# --- value normalisers -------------------------------------------------------------------------


def parse_number(text: str) -> float | None:
    """'1,234' -> 1234, '12.5' -> 12.5, '1.2k' -> 1200, '45%' -> 45, 'n/a' -> None."""
    value = text.strip().lower().replace(" ", "")
    if value in NULL_WORDS:
        return None
    multiplier = 1.0
    if value.endswith("k"):
        multiplier, value = 1_000.0, value[:-1]
    elif value.endswith("m"):
        multiplier, value = 1_000_000.0, value[:-1]
    value = value.rstrip("%").replace(",", "")
    try:
        return float(value) * multiplier
    except ValueError:
        return None


def domain_of(text: str) -> str:
    """Host name without scheme, 'www.', port, path or trailing dot, lower-cased."""
    value = text.strip().lower()
    if not value or value in NULL_WORDS:
        return ""
    if "://" not in value:
        value = "http://" + value
    host = urlsplit(value).hostname or ""
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def normalise_url(text: str) -> str:
    """Lower-case host, drop fragments and tracking parameters, strip a trailing slash."""
    value = text.strip()
    if not value or value.lower() in NULL_WORDS:
        return ""
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    host = (parts.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith(TRACKING_PARAMS)]
    path = parts.path.rstrip("/") or ""
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))


def normalise_date(text: str) -> str:
    value = text.strip()
    if not value or value.lower() in NULL_WORDS:
        return ""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def normalise_link_type(text: str) -> str:
    value = text.strip().lower()
    if not value or value in NULL_WORDS:
        return ""
    if "nofollow" in value or value in {"no", "false", "0"}:
        return "nofollow"
    if "ugc" in value:
        return "ugc"
    if "sponsored" in value:
        return "sponsored"
    if "follow" in value or value in {"yes", "true", "1", "do"}:
        return "dofollow"
    return value


# --- the cleaning pass -------------------------------------------------------------------------


def clean_table(table: Table, mapping: Mapping) -> CleanResult:
    """Normalise every mapped value, drop empty and duplicate rows, and flag the suspicious ones."""
    rows: list[Row] = []
    changes: list[Change] = []
    dropped_empty = 0

    def get(raw: dict[str, str], name: str) -> str:
        header = mapping.header_for(name)
        return raw.get(header, "") if header else ""

    for number, raw in enumerate(table.rows, start=1):
        row = Row(index=number)
        row.referring_page = _norm(number, "referring_page", get(raw, "referring_page"), normalise_url, changes, "URL normalised")
        domain_text = get(raw, "referring_domain") or row.referring_page
        row.referring_domain = _norm(number, "referring_domain", domain_text, domain_of, changes, "domain extracted")
        if not row.referring_domain and not row.referring_page and not get(raw, "domain_rating"):
            dropped_empty += 1
            continue
        row.domain_rating = _number(number, "domain_rating", get(raw, "domain_rating"), changes)
        rd = _number(number, "referring_domains", get(raw, "referring_domains"), changes)
        row.referring_domains = None if rd is None else round(rd)
        row.target_url = _norm(number, "target_url", get(raw, "target_url"), normalise_url, changes, "URL normalised")
        row.anchor = " ".join(get(raw, "anchor").split())
        row.first_seen = _norm(number, "first_seen", get(raw, "first_seen"), normalise_date, changes, "date normalised to ISO")
        row.link_type = _norm(number, "link_type", get(raw, "link_type"), normalise_link_type, changes, "link type normalised")
        rows.append(row)

    rows, duplicates = _dedupe(rows, changes)
    competitor = _competitor(rows)
    flags = [flag for row in rows for flag in flag_row(row, competitor)]
    return CleanResult(
        name=table.name,
        competitor=competitor,
        rows=rows,
        changes=changes,
        flags=flags,
        source_rows=len(table.rows),
        dropped_empty=dropped_empty,
        duplicates=duplicates,
    )


def _norm(number: int, name: str, text: str, fn: Callable[[str], str], changes: list[Change], reason: str) -> str:
    after = fn(text)
    if text.strip() and after != text.strip():
        changes.append(Change(number, name, text, after, reason))
    return after


def _number(number: int, name: str, text: str, changes: list[Change]) -> float | None:
    value = parse_number(text)
    if value is None:
        if text.strip():
            changes.append(Change(number, name, text, "", "not a number, left blank"))
        return None
    if name == "domain_rating":
        clamped = min(max(value, 0.0), 100.0)
        if clamped != value:
            changes.append(Change(number, name, text, str(clamped), "rating clamped to 0-100"))
        value = clamped
    shown = str(int(value)) if value.is_integer() else str(value)
    if shown != text.strip():
        changes.append(Change(number, name, text, shown, "number parsed"))
    return value


def _dedupe(rows: list[Row], changes: list[Change]) -> tuple[list[Row], int]:
    """One row per referring domain: keep the strongest (highest rating), log the ones dropped."""
    best: dict[str, Row] = {}
    for row in rows:
        key = row.referring_domain or f"__row{row.index}"
        kept = best.get(key)
        if kept is None:
            best[key] = row
        elif (row.domain_rating or 0) > (kept.domain_rating or 0):
            changes.append(Change(kept.index, "row", kept.referring_domain, "", f"duplicate of row {row.index} (lower rating), dropped"))
            best[key] = row
        else:
            changes.append(Change(row.index, "row", row.referring_domain, "", f"duplicate of row {kept.index}, dropped"))
    kept_rows = sorted(best.values(), key=lambda r: r.index)
    return kept_rows, len(rows) - len(kept_rows)


def _competitor(rows: list[Row]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        host = domain_of(row.target_url)
        if host:
            counts[host] = counts.get(host, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else "unknown"


def flag_row(row: Row, competitor: str) -> list[Flag]:
    """Rule-based checks. Each flag carries a sentence a non-expert can act on."""
    flags: list[Flag] = []
    domain = row.referring_domain

    def add(code: str, message: str, severity: str = "warn") -> None:
        flags.append(Flag(row.index, domain, code, message, severity))

    if row.domain_rating is None or row.referring_domains is None:
        add("missing_metrics", "Rating or referring-domain count is missing, so this row cannot be placed in a band.")
    elif row.domain_rating == 0 and row.referring_domains == 0:
        add("no_authority", "Rating 0 with no referring domains: probably a brand-new or parked site.", "info")
    elif row.domain_rating >= 70 and row.referring_domains < 10:
        add("implausible", f"Rating {row.domain_rating:g} is very high for only {row.referring_domains} referring domains; check the export.")
    elif row.domain_rating < 10 and row.referring_domains > 5000:
        add("implausible", f"{row.referring_domains} referring domains but a rating of {row.domain_rating:g}; the metrics disagree.")
    if domain:
        tld = domain.rsplit(".", 1)[-1]
        if tld in SUSPICIOUS_TLDS:
            add("suspicious_tld", f"The .{tld} ending is common among throw-away link farms.")
        if any(word in domain for word in SPAM_WORDS):
            add("spam_words", "The domain name contains words typical of spam networks.")
        label = domain.split(".")[0]
        if label.count("-") >= 3 or (len(label) > 30) or re.fullmatch(r"[a-z]*\d{4,}[a-z]*", label):
            add("odd_name", "Unusually long, hyphen-heavy or number-heavy domain name; often auto-generated.", "info")
        if competitor != "unknown" and (domain == competitor or domain.endswith("." + competitor)):
            add("self_link", "The link comes from the competitor's own site, so it adds no outside authority.", "info")
    if row.link_type in {"nofollow", "ugc", "sponsored"}:
        add("not_followed", f"Marked {row.link_type}: search engines give it little or no weight.", "info")
    return flags
