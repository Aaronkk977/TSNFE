#!/usr/bin/env python3
"""ETL-02: Download media only when transcript cache is unavailable."""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.utils.config import get_settings
from tw_analyst_pipeline.utils.logging import setup_logging
from tw_analyst_pipeline.youtube.downloader import AudioDownloader

from etl_common import TZ_TAIPEI, read_json, write_json


def _load_pending_items(settings, input_file: str | None) -> list:
    if input_file:
        return read_json(Path(input_file)).get("items", [])
    
    registry_path = settings.data_metadata_dir / "video_registry.json"
    if registry_path.exists():
        import json
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        
        # Only download if it's pending (or discovered)
        items = [v for k, v in registry.items() if v.get("status") in ("pending", "discovered")]
        return items
        
    return []

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

def _resolve_youtube_cookie_path(settings) -> Path | None:
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


def _has_youtube_cc(video_id: str, settings) -> bool:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        languages = ["zh-Hant", "zh-TW", "zh-Hans", "zh", "en"]
        
        # 1. 取得該影片的字幕清單 (不依賴 cookie)
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # 2. 嘗試找尋我們支援的語言
        transcript_list.find_transcript(languages)
        return True
    except Exception as e:
        # 如果真的找不到字幕，或是被擋，印出具體原因
        print(f"[WARN] CC check failed/missing for {video_id}: {type(e).__name__}")
        return False


