#!/usr/bin/env python3
"""ETL-05: Build markdown/csv daily reports from saved signal JSON files."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.extraction.schemas import VideoAnalysis, normalize_label
from tw_analyst_pipeline.stock_data.validators import StockValidator
from tw_analyst_pipeline.utils.config import get_pipeline_config, get_settings
from tw_analyst_pipeline.utils.logging import setup_logging

from etl_common import TZ_TAIPEI, write_json


LABEL_PRIORITY = {
    "買進": 3,
    "賣出": 2,
    "中立": 1,
    "模糊": 0,
}


def _convert_to_traditional(text: str) -> str:
    try:
        import opencc

        converter = opencc.OpenCC("s2twp")
        return converter.convert(text or "")
    except Exception:
        return text or ""


def _signal_label(signal) -> str:
    label = normalize_label(signal.normalized_label or signal.implied_label)
    if label != "中立":
        return label

    action_value = str(getattr(getattr(signal, "action", None), "value", getattr(signal, "action", ""))).strip().lower()
    if action_value == "buy":
        return "買進"
    if action_value == "sell":
        return "賣出"
    if action_value == "hold":
        return "中立"
    return label


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

            label = _signal_label(signal)
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
            label = _signal_label(signal)

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
        ordered = sorted(source.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        return [
            {
                "stock_code": code,
                "stock_name": stock_names.get(code, code),
                "analyst_count": len(analyst_set),
            }
            for code, analyst_set in ordered[:limit]
        ]

    def _top_by_view(source: dict) -> List[dict]:
        ordered = sorted(source.items(), key=lambda kv: (-kv[1], kv[0]))
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


def _load_signal_analyses(signal_dir: Path) -> List[VideoAnalysis]:
    analyses: List[VideoAnalysis] = []
    if not signal_dir.exists():
        return analyses

    for path in sorted(signal_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            analyses.append(VideoAnalysis(**payload))
        except Exception:
            continue

    return analyses


def _iter_dates(
    signal_base_dir: Path,
    target_date: str | None,
    start_date: str | None,
    end_date: str | None,
    all_dates: bool,
) -> List[str]:
    if target_date:
        return [target_date]

    if all_dates:
        return sorted([d.name for d in signal_base_dir.iterdir() if d.is_dir()]) if signal_base_dir.exists() else []

    if start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        if start > end:
            raise ValueError("--start-date must be <= --end-date")
        days = []
        cur = start
        while cur <= end:
            days.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        return days

    raise ValueError("Use one mode: --target-date, --start-date+--end-date, or --all-dates")


def _build_one_date(settings, target_date: str) -> tuple[bool, list]:
    subfolder = os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "daily")
    signal_dir = settings.data_signals_dir / subfolder / target_date
    analyses = _load_signal_analyses(signal_dir)
    if not analyses:
        print(f"[WARN] No signal json found under {signal_dir}")
        return False, []

    # Always apply smart validation for report generation:
    # alias/name/code resolution + fuzzy matching + optional homophone correction.
    validator = StockValidator(settings)
    registry = {}
    registry_path = settings.data_metadata_dir / "video_registry.json"
    if registry_path.exists():
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
        except Exception:
            registry = {}

    dropped_signals = []
    for analysis in analyses:
        # Normalize Chinese variant before validation/matching.
        for sig in analysis.signals:
            if getattr(sig, "stock_name", None):
                sig.stock_name = _convert_to_traditional(sig.stock_name)
            if getattr(sig, "reasoning", None):
                sig.reasoning = _convert_to_traditional(sig.reasoning)
            if getattr(sig, "label_reason", None):
                sig.label_reason = _convert_to_traditional(sig.label_reason)

        validated, dropped = validator.resolve_signals(analysis.signals, include_dropped=True)
        analysis.signals = validated
        video_meta = registry.get(analysis.video_id, {}) if isinstance(registry, dict) else {}
        for row in dropped:
            dropped_signals.append(
                {
                    "report_date": target_date,
                    "video_id": analysis.video_id,
                    "analyst_name": analysis.analyst_name,
                    "video_title": video_meta.get("title"),
                    "video_published_at": analysis.video_published_at or video_meta.get("published_at"),
                    **row,
                }
            )

    ordered_stocks, matrix, stock_display = _collect_matrix(analyses)
    stock_rankings = _build_stock_rankings(analyses)

    now = datetime.now(TZ_TAIPEI)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    out_dir = settings.data_reports_dir / subfolder / target_date
    out_dir.mkdir(parents=True, exist_ok=True)

    md_file = out_dir / f"analyst_stock_matrix_{timestamp}.md"
    csv_file = out_dir / f"analyst_stock_matrix_{timestamp}.csv"
    summary_file = out_dir / f"daily_run_summary_{timestamp}.json"

    _write_markdown_table(md_file, ordered_stocks, matrix, stock_display, stock_rankings)
    _write_csv_table(csv_file, ordered_stocks, matrix, stock_display)

    summary_payload = {
        "completed_at": now.isoformat(timespec="seconds"),
        "target_date": target_date,
        "signal_file_count": len(analyses),
        "stock_rankings": stock_rankings,
        "dropped_signal_count": len(dropped_signals),
        "dated_files": {
            "markdown": str(md_file),
            "csv": str(csv_file),
            "summary": str(summary_file),
        },
    }
    write_json(summary_file, summary_payload)

    latest_payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "target_date": target_date,
        "count": len(analyses),
        "items": [{"video_id": a.video_id, "analyst_name": a.analyst_name, "signal_count": len(a.signals)} for a in analyses],
    }
    write_json(settings.data_metadata_dir / "report_status_latest.json", latest_payload)

    print(f"[INFO] Markdown report: {md_file}")
    print(f"[INFO] CSV report: {csv_file}")
    print(f"[INFO] Summary report: {summary_file}")
    return True, dropped_signals


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL-05 build daily reports")
    parser.add_argument("--target-date", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--start-date", type=str, default=None, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end-date", type=str, default=None, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--all-dates", action="store_true", help="Build reports for every signal date folder")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    args = parser.parse_args()

    setup_logging(level=args.log_level)
    settings = get_settings()
    pipeline_config = get_pipeline_config()
    settings.stock_validation_provider = (
        pipeline_config.get("stock_data.validation_provider") or settings.stock_validation_provider
    )

    subfolder = os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "daily")
    signal_base_dir = settings.data_signals_dir / subfolder
    try:
        dates = _iter_dates(signal_base_dir, args.target_date, args.start_date, args.end_date, args.all_dates)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1

    if not dates:
        print(f"[WARN] No date folders found under {signal_base_dir}")
        return 1

    ok = 0
    run_started = datetime.now(TZ_TAIPEI)
    all_dropped: list = []
    for day in dates:
        success, dropped_rows = _build_one_date(settings, day)
        if success:
            ok += 1
        all_dropped.extend(dropped_rows)

    subfolder = os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "daily")
    history_reports = settings.data_reports_dir / subfolder
    dropped_bundle = history_reports / f"dropped_signals_{run_started.strftime('%Y%m%d_%H%M%S')}.json"
    write_json(
        dropped_bundle,
        {
            "generated_at": datetime.now(TZ_TAIPEI).isoformat(timespec="seconds"),
            "subfolder": subfolder,
            "date_range": {"start": dates[0], "end": dates[-1], "count_days": len(dates)},
            "total_dropped": len(all_dropped),
            "items": all_dropped,
        },
    )
    print(f"[INFO] Consolidated dropped signals: {dropped_bundle}")

    print(f"[INFO] Report build completed: {ok}/{len(dates)} date folders")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
