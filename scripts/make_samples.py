"""Generate the bundled sample exports: three invented competitors, three invented tool formats.

Everything is made up (domains, metrics, dates). Run from the repo root:  python scripts/make_samples.py
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).resolve().parent.parent / "sample_data"
ADJECTIVES = [
    "quiet",
    "bright",
    "northern",
    "copper",
    "maple",
    "cedar",
    "silver",
    "granite",
    "harbour",
    "meadow",
    "summit",
    "valley",
    "ember",
    "willow",
    "lantern",
]
NOUNS = ["journal", "review", "notes", "digest", "gazette", "weekly", "tribune", "ledger", "almanac", "compass", "atlas", "field", "trail", "cabin", "outpost"]
TLDS = ["com"] * 8 + ["net", "org", "co", "io", "blog", "info"]
SPAM = ["best-casino-bonus-now.xyz", "cheap-pills-express.top", "seo1234567.click", "payday-loan-fast-cash-today.icu", "replica-watch-deals.buzz"]


def domain(rng: random.Random) -> str:
    return f"{rng.choice(ADJECTIVES)}{rng.choice(NOUNS)}.{rng.choice(TLDS)}"


def metrics(rng: random.Random, strength: float) -> tuple[int, int]:
    """Rating and referring domains that roughly go together; `strength` shifts the whole profile."""
    rating = int(min(100, max(0, rng.gauss(30 + 25 * strength, 18))))
    rd = int(max(0, rng.gauss(rating * 22, rating * 9 + 20)))
    return rating, rd


def rows_for(rng: random.Random, competitor: str, n: int, strength: float) -> list[dict[str, object]]:
    pages = ["", "/", "/products", "/blog/best-gear", "/about", "/guides/packing-list"]
    rows: list[dict[str, object]] = []
    for _ in range(n):
        d = domain(rng)
        rating, rd = metrics(rng, strength)
        rows.append(
            {
                "domain": d,
                "page": f"https://{d}/{rng.choice(['posts', 'reviews', 'links', 'resources'])}/{rng.randint(10, 999)}",
                "rating": rating,
                "rd": rd,
                "target": f"https://{competitor}{rng.choice(pages)}",
                "anchor": rng.choice([competitor.split(".", maxsplit=1)[0], "this guide", "click here", "outdoor gear", "read more", ""]),
                "date": (rng.randint(2023, 2026), rng.randint(1, 12), rng.randint(1, 28)),
                "follow": rng.random() > 0.22,
            }
        )
    # messiness: duplicates with www and different paths, spam domains, a self-link and implausible metrics
    for _ in range(4):
        dup = dict(rng.choice(rows))
        dup["page"] = str(dup["page"]).replace("https://", "https://www.").replace("/posts/", "/archive/")
        dup["rating"] = max(0, int(dup["rating"]) - rng.randint(0, 5))  # type: ignore[call-overload]
        rows.append(dup)
    for spam in rng.sample(SPAM, 3):
        rows.append(
            {
                "domain": spam,
                "page": f"http://{spam}/",
                "rating": rng.randint(0, 12),
                "rd": rng.randint(0, 40),
                "target": f"https://{competitor}/",
                "anchor": "best deals",
                "date": (2026, 1, rng.randint(1, 28)),
                "follow": True,
            }
        )
    rows.append(
        {
            "domain": "blog." + competitor,
            "page": f"https://blog.{competitor}/news/1",
            "rating": 48,
            "rd": 900,
            "target": f"https://{competitor}/",
            "anchor": "home",
            "date": (2024, 5, 2),
            "follow": True,
        }
    )
    rows.append(
        {"domain": domain(rng), "page": "", "rating": 88, "rd": 4, "target": f"https://{competitor}/", "anchor": "", "date": (2025, 7, 9), "follow": True}
    )
    rows.append(
        {"domain": domain(rng), "page": "", "rating": "n/a", "rd": "-", "target": f"https://{competitor}/about", "anchor": "about", "date": "", "follow": False}
    )
    rng.shuffle(rows)
    return rows


def tool_a(rows: list[dict[str, object]], path: Path) -> None:
    """Tool A: tidy CSV, thousands separators, decimal ratings, ISO dates."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Referring page URL", "Domain rating", "Referring domains", "Target URL", "Anchor", "First seen", "Type"])
        for r in rows:
            rd = r["rd"]
            date = r["date"]
            w.writerow(
                [
                    r["page"] or f"https://{r['domain']}/",
                    f"{r['rating']:.1f}" if isinstance(r["rating"], int) else r["rating"],
                    f"{rd:,}" if isinstance(rd, int) else rd,
                    r["target"],
                    r["anchor"],
                    f"{date[0]:04d}-{date[1]:02d}-{date[2]:02d}" if isinstance(date, tuple) else date,
                    "Dofollow" if r["follow"] else "Nofollow",
                ]
            )
        w.writerow([])  # trailing blank line, as some tools write


def tool_b(rows: list[dict[str, object]], path: Path) -> None:
    """Tool B: XLSX, 'DA' and 'Linking Root Domains', 1.2k numbers, US dates, Yes/No follow."""
    book = Workbook()
    sheet = book.worksheets[0]
    sheet.append(["Source URL", "DA", "Linking Root Domains", "Target", "Link Text", "Discovered", "Follow", "Spam Score"])
    for r in rows:
        rd = r["rd"]
        date = r["date"]
        rd_text = (f"{rd / 1000:.1f}k" if isinstance(rd, int) and rd >= 1000 else rd) if rd != "-" else "—"
        sheet.append(
            [
                r["page"] or r["domain"],
                r["rating"],
                rd_text,
                r["target"],
                r["anchor"],
                f"{date[1]:02d}/{date[2]:02d}/{date[0]:04d}" if isinstance(date, tuple) else "",
                "Yes" if r["follow"] else "No",
                random.Random(str(r["domain"])).randint(0, 30),
            ]
        )
    book.save(path)


def tool_c(rows: list[dict[str, object]], path: Path) -> None:
    """Tool C: tab-separated, unusual headers that need the fuzzy matcher, the AI or a person."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["Linking Site", "Trust Score", "Sites Linking In", "Page To", "Rel", "Found On", "Category"])
        for r in rows:
            date = r["date"]
            w.writerow(
                [
                    str(r["domain"]).upper() if random.Random(str(r["domain"])).random() < 0.2 else r["domain"],
                    r["rating"],
                    r["rd"],
                    str(r["target"]) + "?utm_source=tool-c",
                    "" if r["follow"] else "rel=nofollow",
                    f"{date[2]:02d}.{date[1]:02d}.{date[0]:04d}" if isinstance(date, tuple) else "",
                    "outdoor",
                ]
            )


def main() -> None:
    OUT.mkdir(exist_ok=True)
    tool_a(rows_for(random.Random(1), "acme-outdoors.com", 120, 0.9), OUT / "acme-outdoors_tool-a.csv")
    tool_b(rows_for(random.Random(2), "northwind-gear.co", 95, 0.4), OUT / "northwind-gear_tool-b.xlsx")
    tool_c(rows_for(random.Random(3), "summit-supply.net", 70, 0.1), OUT / "summit-supply_tool-c.tsv")
    print(f"wrote 3 sample files to {OUT}")


if __name__ == "__main__":
    main()
