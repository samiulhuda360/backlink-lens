"""Score column detection and cleaning on the labelled sets in evaluation/data (all invented data).

    python -m backlink_lens evaluate            # heuristics only; add AI_API_KEY to score the AI step too

Writes evaluation/results.json and prints the same numbers.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import cast

from backlink_lens.ai import AIClient, propose_mapping
from backlink_lens.cleaning import clean_table
from backlink_lens.columns import UNCERTAIN, detect_mapping, match_header
from backlink_lens.readers import read_file

DATA = Path(__file__).resolve().parent / "data"
RESULTS = Path(__file__).resolve().parent / "results.json"


def score_headers(client: AIClient | None) -> dict[str, object]:
    labelled = json.loads((DATA / "headers.json").read_text(encoding="utf-8"))
    heuristic_hits = 0
    combined_hits = 0
    ai_calls = 0
    misses: list[dict[str, object]] = []
    for item in labelled:
        header, truth = item["header"], item["field"]
        name, score = match_header(header)
        guess = name if score >= UNCERTAIN else None
        heuristic_hits += guess == truth
        final = guess
        if client is not None and client.enabled and (guess is None or score < 0.85):
            mapping = detect_mapping([header])
            mapping = propose_mapping(client, [header], [], mapping)
            ai_calls += 1
            final = next((f for f, h in mapping.fields.items() if h == header), None)
        combined_hits += final == truth
        if final != truth:
            misses.append({"header": header, "expected": truth, "heuristic": guess, "final": final})
    total = len(labelled)
    ai_answered = client is not None and client.enabled and any(client.config.cache_dir.glob("*.json"))
    return {
        "headers": total,
        "heuristic_accuracy": round(heuristic_hits / total, 3),
        # Only reported when the model actually answered (cached or live); quota errors fall back silently.
        "with_ai_accuracy": round(combined_hits / total, 3) if ai_answered else None,
        "ai_headers_asked": ai_calls,
        "misses": misses,
    }


def score_cleaning() -> dict[str, object]:
    table = read_file(DATA / "messy.csv")
    mapping = detect_mapping(table.headers)
    result = clean_table(table, mapping)
    with (DATA / "messy_labels.csv").open(encoding="utf-8") as fh:
        labels = {int(r["row"]): r for r in csv.DictReader(fh)}
    by_row = {row.index: row for row in result.rows}
    all_rows = {row.index for row in result.rows} | {c.row for c in result.changes if c.field == "row"}

    value_hits = value_total = 0
    kept_hits = 0
    expected_flags: set[tuple[int, str]] = set()
    for number, label in labels.items():
        kept = number in by_row
        kept_hits += kept == (label["kept"] == "yes")
        for code in filter(None, label["flags"].split(";")):
            expected_flags.add((number, code))
        if not kept:
            continue
        row = by_row[number]
        for want, got in (
            (label["domain"], row.referring_domain),
            (label["rating"], "" if row.domain_rating is None else f"{row.domain_rating:g}"),
            (label["rd"], "" if row.referring_domains is None else str(row.referring_domains)),
        ):
            value_total += 1
            value_hits += want == got
    found_flags = {(f.row, f.code) for f in result.flags}
    tp = len(found_flags & expected_flags)
    precision = tp / len(found_flags) if found_flags else 1.0
    recall = tp / len(expected_flags) if expected_flags else 1.0
    return {
        "rows": len(table.rows),
        "labelled_rows": len(labels),
        "rows_seen": len(all_rows),
        "competitor": result.competitor,
        "value_accuracy": round(value_hits / value_total, 3),
        "values_checked": value_total,
        "dedupe_accuracy": round(kept_hits / len(labels), 3),
        "duplicates_found": result.duplicates,
        "empty_rows_dropped": result.dropped_empty,
        "flag_precision": round(precision, 3),
        "flag_recall": round(recall, 3),
        "flags_expected": len(expected_flags),
        "flags_found": len(found_flags),
        "flag_errors": sorted(f"{r}:{c}" for r, c in (found_flags ^ expected_flags)),
    }


def main() -> int:
    client = AIClient()
    cd = score_headers(client)
    cl = score_cleaning()
    results = {"ai": client.label, "column_detection": cd, "cleaning": cl}
    RESULTS.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"AI: {client.label}")
    print(f"Column detection on {cd['headers']} labelled headers: heuristics {cd['heuristic_accuracy']:.1%}", end="")
    if cd["with_ai_accuracy"] is not None:
        print(f", heuristics + AI {cd['with_ai_accuracy']:.1%} ({cd['ai_headers_asked']} headers sent to the model)")
    else:
        print("  (AI step not scored: no key, or the model did not answer)" if client.enabled else "")
    for miss in cast(list[dict[str, object]], cd["misses"]):
        print(f"  miss: {miss['header']!r} expected {miss['expected']} got {miss['final']}")
    print(
        f"Cleaning on {cl['labelled_rows']} labelled rows: values {cl['value_accuracy']:.1%} of {cl['values_checked']}, "
        f"de-duplication {cl['dedupe_accuracy']:.1%}, flags precision {cl['flag_precision']:.1%} recall {cl['flag_recall']:.1%}"
    )
    for err in cast(list[str], cl["flag_errors"]):
        print(f"  flag mismatch: {err}")
    print(f"written: {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
