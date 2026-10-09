"""Command line: serve the web app, run the demo, clean files to a workbook, or run the evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ai import AIClient, AIConfig
from .pipeline import analyse
from .readers import read_file
from .report import build_workbook, changes_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backlink_lens", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--var", default="var", help="folder for uploads and the AI cache")

    demo = sub.add_parser("demo", help="start the web app and open the bundled sample straight away")
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=5000)
    demo.add_argument("--var", default="var")

    clean = sub.add_parser("clean", help="clean exports and write the comparison workbook")
    clean.add_argument("files", nargs="+", type=Path)
    clean.add_argument("--out", type=Path, default=Path("backlink-comparison.xlsx"))
    clean.add_argument("--changes", type=Path, help="also write the change log as CSV")
    clean.add_argument("--json", action="store_true", help="print the summary as JSON")
    clean.add_argument("--no-ai", action="store_true", help="never call the model, even with a key")

    sub.add_parser("evaluate", help="score column detection and cleaning on the labelled sets")

    args = parser.parse_args(argv)
    if args.command in {"serve", "demo"}:
        return _serve(args.host, args.port, Path(args.var), demo=args.command == "demo")
    if args.command == "clean":
        return _clean(args)
    from evaluation.evaluate import main as evaluate_main

    return evaluate_main()


def _serve(host: str, port: int, var: Path, *, demo: bool) -> int:
    from .app import create_app

    client = AIClient(AIConfig.from_env(cache_dir=var / "ai-cache"))
    ai_label = client.label
    app = create_app(var_dir=var, ai=client)
    url = f"http://{host}:{port}/"
    print(f"Backlink Lens at {url}  (AI: {ai_label})", flush=True)
    if demo:
        print("Demo: press 'Run the sample' on the start page, or open the sample job directly:", flush=True)
        with app.test_client() as web:
            location = web.post("/sample").headers.get("Location", "")
        print(f"  {url.rstrip('/')}{location}", flush=True)
    app.run(host=host, port=port, debug=False)
    return 0


def _clean(args: argparse.Namespace) -> int:
    tables = [read_file(path) for path in args.files]
    client = None if args.no_ai else AIClient()
    analysis = analyse(tables, client)
    for item in analysis.files:
        if not item.ready:
            print(f"{item.table.name}: missing required columns {item.mapping.missing_required}; headers: {item.table.headers}", file=sys.stderr)
    if not analysis.results:
        return 1
    args.out.write_bytes(build_workbook(analysis.results, analysis.comparison, analysis.mappings, analysis.summary or ""))
    if args.changes:
        args.changes.write_text(changes_csv(analysis.results), encoding="utf-8")
    if args.json:
        print(json.dumps({"files": [r.summary() for r in analysis.results], "summary": analysis.summary}, indent=1))
    else:
        for result in analysis.results:
            s = result.summary()
            print(
                f"{s['name']}: {s['competitor']}  rows {s['source_rows']} -> {s['clean_rows']}  "
                f"(duplicates {s['duplicates']}, empty {s['dropped_empty']}, changes {s['changes']}, flagged rows {s['flagged_rows']})"
            )
        print(f"workbook: {args.out}")
        if analysis.summary:
            print(f"\n{analysis.summary}")
    return 0
