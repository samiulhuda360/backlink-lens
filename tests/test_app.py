import io
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from backlink_lens.ai import AIClient, AIConfig
from backlink_lens.app import create_app

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"

TOOL_C = (
    "Linking Site\tTrust Score\tSites Linking In\tPage To\tRel\n"
    "quiet.com\t45\t1200\thttps://acme.com/?utm_source=x\t\n"
    "spam-casino-now.xyz\t2\t3\thttps://acme.com/\trel=nofollow\n"
)


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    flask_app = create_app(var_dir=tmp_path, ai=AIClient(AIConfig(api_key="", cache_dir=tmp_path / "cache")))
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def upload(client: FlaskClient, files: list[tuple[str, bytes]]) -> str:
    data = {"files": [(io.BytesIO(content), name) for name, content in files]}
    response = client.post("/upload", data=data, content_type="multipart/form-data")
    assert response.status_code == 302
    location = response.headers["Location"]
    return location.split("/jobs/")[1].split("/")[0]


def test_home_page_lists_samples_and_ai_state(client: FlaskClient) -> None:
    page = client.get("/").get_data(as_text=True)
    assert "Backlink Lens" in page
    assert "acme-outdoors_tool-a.csv" in page
    assert "off (no AI_API_KEY)" in page


def test_upload_without_files_flashes_an_error(client: FlaskClient) -> None:
    response = client.post("/upload", data={}, content_type="multipart/form-data", follow_redirects=True)
    assert "Choose at least one export file" in response.get_data(as_text=True)


def test_unreadable_file_is_rejected(client: FlaskClient) -> None:
    data = {"files": [(io.BytesIO(b"not a workbook"), "bad.xlsx")]}
    response = client.post("/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert "not a readable workbook" in response.get_data(as_text=True)


def test_sample_runs_end_to_end(client: FlaskClient) -> None:
    response = client.post("/sample")
    job_id = response.headers["Location"].split("/jobs/")[1].split("/")[0]
    columns = client.get(f"/jobs/{job_id}/columns").get_data(as_text=True)
    assert "known header" in columns
    assert "summit-supply_tool-c.tsv" in columns
    report = client.get(f"/jobs/{job_id}/report").get_data(as_text=True)
    assert "acme-outdoors.com" in report and "northwind-gear.co" in report
    assert "was skipped" in report  # tool C needs a person (or the AI) without a key
    assert "<svg" in report
    xlsx = client.get(f"/jobs/{job_id}/report.xlsx")
    assert xlsx.status_code == 200 and xlsx.data[:2] == b"PK"
    csv_text = client.get(f"/jobs/{job_id}/changes.csv").get_data(as_text=True)
    assert csv_text.startswith("file,competitor,row,field,before,after,reason")
    summary = client.get(f"/jobs/{job_id}/summary.json").get_json()
    assert summary["ai"] == "off (no AI_API_KEY)"
    assert {c["competitor"] for c in summary["competitors"]} == {"acme-outdoors.com", "northwind-gear.co"}


def test_user_can_fix_the_mapping_and_get_a_report(client: FlaskClient) -> None:
    job_id = upload(client, [("tool-c.tsv", TOOL_C.encode())])
    response = client.get(f"/jobs/{job_id}/report", follow_redirects=True)
    assert "No file has all three required columns" in response.get_data(as_text=True)
    form = {"tool-c.tsv::domain_rating": "Trust Score", "tool-c.tsv::referring_domains": "Sites Linking In", "tool-c.tsv::target_url": "Page To"}
    client.post(f"/jobs/{job_id}/columns", data=form)
    report = client.get(f"/jobs/{job_id}/report").get_data(as_text=True)
    assert "acme.com" in report
    assert "suspicious_tld" in report
    columns = client.get(f"/jobs/{job_id}/columns").get_data(as_text=True)
    assert "you</td>" in columns


def test_unknown_job_is_404(client: FlaskClient) -> None:
    assert client.get("/jobs/doesnotexist/report").status_code == 404
    assert client.get("/jobs/../etc/report").status_code == 404


def test_healthz(client: FlaskClient) -> None:
    assert client.get("/healthz").get_json()["status"] == "ok"
