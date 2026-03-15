from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path

import httpx


DEFAULT_TEXT = """而家市場變化好快，
好多人開始重新思考一個問題……

我嘅資金，
除咗儲蓄之外，
仲可唔可以有更好嘅規劃？

最近推出咗一個
保險投資計劃。

佢結合咗
資產增長潛力，
同埋基本人壽保障。

幫你為未來，
建立一個
更加穩定嘅財務基礎。

計劃設計亦都比較靈活，
可以根據你嘅
財務目標、預算，
同埋風險承受程度
去做調整。

如果你想了解
呢個計劃
係唔係適合你……

歡迎聯絡我，
我可以幫你做一個
簡單嘅保障同財務分析。"""

DEFAULT_INSTRUCTIONS = (
    "Speak in natural Cantonese for a Hong Kong audience with a warm, trustworthy, "
    "professional tone. Use a measured pace suited for a short insurance explainer."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate TTS audio with the OpenAI speech API.")
    parser.add_argument(
        "--output",
        default="data/tts/insurance_plan_cantonese.mp3",
        help="Output audio path.",
    )
    parser.add_argument(
        "--voice",
        default="coral",
        help="Voice name for the speech model.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        help="Speech model to use.",
    )
    parser.add_argument(
        "--format",
        default="mp3",
        choices=["mp3", "wav", "opus", "aac", "flac", "pcm"],
        help="Audio format.",
    )
    parser.add_argument(
        "--instructions",
        default=DEFAULT_INSTRUCTIONS,
        help="Delivery instructions for the voice model.",
    )
    parser.add_argument(
        "--text-file",
        help="Optional UTF-8 text file to read instead of the built-in script.",
    )
    parser.add_argument(
        "--print-request",
        action="store_true",
        help="Print the API request metadata before sending it.",
    )
    return parser.parse_args()


def load_text(text_file: str | None) -> str:
    if not text_file:
        return DEFAULT_TEXT
    return Path(text_file).read_text(encoding="utf-8").strip()


def main() -> int:
    args = parse_args()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")

    text = load_text(args.text_file)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "model": args.model,
        "voice": args.voice,
        "input": text,
        "instructions": args.instructions,
        "response_format": args.format,
    }

    if args.print_request:
        print(json.dumps({k: v for k, v in payload.items() if k != "input"}, ensure_ascii=False, indent=2))
        print(f"input_length={len(text)}")

    mime_type = mimetypes.guess_type(f"file.{args.format}")[0] or "application/octet-stream"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": mime_type,
    }

    with httpx.Client(timeout=120.0) as client:
        response = client.post("https://api.openai.com/v1/audio/speech", headers=headers, json=payload)
        response.raise_for_status()
        output_path.write_bytes(response.content)

    print(f"Saved TTS audio to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
