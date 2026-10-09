"""Optional AI help through any OpenAI-compatible endpoint.

Three jobs: propose a column mapping for headers the alias table does not know, explain flagged rows in
plain words, and write a short comparison summary. Every answer is cached on disk, calls are spaced at
least 2.5 seconds apart, and without an API key every function returns None so the app works unaided.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .columns import FIELDS, Mapping

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemini-flash-lite-latest"
MIN_INTERVAL = 2.5


@dataclass
class AIConfig:
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    cache_dir: Path = Path("var/ai-cache")

    @classmethod
    def from_env(cls, cache_dir: Path | None = None) -> AIConfig:
        return cls(
            api_key=os.environ.get("AI_API_KEY", ""),
            base_url=os.environ.get("AI_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("AI_MODEL", DEFAULT_MODEL),
            cache_dir=cache_dir or Path(os.environ.get("AI_CACHE_DIR", "var/ai-cache")),
        )


class AIClient:
    """Thin client: cached, rate-limited chat completions that degrade to None."""

    def __init__(self, config: AIConfig | None = None) -> None:
        self.config = config or AIConfig.from_env()
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._client: Any = None
        self.calls = 0  # real network calls made (not cache hits)

    @property
    def enabled(self) -> bool:
        return bool(self.config.api_key)

    @property
    def label(self) -> str:
        return f"{self.config.model}" if self.enabled else "off (no AI_API_KEY)"

    def ask(self, system: str, user: str, *, json_mode: bool = False, temperature: float = 0.0) -> str | None:
        """Return the model's text, from cache when possible; None when disabled or on any error."""
        if not self.enabled:
            return None
        key = hashlib.sha256(json.dumps([self.config.model, system, user, json_mode], sort_keys=True).encode()).hexdigest()
        path = self.config.cache_dir / f"{key}.json"
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            text = cached.get("text")
            return str(text) if text is not None else None
        text = self._call(system, user, json_mode, temperature)
        if text is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"model": self.config.model, "text": text}, ensure_ascii=False), encoding="utf-8")
        return text

    def _call(self, system: str, user: str, json_mode: bool, temperature: float) -> str | None:
        with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                if self._client is None:
                    from openai import OpenAI

                    self._client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url, timeout=60)
                kwargs: dict[str, Any] = {}
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                response = self._client.chat.completions.create(
                    model=self.config.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=temperature,
                    **kwargs,
                )
                self.calls += 1
                content = response.choices[0].message.content
                return str(content).strip() if content else None
            except Exception:  # noqa: BLE001 - any provider error means "no AI help this time"
                return None
            finally:
                self._last_call = time.monotonic()


# --- tasks -------------------------------------------------------------------------------------

MAPPING_SYSTEM = (
    "You map column headers from SEO backlink exports onto canonical fields. Answer with a JSON object whose "
    "keys are canonical field names and whose values are the exact header text, or omit a field when no header "
    "fits. Never invent headers. Canonical fields:\n" + "\n".join(f"- {name}: {label}" for name, (label, _, _) in FIELDS.items())
)


def propose_mapping(client: AIClient, headers: list[str], sample: list[dict[str, str]], mapping: Mapping) -> Mapping:
    """Ask the model about headers the heuristics left unmapped or uncertain. Fills gaps only."""
    candidates = [h for h in headers if h in mapping.unmapped or h in mapping.uncertain]
    wanted = [name for name in FIELDS if name not in mapping.fields or mapping.fields[name] in mapping.uncertain]
    if not candidates or not wanted or not client.enabled:
        return mapping
    rows = [{h: r.get(h, "")[:60] for h in candidates} for r in sample[:3]]
    user = json.dumps({"headers": candidates, "fields_to_fill": wanted, "sample_rows": rows}, ensure_ascii=False)
    text = client.ask(MAPPING_SYSTEM, user, json_mode=True)
    proposal = _parse_json(text)
    for name, header in proposal.items():
        if name in wanted and isinstance(header, str) and header in candidates:
            for other, used in list(mapping.fields.items()):
                if used == header and other != name:  # the model moved an uncertain header to a better field
                    del mapping.fields[other]
                    mapping.confidence.pop(other, None)
                    mapping.source.pop(other, None)
            mapping.fields[name] = header
            mapping.confidence[name] = 0.8
            mapping.source[name] = "ai"
    taken = set(mapping.fields.values())
    mapping.unmapped = [h for h in headers if h not in taken and h.strip()]
    mapping.uncertain = [h for h in mapping.uncertain if h not in taken]
    return mapping


EXPLAIN_SYSTEM = (
    "You are an SEO analyst explaining backlink audit findings to a business owner. For each flagged domain, "
    "write one plain-English sentence on what the flag means for them and whether to ignore, keep or investigate "
    "the link. Answer as a JSON object: domain -> sentence. No jargon, no markdown."
)


def explain_flags(client: AIClient, flags: list[dict[str, object]], limit: int = 12) -> dict[str, str]:
    """Plain-language explanations for the first `limit` distinct flagged domains. Empty when AI is off."""
    if not client.enabled or not flags:
        return {}
    seen: dict[str, list[str]] = {}
    for flag in flags:
        domain = str(flag.get("domain") or "")
        if domain and (len(seen) < limit or domain in seen):
            seen.setdefault(domain, []).append(f"{flag.get('code')}: {flag.get('message')}")
    if not seen:
        return {}
    text = client.ask(EXPLAIN_SYSTEM, json.dumps(seen, ensure_ascii=False), json_mode=True)
    return {k: str(v) for k, v in _parse_json(text).items() if k in seen}


SUMMARY_SYSTEM = (
    "You summarise a backlink comparison for a marketing manager in at most 120 words of plain English: who has "
    "the strongest profile, where the biggest gaps are, and one concrete next step. No markdown, no headings."
)


def summarise(client: AIClient, comparison_table: dict[str, object]) -> str | None:
    if not client.enabled:
        return None
    return client.ask(SUMMARY_SYSTEM, json.dumps(comparison_table, ensure_ascii=False), temperature=0.2)


def _parse_json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[4:] if cleaned.startswith("json") else cleaned
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
