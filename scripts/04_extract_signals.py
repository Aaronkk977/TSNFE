#!/usr/bin/env python3
"""ETL-04: Extract structured signals from transcript text files."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import opencc

from tw_analyst_pipeline.extraction.llm_client import LLMExtractorFactory
from tw_analyst_pipeline.extraction.schemas import (
    RecommendationFeature,
    RecommendationStock,
    TranscriptResult,
    VideoAnalysis,
    normalize_label,
)
from tw_analyst_pipeline.stock_data.validators import StockValidator
from tw_analyst_pipeline.utils.config import get_pipeline_config, get_settings
from tw_analyst_pipeline.utils.logging import setup_logging

from etl_common import TZ_TAIPEI, latest_by_pattern, read_json, write_json


def _load_items(settings, input_file: str | None) -> list:
    if input_file:
        return read_json(Path(input_file)).get("items", [])
    
    registry_path = settings.data_metadata_dir / "video_registry.json"
    if registry_path.exists():
        import json
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        
        items = [
            v
            for k, v in registry.items()
            if v.get("status") in ("transcribed", "extract_failed")
        ]
        return items
        
    return []


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


def _get_existing_signal(settings, video_id: str) -> Path | None:
    pattern = f"**/{video_id}_*.json"
    candidates = list(settings.data_signals_dir.glob(pattern))
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _convert_to_traditional(text: str) -> str:
    converter = opencc.OpenCC("s2twp")
    return converter.convert(text or "")


def _action_to_label(action) -> str:
    action_value = getattr(action, "value", action)
    return {
        "buy": "買進",
        "sell": "賣出",
        "hold": "中立",
        "unknown": "中立",
    }.get(str(action_value).strip().lower(), "")


def _update_registry(settings, video_id: str, updates: dict) -> None:
    registry_path = settings.data_metadata_dir / "video_registry.json"
    if not registry_path.exists():
        return

    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)

        if video_id in registry:
            registry[video_id].update(updates)
            write_json(registry_path, registry)
    except Exception as e:
        print(f"[ERROR] Failed to update registry for {video_id}: {e}")


def _build_recommendation_feature(analysis: VideoAnalysis) -> RecommendationFeature:
    recommended = []
    for signal in analysis.signals:
        action_label = _action_to_label(signal.action)
        stock_label = action_label or signal.normalized_label or normalize_label(signal.implied_label)
        recommended.append(
            RecommendationStock(
                stock_code=signal.stock_code,
                stock_name=signal.stock_name,
                label=stock_label,
            )
        )

    return RecommendationFeature(
        timestamp=analysis.processed_at,
        view_count=analysis.video_view_count or 0,
        recommended_stocks=recommended,
    )


def _save_analysis(settings, analysis: VideoAnalysis) -> Path:
    folder_date = analysis.processed_at
    if analysis.video_published_at:
        try:
            published_at = analysis.video_published_at.strip()
            if published_at.endswith("+00:00Z"):
                published_at = published_at[:-1]
            elif published_at.endswith("Z"):
                published_at = published_at[:-1]
            dt = datetime.fromisoformat(published_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            folder_date = dt.astimezone(TZ_TAIPEI)
        except Exception:
            pass

    output_dir = settings.data_signals_dir / os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "history") / folder_date.strftime("%Y-%m-%d")
    output_file = output_dir / f"{analysis.video_id}_{analysis.processed_at.strftime('%Y%m%d_%H%M%S')}.json"

    data = {
        "video_id": analysis.video_id,
        "analyst_name": analysis.analyst_name,
        "signals": [sig.model_dump(mode="json", exclude_none=True) for sig in analysis.signals],
        "processed_at": analysis.processed_at.isoformat(),
    }
    optional_fields = {
        "market_outlook": analysis.market_outlook,
        "processing_duration_seconds": analysis.processing_duration_seconds,
        "transcript_length_chars": analysis.transcript_length_chars,
        "video_view_count": analysis.video_view_count,
        "video_published_at": analysis.video_published_at,
        "recommendation_feature": (
            analysis.recommendation_feature.model_dump(mode="json", exclude_none=True)
            if analysis.recommendation_feature
            else None
        ),
    }
    data.update({key: value for key, value in optional_fields.items() if value is not None})

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return output_file


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
    parser.add_argument("--force", action="store_true", help="Force extraction even if already extracted")
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
    settings.stock_validation_provider = (
        pipeline_config.get("stock_data.validation_provider") or settings.stock_validation_provider
    )
    llm_extractor = LLMExtractorFactory.create(settings, pipeline_config)
    validator = StockValidator(settings)

    source_file = Path(args.input) if args.input else settings.data_metadata_dir / "video_registry.json"
    items = _load_items(settings, args.input)
    if not isinstance(items, list):
        items = []
        
    if not args.force:
        unextracted = []
        for item in items:
            vid = str(item.get("video_id", "")).strip()
            if vid and not _get_existing_signal(settings, vid):
                unextracted.append(item)
            elif not vid:
                unextracted.append(item)
        items = unextracted
        
    if args.limit > 0:
        items = items[: args.limit]

    results = []
    ok_count = 0

    for item in items:
        video_id = str(item.get("video_id", "")).strip()
        if not video_id:
            results.append({**item, "status": "invalid", "error": "missing video_id"})
            continue
            
        if not args.force:
            existing_signal = _get_existing_signal(settings, video_id)
            if existing_signal:
                print(f"[INFO] Signal already exists for {video_id}, skipping...")
                results.append({**item, "status": "extracted", "skipped": True})
                # Include them in ok_count so they don't look like failures
                ok_count += 1
                continue

        analyst_name = item.get("analyst_name")
        view_count = item.get("view_count")
        published_at = item.get("published_at")

        try:
            start_time = time.time()
            transcript_text = _load_latest_transcript_text(settings, video_id)
            transcript_text = _convert_to_traditional(transcript_text)
            analysis = llm_extractor.extract_signals(
                transcript=transcript_text,
                video_id=video_id,
                analyst_name=analyst_name,
            )

            for sig in analysis.signals:
                if getattr(sig, "stock_name", None):
                    sig.stock_name = _convert_to_traditional(sig.stock_name)
                if getattr(sig, "reasoning", None):
                    sig.reasoning = _convert_to_traditional(sig.reasoning)
                if getattr(sig, "label_reason", None):
                    sig.label_reason = _convert_to_traditional(sig.label_reason)

            # Deduplicate/combine contradictory signals by exact code (or name if code is absent)
            # but do NOT correct or drop them yet so we keep the raw output.
            from collections import OrderedDict
            dedup_signals = OrderedDict()
            for sig in analysis.signals:
                key = str(sig.stock_code).strip() if sig.stock_code else str(sig.stock_name).strip()
                if key in dedup_signals:
                    existing_sig = dedup_signals[key]
                    if getattr(existing_sig, "confidence", 0) < getattr(sig, "confidence", 0):
                        new_reason = existing_sig.reasoning + " | " + sig.reasoning if existing_sig.reasoning != sig.reasoning else sig.reasoning
                        sig.reasoning = new_reason
                        dedup_signals[key] = sig
                    else:
                        new_reason = existing_sig.reasoning + " | " + sig.reasoning if existing_sig.reasoning != sig.reasoning else existing_sig.reasoning
                        existing_sig.reasoning = new_reason
                else:
                    dedup_signals[key] = sig
            analysis.signals = list(dedup_signals.values())

            analysis.video_view_count = view_count
            analysis.video_published_at = published_at
            analysis.transcript_length_chars = len(transcript_text)
            analysis.recommendation_feature = _build_recommendation_feature(analysis)
            analysis.processing_duration_seconds = time.time() - start_time
            signal_path = _save_analysis(settings, analysis)
            _update_registry(settings, video_id, {"status": "signals_extracted", "signal_path": str(signal_path)})
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
            _update_registry(settings, video_id, {"status": "extract_failed"})
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
