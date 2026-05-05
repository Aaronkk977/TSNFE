#!/usr/bin/env python3
"""ETL-03: Generate transcripts from CC/cache first, then local audio fallback."""

from __future__ import annotations

import argparse
import os
import random
import re
import subprocess
import sys
import time
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

_LAST_CC_REQUEST_AT = 0.0


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


def _fetch_youtube_transcript_items(
    video_id: str, languages: list[str], cookie_path: Optional[Path]
) -> list:
    """
    youtube-transcript-api has breaking changes between 0.6.x and 1.x:
    - 0.6: YouTubeTranscriptApi.get_transcript(video_id, languages=...)
    - 1.x: list_transcripts -> find_transcript -> fetch() (returns FetchedTranscript / snippets)
    There is no stable instance method api.fetch(video_id) across versions.
    """
    last_err: Exception | None = None

    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        if cookie_path:
            try:
                items = YouTubeTranscriptApi.get_transcript(
                    video_id,
                    languages=languages,
                    cookies=str(cookie_path),
                )
                print(f"[INFO] YouTube CC request using cookies: {cookie_path}")
                return list(items) if items is not None else []
            except TypeError:
                pass
            except Exception as e:
                last_err = e
        try:
            items = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
            return list(items) if items is not None else []
        except Exception as e:
            last_err = e

    if hasattr(YouTubeTranscriptApi, "list_transcripts"):
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = transcript_list.find_transcript(languages)
            fetched = transcript.fetch()
            if hasattr(fetched, "to_raw_data"):
                return list(fetched.to_raw_data())
            return list(fetched)
        except Exception as e:
            last_err = e

    api = YouTubeTranscriptApi()
    if hasattr(api, "list"):
        try:
            transcript_list = api.list(video_id)
            transcript = transcript_list.find_transcript(languages)
            if hasattr(transcript, "fetch"):
                fetched = transcript.fetch()
                if hasattr(fetched, "to_raw_data"):
                    return list(fetched.to_raw_data())
                return list(fetched)
        except Exception as e:
            last_err = e

    if last_err is not None:
        raise last_err
    raise RuntimeError("youtube_transcript_api: no compatible transcript fetch API found")


def _is_rate_limit_error(err: Exception) -> bool:
    msg = str(err).lower()
    return "429" in msg or "too many requests" in msg


def _throttle_youtube_cc_requests(min_interval_seconds: float = 3.0) -> None:
    global _LAST_CC_REQUEST_AT
    now = time.time()
    wait_seconds = min_interval_seconds - (now - _LAST_CC_REQUEST_AT)
    if wait_seconds > 0:
        # Add a small jitter to avoid lockstep API patterns.
        time.sleep(wait_seconds + random.uniform(0.2, 0.8))
    _LAST_CC_REQUEST_AT = time.time()


def _vtt_to_segments(vtt_text: str) -> list[dict]:
    def _to_seconds(ts: str) -> float:
        t = ts.replace(",", ".").strip()
        parts = t.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(parts[0])

    lines = [line.rstrip("\n") for line in vtt_text.splitlines()]
    segments: list[dict] = []
    cue_lines: list[str] = []
    start_sec = 0.0
    end_sec = None
    in_cue = False
    cue_id = 0
    timestamp_re = re.compile(r"^\s*([\d:\.,]+)\s*-->\s*([\d:\.,]+)")

    def _flush_cue() -> None:
        nonlocal cue_id, cue_lines, in_cue
        text = " ".join(x.strip() for x in cue_lines if x.strip())
        if text:
            segments.append(
                {
                    "id": cue_id,
                    "start": start_sec,
                    "end": end_sec,
                    "text": re.sub(r"<[^>]+>", "", text).strip(),
                    "confidence": None,
                }
            )
            cue_id += 1
        cue_lines = []
        in_cue = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if in_cue:
                _flush_cue()
            continue

        if line.upper() == "WEBVTT":
            continue

        match = timestamp_re.match(line)
        if match:
            if in_cue:
                _flush_cue()
            start_sec = _to_seconds(match.group(1))
            end_sec = _to_seconds(match.group(2))
            in_cue = True
            continue

        if in_cue:
            cue_lines.append(line)

    if in_cue:
        _flush_cue()

    return segments


