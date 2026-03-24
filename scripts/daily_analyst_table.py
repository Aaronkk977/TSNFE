#!/usr/bin/env python3
"""
Daily automation script:
- Read analyst channels from config/analysts.yaml
- Process latest video for each analyst
- Output analyst (rows) x stock (columns) table in markdown/csv
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.extraction.schemas import VideoAnalysis, normalize_label
from tw_analyst_pipeline.pipeline.orchestrator import SignalPipeline
from tw_analyst_pipeline.utils.config import get_pipeline_config, get_settings
from tw_analyst_pipeline.utils.logging import setup_logging
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

    with open(analysts_file, "r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}

    analysts = payload.get("analysts", [])
    if not isinstance(analysts, list) or not analysts:
        raise ValueError("config/analysts.yaml must contain a non-empty 'analysts' list")

    cleaned = []
    for row in analysts:
        name = str(row.get("name", "")).strip()
        channel = str(row.get("channel", "")).strip()
        if not name or not channel:
            continue
        cleaned.append({"name": name, "channel": channel})

    if not cleaned:
        raise ValueError("No valid analyst rows in config/analysts.yaml")
    return cleaned


def _pick_cell_value(current: str, incoming: str) -> str:
    if not current:
        return incoming
    return incoming if LABEL_PRIORITY.get(incoming, -1) >= LABEL_PRIORITY.get(current, -1) else current


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


def _write_run_summary(output_file: Path, rows: List[dict]):
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"count": len(rows), "items": rows}, f, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate daily analyst x stock matrix")
    parser.add_argument("--analysts-file", default="config/analysts.yaml")
    parser.add_argument("--max-videos", type=int, default=1)
    parser.add_argument("--days-back", type=int, default=2)
    parser.add_argument("--mode", choices=["audio", "url", "text"], default=None)
    parser.add_argument("--text-source", choices=["auto", "cc", "gemini"], default=None)
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    args = parser.parse_args()

    setup_logging(level=args.log_level)

    settings = get_settings()
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

    for item in analysts:
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
                max_results=max(1, args.max_videos),
                days_back=args.days_back,
            )
            if not videos:
                run_rows.append({"analyst": analyst_name, "status": "no_video"})
                continue

            selected_video = videos[0]
            video_url = f"https://youtube.com/watch?v={selected_video.video_id}"

            analysis = pipeline.process_video(
                video_url=video_url,
                video_id=selected_video.video_id,
                analyst_name=analyst_name,
                skip_download=False,
                mode=mode,
                text_transcript_source=text_source,
            )

            if analysis is None:
                run_rows.append({"analyst": analyst_name, "status": "analysis_none"})
                continue

            analyses.append(analysis)
            run_rows.append(
                {
                    "analyst": analyst_name,
                    "status": "ok",
                    "video_id": analysis.video_id,
                    "signals": len(analysis.signals),
                }
            )

        except Exception as e:
            run_rows.append({"analyst": analyst_name, "status": "error", "error": str(e)})

    ordered_stocks, matrix, stock_display = _collect_matrix(analyses)

    md_file = settings.data_reports_dir / "analyst_stock_matrix.md"
    csv_file = settings.data_reports_dir / "analyst_stock_matrix.csv"
    summary_file = settings.data_reports_dir / "daily_run_summary.json"

    _write_markdown_table(md_file, ordered_stocks, matrix, stock_display)
    _write_csv_table(csv_file, ordered_stocks, matrix, stock_display)
    _write_run_summary(summary_file, run_rows)

    print(f"[INFO] Markdown table: {md_file}")
    print(f"[INFO] CSV table: {csv_file}")
    print(f"[INFO] Summary: {summary_file}")

    try:
        import os

        summary_path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
        if summary_path:
            github_summary = Path(summary_path)
            with open(md_file, "r", encoding="utf-8") as f:
                table_md = f.read()
            with open(github_summary, "a", encoding="utf-8") as f:
                f.write("## Daily Analyst x Stock Table\n\n")
                f.write(table_md)
                f.write("\n")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
