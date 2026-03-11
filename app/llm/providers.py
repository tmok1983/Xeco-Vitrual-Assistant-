from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from typing import Protocol
from urllib import request

import certifi


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass
class MockLLMClient:
    def complete(self, prompt: str) -> str:
        return f"[MOCK_LLM]\n{prompt[:1200]}"


@dataclass
class OpenAIClient:
    api_key: str
    model: str

    def complete(self, prompt: str) -> str:
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            body = {
                "model": self.model,
                "input": prompt,
            }
            req = request.Request(
                url="https://api.openai.com/v1/responses",
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(body).encode("utf-8"),
            )
            with request.urlopen(req, timeout=30, context=ssl_ctx) as resp:  # nosec B310
                payload = json.loads(resp.read().decode("utf-8"))

            output = payload.get("output", [])
            for item in output:
                for c in item.get("content", []):
                    if c.get("type") == "output_text" and c.get("text"):
                        return c["text"]
            return json.dumps(payload)
        except Exception as exc:
            return f"[LLM_FALLBACK:openai_error={type(exc).__name__}] {prompt[:1200]}"


@dataclass
class GeminiClient:
    api_key: str
    model: str

    def complete(self, prompt: str) -> str:
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
                f"?key={self.api_key}"
            )
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2},
            }
            req = request.Request(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps(body).encode("utf-8"),
            )
            with request.urlopen(req, timeout=30, context=ssl_ctx) as resp:  # nosec B310
                payload = json.loads(resp.read().decode("utf-8"))

            candidates = payload.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and parts[0].get("text"):
                    return parts[0]["text"]
            return json.dumps(payload)
        except Exception as exc:
            return f"[LLM_FALLBACK:gemini_error={type(exc).__name__}] {prompt[:1200]}"
