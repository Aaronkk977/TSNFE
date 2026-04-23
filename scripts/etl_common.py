#!/usr/bin/env python3
"""Shared utilities for ETL-style scripts in scripts/01-06."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml


TZ_TAIPEI = timezone(timedelta(hours=8))


def load_analysts(analysts_file: Path) -> List[Dict[str, str]]:
    if not analysts_file.exists():
        raise FileNotFoundError(f"Analysts file not found: {analysts_file}")

    with open(analysts_file, "r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}

    analysts = payload.get("analysts", [])
    if not isinstance(analysts, list) or not analysts:
        raise ValueError("config/analysts.yaml must contain a non-empty 'analysts' list")

    cleaned: List[Dict[str, str]] = []
    for row in analysts:
        name = str(row.get("name", "")).strip()
        channel = str(row.get("channel", "")).strip()
        if not name or not channel:
            continue
        cleaned.append({"name": name, "channel": channel})

    if not cleaned:
        raise ValueError("No valid analyst rows in analysts file")
    return cleaned


def normalize_channel(channel: str) -> str:
    channel = (channel or "").strip()
    if "youtube.com/@" in channel:
        channel = "@" + channel.split("@", 1)[1].split("/", 1)[0]
    if channel and not channel.startswith("@"):
        channel = f"@{channel}"
    return channel


def get_previous_trading_day(dt: datetime) -> datetime:
    prev = dt - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


def resolve_window(target_date: str | None) -> tuple[datetime, datetime, str]:
    if target_date:
        target_dt = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=TZ_TAIPEI)
    else:
        now_dt = datetime.now(TZ_TAIPEI)
        target_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if now_dt.hour >= 9:
            target_dt += timedelta(days=1)

    window_end_dt = target_dt.replace(hour=9, minute=0, second=0, microsecond=0)
    prev_td = get_previous_trading_day(window_end_dt)
    window_start_dt = prev_td.replace(hour=9, minute=0, second=0, microsecond=0)
    folder_date = target_dt.strftime("%Y-%m-%d") if not target_date else target_date
    return window_start_dt, window_end_dt, folder_date


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def latest_by_pattern(directory: Path, pattern: str) -> Path | None:
    candidates = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def sanitize_date_tag(date_text: str) -> str:
    return re.sub(r"[^0-9]", "", date_text)[:8]
