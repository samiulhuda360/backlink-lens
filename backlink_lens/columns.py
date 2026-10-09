"""Detect which spreadsheet column holds which backlink metric.

Every SEO tool exports backlinks with its own headers ("Domain rating", "DR", "Domain Authority",
"Linking Root Domains" ...). This module maps those headers onto a small set of canonical fields, using
an alias table first and a fuzzy match second, and reports how sure it is so an AI model or a person
can take over the doubtful ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Canonical field -> (label, required, aliases). Aliases are compared after normalise_header().
FIELDS: dict[str, tuple[str, bool, tuple[str, ...]]] = {
    "referring_page": (
        "Referring page",
        False,
        (
            "referring page url",
            "referring page",
            "source url",
            "source page",
            "backlink url",
            "link url",
            "linking page",
            "from url",
            "page url",
            "url from",
            "source",
        ),
    ),
    "referring_domain": (
        "Referring domain",
        False,
        (
            "referring domain",
            "source domain",
            "linking domain",
            "linking site",
            "root domain",
            "domain",
            "site",
            "from domain",
        ),
    ),
    "domain_rating": (
        "Domain rating",
        True,
        (
            "domain rating",
            "dr",
            "da",
            "domain authority",
            "domain trust",
            "authority score",
            "domain score",
            "trust flow",
            "domain strength",
            "site authority",
            "page authority",
            "pa",
            "ur",
            "url rating",
        ),
    ),
    "referring_domains": (
        "Referring domains",
        True,
        (
            "referring domains",
            "ref domains",
            "rd",
            "linking root domains",
            "linking domains",
            "root domains",
            "ref doms",
            "domains linking",
            "number of referring domains",
            "rdoms",
        ),
    ),
    "target_url": (
        "Target URL",
        True,
        (
            "target url",
            "target",
            "target page",
            "destination url",
            "destination",
            "linked page",
            "url to",
            "to url",
            "link target",
            "landing page",
        ),
    ),
    "anchor": ("Anchor text", False, ("anchor", "anchor text", "link text", "anchor label")),
    "first_seen": (
        "First seen",
        False,
        ("first seen", "first found", "discovered", "found on", "date found", "first indexed", "first detected"),
    ),
    "link_type": ("Link type", False, ("type", "link type", "nofollow", "follow", "rel", "link attributes")),
}

REQUIRED = tuple(name for name, (_, required, _) in FIELDS.items() if required)
CONFIDENT = 0.85
UNCERTAIN = 0.6


def normalise_header(header: str) -> str:
    """Lower-case, drop punctuation and collapse spaces: "Domain Rating (DR)" -> "domain rating dr"."""
    text = re.sub(r"[^a-z0-9]+", " ", header.lower())
    return " ".join(text.split())


def _score(header: str, alias: str) -> float:
    if header == alias:
        return 1.0
    words = set(header.split())
    alias_words = set(alias.split())
    if words and alias_words and alias_words <= words:
        # "domain rating dr" contains the alias "domain rating"; longer aliases cover more of the header
        return 0.85 + 0.1 * len(alias_words) / len(words)
    return SequenceMatcher(None, header, alias).ratio()


def match_header(header: str) -> tuple[str | None, float]:
    """Return the best canonical field for one header and the match score (0..1)."""
    norm = normalise_header(header)
    if not norm:
        return None, 0.0
    best: tuple[str | None, float] = (None, 0.0)
    for name, (_, _, aliases) in FIELDS.items():
        for alias in aliases:
            score = _score(norm, alias)
            if score > best[1]:
                best = (name, score)
    return best


@dataclass
class Mapping:
    """Which header feeds each canonical field, and how that decision was made."""

    fields: dict[str, str] = field(default_factory=dict)  # canonical field -> header
    confidence: dict[str, float] = field(default_factory=dict)
    source: dict[str, str] = field(default_factory=dict)  # "alias" | "fuzzy" | "ai" | "user"
    unmapped: list[str] = field(default_factory=list)  # headers nothing claimed
    uncertain: list[str] = field(default_factory=list)  # headers matched below CONFIDENT

    @property
    def missing_required(self) -> list[str]:
        return [name for name in REQUIRED if name not in self.fields]

    def header_for(self, name: str) -> str | None:
        return self.fields.get(name)

    def to_dict(self) -> dict[str, object]:
        return {
            "fields": dict(self.fields),
            "confidence": dict(self.confidence),
            "source": dict(self.source),
            "unmapped": list(self.unmapped),
            "uncertain": list(self.uncertain),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Mapping:
        fields = data.get("fields")
        confidence = data.get("confidence")
        source = data.get("source")
        return cls(
            fields=dict(fields) if isinstance(fields, dict) else {},
            confidence=dict(confidence) if isinstance(confidence, dict) else {},
            source=dict(source) if isinstance(source, dict) else {},
            unmapped=list(data.get("unmapped", [])),  # type: ignore[call-overload]
            uncertain=list(data.get("uncertain", [])),  # type: ignore[call-overload]
        )


def detect_mapping(headers: list[str]) -> Mapping:
    """Map a header row onto canonical fields. Each field takes its best header; the rest are unmapped."""
    scored: list[tuple[float, str, str]] = []
    for header in headers:
        name, score = match_header(header)
        if name is not None and score >= UNCERTAIN:
            scored.append((score, header, name))
    scored.sort(key=lambda item: (-item[0], headers.index(item[1])))

    mapping = Mapping()
    taken: set[str] = set()
    for score, header, name in scored:
        if name in mapping.fields or header in taken:
            continue
        mapping.fields[name] = header
        mapping.confidence[name] = round(score, 3)
        mapping.source[name] = "alias" if score >= 0.9 else "fuzzy"
        taken.add(header)
        if score < CONFIDENT:
            mapping.uncertain.append(header)
    mapping.unmapped = [h for h in headers if h not in taken and h.strip()]
    return mapping


def apply_overrides(mapping: Mapping, overrides: dict[str, str], headers: list[str]) -> Mapping:
    """Apply user (or AI) choices: field -> header. An empty header clears the field."""
    for name, header in overrides.items():
        if name not in FIELDS:
            continue
        if not header:
            mapping.fields.pop(name, None)
            mapping.confidence.pop(name, None)
            mapping.source.pop(name, None)
            continue
        if header not in headers:
            continue
        for other, used in list(mapping.fields.items()):
            if used == header and other != name:
                del mapping.fields[other]
                mapping.confidence.pop(other, None)
                mapping.source.pop(other, None)
        mapping.fields[name] = header
        mapping.confidence[name] = 1.0
        mapping.source[name] = "user"
    taken = set(mapping.fields.values())
    mapping.unmapped = [h for h in headers if h not in taken and h.strip()]
    mapping.uncertain = [h for n, h in mapping.fields.items() if mapping.confidence.get(n, 0.0) < CONFIDENT]
    return mapping
