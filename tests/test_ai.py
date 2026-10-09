"""The AI layer is tested with a fake transport: no network, no key, and the cache and spacing are checked."""

from pathlib import Path
from typing import Any

import pytest

from backlink_lens import ai
from backlink_lens.ai import AIClient, AIConfig, explain_flags, propose_mapping, summarise
from backlink_lens.columns import detect_mapping
from backlink_lens.pipeline import analyse
from backlink_lens.readers import Table


class FakeCompletions:
    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        text = self.answers.pop(0)
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": text})()})()]})()


def fake_client(tmp_path: Path, answers: list[str]) -> tuple[AIClient, FakeCompletions]:
    client = AIClient(AIConfig(api_key="test-key-not-real", cache_dir=tmp_path / "cache"))
    completions = FakeCompletions(answers)
    client._client = type("OpenAI", (), {"chat": type("Chat", (), {"completions": completions})()})()
    return client, completions


def test_disabled_without_key(tmp_path: Path) -> None:
    client = AIClient(AIConfig(api_key="", cache_dir=tmp_path))
    assert not client.enabled
    assert client.ask("s", "u") is None
    assert summarise(client, {}) is None
    assert explain_flags(client, [{"domain": "x.com", "code": "c", "message": "m"}]) == {}


def test_answers_are_cached_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai, "MIN_INTERVAL", 0.0)
    client, completions = fake_client(tmp_path, ["first answer"])
    assert client.ask("system", "user") == "first answer"
    assert client.ask("system", "user") == "first answer"  # second call served from the cache
    assert len(completions.calls) == 1
    assert client.calls == 1
    assert list((tmp_path / "cache").glob("*.json"))


def test_calls_are_spaced_apart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(ai.time, "sleep", slept.append)
    client, _ = fake_client(tmp_path, ["a", "b"])
    client.ask("s", "u1")
    client.ask("s", "u2")
    assert slept and 0 < slept[-1] <= ai.MIN_INTERVAL


def test_provider_errors_degrade_to_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai, "MIN_INTERVAL", 0.0)
    client, _ = fake_client(tmp_path, [])  # popping from an empty list raises inside create()
    assert client.ask("s", "u") is None
    assert not list((tmp_path / "cache").glob("*.json"))


def test_propose_mapping_fills_gaps_and_moves_uncertain_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai, "MIN_INTERVAL", 0.0)
    headers = ["Linking Site", "Trust Score", "Sites Linking In", "Page To", "Category"]
    mapping = detect_mapping(headers)
    assert "referring_domains" in mapping.missing_required
    answer = '{"domain_rating": "Trust Score", "referring_domains": "Sites Linking In", "target_url": "Page To", "anchor": "Made Up"}'
    client, completions = fake_client(tmp_path, [answer])
    mapping = propose_mapping(client, headers, [{"Trust Score": "45"}], mapping)
    assert mapping.missing_required == []
    assert mapping.fields["target_url"] == "Page To"
    assert mapping.source["referring_domains"] == "ai"
    assert "anchor" not in mapping.fields  # invented header rejected
    assert "Category" in mapping.unmapped
    assert completions.calls[0]["response_format"] == {"type": "json_object"}


def test_analyse_with_ai_adds_summary_and_explanations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai, "MIN_INTERVAL", 0.0)
    headers = ["Referring page URL", "DR", "RD", "Target URL"]
    table = Table("a.csv", headers, [dict(zip(headers, r, strict=True)) for r in [["https://spam-casino.xyz/", "2", "3", "https://acme.com/"]]])
    client, _ = fake_client(tmp_path, ['{"spam-casino.xyz": "Ignore this one."}', "Acme has a thin profile."])
    analysis = analyse([table], client)
    assert analysis.summary == "Acme has a thin profile."
    assert analysis.explanations == {"spam-casino.xyz": "Ignore this one."}
    assert analysis.ai_label == "gemini-flash-lite-latest"


def test_parse_json_accepts_fenced_blocks() -> None:
    assert ai._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ai._parse_json("not json") == {}
    assert ai._parse_json("[1, 2]") == {}
