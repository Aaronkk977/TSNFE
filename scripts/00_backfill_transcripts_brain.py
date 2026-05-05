#!/usr/bin/env python3
"""Pipeline brain for transcript backfill on a fixed date range."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def _run_command(command: list[str], env: dict[str, str]) -> int:
    print(f"[Brain] Running: {' '.join(command)}")
    completed = subprocess.run(command, env=env, check=False)
    return completed.returncode


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _collect_transcribed_ids(registry_path: Path, target_ids: set[str]) -> set[str]:
    if not registry_path.exists():
        return set()
    payload = _load_json(registry_path)
    done: set[str] = set()
    for video_id in target_ids:
        row = payload.get(video_id) or {}
        if row.get("status") == "transcribed":
            done.add(video_id)
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill transcripts for a date range")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--analysts-file", default="config/analysts.yaml")
    parser.add_argument("--max-videos", type=int, default=400)
    parser.add_argument("--max-videos-per-analyst", type=int, default=200)
    parser.add_argument("--min-duration-seconds", type=int, default=180)
    parser.add_argument("--max-audio-cache", type=int, default=20)
    parser.add_argument("--transcribe-limit", type=int, default=20)
    parser.add_argument("--max-rounds", type=int, default=120)
    parser.add_argument("--sleep-seconds", type=int, default=20)
    parser.add_argument(
        "--transcription-provider",
        choices=["gemini", "whisper"],
        default=None,
        help="Override config/config.yaml transcription.provider; omit to use YAML",
    )
    parser.add_argument("--text-source", choices=["auto", "cc", "gemini"], default="auto")
    parser.add_argument("--output-subfolder", default="history")
    args = parser.parse_args()

    run_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    repo_root = Path(__file__).resolve().parent.parent
    metadata_dir = repo_root / "data" / "processing" / "metadata"
    pending_latest = metadata_dir / "pending_videos_latest.json"
    registry_path = metadata_dir / "video_registry.json"

    env = os.environ.copy()
    env["PIPELINE_OUTPUT_SUBFOLDER"] = args.output_subfolder

    step1 = [
        sys.executable,
        "scripts/01_fetch_video_list.py",
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
        "--analysts-file",
        args.analysts_file,
        "--max-videos",
        str(args.max_videos),
        "--max-videos-per-analyst",
        str(args.max_videos_per_analyst),
        "--min-duration-seconds",
        str(args.min_duration_seconds),
    ]
    if _run_command(step1, env) != 0:
        print("[Brain] ETL-01 failed, abort.")
        return 1

    if not pending_latest.exists():
        print(f"[Brain] Missing file: {pending_latest}")
        return 1

    pending_payload = _load_json(pending_latest)
    items = pending_payload.get("items", [])
    target_ids = {str(item.get("video_id", "")).strip() for item in items if item.get("video_id")}
    target_ids = {video_id for video_id in target_ids if video_id}
    total = len(target_ids)
    if total == 0:
        print("[Brain] No candidate videos in target date range.")
        return 0

    print(f"[Brain] run_tag={run_tag}, target videos={total}")
    stale_rounds = 0
    prev_done_count = -1

    for round_index in range(1, args.max_rounds + 1):
        print(f"[Brain] ===== Round {round_index}/{args.max_rounds} =====")

        step2 = [
            sys.executable,
            "scripts/02_download_media.py",
            "--input",
            str(pending_latest),
            "--start-date",
            args.start_date,
            "--end-date",
            args.end_date,
            "--max-audio-cache",
            str(args.max_audio_cache),
            "--run-tag",
            run_tag,
        ]
        if _run_command(step2, env) != 0:
            print("[Brain] ETL-02 failed in this round; continue next round.")

        step3 = [
            sys.executable,
            "scripts/03_generate_transcripts.py",
            "--input",
            str(pending_latest),
            "--text-source",
            args.text_source,
            "--limit",
            str(args.transcribe_limit),
            "--run-tag",
            run_tag,
        ]
        if args.transcription_provider:
            step3.extend(["--transcription-provider", args.transcription_provider])
        if _run_command(step3, env) != 0:
            print("[Brain] ETL-03 failed in this round; continue next round.")

        done_ids = _collect_transcribed_ids(registry_path, target_ids)
        done_count = len(done_ids)
        remaining = total - done_count
        print(f"[Brain] Progress: transcribed={done_count}/{total}, remaining={remaining}")

        if done_count >= total:
            print("[Brain] Completed target range transcript backfill.")
            return 0

        if done_count == prev_done_count:
            stale_rounds += 1
        else:
            stale_rounds = 0
            prev_done_count = done_count

        if stale_rounds >= 5:
            print("[Brain] No progress for 5 rounds. Stop to avoid endless loop.")
            break

        time.sleep(max(0, args.sleep_seconds))

    done_ids = _collect_transcribed_ids(registry_path, target_ids)
    remaining_ids = sorted(target_ids - done_ids)
    print(f"[Brain] Incomplete after max rounds. Remaining={len(remaining_ids)}")
    if remaining_ids:
        preview = ", ".join(remaining_ids[:20])
        print(f"[Brain] Remaining video_ids (first 20): {preview}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
