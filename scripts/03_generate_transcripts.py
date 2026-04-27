#!/usr/bin/env python3
"""ETL-03: Generate transcripts from CC/cache first, then local audio fallback."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.transcription import TranscriberFactory
from tw_analyst_pipeline.extraction.schemas import TranscriptResult
from tw_analyst_pipeline.utils.config import get_pipeline_config, get_settings
from tw_analyst_pipeline.utils.logging import setup_logging
from tw_analyst_pipeline.youtube.downloader import AudioDownloader
from youtube_transcript_api import YouTubeTranscriptApi

from etl_common import TZ_TAIPEI, read_json, write_json


def _resolve_youtube_cookie_path(settings) -> Optional[Path]:
    configured_cookie = (settings.yt_cookies_file or "").strip()
    cookie_candidates = []
    if configured_cookie:
        cookie_candidates.append(Path(configured_cookie))
    cookie_candidates.extend(
        [
            Path("local") / "cookies.txt",
            Path("local") / "@local" / "cookies.txt",
            Path("@local") / "cookies.txt",
        ]
    )

    for cookie_path in cookie_candidates:
        if cookie_path.exists() and cookie_path.is_file():
            return cookie_path
    return None


def _current_date_folder(published_at: Optional[str] = None) -> str:
    if published_at:
        try:
            from datetime import timezone, timedelta
            import dateutil.parser

            if isinstance(published_at, datetime):
                dt = published_at
            else:
                dt_str = str(published_at)
                if dt_str.endswith("+00:00Z"):
                    dt_str = dt_str[:-1]
                dt = dateutil.parser.parse(dt_str)

            tz_taipei = timezone(timedelta(hours=8))
            return dt.astimezone(tz_taipei).strftime("%Y-%m-%d")
        except Exception:
            pass
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")


def _save_transcript_result(settings, transcript_result: TranscriptResult, item: dict) -> Path:
    subfolder = os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "daily")
    published_at = item.get("published_at")
    output_dir = settings.data_transcripts_dir / subfolder / _current_date_folder(published_at)
    output_dir.mkdir(parents=True, exist_ok=True)
    now_utc = datetime.now(UTC)
    output_file = output_dir / f"{transcript_result.video_id}_{now_utc.strftime('%Y%m%d_%H%M%S')}.json"

    payload = {
        "channel_name": item.get("channel"),
        "analyst_name": item.get("analyst_name"),
        "video_title": item.get("title"),
        "published_at": item.get("published_at"),
        "view_count": item.get("view_count"),
        "duration": item.get("duration"),
        "video_id": transcript_result.video_id,
        "text": transcript_result.text,
        "segments": getattr(transcript_result, "segments", []),
        "language": getattr(transcript_result, "language", "zh"),
        "duration_seconds": getattr(transcript_result, "duration_seconds", item.get("duration", None)),
        "processing_time_seconds": getattr(transcript_result, "processing_time_seconds", None),
        "saved_at": now_utc.isoformat(),
    }
    write_json(output_file, payload)
    return output_file


def _try_youtube_cc(video_id: str, item: dict, settings) -> Optional[tuple[TranscriptResult, Path]]:
    try:
        languages = ["zh-Hant", "zh-TW", "zh-Hans", "zh", "en"]
        transcript_items = None
        cookie_path = _resolve_youtube_cookie_path(settings)
        print(f"[INFO] Trying YouTube CC for {video_id} (languages={languages})")

        if cookie_path:
            try:
                if hasattr(YouTubeTranscriptApi, "get_transcript"):
                    transcript_items = YouTubeTranscriptApi.get_transcript(
                        video_id,
                        languages=languages,
                        cookies=str(cookie_path),
                    )
                    print(f"[INFO] YouTube CC request using cookies: {cookie_path}")
                else:
                    print(
                        "[WARN] youtube-transcript-api has no get_transcript(); "
                        "fallback to fetch() without explicit cookies"
                    )
            except (TypeError, AttributeError):
                print(
                    "[WARN] youtube-transcript-api cannot use cookies in get_transcript; "
                    "fallback to fetch() without explicit cookies"
                )

        if transcript_items is None:
            api = YouTubeTranscriptApi()
            transcript_items = api.fetch(video_id, languages=languages)

        segments = []
        text_chunks = []

        for idx, seg_item in enumerate(transcript_items):
            # YouTubeTranscriptApi now returns dict instances for snippets or FetchedTranscriptSnippet depending on version
            if hasattr(seg_item, "text"):
                seg_text = (getattr(seg_item, "text", "") or "").strip()
                start_sec = float(getattr(seg_item, "start", 0.0) or 0.0)
                duration = float(getattr(seg_item, "duration", 0.0) or 0.0)
            else:
                seg_text = (seg_item.get("text", "") or "").strip()
                start_sec = float(seg_item.get("start", 0.0) or 0.0)
                duration = float(seg_item.get("duration", 0.0) or 0.0)

            if not seg_text:
                continue

            segments.append(
                {
                    "id": idx,
                    "start": start_sec,
                    "end": start_sec + duration if duration > 0 else None,
                    "text": seg_text,
                    "confidence": None,
                }
            )
            text_chunks.append(seg_text)

        full_text = "\n".join(text_chunks).strip()
        if not full_text:
            return None

        result = TranscriptResult(
            video_id=video_id,
            text=full_text,
            segments=segments,
            language="zh",
            duration_seconds=(segments[-1]["end"] if segments and segments[-1]["end"] else None),
            processing_time_seconds=None,
        )
        output_file = _save_transcript_result(settings, result, item)
        print(f"[INFO] YouTube CC extracted for {video_id}: chars={len(full_text)}")
        return result, output_file
    except Exception as e:
        print(f"[WARN] YouTube CC unavailable for {video_id}: {e}")
        return None


def _update_registry(settings, video_id: str, updates: dict):
    registry_path = settings.data_metadata_dir / "video_registry.json"
    import json
    if registry_path.exists():
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
            if video_id in registry:
                registry[video_id].update(updates)
                write_json(registry_path, registry)
        except Exception as e:
            print(f"[ERROR] Failed to update registry for {video_id}: {e}")

def _load_items(settings, input_file: str | None) -> list:
    if input_file:
        return read_json(Path(input_file)).get("items", [])
    
    registry_path = settings.data_metadata_dir / "video_registry.json"
    if registry_path.exists():
        import json
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        
        items = [v for k, v in registry.items() if v.get("status") in ("audio_ready", "cc_ready")]
        return items
        
    return []

def main() -> int:
    parser = argparse.ArgumentParser(description="ETL-03 generate transcripts")
    parser.add_argument("--input", type=str, default=None, help="download_status or pending_videos JSON path")
    parser.add_argument("--text-source", choices=["auto", "cc", "gemini"], default="auto")
    parser.add_argument("--limit", type=int, default=0, help="Optional cap for processed items")
    parser.add_argument("--transcription-provider", choices=["gemini", "whisper"], default=None)
    parser.add_argument("--whisper-model", type=str, default=None)
    parser.add_argument("--whisper-device", choices=["cuda", "cpu"], default=None)
    parser.add_argument("--whisper-compute-type", choices=["float16", "float32", "int8"], default=None)
    parser.add_argument("--run-tag", type=str, default=None, help="Stable tag for status filename")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    args = parser.parse_args()

    setup_logging(level=args.log_level)
    settings = get_settings()
    pipeline_config = get_pipeline_config()

    if args.transcription_provider:
        pipeline_config.data.setdefault("transcription", {})["provider"] = args.transcription_provider
    if args.whisper_model:
        pipeline_config.data.setdefault("transcription", {})["model"] = args.whisper_model
    if args.whisper_device:
        pipeline_config.data.setdefault("transcription", {})["device"] = args.whisper_device
    if args.whisper_compute_type:
        pipeline_config.data.setdefault("transcription", {})["compute_type"] = args.whisper_compute_type

    transcriber = None
    downloader = AudioDownloader(settings)

    def _get_transcriber():
        nonlocal transcriber
        if transcriber is None:
            transcriber = TranscriberFactory.create(settings, pipeline_config)
        return transcriber

    source_file = Path(args.input) if args.input else settings.data_metadata_dir / "video_registry.json"
    items = _load_items(settings, args.input)

    if args.limit > 0:
        items = items[: args.limit]

    outputs = []

    def _get_existing_transcript(settings, video_id: str) -> Optional[Path]:
        pattern = f"{video_id}_*.json"
        for dated_dir in settings.data_transcripts_dir.glob("*/*"):
            if not dated_dir.is_dir():
                continue
            matches = list(dated_dir.glob(pattern))
            if matches:
                return sorted(matches)[-1]
        return None

    for item in items:
        video_id = str(item.get("video_id", "")).strip()
        published_at = item.get("published_at")
        transcript_source = None
        if not video_id:
            outputs.append({**item, "status": "invalid", "error": "missing video_id"})
            continue

        existing_file = _get_existing_transcript(settings, video_id)
        if existing_file:
            print(f"[INFO] Transcript already exists for {video_id}, skipping...")
            try:
                with open(existing_file, "r", encoding="utf-8") as f:
                    import json
                    saved_data = json.load(f)
                    t_chars = len(saved_data.get("text", ""))
            except Exception:
                t_chars = 0
            outputs.append({
                **item,
                "status": "skipped_transcript_cached",
                "transcript_chars": t_chars,
                "transcript_path": str(existing_file),
            })

            _update_registry(settings, video_id, {"status": "transcribed", "transcript_path": str(existing_file)})
            
            continue

        transcript_result = None
        transcript_file = None

        try:
            if transcript_result is None and args.text_source in {"auto", "cc"}:
                cc_result = _try_youtube_cc(video_id, item, settings)
                if cc_result is not None:
                    transcript_result, transcript_file = cc_result
                    transcript_source = "youtube_cc"

            if transcript_result is None and args.text_source in {"auto", "gemini", "whisper"}:
                audio_path = downloader._find_latest_audio_file(video_id)
                if audio_path is None:
                    raise FileNotFoundError(f"No local audio found for video_id={video_id}")
                print(f"[INFO] Fallback to audio transcription for {video_id}: {audio_path.name}")
                active_transcriber = _get_transcriber()
                transcript_result = active_transcriber.transcribe(audio_path, video_id, published_at=published_at)
                transcript_source = "audio_transcribe"
                
                # Overwrite/save with extra metadata
                transcript_file = _save_transcript_result(settings, transcript_result, item)

            if not transcript_result or not transcript_result.text:
                raise RuntimeError("transcript is empty")

            outputs.append(
                {
                    **item,
                    "status": "transcribed",
                    "transcript_source": transcript_source,
                    "transcript_chars": len(transcript_result.text),
                    "transcript_path": str(transcript_file) if transcript_file else None,
                }
            )
            print(
                f"[INFO] Transcription success for {video_id}: "
                f"source={transcript_source}, chars={len(transcript_result.text)}"
            )
            _update_registry(settings, video_id, {"status": "transcribed", "transcript_path": str(transcript_file) if transcript_file else None})
        except Exception as e:
            print(f"[ERROR] Transcription failed for {video_id}: {e}")
            outputs.append({**item, "status": "transcribe_failed", "error": str(e)})

    output_payload = {
        "generated_at": datetime.now(TZ_TAIPEI).isoformat(timespec="seconds"),
        "source_file": str(source_file),
        "count": len(outputs),
        "items": outputs,
    }

    ok_count = sum(1 for row in outputs if row.get("status") in {"transcribed", "skipped_transcript_cached"})
    cc_success_count = sum(1 for row in outputs if row.get("status") == "transcribed" and row.get("transcript_source") == "youtube_cc")
    audio_success_count = sum(1 for row in outputs if row.get("status") == "transcribed" and row.get("transcript_source") == "audio_transcribe")
    fail_count = sum(1 for row in outputs if row.get("status") == "transcribe_failed")
    has_new_transcription = any(row.get("status") == "transcribed" for row in outputs)
    latest_file = settings.data_metadata_dir / "transcript_status_latest.json"
    if has_new_transcription:
        run_tag = (args.run_tag or "").strip() or datetime.now(TZ_TAIPEI).strftime("%Y%m%d_%H%M%S")
        out_file = settings.data_metadata_dir / f"transcript_status_{run_tag}.json"
        write_json(out_file, output_payload)
        print(f"[INFO] Transcript status saved: {out_file}")
    else:
        print("[INFO] No newly transcribed videos in this batch; skip timestamped status file.")

    write_json(latest_file, output_payload)
    print(f"[INFO] Transcript latest status updated: {latest_file}")
    print(f"[INFO] Processed videos (transcribed/cached): {ok_count}/{len(outputs)}")
    print(
        f"[INFO] Transcript source summary: youtube_cc={cc_success_count}, "
        f"audio_transcribe={audio_success_count}, failed={fail_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
