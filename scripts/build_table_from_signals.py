#!/usr/bin/env python3
"""
Build analyst-stock table from existing signal JSON files.
Useful when a daily run is interrupted but partial signal files already exist.
"""

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List


LABEL_PRIORITY = {
    "買進": 3,
    "賣出": 2,
    "中立": 1,
    "中立": 0,
}


def normalize_label(label):
    if not label:
        return "中立"

    normalized = str(label).strip().lower().replace("_", " ")
    buy_aliases = {"buy", "strong buy", "bullish", "加碼", "買進", "看多", "long"}
    sell_aliases = {"sell", "strong sell", "bearish", "減碼", "賣出", "看空", "short"}
    hold_aliases = {"hold", "neutral", "中立", "觀望", "持有", "wait"}
    ambiguous_aliases = {"ambiguous", "unclear", "mixed", "中立", "不明", "不確定"}

    if normalized in buy_aliases:
        return "買進"
    if normalized in sell_aliases:
        return "賣出"
    if normalized in hold_aliases:
        return "中立"
    if normalized in ambiguous_aliases:
        return "中立"
    return "中立"


def _pick_cell_value(current: str, incoming: str) -> str:
    if not current:
        return incoming
    return incoming if LABEL_PRIORITY.get(incoming, -1) >= LABEL_PRIORITY.get(current, -1) else current


def _load_signal_files(signals_dir: Path, start_dt: datetime, end_dt: datetime) -> List[dict]:
    rows = []
    for path in sorted(signals_dir.rglob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        processed_at_raw = payload.get("processed_at")
        if not processed_at_raw:
            continue

        try:
            processed_at = datetime.fromisoformat(processed_at_raw)
        except Exception:
            continue

        if processed_at.tzinfo is None:
            processed_at = processed_at.replace(tzinfo=timezone.utc)

        if processed_at < start_dt or processed_at > end_dt:
            continue

        if not isinstance(payload.get("signals"), list):
            continue

        rows.append(payload)

    return rows


def _build_matrix(analyses: List[dict]):
    stocks = set()
    stock_display = {}
    matrix: Dict[str, Dict[str, str]] = {}

    for analysis in analyses:
        analyst = analysis.get("analyst_name") or "Unknown"
        matrix.setdefault(analyst, {})

        for signal in analysis.get("signals", []):
            code = signal.get("stock_code")
            if not code:
                continue

            name = signal.get("stock_name") or code
            stocks.add(code)
            stock_display[code] = f"{code} {name}"

            label = normalize_label(signal.get("normalized_label") or signal.get("implied_label"))
            existing = matrix[analyst].get(code, "")
            matrix[analyst][code] = _pick_cell_value(existing, label)

    return sorted(stocks), matrix, stock_display


def _count_recommendations(signals: List[dict]) -> Dict[str, int]:
    recommended = 0
    not_recommended = 0
    neutral = 0

    for signal in signals:
        label = normalize_label(signal.get("normalized_label") or signal.get("implied_label"))
        if label == "買進":
            recommended += 1
        elif label == "賣出":
            not_recommended += 1
        else:
            neutral += 1

    return {
        "recommended_count": recommended,
        "not_recommended_count": not_recommended,
        "neutral_count": neutral,
    }


def _write_markdown(output_file: Path, ordered_stocks: List[str], matrix: Dict[str, Dict[str, str]], stock_display: Dict[str, str]):
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


def _write_csv(output_file: Path, ordered_stocks: List[str], matrix: Dict[str, Dict[str, str]], stock_display: Dict[str, str]):
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build table from existing signal JSON files")
    parser.add_argument("--signals-dir", default="data/signals/daily")
    parser.add_argument("--days-back", type=int, default=1)
    parser.add_argument("--all", action="store_true", help="Use all signal JSON files without time filtering")
    parser.add_argument("--tz-offset-hours", type=int, default=8)
    args = parser.parse_args()

    reports_dir = Path("data/reports")
    tz = timezone(timedelta(hours=args.tz_offset_hours))
    now = datetime.now(tz)
    start_dt = now - timedelta(days=args.days_back)
    end_dt = now

    if args.all:
        start_dt = datetime.min.replace(tzinfo=timezone.utc)
        end_dt = datetime.max.replace(tzinfo=timezone.utc)

    signals_dir = Path(args.signals_dir)
    analyses = _load_signal_files(signals_dir, start_dt, end_dt)
    if not analyses:
        print("[INFO] No matching signal files found; table not generated.")
        return 0

    ordered_stocks, matrix, stock_display = _build_matrix(analyses)

    completed_at = datetime.now(tz)
    date_folder = reports_dir / "daily" / completed_at.strftime("%Y-%m-%d")
    timestamp_tag = completed_at.strftime("%Y%m%d_%H%M%S")

    md_file = date_folder / f"analyst_stock_matrix_from_signals_{timestamp_tag}.md"
    csv_file = date_folder / f"analyst_stock_matrix_from_signals_{timestamp_tag}.csv"

    _write_markdown(md_file, ordered_stocks, matrix, stock_display)
    _write_csv(csv_file, ordered_stocks, matrix, stock_display)

    items = []
    for analysis in sorted(analyses, key=lambda x: x.get("processed_at", "")):
        counts = _count_recommendations(analysis.get("signals", []))
        items.append(
            {
                "analyst": analysis.get("analyst_name") or "Unknown",
                "status": "ok",
                "video_id": analysis.get("video_id"),
                "video_view_count": analysis.get("video_view_count"),
                "video_published_at": analysis.get("video_published_at"),
                "signals": len(analysis.get("signals", [])),
                **counts,
            }
        )

    summary_file = date_folder / f"daily_run_summary_{timestamp_tag}.json"
    summary_payload = {
        "completed_at": completed_at.isoformat(timespec="seconds"),
        "window_start": start_dt.isoformat(timespec="minutes"),
        "window_end": end_dt.isoformat(timespec="minutes"),
        "tracking_list_count": len({item["analyst"] for item in items}),
        "updated_video_total": len(items),
        "processed_video_total": len(items),
        "count": len(items),
        "date_folder": str(date_folder),
        "timestamp_tag": timestamp_tag,
        "dated_files": {
            "markdown": str(md_file),
            "csv": str(csv_file),
            "summary": str(summary_file),
        },
        "items": items,
    }
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_payload, f, ensure_ascii=False, indent=2)

    print(f"[INFO] Analyses used: {len(analyses)}")
    print(f"[INFO] Markdown table: {md_file}")
    print(f"[INFO] CSV table: {csv_file}")
    print(f"[INFO] Summary: {summary_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
