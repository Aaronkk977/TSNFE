#!/usr/bin/env python3
"""Remove low-quality videos and related local assets.

Rules (default):
- view_count < 3000, OR
- duration_seconds < 180
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.utils.config import get_settings
from etl_common import read_json, write_json


def _parse_duration_seconds(value) -> int:
    """Support int seconds or HH:MM:SS / MM:SS / SS strings."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)

    parts = text.split(":")
    if not all(p.isdigit() for p in parts):
        return 0

    if len(parts) == 3:
        h, m, s = (int(parts[0]), int(parts[1]), int(parts[2]))
        return h * 3600 + m * 60 + s
    if len(parts) == 2:
        m, s = (int(parts[0]), int(parts[1]))
        return m * 60 + s
    if len(parts) == 1:
        return int(parts[0])
    return 0


def _safe_unlink(path: Path, dry_run: bool) -> bool:
    if not path.exists() or not path.is_file():
        return False
    if dry_run:
        return True
    path.unlink(missing_ok=True)
    return True


def _collect_candidate_files(video_id: str, raw_dir: Path, transcripts_dir: Path) -> set[Path]:
    files: set[Path] = set()
    if not video_id:
        return files

    files.update(p for p in raw_dir.glob(f"**/*{video_id}*.wav") if p.is_file())
    files.update(p for p in transcripts_dir.glob(f"**/{video_id}_*.json") if p.is_file())
    files.update(p for p in transcripts_dir.glob(f"**/{video_id}.json") if p.is_file())
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean low-quality videos and related audio/CC files")
    parser.add_argument(
        "--registry",
        default="data/processing/metadata/video_registry.json",
        help="Path to video_registry.json",
    )
    parser.add_argument("--min-views", type=int, default=1500, help="Remove if view_count is lower than this")
    parser.add_argument("--min-duration-seconds", type=int, default=180, help="Remove if duration is shorter than this")
    parser.add_argument("--dry-run", action="store_true", help="Only print what would be removed")
    args = parser.parse_args()

    settings = get_settings()
    registry_path = Path(args.registry)
    if not registry_path.exists():
        raise FileNotFoundError(f"Registry not found: {registry_path}")

    registry = read_json(registry_path)
    if not isinstance(registry, dict):
        raise ValueError("Registry must be a JSON object keyed by video_id")

    raw_dir = Path(settings.data_raw_dir)
    transcripts_dir = Path(settings.data_transcripts_dir)

    remove_ids: list[str] = []
    deleted_files: list[Path] = []

    for video_id, item in registry.items():
        if not isinstance(item, dict):
            continue

        view_count = int(item.get("view_count") or 0)
        duration_seconds = _parse_duration_seconds(item.get("duration"))

        should_remove = (view_count < args.min_views) or (duration_seconds < args.min_duration_seconds)
        if not should_remove:
            continue

        remove_ids.append(video_id)
        candidate_files = _collect_candidate_files(video_id, raw_dir=raw_dir, transcripts_dir=transcripts_dir)

        audio_path = item.get("audio_path")
        transcript_path = item.get("transcript_path")
        if audio_path:
            candidate_files.add(Path(str(audio_path)))
        if transcript_path:
            candidate_files.add(Path(str(transcript_path)))

        for file_path in sorted(candidate_files):
            if _safe_unlink(file_path, dry_run=args.dry_run):
                deleted_files.append(file_path)

    for video_id in remove_ids:
        registry.pop(video_id, None)

    if not args.dry_run:
        write_json(registry_path, registry)

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] removed_videos={len(remove_ids)} removed_files={len(deleted_files)}")
    if remove_ids:
        print(f"[{mode}] first_10_video_ids={remove_ids[:10]}")
    if deleted_files:
        preview = [str(p) for p in deleted_files[:10]]
        print(f"[{mode}] first_10_deleted_files={preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
