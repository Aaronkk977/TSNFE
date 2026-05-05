#!/usr/bin/env python3
"""Delete cached transcript JSON and reset registry so ETL-03 will transcribe again.

Use after incomplete Gemini/Whisper runs (e.g. old max_output_tokens limit).

Example:
  python scripts/requeue_transcription.py --video-id yXi1WrvGIuI J--QFqYe9kw
  python scripts/requeue_transcription.py --ids-file ids.txt
  python scripts/requeue_transcription.py --scan-dir data/processing/transcripts/history
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.utils.config import get_settings
from tw_analyst_pipeline.youtube.downloader import AudioDownloader

from etl_common import write_json

# ETL 存檔格式: {video_id}_{YYYYMMDD}_{HHMMSS}.json（video_id 可含底線）
_STEM_RE = re.compile(r"^(.+)_(\d{8})_(\d{6})$")


def _video_id_from_transcript_path(path: Path) -> str | None:
    stem = path.stem
    m = _STEM_RE.match(stem)
    if m:
        return m.group(1)
    return None


def _ids_from_scan_dir(scan_dir: Path) -> list[str]:
    if not scan_dir.is_dir():
        raise FileNotFoundError(f"Not a directory: {scan_dir}")
    found: list[str] = []
    for path in scan_dir.rglob("*.json"):
        if not path.is_file():
            continue
        vid = _video_id_from_transcript_path(path)
        if vid:
            found.append(vid)
    return list(dict.fromkeys(found))


def _collect_ids(args: argparse.Namespace) -> list[str]:
    ids: list[str] = []
    for x in args.video_id or []:
        x = str(x).strip()
        if x:
            ids.append(x)
    if args.ids_file:
        text = Path(args.ids_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ids.append(line)
    if args.scan_dir:
        scan_path = Path(args.scan_dir)
        for vid in _ids_from_scan_dir(scan_path):
            ids.append(vid)
    return list(dict.fromkeys(ids))


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-queue videos for transcription (ETL-03)")
    parser.add_argument("--video-id", nargs="*", default=[], help="YouTube video id(s)")
    parser.add_argument("--ids-file", type=str, default=None, help="One video id per line")
    parser.add_argument(
        "--scan-dir",
        type=str,
        default=None,
        help="Recursively scan for transcript JSON (*_YYYYMMDD_HHMMSS.json) and re-queue all video_ids found",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions only; do not delete files or write registry",
    )
    args = parser.parse_args()

    ids = _collect_ids(args)
    if not ids:
        print("No video ids: use --video-id, --ids-file, or --scan-dir", file=sys.stderr)
        return 2

    settings = get_settings()
    downloader = AudioDownloader(settings)
    registry_path = settings.data_metadata_dir / "video_registry.json"
    if not registry_path.exists():
        print(f"Missing registry: {registry_path}", file=sys.stderr)
        return 1

    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    transcript_root = settings.data_transcripts_dir
    deleted_files = 0

    for vid in ids:
        if vid not in registry:
            print(f"[WARN] {vid} not in video_registry.json, skip registry update")
        elif not args.dry_run:
            row = registry[vid]
            audio_path = None
            ap = row.get("audio_path")
            if ap:
                p = Path(str(ap))
                if p.is_file():
                    audio_path = p
            if audio_path is None:
                found = downloader._find_latest_audio_file(vid)
                if found is not None:
                    audio_path = found

            if audio_path is not None:
                row["status"] = "audio_ready"
                row["audio_path"] = str(audio_path)
            else:
                row["status"] = "cc_ready"
            row.pop("transcript_path", None)

        pattern = f"{vid}_*.json"
        for path in transcript_root.rglob(pattern):
            if path.is_file():
                if args.dry_run:
                    print(f"[DRY-RUN] would delete {path}")
                    deleted_files += 1
                else:
                    path.unlink(missing_ok=True)
                    deleted_files += 1
                    print(f"[INFO] deleted {path}")

    if args.dry_run:
        print(f"[DRY-RUN] would write registry; deleted transcript files={deleted_files}, video_ids={len(ids)}")
        return 0

    write_json(registry_path, registry)
    print(f"[INFO] updated registry, deleted transcript files={deleted_files}, video_ids={len(ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