def _parse_published_datetime(published_at: str | None) -> datetime | None:
    if not published_at:
        return None
    text = str(published_at).strip()
    if not text:
        return None
    # Upstream metadata can contain non-standard ISO like "...+00:00Z".
    # If offset already exists, strip trailing Z; otherwise treat Z as UTC.
    if text.endswith("Z"):
        body = text[:-1]
        has_explicit_offset = False
        t_idx = body.find("T")
        if t_idx >= 0:
            time_part = body[t_idx + 1 :]
            has_explicit_offset = ("+" in time_part) or ("-" in time_part)
        text = body if has_explicit_offset else f"{body}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _in_date_window(published_at: str | None, start_date: str | None, end_date: str | None) -> bool:
    if not start_date and not end_date:
        return True
    dt = _parse_published_datetime(published_at)
    if dt is None:
        return False

    tz_taipei = timezone(timedelta(hours=8))
    local_date = dt.astimezone(tz_taipei).date()
    if start_date and local_date < datetime.strptime(start_date, "%Y-%m-%d").date():
        return False
    if end_date and local_date > datetime.strptime(end_date, "%Y-%m-%d").date():
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL-02 download media")
    parser.add_argument("--input", type=str, default=None, help="pending_videos JSON path")
    parser.add_argument("--min-wait-seconds", type=float, default=10.0)
    parser.add_argument("--max-wait-seconds", type=float, default=20.0)
    parser.add_argument("--start-date", type=str, default=None, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end-date", type=str, default=None, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--run-tag", type=str, default=None, help="Stable tag for status filename")
    parser.add_argument(
        "--max-audio-cache",
        type=int,
        default=20,
        help="Keep at most N local audio files; only untranscribed files are deletable",
    )
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    args = parser.parse_args()
    if bool(args.start_date) != bool(args.end_date):
        raise ValueError("--start-date and --end-date must be provided together")

    setup_logging(level=args.log_level)
    os.environ["AUDIO_CACHE_MAX_KEEP"] = str(max(0, args.max_audio_cache))

    def _load_items(settings, input_file: str | None) -> list:
        if input_file:
            return read_json(Path(input_file)).get("items", [])
        
        registry_path = settings.data_metadata_dir / "video_registry.json"
        if registry_path.exists():
            import json
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
            
            # Fetch videos needing download
            items = [
                v
                for k, v in registry.items()
                if v.get("status") in ("pending", "discovered")
            ]
            return items
            
        return []

    settings = get_settings()
    downloader = AudioDownloader(settings)

    source_file = Path(args.input) if args.input else settings.data_metadata_dir / "video_registry.json"
    pending_items = _load_items(settings, args.input)
    # Optional cap
    items = pending_items[:50]
    
    results = []
    downloaded_count = 0

    for idx, item in enumerate(items):
        video_id = str(item.get("video_id", "")).strip()
        video_url = str(item.get("video_url", "")).strip()
        published_at = item.get("published_at")

        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"

        if not video_id or not video_url:
            print(f"[WARN] 略過 {video_id} - 缺少 ID 或 URL")
            results.append({**item, "status": "invalid", "error": "missing video_id/video_url"})
            _update_registry(settings, video_id, {"status": "invalid"}) # <--- 關鍵：清出佇列
            continue

        if not _in_date_window(published_at, args.start_date, args.end_date):
            print(f"[WARN] 略過 {video_id} - 不在日期範圍內 ({published_at})")
            results.append(
                {
                    **item,
                    "status": "skipped_out_of_window",
                    "audio_path": None,
                    "error": "published_at not in requested date window",
                }
            )
            _update_registry(settings, video_id, {"status": "skipped_out_of_window"}) # <--- 關鍵：清出佇列
            continue

        if _has_youtube_cc(video_id, settings):
            results.append({**item, "status": "cc_ready", "audio_path": None})
            _update_registry(settings, video_id, {"status": "cc_ready"})
            print(f"[INFO] Skipping download for {video_id}: YouTube CC is available.")
            continue

        # print(f"[WARN] 略過 {video_id} - 沒有 CC 字幕，且目前環境無法安全下載音檔")
        # results.append({**item, "status": "skipped_no_cc", "audio_path": None})
        # _update_registry(settings, video_id, {"status": "skipped_no_cc"})
        # continue

        existing_audio = downloader._find_latest_audio_file(video_id)
        if existing_audio:
            print(f"[INFO] Skipping download for {video_id}: Local audio {existing_audio.name} already exists.")
            results.append({**item, "status": "audio_ready", "audio_path": str(existing_audio)})
            _update_registry(settings, video_id, {"status": "audio_ready", "audio_path": str(existing_audio)})
            continue

        if idx > 0 and args.max_wait_seconds > 0:
            delay = random.uniform(max(0.0, args.min_wait_seconds), max(args.min_wait_seconds, args.max_wait_seconds))
            print(f"[INFO] Sleeping {delay:.1f}s before next download")
            time.sleep(delay)

        try:
            audio_path = downloader.download(video_url, published_at=published_at)
            if audio_path is None:
                raise RuntimeError("download returned empty path")
            downloaded_count += 1
            results.append({**item, "status": "audio_ready", "audio_path": str(audio_path)})
            _update_registry(settings, video_id, {"status": "audio_ready", "audio_path": str(audio_path)})
        except Exception as e:
            results.append({**item, "status": "download_failed", "audio_path": None, "error": str(e)})
            _update_registry(settings, video_id, {"status": "download_failed"})

    run_tag = (args.run_tag or "").strip() or datetime.now(TZ_TAIPEI).strftime("%Y%m%d_%H%M%S")
    out_file = settings.data_metadata_dir / f"download_status_{run_tag}.json"
    output_payload = {
        "generated_at": datetime.now(TZ_TAIPEI).isoformat(timespec="seconds"),
        "source_file": str(source_file),
        "count": len(results),
        "downloaded_count": downloaded_count,
        "items": results,
    }
    write_json(out_file, output_payload)
    write_json(settings.data_metadata_dir / "download_status_latest.json", output_payload)

    removed_dirs = downloader.cleanup_empty_audio_dirs()

    print(f"[INFO] Download status saved: {out_file}")
    print(f"[INFO] Downloaded files: {downloaded_count}")
    if removed_dirs > 0:
        print(f"[INFO] Removed empty audio directories: {removed_dirs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
