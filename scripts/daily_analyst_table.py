import os
#!/usr/bin/env python3
"""
Daily automation script:
- Read analyst channels from config/analysts.yaml or local/analyst_list.txt
- Process all videos updated within the configured window for each analyst
- Output analyst (rows) x stock (columns) table in markdown/csv
"""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
import random
import re
import time
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.extraction.schemas import VideoAnalysis, normalize_label
from tw_analyst_pipeline.pipeline.orchestrator import SignalPipeline
from tw_analyst_pipeline.utils.config import get_pipeline_config, get_settings
from tw_analyst_pipeline.utils.logging import setup_logging
from tw_analyst_pipeline.youtube.downloader import AudioDownloader
from tw_analyst_pipeline.youtube.fetcher import YouTubeFetcher


LABEL_PRIORITY = {
    "買進": 3,
    "賣出": 2,
    "中立": 1,
    "模糊": 0,
}


def _load_analysts(analysts_file: Path) -> List[dict]:
    if not analysts_file.exists():
        raise FileNotFoundError(f"Analysts file not found: {analysts_file}")

    cleaned = []

    if analysts_file.suffix.lower() in {".yaml", ".yml"}:
        with open(analysts_file, "r", encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}

        analysts = payload.get("analysts", [])
        if not isinstance(analysts, list) or not analysts:
            raise ValueError("config/analysts.yaml must contain a non-empty 'analysts' list")

        for row in analysts:
            name = str(row.get("name", "")).strip()
            channel = str(row.get("channel", "")).strip()
            if not name or not channel:
                continue
            cleaned.append({"name": name, "channel": channel})
    else:
        with open(analysts_file, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                match = re.search(r"https?://\S+", line)
                if not match:
                    continue

                name = line[: match.start()].strip().rstrip("-").strip()
                channel = match.group(0).rstrip(").,;]")
                if not name:
                    name = channel
                cleaned.append({"name": name, "channel": channel})

    if not cleaned:
        raise ValueError("No valid analyst rows in config/analysts.yaml")
    return cleaned


def _pick_cell_value(current: str, incoming: str) -> str:
    if not current:
        return incoming
    return incoming if LABEL_PRIORITY.get(incoming, -1) >= LABEL_PRIORITY.get(current, -1) else current


def _sleep_before_next_video(processed_videos: int) -> None:
    if processed_videos <= 0:
        return

    delay_seconds = random.uniform(5, 15)
    print(f"[INFO] Sleeping {delay_seconds:.1f}s before next video")
    time.sleep(delay_seconds)


def _collect_matrix(analyses: List[VideoAnalysis]) -> Tuple[List[str], Dict[str, Dict[str, str]], Dict[str, str]]:
    stocks = set()
    stock_display = {}
    matrix: Dict[str, Dict[str, str]] = {}

    for analysis in analyses:
        analyst = analysis.analyst_name or "Unknown"
        matrix.setdefault(analyst, {})

        for signal in analysis.signals:
            code = signal.stock_code
            name = signal.stock_name or code
            stocks.add(code)
            stock_display[code] = f"{code} {name}"

            label = normalize_label(signal.normalized_label or signal.implied_label)
            existing = matrix[analyst].get(code, "")
            matrix[analyst][code] = _pick_cell_value(existing, label)

    ordered_stocks = sorted(stocks)
    return ordered_stocks, matrix, stock_display


def _write_markdown_table(
    output_file: Path,
    ordered_stocks: List[str],
    matrix: Dict[str, Dict[str, str]],
    stock_display: Dict[str, str],
    stock_rankings: dict,
):
    output_file.parent.mkdir(parents=True, exist_ok=True)

    headers = ["分析師"] + [stock_display.get(code, code) for code in ordered_stocks]
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

    for analyst in sorted(matrix.keys()):
        row = [analyst]
        for code in ordered_stocks:
            row.append(matrix[analyst].get(code, ""))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append("## 股票重點清單")

    section_specs = [
        ("最多分析師推薦的股票", "most_recommended_by_analysts", "analyst_count", "位分析師"),
        ("最多分析師不推薦的股票", "most_not_recommended_by_analysts", "analyst_count", "位分析師"),
        ("觀看數最多的推薦股票", "most_viewed_recommended_stocks", "view_sum", "累計觀看"),
        ("觀看數最多的不推薦股票", "most_viewed_not_recommended_stocks", "view_sum", "累計觀看"),
    ]

    for title, key, metric_key, metric_suffix in section_specs:
        lines.append("")
        lines.append(f"### {title}")
        ranking_items = stock_rankings.get(key, [])
        if not ranking_items:
            lines.append("- 無")
            continue

        for idx, item in enumerate(ranking_items, start=1):
            code = item.get("stock_code", "")
            name = item.get("stock_name", code)
            metric = item.get(metric_key, 0)
            lines.append(f"{idx}. {code} {name} - {metric} {metric_suffix}")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_csv_table(
    output_file: Path,
    ordered_stocks: List[str],
    matrix: Dict[str, Dict[str, str]],
    stock_display: Dict[str, str],
):
    output_file.parent.mkdir(parents=True, exist_ok=True)

    headers = ["analyst"] + [stock_display.get(code, code) for code in ordered_stocks]

    with open(output_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for analyst in sorted(matrix.keys()):
            row = [analyst]
            for code in ordered_stocks:
                row.append(matrix[analyst].get(code, ""))
            writer.writerow(row)


def _write_run_summary(output_file: Path, payload: dict):
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _count_signal_labels(analysis: VideoAnalysis) -> Tuple[int, int, int]:
    recommended = 0
    not_recommended = 0
    neutral = 0

    for signal in analysis.signals:
        label = normalize_label(signal.normalized_label or signal.implied_label)
        if label == "買進":
            recommended += 1
        elif label == "賣出":
            not_recommended += 1
        else:
            neutral += 1

    return recommended, not_recommended, neutral


def _build_stock_rankings(analyses: List[VideoAnalysis], limit: int = 10) -> dict:
    analyst_recommended = defaultdict(set)
    analyst_not_recommended = defaultdict(set)
    viewed_recommended = defaultdict(int)
    viewed_not_recommended = defaultdict(int)
    stock_names: Dict[str, str] = {}

    for analysis in analyses:
        analyst = analysis.analyst_name or "Unknown"
        view_count = analysis.video_view_count or 0
        seen_pairs = set()

        for signal in analysis.signals:
            code = signal.stock_code
            stock_names[code] = signal.stock_name or code
            label = normalize_label(signal.normalized_label or signal.implied_label)

            if label == "買進":
                analyst_recommended[code].add(analyst)
            elif label == "賣出":
                analyst_not_recommended[code].add(analyst)
            else:
                continue

            dedupe_key = (analysis.video_id, code, label)
            if dedupe_key in seen_pairs:
                continue
            seen_pairs.add(dedupe_key)

            if label == "買進":
                viewed_recommended[code] += view_count
            else:
                viewed_not_recommended[code] += view_count

    def _top_by_analyst(source: dict) -> List[dict]:
        ordered = sorted(
            source.items(),
            key=lambda kv: (-len(kv[1]), kv[0]),
        )
        return [
            {
                "stock_code": code,
                "stock_name": stock_names.get(code, code),
                "analyst_count": len(analyst_set),
            }
            for code, analyst_set in ordered[:limit]
        ]

    def _top_by_view(source: dict) -> List[dict]:
        ordered = sorted(
            source.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )
        return [
            {
                "stock_code": code,
                "stock_name": stock_names.get(code, code),
                "view_sum": int(view_sum),
            }
            for code, view_sum in ordered[:limit]
        ]

    return {
        "most_recommended_by_analysts": _top_by_analyst(analyst_recommended),
        "most_not_recommended_by_analysts": _top_by_analyst(analyst_not_recommended),
        "most_viewed_recommended_stocks": _top_by_view(viewed_recommended),
        "most_viewed_not_recommended_stocks": _top_by_view(viewed_not_recommended),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate daily analyst x stock matrix")
    parser.add_argument("--analysts-file", default="config/analysts.yaml")
    parser.add_argument("--max-videos", type=int, default=20)
    parser.add_argument(
        "--max-videos-per-analyst",
        type=int,
        default=50,
        help="Max candidate videos fetched per analyst before applying global --max-videos cap",
    )
    parser.add_argument("--days-back", type=int, default=2)
    parser.add_argument("--target-date", type=str, help="Target date YYYY-MM-DD. If provided, sets window from target-date to target-date+1")
    parser.add_argument("--cleanup-audio", action="store_true", help="Delete audio file after processing to save space")
    parser.add_argument("--exclude-shorts", action="store_true", default=True)
    parser.add_argument("--include-shorts", dest="exclude_shorts", action="store_false")
    parser.add_argument("--min-duration-seconds", type=int, default=180)
    parser.add_argument("--mode", choices=["audio", "url", "text"], default=None)
    parser.add_argument("--text-source", choices=["auto", "cc", "gemini"], default=None)
    parser.add_argument(
        "--download-workers",
        type=int,
        default=4,
        help="Parallel workers for pre-download stage",
    )
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    parser.add_argument(
        "--llm-provider",
        type=str,
        default=None,
        choices=["openai", "anthropic", "gemini", "google", "qwen", "local_hf"],
        help="Override LLM provider for extraction",
    )
    parser.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help="Override LLM model for extraction",
    )
    parser.add_argument(
        "--llm-temperature",
        type=float,
        default=None,
        help="Override LLM temperature",
    )
    parser.add_argument(
        "--llm-max-tokens",
        type=int,
        default=None,
        help="Override maximum tokens for LLM response",
    )
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

    mode = (args.mode or pipeline_config.get("execution.mode", "audio") or "audio").lower()
    text_source = (
        args.text_source
        or pipeline_config.get("execution.text_transcript_source", "auto")
        or "auto"
    ).lower()

    analysts = _load_analysts(Path(args.analysts_file))

    fetcher = YouTubeFetcher(settings)
    pipeline = SignalPipeline(settings, pipeline_config)

    analyses: List[VideoAnalysis] = []
    run_rows = []
    processed_video_total = 0
    updated_video_total = 0

    def get_previous_trading_day(dt):
        prev = dt - timedelta(days=1)
        while prev.weekday() >= 5: # 5=Sat, 6=Sun
            prev -= timedelta(days=1)
        return prev

    tz_taipei = timezone(timedelta(hours=8))
    
    if args.target_date:
        target_dt = datetime.strptime(args.target_date, "%Y-%m-%d").replace(tzinfo=tz_taipei)
        
        # 開盤前到上個交易日的開盤 (09:00 to 09:00)
        window_end_dt = target_dt.replace(hour=9, minute=0, second=0, microsecond=0)
        prev_td = get_previous_trading_day(window_end_dt)
        window_start_dt = prev_td.replace(hour=9, minute=0, second=0, microsecond=0)
        
        days_back_param = None
        published_after_dt = window_start_dt
        published_before_dt = window_end_dt
    else:
        now_dt = datetime.now(tz_taipei)
        target_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if now_dt.hour >= 9:
             target_dt += timedelta(days=1)
        
        window_end_dt = target_dt.replace(hour=9, minute=0, second=0, microsecond=0)
        prev_td = get_previous_trading_day(window_end_dt)
        window_start_dt = prev_td.replace(hour=9, minute=0, second=0, microsecond=0)
        
        days_back_param = None
        published_after_dt = window_start_dt
        published_before_dt = window_end_dt

    video_tasks = []
    max_videos = max(1, args.max_videos)

    for item in analysts:
        if len(video_tasks) >= max_videos:
            break

        analyst_name = item["name"]
        channel = item["channel"]
        print(f"[INFO] Analyst={analyst_name}, channel={channel}")

        try:
            channel_id = fetcher.get_channel_id_from_handle(channel)
            if not channel_id:
                run_rows.append({"analyst": analyst_name, "status": "channel_not_found"})
                continue

            videos = fetcher.get_channel_videos(
                channel_id=channel_id,
                max_results=max(1, args.max_videos_per_analyst),
                days_back=days_back_param,
                published_after_dt=published_after_dt,
                published_before_dt=published_before_dt,
                exclude_shorts=args.exclude_shorts,
                min_duration_seconds=args.min_duration_seconds,
            )
            if not videos:
                run_rows.append({"analyst": analyst_name, "status": "no_video"})
                continue

            videos = sorted(
                videos,
                key=lambda video: video.view_count if video.view_count is not None else -1,
                reverse=True,
            )
            updated_video_total += len(videos)

            for video_index, selected_video in enumerate(videos, start=1):
                if len(video_tasks) >= max_videos:
                    break

                video_url = f"https://youtube.com/watch?v={selected_video.video_id}"
                video_tasks.append(
                    {
                        "task_id": len(video_tasks) + 1,
                        "analyst_name": analyst_name,
                        "video": selected_video,
                        "video_url": video_url,
                        "video_index": video_index,
                    }
                )

        except Exception as e:
            run_rows.append({"analyst": analyst_name, "status": "error", "error": str(e)})

    print(f"[INFO] Collected {len(video_tasks)} video tasks")

    download_results = {task["task_id"]: {"ok": True, "audio_path": None, "error": None} for task in video_tasks}

    if mode != "url" and video_tasks:
        download_workers = max(1, min(args.download_workers, len(video_tasks)))
        print(f"[INFO] Stage 1: Pre-downloading audio with {download_workers} workers")

        def _download_task(task):
            downloader = AudioDownloader(settings)
            selected_video = task["video"]
            audio_path = downloader.download(task["video_url"], published_at=selected_video.published_at)
            return task["task_id"], str(audio_path) if audio_path else None

        with ThreadPoolExecutor(max_workers=download_workers) as executor:
            future_to_task = {executor.submit(_download_task, task): task for task in video_tasks}
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                task_id = task["task_id"]
                try:
                    _, audio_path = future.result()
                    if not audio_path:
                        raise RuntimeError("download returned empty path")
                    download_results[task_id] = {"ok": True, "audio_path": audio_path, "error": None}
                except Exception as e:
                    download_results[task_id] = {"ok": False, "audio_path": None, "error": str(e)}

    print("[INFO] Stage 2: Running serial inference with GPU-safe mode")
    for task in video_tasks:
        selected_video = task["video"]
        analyst_name = task["analyst_name"]
        video_index = task["video_index"]
        task_id = task["task_id"]

        if mode != "url" and not download_results[task_id]["ok"]:
            run_rows.append(
                {
                    "analyst": analyst_name,
                    "status": "download_error",
                    "error": download_results[task_id]["error"],
                    "video_id": selected_video.video_id,
                    "video_title": selected_video.title,
                    "video_view_count": selected_video.view_count,
                    "video_published_at": selected_video.published_at,
                    "video_index": video_index,
                }
            )
            continue

        _sleep_before_next_video(processed_video_total)
        processed_video_total += 1

        try:
            analysis = pipeline.process_video(
                video_url=task["video_url"],
                video_id=selected_video.video_id,
                analyst_name=analyst_name,
                skip_download=(mode != "url"),
                mode=mode,
                text_transcript_source=text_source,
            )

            if analysis is None:
                run_rows.append(
                    {
                        "analyst": analyst_name,
                        "status": "analysis_none",
                        "video_id": selected_video.video_id,
                        "video_title": selected_video.title,
                        "video_view_count": selected_video.view_count,
                        "video_published_at": selected_video.published_at,
                        "video_index": video_index,
                    }
                )
            else:
                rec_count, no_rec_count, neutral_count = _count_signal_labels(analysis)
                analyses.append(analysis)
                run_rows.append(
                    {
                        "analyst": analyst_name,
                        "status": "ok",
                        "video_id": analysis.video_id,
                        "video_title": selected_video.title,
                        "signals": len(analysis.signals),
                        "video_view_count": analysis.video_view_count,
                        "video_published_at": analysis.video_published_at,
                        "video_index": video_index,
                        "recommended_count": rec_count,
                        "not_recommended_count": no_rec_count,
                        "neutral_count": neutral_count,
                    }
                )
        except Exception as e:
            run_rows.append(
                {
                    "analyst": analyst_name,
                    "status": "error",
                    "error": str(e),
                    "video_id": selected_video.video_id,
                    "video_title": selected_video.title,
                    "video_view_count": selected_video.view_count,
                    "video_published_at": selected_video.published_at,
                    "video_index": video_index,
                }
            )

    if settings.validate_stock_codes:
        from tw_analyst_pipeline.stock_data.validators import StockValidator
        validator = StockValidator(settings)
        for analysis in analyses:
            analysis.signals = validator.resolve_signals(analysis.signals)

    ordered_stocks, matrix, stock_display = _collect_matrix(analyses)
    stock_rankings = _build_stock_rankings(analyses)

    completed_at_dt = datetime.now(tz_taipei)
    if args.target_date:
        folder_date_str = args.target_date
    else:
        folder_date_str = completed_at_dt.strftime("%Y-%m-%d")
        
    date_folder = settings.data_reports_dir / os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "daily") / folder_date_str
    date_folder.mkdir(parents=True, exist_ok=True)
    timestamp_tag = completed_at_dt.strftime("%Y%m%d_%H%M%S")

    dated_md_file = date_folder / f"analyst_stock_matrix_{timestamp_tag}.md"
    dated_csv_file = date_folder / f"analyst_stock_matrix_{timestamp_tag}.csv"
    dated_summary_file = date_folder / f"daily_run_summary_{timestamp_tag}.json"

    _write_markdown_table(dated_md_file, ordered_stocks, matrix, stock_display, stock_rankings)
    _write_csv_table(dated_csv_file, ordered_stocks, matrix, stock_display)
    summary_payload = {
        "completed_at": completed_at_dt.isoformat(timespec="seconds"),
        "window_start": window_start_dt.isoformat(timespec="minutes"),
        "window_end": window_end_dt.isoformat(timespec="minutes"),
        "tracking_list_count": len(analysts),
        "updated_video_total": updated_video_total,
        "processed_video_total": processed_video_total,
        "count": processed_video_total,
        "max_videos": args.max_videos,
        "max_videos_per_analyst": args.max_videos_per_analyst,
        "date_folder": str(date_folder),
        "timestamp_tag": timestamp_tag,
        "stock_rankings": stock_rankings,
        "dated_files": {
            "markdown": str(dated_md_file),
            "csv": str(dated_csv_file),
            "summary": str(dated_summary_file),
        },
        "items": run_rows,
    }

    _write_run_summary(dated_summary_file, summary_payload)

    print(f"[INFO] Dated markdown: {dated_md_file}")
    print(f"[INFO] Dated csv: {dated_csv_file}")
    print(f"[INFO] Dated summary: {dated_summary_file}")

    try:


        summary_path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
        if summary_path:
            github_summary = Path(summary_path)
            with open(dated_md_file, "r", encoding="utf-8") as f:
                table_md = f.read()
            with open(github_summary, "a", encoding="utf-8") as f:
                f.write("## Daily Analyst x Stock Table\n\n")
                f.write(table_md)
                f.write("\n")
    except Exception:
        pass

    if getattr(args, "cleanup_audio", False):
        print("[INFO] --cleanup-audio was requested, but cleanup is disabled.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
