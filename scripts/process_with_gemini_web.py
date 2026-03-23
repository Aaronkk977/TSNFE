#!/usr/bin/env python3
"""Process a YouTube URL via Gemini Web (@YouTube) and save JSON output.

Usage:
  python scripts/process_with_gemini_web.py "https://www.youtube.com/watch?v=..."
"""

import argparse
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.extraction.gemini_web_client import GeminiWebClient


DEFAULT_PROMPT = """
請分析此投顧影片，輸出 JSON 陣列，每筆需包含：
- ticker
- stock_name
- action（buy/sell/hold）
- sentiment_score（0-10）
- urgency（0-10）
- label（買進/賣出/中立（持有）/模糊）
- label_reason
- reasoning

規則：
1) 全量列出影片中提及的可交易台股/ETF/槓反ETF。
2) 若態度不明，label 必須是模糊。
3) 僅輸出 JSON，不要多餘說明文字。
""".strip()


def extract_video_id(video_url: str) -> str:
    if "v=" in video_url:
        return video_url.split("v=")[1].split("&")[0]
    if "youtu.be/" in video_url:
        return video_url.split("youtu.be/")[1].split("?")[0]
    return "unknown_video"


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemini web automation @YouTube extractor")
    parser.add_argument("video_url", help="YouTube video URL")
    parser.add_argument(
        "--prompt",
        type=str,
        default=DEFAULT_PROMPT,
        help="Prompt text to send after @YouTube <url>",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=240,
        help="Wait timeout seconds for Gemini response",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (default false for easier first-time login)",
    )
    parser.add_argument(
        "--profile-dir",
        type=str,
        default="./data/browser_profile",
        help="Persistent browser profile directory",
    )
    parser.add_argument(
        "--cdp-url",
        type=str,
        default=None,
        help="Attach to existing Chrome via CDP, e.g. http://127.0.0.1:9222",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path (default: data/signals/<video_id>_web.json)",
    )
    args = parser.parse_args()

    video_id = extract_video_id(args.video_url)
    output_path = Path(args.output) if args.output else Path("data/signals") / f"{video_id}_web.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    client = GeminiWebClient(
        user_data_dir=Path(args.profile_dir),
        headless=args.headless,
        cdp_url=args.cdp_url,
    )

    try:
        data = client.run_youtube_prompt(
            video_url=args.video_url,
            prompt=args.prompt,
            timeout_seconds=args.timeout,
        )

        payload = {
            "video_id": video_id,
            "source": "gemini_web_automation",
            "video_url": args.video_url,
            "signals": data if isinstance(data, list) else [data],
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        print(f"✓ Saved: {output_path}")
        print(f"✓ Signals: {len(payload['signals'])}")
        return 0

    except Exception as e:
        print(f"✗ Failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
