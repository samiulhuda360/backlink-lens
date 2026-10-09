"""Group clean backlinks into rating and referring-domain bands, and compare competitors."""

from __future__ import annotations

from dataclasses import dataclass

from .cleaning import CleanResult, Row

INF = float("inf")
DR_BANDS: tuple[tuple[float, float], ...] = tuple((lo, lo + 9 if lo else 10) for lo in (0, 11, 21, 31, 41, 51, 61, 71, 81, 91))
RD_BANDS: tuple[tuple[float, float], ...] = (*tuple((lo, lo + 99) for lo in range(1, 1000, 100)), (1001, INF))


def band_label(band: tuple[float, float]) -> str:
    lo, hi = band
    return f"{lo:g}+" if hi == INF else f"{lo:g}-{hi:g}"


def band_of(value: float | None, bands: tuple[tuple[float, float], ...]) -> tuple[float, float] | None:
    if value is None:
        return None
    for lo, hi in bands:
        if lo <= value <= hi:
            return lo, hi
    return None


@dataclass
class Profile:
    """One competitor's backlink profile, counted per band."""

    competitor: str
    rows: int
    dr_counts: list[int]
    rd_counts: list[int]
    unbanded: int
    median_rating: float
    strong_links: int  # rating 50+

    @property
    def dr_share(self) -> list[float]:
        total = sum(self.dr_counts) or 1
        return [round(100 * c / total, 1) for c in self.dr_counts]


def profile(result: CleanResult) -> Profile:
    dr_counts = [0] * len(DR_BANDS)
    rd_counts = [0] * len(RD_BANDS)
    unbanded = 0
    ratings: list[float] = []
    for row in result.rows:
        dr = band_of(row.domain_rating, DR_BANDS)
        rd = band_of(None if row.referring_domains is None else float(row.referring_domains), RD_BANDS)
        if dr is None or rd is None:
            unbanded += 1
            continue
        dr_counts[DR_BANDS.index(dr)] += 1
        rd_counts[RD_BANDS.index(rd)] += 1
        ratings.append(row.domain_rating or 0.0)
    return Profile(
        competitor=result.competitor,
        rows=len(result.rows),
        dr_counts=dr_counts,
        rd_counts=rd_counts,
        unbanded=unbanded,
        median_rating=_median(ratings),
        strong_links=sum(1 for r in ratings if r >= 50),
    )


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


@dataclass
class Comparison:
    profiles: list[Profile]
    dr_labels: list[str]
    rd_labels: list[str]
    dr_average: list[float]
    rd_average: list[float]

    def gaps(self) -> list[dict[str, object]]:
        """Bands where a competitor has clearly fewer links than the average: the link gap to close."""
        out: list[dict[str, object]] = []
        for p in self.profiles:
            for label, count, avg in zip(self.dr_labels, p.dr_counts, self.dr_average, strict=True):
                if avg >= 3 and count < 0.5 * avg:
                    out.append({"competitor": p.competitor, "band": f"rating {label}", "count": count, "average": avg})
        return out


def compare(results: list[CleanResult]) -> Comparison:
    profiles = [profile(r) for r in results]
    n = len(profiles) or 1
    dr_average = [round(sum(p.dr_counts[i] for p in profiles) / n, 1) for i in range(len(DR_BANDS))]
    rd_average = [round(sum(p.rd_counts[i] for p in profiles) / n, 1) for i in range(len(RD_BANDS))]
    return Comparison(
        profiles=profiles,
        dr_labels=[band_label(b) for b in DR_BANDS],
        rd_labels=[band_label(b) for b in RD_BANDS],
        dr_average=dr_average,
        rd_average=rd_average,
    )


def strongest(rows: list[Row], limit: int = 10) -> list[Row]:
    return sorted((r for r in rows if r.domain_rating is not None), key=lambda r: -(r.domain_rating or 0))[:limit]
