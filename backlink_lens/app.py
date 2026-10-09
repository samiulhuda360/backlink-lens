"""Flask web app: upload exports, review the column mapping, read the comparison, download the workbook."""

from __future__ import annotations

import io
import json
import os
import secrets
import shutil
from pathlib import Path

from flask import Flask, Response, abort, flash, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename
from werkzeug.wrappers import Response as BaseResponse

from .ai import AIClient, AIConfig
from .chart import band_chart
from .columns import FIELDS, REQUIRED
from .pipeline import Analysis, analyse, map_columns
from .readers import ReadError, Table, read_file
from .report import build_workbook, changes_csv

MAX_FILES = 5
MAX_BYTES = 8 * 1024 * 1024
PACKAGE_DIR = Path(__file__).resolve().parent
SAMPLE_DIR = PACKAGE_DIR.parent / "sample_data"


def create_app(var_dir: Path | None = None, ai: AIClient | None = None) -> Flask:
    app = Flask(__name__, template_folder=str(PACKAGE_DIR / "templates"), static_folder=str(PACKAGE_DIR / "static"))
    var = var_dir or Path(os.environ.get("BACKLINK_LENS_VAR", "var"))
    jobs_dir = var / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(16),
        MAX_CONTENT_LENGTH=MAX_FILES * MAX_BYTES,
        JOBS_DIR=jobs_dir,
    )
    client = ai or AIClient(AIConfig.from_env(cache_dir=var / "ai-cache"))

    def job_dir(job_id: str) -> Path:
        if not job_id.isalnum():
            abort(404)
        path = jobs_dir / job_id
        if not path.is_dir():
            abort(404)
        return path

    def load_tables(path: Path) -> list[Table]:
        tables: list[Table] = []
        for file in sorted((path / "files").iterdir()):
            table = read_file(file)
            table.name = file.name[3:]  # drop the "01-" ordering prefix
            tables.append(table)
        return tables

    def load_overrides(path: Path) -> dict[str, dict[str, str]]:
        meta = path / "mapping.json"
        if not meta.exists():
            return {}
        data = json.loads(meta.read_text(encoding="utf-8"))
        return {str(k): {str(f): str(h) for f, h in v.items()} for k, v in data.items()}

    def new_job(sources: list[tuple[str, bytes]]) -> str:
        job_id = secrets.token_hex(6)
        files = jobs_dir / job_id / "files"
        files.mkdir(parents=True)
        for index, (name, data) in enumerate(sources, start=1):
            safe = secure_filename(name) or f"file{index}.csv"
            (files / f"{index:02d}-{safe}").write_bytes(data)
        return job_id

    @app.get("/")
    def index() -> str:
        samples = sorted(p.name for p in SAMPLE_DIR.glob("*.*")) if SAMPLE_DIR.is_dir() else []
        return render_template("index.html", samples=samples, ai_label=client.label, max_files=MAX_FILES)

    @app.post("/upload")
    def upload() -> BaseResponse:
        uploads = [f for f in request.files.getlist("files") if f.filename]
        if not uploads:
            flash("Choose at least one export file.", "error")
            return redirect(url_for("index"))
        if len(uploads) > MAX_FILES:
            flash(f"Up to {MAX_FILES} files at a time.", "error")
            return redirect(url_for("index"))
        sources: list[tuple[str, bytes]] = []
        for upload_file in uploads:
            data = upload_file.read()
            if len(data) > MAX_BYTES:
                flash(f"{upload_file.filename} is larger than {MAX_BYTES // (1024 * 1024)} MB.", "error")
                return redirect(url_for("index"))
            sources.append((upload_file.filename or "export.csv", data))
        job_id = new_job(sources)
        try:
            load_tables(job_dir(job_id))
        except ReadError as exc:
            shutil.rmtree(jobs_dir / job_id, ignore_errors=True)
            flash(str(exc), "error")
            return redirect(url_for("index"))
        return redirect(url_for("columns", job_id=job_id))

    @app.post("/sample")
    def sample() -> BaseResponse:
        if not SAMPLE_DIR.is_dir():
            abort(404)
        sources = [(p.name, p.read_bytes()) for p in sorted(SAMPLE_DIR.glob("*.*"))]
        return redirect(url_for("columns", job_id=new_job(sources)))

    @app.get("/jobs/<job_id>/columns")
    def columns(job_id: str) -> str:
        path = job_dir(job_id)
        analysis = analyse(load_tables(path), client, load_overrides(path), narrate=False)
        labels = {name: label for name, (label, _, _) in FIELDS.items()}
        return render_template("columns.html", job_id=job_id, analysis=analysis, fields=labels, required=REQUIRED, ai_label=client.label)

    @app.post("/jobs/<job_id>/columns")
    def save_columns(job_id: str) -> BaseResponse:
        path = job_dir(job_id)
        # Keep only the choices that differ from the automatic mapping, so "decided by" stays truthful.
        automatic = {t.name: map_columns(t, client).fields for t in load_tables(path)}
        overrides = load_overrides(path)
        for key, value in request.form.items():
            if "::" not in key:
                continue
            file_name, field_name = key.split("::", 1)
            if field_name not in FIELDS or file_name not in automatic:
                continue
            if value != automatic[file_name].get(field_name, ""):
                overrides.setdefault(file_name, {})[field_name] = value
            else:
                overrides.get(file_name, {}).pop(field_name, None)
        (path / "mapping.json").write_text(json.dumps(overrides, ensure_ascii=False, indent=1), encoding="utf-8")
        return redirect(url_for("report", job_id=job_id))

    def run(job_id: str) -> Analysis:
        path = job_dir(job_id)
        return analyse(load_tables(path), client, load_overrides(path))

    @app.get("/jobs/<job_id>/report")
    def report(job_id: str) -> str | BaseResponse:
        analysis = run(job_id)
        if not analysis.results:
            flash("No file has all three required columns yet. Pick them below.", "error")
            return redirect(url_for("columns", job_id=job_id))
        comparison = analysis.comparison
        return render_template(
            "report.html",
            job_id=job_id,
            analysis=analysis,
            comparison=comparison,
            dr_chart=band_chart(comparison.dr_labels, [(p.competitor, p.dr_counts) for p in comparison.profiles]),
            rd_chart=band_chart(comparison.rd_labels, [(p.competitor, p.rd_counts) for p in comparison.profiles]),
            flags=sorted(analysis.flags, key=lambda f: f["severity"] != "warn")[:60],  # warnings first
            ai_label=client.label,
        )

    @app.get("/jobs/<job_id>/report.xlsx")
    def download(job_id: str) -> BaseResponse:
        analysis = run(job_id)
        data = build_workbook(analysis.results, analysis.comparison, analysis.mappings, analysis.summary or "")
        return send_file(io.BytesIO(data), as_attachment=True, download_name="backlink-comparison.xlsx")

    @app.get("/jobs/<job_id>/changes.csv")
    def changes(job_id: str) -> Response:
        analysis = run(job_id)
        return Response(changes_csv(analysis.results), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=change-log.csv"})

    @app.get("/jobs/<job_id>/summary.json")
    def summary_json(job_id: str) -> Response:
        analysis = run(job_id)
        payload = {
            "files": [r.summary() for r in analysis.results],
            "competitors": [p.__dict__ for p in analysis.comparison.profiles],
            "rating_bands": analysis.comparison.dr_labels,
            "referring_domain_bands": analysis.comparison.rd_labels,
            "gaps": analysis.comparison.gaps(),
            "ai": analysis.ai_label,
            "summary": analysis.summary,
        }
        return Response(json.dumps(payload, ensure_ascii=False, indent=1), mimetype="application/json")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "ai": client.label}

    return app
