#!/usr/bin/env python3
"""ETL-07: Aggregate all signal JSONs into a single dataset CSV.

Output columns:
    analyst, view_count, stock_code, stock_name, action,
    published_date (YYYY-MM-DD, TPE), next_trading_day (YYYY-MM-DD)

- Uses the same smart validator pipeline as scripts/05_build_daily_reports.py
  (Traditional Chinese conversion + StockValidator.resolve_signals) so the
  resulting dataset is consistent with the daily reports.
- Hold (中立) signals are dropped.
- "next_trading_day" is the next TWSE trading day at/after the video
  publish moment, derived from yfinance trading-day calendar.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.extraction.schemas import VideoAnalysis, normalize_label
from tw_analyst_pipeline.stock_data.validators import StockValidator
from tw_analyst_pipeline.utils.config import get_pipeline_config, get_settings
from tw_analyst_pipeline.utils.logging import setup_logging

from etl_common import TZ_TAIPEI, write_json


# ---------------------------------------------------------------------------
# Helpers shared in spirit with scripts/05_build_daily_reports.py
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Trading day calendar (yfinance, cached on disk)
# ---------------------------------------------------------------------------


def _load_calendar_cache(cache_path: Path) -> set[date]:
    if not cache_path.exists():
        return set()
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {datetime.strptime(d, "%Y-%m-%d").date() for d in data.get("trading_days", [])}
    except Exception:
        return set()


def _save_calendar_cache(cache_path: Path, trading_days: Iterable[date]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(TZ_TAIPEI).isoformat(timespec="seconds"),
        "trading_days": sorted(d.strftime("%Y-%m-%d") for d in trading_days),
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _fetch_trading_days_from_yf(start: date, end: date) -> set[date]:
    """Use yfinance + a liquid Taiwan ticker (2330.TW) as the canonical
    TWSE trading-day calendar."""
    import yfinance as yf

    ticker = yf.Ticker("2330.TW")
    df = ticker.history(start=start.strftime("%Y-%m-%d"), end=(end + timedelta(days=1)).strftime("%Y-%m-%d"))
    if df is None or df.empty:
        return set()
    return {ts.date() for ts in df.index.to_pydatetime()}


def _build_trading_calendar(min_date: date, max_date: date, cache_path: Path) -> set[date]:
    """Return a set of TWSE trading days covering [min_date, max_date].

    Uses an on-disk cache so we don't hit the network on every run.
    """
    fetch_start = min_date - timedelta(days=10)
    fetch_end = max_date + timedelta(days=45)

    cached = _load_calendar_cache(cache_path)
    needs_refresh = (
        not cached
        or min(cached) > fetch_start
        or max(cached) < fetch_end
    )

    if needs_refresh:
        try:
            fetched = _fetch_trading_days_from_yf(fetch_start, fetch_end)
            if fetched:
                cached = cached.union(fetched)
                _save_calendar_cache(cache_path, cached)
        except Exception as exc:
            print(f"[WARN] Failed to refresh trading calendar via yfinance: {exc}")

    return cached


def _is_trading_day(d: date, calendar: set[date]) -> bool:
    if calendar:
        return d in calendar
    return d.weekday() < 5


def _next_trading_day(reference_dt: datetime, calendar: set[date]) -> Optional[date]:
    """Find the next TWSE trading day strictly *after* reference_dt.

    If reference_dt falls before 09:00 TPE on a trading day, that same date
    counts as the "next opening day" because the market hasn't opened yet.
    """
    ref_tpe = reference_dt.astimezone(TZ_TAIPEI)
    candidate = ref_tpe.date()
    market_open_today = ref_tpe.replace(hour=9, minute=0, second=0, microsecond=0)

    if ref_tpe < market_open_today and _is_trading_day(candidate, calendar):
        return candidate

    candidate += timedelta(days=1)
    for _ in range(45):
        if _is_trading_day(candidate, calendar):
            return candidate
        candidate += timedelta(days=1)
    return None


# ---------------------------------------------------------------------------
# Datetime parsing for the pipeline's published_at strings
# ---------------------------------------------------------------------------


def _parse_published_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    # Some entries have malformed suffixes like "+00:00Z" - strip trailing Z
    # only when an explicit offset is already present.
    if s.endswith("Z") and ("+" in s or "-" in s[10:]):
        s = s[:-1]
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _iter_signal_files(signal_root: Path) -> List[Path]:
    if not signal_root.exists():
        return []
    files: List[Path] = []
    for date_dir in sorted([p for p in signal_root.iterdir() if p.is_dir()]):
        files.extend(sorted(date_dir.glob("*.json")))
    return files


def _load_video_registry(settings) -> dict:
    registry_path = settings.data_metadata_dir / "video_registry.json"
    if not registry_path.exists():
        return {}
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _build_rows(
    signal_files: List[Path],
    settings,
    registry: dict,
) -> Tuple[List[dict], List[date]]:
    validator = StockValidator(settings)

    rows: List[dict] = []
    publish_dates: List[date] = []

    for path in signal_files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            analysis = VideoAnalysis(**payload)
        except Exception as exc:
            print(f"[WARN] Skipping unreadable signal file {path}: {exc}")
            continue

        for sig in analysis.signals:
            if getattr(sig, "stock_name", None):
                sig.stock_name = _convert_to_traditional(sig.stock_name)
            if getattr(sig, "reasoning", None):
                sig.reasoning = _convert_to_traditional(sig.reasoning)
            if getattr(sig, "label_reason", None):
                sig.label_reason = _convert_to_traditional(sig.label_reason)

        validated, _dropped = validator.resolve_signals(analysis.signals, include_dropped=True)
        if not validated:
            continue

        published_raw = analysis.video_published_at or registry.get(analysis.video_id, {}).get("published_at")
        published_dt = _parse_published_at(published_raw)
        published_date_tpe = published_dt.astimezone(TZ_TAIPEI).date() if published_dt else None
        if published_date_tpe is not None:
            publish_dates.append(published_date_tpe)

        analyst = analysis.analyst_name or "Unknown"
        view_count = int(analysis.video_view_count or 0)

        for sig in validated:
            label = _signal_label(sig)
            if label == "中立":
                continue
            rows.append(
                {
                    "analyst": analyst,
                    "view_count": view_count,
                    "stock_code": sig.stock_code,
                    "stock_name": sig.stock_name or "",
                    "action": label,  # 買進 / 賣出
                    "published_date": published_date_tpe.strftime("%Y-%m-%d") if published_date_tpe else "",
                    "_published_dt": published_dt,
                    "video_id": analysis.video_id,
                }
            )

    return rows, publish_dates


def _attach_next_trading_day(rows: List[dict], calendar: set[date]) -> None:
    for row in rows:
        published_dt = row.pop("_published_dt", None)
        if published_dt is None:
            row["next_trading_day"] = ""
            continue
        nxt = _next_trading_day(published_dt, calendar)
        row["next_trading_day"] = nxt.strftime("%Y-%m-%d") if nxt else ""


def _dedupe_rows(rows: List[dict]) -> List[dict]:
    """Within the same video drop duplicate (stock, action) combinations.

    Across videos (different analyst or same analyst on a different day) the
    rows are kept as separate observations.
    """
    seen: set[Tuple[str, str, str]] = set()
    out: List[dict] = []
    for row in rows:
        key = (row.get("video_id", ""), row.get("stock_code", ""), row.get("action", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL-07 build signals dataset")
    parser.add_argument(
        "--subfolder",
        type=str,
        default=os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "history"),
        help="signals subfolder (default: $PIPELINE_OUTPUT_SUBFOLDER or 'history')",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV path. Defaults to data/processed/signals_dataset_<ts>.csv",
    )
    parser.add_argument("--no-dedupe", action="store_true", help="Keep duplicate rows within the same video")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    args = parser.parse_args()

    setup_logging(level=args.log_level)
    settings = get_settings()
    pipeline_config = get_pipeline_config()
    settings.stock_validation_provider = (
        pipeline_config.get("stock_data.validation_provider") or settings.stock_validation_provider
    )

    signal_root = settings.data_signals_dir / args.subfolder
    signal_files = _iter_signal_files(signal_root)
    if not signal_files:
        print(f"[ERROR] No signal JSON files found under {signal_root}")
        return 1
    print(f"[INFO] Loading {len(signal_files)} signal files from {signal_root}")

    registry = _load_video_registry(settings)
    rows, publish_dates = _build_rows(signal_files, settings, registry)

    if not rows:
        print("[WARN] No buy/sell rows produced after validation.")
        return 1

    if not args.no_dedupe:
        rows = _dedupe_rows(rows)

    if publish_dates:
        cal_min = min(publish_dates)
        cal_max = max(publish_dates)
    else:
        today = datetime.now(TZ_TAIPEI).date()
        cal_min = today - timedelta(days=30)
        cal_max = today + timedelta(days=10)

    cache_path = settings.data_metadata_dir / "twse_trading_days.json"
    calendar = _build_trading_calendar(cal_min, cal_max, cache_path)
    print(f"[INFO] Trading calendar: {len(calendar)} days available, range covering {cal_min} ~ {cal_max}")

    _attach_next_trading_day(rows, calendar)

    rows.sort(key=lambda r: (r.get("published_date", ""), r.get("analyst", ""), r.get("stock_code", "")))

    if args.output:
        out_path = Path(args.output)
    else:
        ts = datetime.now(TZ_TAIPEI).strftime("%Y%m%d_%H%M%S")
        out_path = Path(settings.data_dir) / "processed" / f"signals_dataset_{ts}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "analyst",
        "view_count",
        "stock_code",
        "stock_name",
        "action",
        "published_date",
        "next_trading_day",
        "video_id",
    ]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    summary_path = out_path.with_suffix(".summary.json")
    write_json(
        summary_path,
        {
            "generated_at": datetime.now(TZ_TAIPEI).isoformat(timespec="seconds"),
            "subfolder": args.subfolder,
            "signal_file_count": len(signal_files),
            "row_count": len(rows),
            "buy_count": sum(1 for r in rows if r.get("action") == "買進"),
            "sell_count": sum(1 for r in rows if r.get("action") == "賣出"),
            "publish_date_range": {
                "min": min(publish_dates).strftime("%Y-%m-%d") if publish_dates else None,
                "max": max(publish_dates).strftime("%Y-%m-%d") if publish_dates else None,
            },
            "output_csv": str(out_path),
        },
    )

    print(f"[INFO] Dataset written: {out_path} ({len(rows)} rows)")
    print(f"[INFO] Summary written: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
