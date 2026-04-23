#!/usr/bin/env python3
"""ETL-01: Fetch candidate video list only (no download/transcription/LLM)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.utils.config import get_settings
from tw_analyst_pipeline.utils.logging import setup_logging
from tw_analyst_pipeline.youtube.fetcher import YouTubeFetcher

from etl_common import load_analysts, normalize_channel, resolve_window, sanitize_date_tag, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL-01 fetch pending video list")
    parser.add_argument("--analysts-file", default="config/analysts.yaml")
    parser.add_argument("--max-videos", type=int, default=20, help="Global max candidate videos")
    parser.add_argument("--max-videos-per-analyst", type=int, default=50)
    parser.add_argument("--start-date", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--target-date", type=str, default=None, help="Deprecated: use --start-date and --end-date")
    parser.add_argument("--exclude-shorts", action="store_true", default=True)
    parser.add_argument("--include-shorts", dest="exclude_shorts", action="store_false")
    parser.add_argument("--min-duration-seconds", type=int, default=180)
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    args = parser.parse_args()

    setup_logging(level=args.log_level)
    settings = get_settings()
    fetcher = YouTubeFetcher(settings)

    analysts = load_analysts(Path(args.analysts_file))

    from datetime import timezone, timedelta
    TZ_TAIPEI = timezone(timedelta(hours=8))
    
    if args.start_date and args.end_date:
        window_start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=TZ_TAIPEI, hour=0, minute=0, second=0, microsecond=0)
        window_end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=TZ_TAIPEI, hour=23, minute=59, second=59, microsecond=999999)
        folder_date = f"{args.start_date}_to_{args.end_date}"
    else:
        window_start_dt, window_end_dt, folder_date = resolve_window(args.target_date)

    tasks = []
    max_videos = max(1, args.max_videos)

    for row in analysts:
        if len(tasks) >= max_videos:
            break

        analyst_name = row["name"]
        channel = normalize_channel(row["channel"])
        print(f"[INFO] Analyst={analyst_name}, channel={channel}")

        channel_id = fetcher.get_channel_id_from_handle(channel)
        if not channel_id:
            print(f"[WARN] Channel not found: {channel}")
            continue

        videos = fetcher.get_channel_videos(
            channel_id=channel_id,
            max_results=max(1, args.max_videos_per_analyst),
            days_back=None,
            published_after_dt=window_start_dt,
            published_before_dt=window_end_dt,
            exclude_shorts=args.exclude_shorts,
            min_duration_seconds=args.min_duration_seconds,
        )
        if not videos:
            continue

        videos = sorted(videos, key=lambda v: v.view_count if v.view_count is not None else -1, reverse=True)
        for video in videos:
            if len(tasks) >= max_videos:
                break
            tasks.append(
                {
                    "video_id": video.video_id,
                    "video_url": f"https://youtube.com/watch?v={video.video_id}",
                    "title": video.title,
                    "published_at": video.published_at,
                    "view_count": video.view_count,
                    "duration": video.duration,
                    "analyst_name": analyst_name,
                    "channel": channel,
                    "channel_id": channel_id,
                    "status": "pending",
                }
            )

    deduped = {}
    for item in tasks:
        key = item["video_id"]
        old = deduped.get(key)
        if old is None:
            deduped[key] = item
            continue
        old_vc = old.get("view_count") or -1
        new_vc = item.get("view_count") or -1
        if new_vc > old_vc:
            deduped[key] = item

    pending_items = list(deduped.values())
    
    # -----------------------------------------------------
    # REGISTRY: Update shared video_registry
    # -----------------------------------------------------
    import json
    registry_path = settings.data_metadata_dir / "video_registry.json"
    registry = {}
    if registry_path.exists():
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
        except Exception:
            pass
            
    for item in pending_items:
        v_id = item["video_id"]
        if v_id not in registry:
            registry[v_id] = item
        else:
            # Update fields but keep existing status if it has progressed
            existing_status = registry[v_id].get("status", "discovered")
            if existing_status in ["discovered", "pending"]:
                registry[v_id].update(item)
            else:
                registry[v_id]["view_count"] = max(registry[v_id].get("view_count") or -1, item.get("view_count") or -1)

    write_json(registry_path, registry)

    now = datetime.now()
    tag = sanitize_date_tag(folder_date)
    output = settings.data_metadata_dir / f"pending_videos_{tag}.json"

    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_start": window_start_dt.isoformat(timespec="minutes"),
        "window_end": window_end_dt.isoformat(timespec="minutes"),
        "target_date": folder_date,
        "count": len(pending_items),
        "items": pending_items,
    }
    write_json(output, payload)
    write_json(settings.data_metadata_dir / "pending_videos_latest.json", payload)

    print(f"[INFO] Pending list saved: {output}")
    print(f"[INFO] Total pending videos: {len(pending_items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
