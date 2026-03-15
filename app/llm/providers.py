from __future__ import annotations

import json
import ssl
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib import request

import certifi
import httpx


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...
    def analyze_image(self, image_path: str, prompt: str) -> str: ...
    def transcribe_audio(self, audio_path: str) -> str: ...


@dataclass
class MockLLMClient:
    def complete(self, prompt: str) -> str:
        return f"[MOCK_LLM]\n{prompt[:1200]}"

    def analyze_image(self, image_path: str, prompt: str) -> str:
        return f"[MOCK_IMAGE_ANALYSIS] path={image_path}"

    def transcribe_audio(self, audio_path: str) -> str:
        return f"[MOCK_AUDIO_TRANSCRIPT] path={audio_path}"


@dataclass
class OpenAIClient:
    api_key: str
    model: str
    transcribe_model: str = "gpt-4o-mini-transcribe"

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

    def analyze_image(self, image_path: str, prompt: str) -> str:
        try:
            image_bytes = Path(image_path).read_bytes()
            data_url = f"data:image/jpeg;base64,{b64encode(image_bytes).decode('utf-8')}"
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            body = {
                "model": self.model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": data_url},
                        ],
                    }
                ],
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
            with request.urlopen(req, timeout=60, context=ssl_ctx) as resp:  # nosec B310
                payload = json.loads(resp.read().decode("utf-8"))
            output = payload.get("output", [])
            for item in output:
                for c in item.get("content", []):
                    if c.get("type") == "output_text" and c.get("text"):
                        return c["text"]
            return json.dumps(payload)
        except Exception as exc:
            return f"[LLM_FALLBACK:image_error={type(exc).__name__}] {image_path}"

    def transcribe_audio(self, audio_path: str) -> str:
        try:
            with httpx.Client(timeout=120.0, verify=certifi.where()) as client:
                with open(audio_path, "rb") as handle:
                    response = client.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        data={"model": self.transcribe_model},
                        files={"file": (Path(audio_path).name, handle, "application/octet-stream")},
                    )
                response.raise_for_status()
                payload = response.json()
            return payload.get("text", json.dumps(payload))
        except Exception as exc:
            return f"[LLM_FALLBACK:audio_error={type(exc).__name__}] {audio_path}"


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

    def analyze_image(self, image_path: str, prompt: str) -> str:
        return f"[LLM_FALLBACK:gemini_image_unsupported] {image_path}"

    def transcribe_audio(self, audio_path: str) -> str:
        return f"[LLM_FALLBACK:gemini_audio_unsupported] {audio_path}"
