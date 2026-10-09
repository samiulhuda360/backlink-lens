"""Geometry for the grouped bar charts drawn as inline SVG in the report page (no JavaScript needed)."""

from __future__ import annotations

from dataclasses import dataclass

PALETTE = ("#1f3a3d", "#b9672c", "#5b8a72", "#8c6d3f", "#4a6b8a")


@dataclass
class Bar:
    x: float
    y: float
    width: float
    height: float
    colour: str
    label: str
    value: int


@dataclass
class Chart:
    width: int
    height: int
    bars: list[Bar]
    ticks: list[tuple[float, str]]  # (x, band label)
    gridlines: list[tuple[float, str]]  # (y, value label)
    legend: list[tuple[str, str]]  # (series name, colour)


def band_chart(labels: list[str], series: list[tuple[str, list[int]]], width: int = 720, height: int = 260) -> Chart:
    left, right, top, bottom = 44, 12, 12, 40
    plot_w, plot_h = width - left - right, height - top - bottom
    peak = max((v for _, values in series for v in values), default=0) or 1
    step = _nice_step(peak)
    top_value = step * (peak // step + 1)
    group_w = plot_w / max(len(labels), 1)
    gap = group_w * 0.2
    bar_w = (group_w - gap) / max(len(series), 1)
    bars: list[Bar] = []
    for s_index, (name, values) in enumerate(series):
        colour = PALETTE[s_index % len(PALETTE)]
        for b_index, value in enumerate(values):
            h = plot_h * value / top_value
            bars.append(
                Bar(
                    x=round(left + b_index * group_w + gap / 2 + s_index * bar_w, 1),
                    y=round(top + plot_h - h, 1),
                    width=round(bar_w, 1),
                    height=round(h, 1),
                    colour=colour,
                    label=f"{name}: {value} in band {labels[b_index]}",
                    value=value,
                )
            )
    ticks = [(round(left + i * group_w + group_w / 2, 1), label) for i, label in enumerate(labels)]
    gridlines = []
    value = 0
    while value <= top_value:
        gridlines.append((round(top + plot_h - plot_h * value / top_value, 1), f"{value:g}"))
        value += step
    legend = [(name, PALETTE[i % len(PALETTE)]) for i, (name, _) in enumerate(series)]
    return Chart(width=width, height=height, bars=bars, ticks=ticks, gridlines=gridlines, legend=legend)


def _nice_step(peak: int) -> int:
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000, 2000, 5000):
        if peak / step <= 6:
            return step
    return 10_000