def _try_ytdlp_subtitle_fallback(video_id: str, item: dict) -> Optional[TranscriptResult]:
    output_dir = Path("data/processing/debug/yt_dlp_subtitles")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_tpl = str(output_dir / f"{video_id}.%(ext)s")
    video_url = (item.get("video_url") or "").strip() or f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        "zh-TW,zh-Hant,zh-Hans,zh,en.*",
        "--sub-format",
        "vtt",
        "--output",
        output_tpl,
        video_url,
    ]
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if completed.stdout.strip():
            print(f"[INFO] yt-dlp subtitle stdout for {video_id}: {completed.stdout.strip()}")
        if completed.stderr.strip():
            print(f"[INFO] yt-dlp subtitle stderr for {video_id}: {completed.stderr.strip()}")
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        stdout = (e.stdout or "").strip()
        if stdout:
            print(f"[WARN] yt-dlp subtitle stdout for {video_id}: {stdout}")
        if stderr:
            print(f"[WARN] yt-dlp subtitle stderr for {video_id}: {stderr}")
        print(f"[WARN] yt-dlp subtitle fallback failed for {video_id}: exit_code={e.returncode}")
        return None
    except Exception as e:
        print(f"[WARN] yt-dlp subtitle fallback failed for {video_id}: {e}")
        return None

    candidates = sorted(output_dir.glob(f"{video_id}*.vtt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None

    vtt_path = candidates[0]
    try:
        vtt_text = vtt_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[WARN] Failed to read yt-dlp subtitle file for {video_id}: {e}")
        return None

    segments = _vtt_to_segments(vtt_text)
    text = "\n".join(seg.get("text", "").strip() for seg in segments if seg.get("text")).strip()
    if not text:
        return None

    print(f"[INFO] yt-dlp subtitle fallback extracted for {video_id}: chars={len(text)}")
    return TranscriptResult(
        video_id=video_id,
        text=text,
        segments=segments,
        language="zh",
        duration_seconds=(segments[-1]["end"] if segments and segments[-1]["end"] else None),
        processing_time_seconds=None,
    )


def _try_youtube_cc(video_id: str, item: dict, settings) -> Optional[tuple[TranscriptResult, Path]]:
    languages = ["zh-Hant", "zh-TW", "zh-Hans", "zh", "en"]
    cookie_path = _resolve_youtube_cookie_path(settings)
    print(f"[INFO] Trying YouTube CC for {video_id} (languages={languages})")
    if cookie_path is None:
        print("[WARN] No YouTube cookies detected; CC requests are more likely to hit rate limit.")

    max_attempts = 3
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            _throttle_youtube_cc_requests()
            transcript_items = _fetch_youtube_transcript_items(video_id, languages, cookie_path)
            last_err = None
            break
        except Exception as e:
            last_err = e
            if _is_rate_limit_error(e) and attempt < max_attempts:
                backoff = 5 * (2 ** (attempt - 1)) + random.uniform(0.5, 1.5)
                print(f"[WARN] YouTube CC rate limited for {video_id}; retrying in {backoff:.1f}s")
                time.sleep(backoff)
                continue
            break

    if last_err is not None:
        print(f"[WARN] YouTube CC unavailable for {video_id}: {last_err}")
        return None

    try:
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
        
        items = [
            v
            for k, v in registry.items()
            if v.get("status") in ("audio_ready", "cc_ready", "transcribe_failed")
        ]
        return items
        
    return []

def main() -> int:
    parser = argparse.ArgumentParser(description="ETL-03 generate transcripts")
    parser.add_argument("--input", type=str, default=None, help="download_status or pending_videos JSON path")
    parser.add_argument("--text-source", choices=["auto", "cc", "gemini"], default="auto")
    parser.add_argument("--limit", type=int, default=0, help="Optional cap for processed items")
    parser.add_argument(
        "--transcription-provider",
        choices=["gemini", "whisper"],
        default=None,
        help="Override transcription.provider in config/config.yaml; omit to use YAML",
    )
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
                elif args.text_source == "auto":
                    ytdlp_cc_result = _try_ytdlp_subtitle_fallback(video_id, item)
                    if ytdlp_cc_result is not None:
                        transcript_result = ytdlp_cc_result
                        transcript_source = "ytdlp_subtitle"
                        transcript_file = _save_transcript_result(settings, transcript_result, item)

            if transcript_result is None and args.text_source in {"auto", "gemini", "whisper"}:
                audio_path = downloader._find_latest_audio_file(video_id)
                if audio_path is None:
                    video_url = (item.get("video_url") or "").strip()
                    if not video_url:
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                    print(
                        f"[INFO] No local audio for {video_id}; downloading before transcription..."
                    )
                    audio_path = downloader.download(video_url, published_at=published_at)
                    _update_registry(
                        settings,
                        video_id,
                        {
                            "audio_path": str(audio_path).replace("\\", "/"),
                            "status": "audio_ready",
                        },
                    )
                print(f"[INFO] Fallback to audio transcription for {video_id}: {audio_path.name}")
                active_transcriber = _get_transcriber()
                # Transcribers also call _save_transcript internally; avoid double files by
                # persisting only the ETL copy (channel/title metadata).
                transcript_result = active_transcriber.transcribe(
                    audio_path, video_id, published_at=published_at, persist_to_disk=False
                )
                transcript_source = "audio_transcribe"
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
