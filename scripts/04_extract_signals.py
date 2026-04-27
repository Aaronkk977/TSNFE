#!/usr/bin/env python3
"""ETL-04: Extract structured signals from transcript text files."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.extraction.schemas import TranscriptResult
from tw_analyst_pipeline.pipeline.orchestrator import SignalPipeline
from tw_analyst_pipeline.utils.config import get_pipeline_config, get_settings
from tw_analyst_pipeline.utils.logging import setup_logging

from etl_common import TZ_TAIPEI, latest_by_pattern, read_json, write_json


def _load_source_file(settings, input_file: str | None) -> Path:
    if input_file:
        path = Path(input_file)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        return path

    candidates = [
        latest_by_pattern(settings.data_metadata_dir, "transcript_status_*.json"),
        latest_by_pattern(settings.data_metadata_dir, "pending_videos_*.json"),
        settings.data_metadata_dir / "transcript_status_latest.json",
        settings.data_metadata_dir / "pending_videos_latest.json",
    ]
    for path in candidates:
        if path and path.exists():
            return path
    raise FileNotFoundError("No input metadata JSON found for extraction stage")


def _load_latest_transcript_text(settings, video_id: str) -> str:
    pattern = f"**/{video_id}_*.json"
    candidates = sorted(
        settings.data_transcripts_dir.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No transcript JSON found for video_id={video_id}")

    latest = candidates[0]
    with open(latest, "r", encoding="utf-8") as f:
        payload = json.load(f)
    transcript = TranscriptResult(**payload)
    if not transcript.text:
        raise RuntimeError(f"Transcript is empty for video_id={video_id}")
    return transcript.text


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL-04 extract signals from transcript")
    parser.add_argument("--input", type=str, default=None, help="transcript_status or pending_videos JSON path")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--llm-provider",
        type=str,
        default=None,
        choices=["openai", "anthropic", "gemini", "google", "qwen", "local_hf"],
    )
    parser.add_argument("--llm-model", type=str, default=None)
    parser.add_argument("--llm-temperature", type=float, default=None)
    parser.add_argument("--llm-max-tokens", type=int, default=None)
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    args = parser.parse_args()

    setup_logging(level=args.log_level)

    settings = get_settings()
    if args.llm_provider:
        settings.llm_provider = args.llm_provider
        try:
            settings.model_fields_set.add("llm_provider")
        except Exception:
            pass
    if args.llm_model:
        settings.llm_model = args.llm_model
        try:
            settings.model_fields_set.add("llm_model")
        except Exception:
            pass
    if args.llm_temperature is not None:
        settings.llm_temperature = args.llm_temperature
        try:
            settings.model_fields_set.add("llm_temperature")
        except Exception:
            pass
    if args.llm_max_tokens is not None:
        settings.llm_max_tokens = args.llm_max_tokens
        try:
            settings.model_fields_set.add("llm_max_tokens")
        except Exception:
            pass

    pipeline_config = get_pipeline_config()
    pipeline = SignalPipeline(settings, pipeline_config)

    source_file = _load_source_file(settings, args.input)
    payload = read_json(source_file)
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("Invalid source JSON: expected list or payload.items list")
    if args.limit > 0:
        items = items[: args.limit]

    results = []
    ok_count = 0

    for item in items:
        video_id = str(item.get("video_id", "")).strip()
        if not video_id:
            results.append({**item, "status": "invalid", "error": "missing video_id"})
            continue

        analyst_name = item.get("analyst_name")
        view_count = item.get("view_count")
        published_at = item.get("published_at")

        try:
            transcript_text = _load_latest_transcript_text(settings, video_id)
            analysis = pipeline.process_transcript(
                transcript=transcript_text,
                video_id=video_id,
                analyst_name=analyst_name,
                video_view_count=view_count,
                video_published_at=published_at,
                save_result=True,
            )
            ok_count += 1
            results.append(
                {
                    **item,
                    "status": "signals_extracted",
                    "signal_count": len(analysis.signals),
                    "processing_duration_seconds": analysis.processing_duration_seconds,
                }
            )
        except Exception as e:
            results.append({**item, "status": "extract_failed", "error": str(e)})

    timestamp = datetime.now(TZ_TAIPEI).strftime("%Y%m%d_%H%M%S")
    out_file = settings.data_metadata_dir / f"signal_status_{timestamp}.json"
    output_payload = {
        "generated_at": datetime.now(TZ_TAIPEI).isoformat(timespec="seconds"),
        "source_file": str(source_file),
        "count": len(results),
        "success_count": ok_count,
        "items": results,
    }
    write_json(out_file, output_payload)
    write_json(settings.data_metadata_dir / "signal_status_latest.json", output_payload)

    print(f"[INFO] Signal status saved: {out_file}")
    print(f"[INFO] Extracted signals: {ok_count}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
