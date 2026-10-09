"""The whole run for a set of exports: read, map columns, clean, band, compare, add AI notes."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ai as ai_tasks
from .ai import AIClient
from .bands import Comparison, compare
from .cleaning import CleanResult, clean_table
from .columns import Mapping, apply_overrides, detect_mapping
from .readers import Table


@dataclass
class FileAnalysis:
    table: Table
    mapping: Mapping
    result: CleanResult | None = None  # None when a required column is missing

    @property
    def ready(self) -> bool:
        return not self.mapping.missing_required


@dataclass
class Analysis:
    files: list[FileAnalysis]
    comparison: Comparison
    summary: str | None = None
    explanations: dict[str, str] = field(default_factory=dict)
    ai_label: str = ""

    @property
    def results(self) -> list[CleanResult]:
        return [f.result for f in self.files if f.result is not None]

    @property
    def mappings(self) -> dict[str, Mapping]:
        return {f.table.name: f.mapping for f in self.files}

    @property
    def flags(self) -> list[dict[str, object]]:
        return [{"file": r.name, "competitor": r.competitor, **flag} for r in self.results for flag in r.flags_as_dicts()]


def map_columns(table: Table, client: AIClient | None = None, overrides: dict[str, str] | None = None) -> Mapping:
    """Heuristics first, then the model for what is left, then the user's explicit choices."""
    mapping = detect_mapping(table.headers)
    if client is not None and client.enabled and (mapping.unmapped or mapping.uncertain):
        mapping = ai_tasks.propose_mapping(client, table.headers, table.rows, mapping)
    if overrides:
        mapping = apply_overrides(mapping, overrides, table.headers)
    return mapping


def analyse(tables: list[Table], client: AIClient | None = None, overrides: dict[str, dict[str, str]] | None = None, *, narrate: bool = True) -> Analysis:
    files: list[FileAnalysis] = []
    for table in tables:
        mapping = map_columns(table, client, (overrides or {}).get(table.name))
        item = FileAnalysis(table=table, mapping=mapping)
        if item.ready:
            item.result = clean_table(table, mapping)
        files.append(item)
    results = [f.result for f in files if f.result is not None]
    analysis = Analysis(files=files, comparison=compare(results), ai_label=client.label if client else "off")
    if client is not None and client.enabled and narrate and results:
        analysis.explanations = ai_tasks.explain_flags(client, analysis.flags)
        analysis.summary = ai_tasks.summarise(client, comparison_table(analysis.comparison))
    return analysis


def comparison_table(comparison: Comparison) -> dict[str, object]:
    return {
        "rating_bands": comparison.dr_labels,
        "competitors": [
            {
                "competitor": p.competitor,
                "clean_links": p.rows,
                "median_rating": p.median_rating,
                "links_rated_50_plus": p.strong_links,
                "rating_band_counts": p.dr_counts,
                "referring_domain_band_counts": p.rd_counts,
            }
            for p in comparison.profiles
        ],
        "rating_band_average": comparison.dr_average,
        "gaps": comparison.gaps(),
    }
