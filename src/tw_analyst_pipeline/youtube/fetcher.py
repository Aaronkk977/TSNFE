"""
YouTube Data API v3 integration
Fetch videos from analyst channels
"""

import os
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sys
from typing import List, Optional
from urllib.parse import unquote, urlparse

 
import yt_dlp
from urllib.error import HTTPError as HttpError

from ..utils.config import Settings
from ..utils.logging import LoggerMixin
from ..utils.retry import retry_with_backoff


class VideoInfo:
    """Information about a YouTube video."""

    def __init__(
        self,
        video_id: str,
        title: str,
        description: str,
        published_at: str,
        channel_id: str,
        channel_title: str,
        duration: Optional[str] = None,
        view_count: Optional[int] = None,
    ):
        self.video_id = video_id
        self.title = title
        self.description = description
        self.published_at = published_at
        self.channel_id = channel_id
        self.channel_title = channel_title
        self.duration = duration
        self.view_count = view_count

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "description": self.description,
            "published_at": self.published_at,
            "channel_id": self.channel_id,
            "channel_title": self.channel_title,
            "duration": self.duration,
            "view_count": self.view_count,
        }

    def __repr__(self):
        return f"VideoInfo(id={self.video_id}, title={self.title[:30]}...)"


class YouTubeFetcher(LoggerMixin):
    """Fetch videos from YouTube channels using Data API v3."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.youtube = None
        self._init_youtube_client()

    def _init_youtube_client(self):
        # Keep this method for compatibility; channel/video fetch now uses yt-dlp.
        self.youtube = None

    @staticmethod
    def _detect_js_runtimes() -> dict:
        runtimes = {}
        runtime_order = ("node", "bun", "deno", "quickjs")
        for runtime in runtime_order:
            executable = shutil.which(runtime)
            if executable:
                runtimes[runtime] = {"path": executable}

        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            for runtime in runtime_order:
                if runtime in runtimes:
                    continue
                candidate = Path(conda_prefix) / "bin" / runtime
                if candidate.exists() and candidate.is_file():
                    runtimes[runtime] = {"path": str(candidate)}

        python_bin_dir = Path(sys.executable).resolve().parent
        for runtime in runtime_order:
            if runtime in runtimes:
                continue
            candidate = python_bin_dir / runtime
            if candidate.exists() and candidate.is_file():
                runtimes[runtime] = {"path": str(candidate)}

        return runtimes

    def _base_ydl_opts(self) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "remote_components": ["ejs:github"],
        }
        js_runtimes = self._detect_js_runtimes()
        if js_runtimes:
            opts["js_runtimes"] = js_runtimes
        return opts

    def save_video_list(self, videos: List[VideoInfo], output_file: Path) -> Path:
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        payload = [video.to_dict() for video in videos]

        with open(output_file, "w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, ensure_ascii=False, indent=2)

        self.logger.info(f"Saved {len(videos)} videos to {output_file}")
        return output_file

    @staticmethod
    def _seconds_to_iso8601_duration(seconds: Optional[int]) -> Optional[str]:
        if seconds is None:
            return None
        total = int(seconds)
        hours = total // 3600
        minutes = (total % 3600) // 60
        secs = total % 60
        parts = []
        if hours:
            parts.append(f"{hours}H")
        if minutes:
            parts.append(f"{minutes}M")
        if secs or not parts:
            parts.append(f"{secs}S")
        return "PT" + "".join(parts)

    @staticmethod
    def _as_utc_aware(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def get_channel_id_from_handle(self, handle: str) -> Optional[str]:
        handle = handle.strip()
        if not handle: return None
        if not handle.startswith("http"):
            handle = f"https://www.youtube.com/@{handle.lstrip('@')}"

        ydl_opts = {
            **self._base_ydl_opts(),
            'extract_flat': True,
            'playlistend': 1,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(handle, download=False)
                channel_id = info.get('channel_id') or info.get('id')
                if channel_id:
                    self.logger.info(f"Found channel ID: {channel_id}")
                    return channel_id
        except Exception as e:
            self.logger.error(f"yt-dlp error fetching channel: {e}")
        return None

    def get_channel_videos(
        self, channel_id: str, max_results: int = 10, days_back: Optional[int] = 7,
        exclude_shorts: bool = False, min_duration_seconds: Optional[int] = None,
        published_after_dt: Optional[datetime] = None, published_before_dt: Optional[datetime] = None,
    ) -> List[VideoInfo]:
        self.logger.info(f"Fetching videos from channel: {channel_id}")
        if published_after_dt:
            after_dt = self._as_utc_aware(published_after_dt)
        elif days_back:
            after_dt = datetime.now(timezone.utc) - timedelta(days=days_back)
        else:
            after_dt = None
            
        before_dt = self._as_utc_aware(published_before_dt) if published_before_dt else None
        url = f"https://www.youtube.com/channel/{channel_id}/videos"
        ydl_opts = {
            **self._base_ydl_opts(),
            'extract_flat': True,
            'playlistend': 2000 if published_after_dt else (max_results * 2), # Increased for historical fetch
        }
        videos = []
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                entries = info.get('entries', [])
                if before_dt and entries and entries[0].get('timestamp') is None:
                    self.logger.info("Using binary search for date range (yt-dlp returned no timestamps)")
                    low, high = 0, len(entries) - 1
                    start_idx = 0
                    def fetch_dt(idx):
                        e = entries[idx]
                        if not e: return None
                        vid_url = f"https://www.youtube.com/watch?v={e.get('id')}"
                        try:
                            opts = {'quiet': True,
            'no_warnings': True, 'extract_flat': 'in_playlist'}
                            opts.update(self._base_ydl_opts())
                             
                            with yt_dlp.YoutubeDL(opts) as inner_ydl:
                                d = inner_ydl.extract_info(vid_url, download=False)
                                ts = d.get('timestamp')
                                if ts:
                                    return datetime.fromtimestamp(ts, tz=timezone.utc)
                        except: pass
                        return None
                    
                    while low <= high:
                        mid = (low + high) // 2
                        dt_val = fetch_dt(mid)
                        if not dt_val:
                            low = mid + 1
                            continue
                        if dt_val > before_dt:
                            low = mid + 1
                        else:
                            start_idx = mid
                            high = mid - 1
                    entries = entries[start_idx:]
                    self.logger.info(f"Skipped {start_idx} newer videos.")

                for entry in entries:
                    if not entry: continue
                    pub_timestamp = entry.get('timestamp')
                    if pub_timestamp is None:
                        # Extract the true timestamp to respect boundaries
                        vid_url = f"https://www.youtube.com/watch?v={entry.get('id')}"
                        try:
                            opts = {'quiet': True,
            'no_warnings': True, 'extract_flat': 'in_playlist'}
                            opts.update(self._base_ydl_opts())
                             
                            with yt_dlp.YoutubeDL(opts) as inner_ydl:
                                d = inner_ydl.extract_info(vid_url, download=False)
                                pub_timestamp = d.get('timestamp')
                        except:
                            pass
                    if pub_timestamp:
                        dt = datetime.fromtimestamp(pub_timestamp, tz=timezone.utc)
                        if after_dt and dt < after_dt: break
                        if before_dt and dt > before_dt: continue
                        pub_str = dt.isoformat() + "Z"
                    else:
                        pub_str = ""
                    
                    duration = entry.get('duration')
                    if duration:
                        if exclude_shorts and duration <= 180: continue
                        if min_duration_seconds and duration <= min_duration_seconds: continue

                    videos.append(VideoInfo(
                        video_id=entry.get('id'),
                        title=entry.get('title'),
                        description=entry.get('description', ''),
                        published_at=pub_str,
                        channel_id=info.get('channel_id', channel_id),
                        channel_title=info.get('uploader', ''),
                        duration=str(duration) if duration else None,
                        view_count=entry.get('view_count')
                    ))
                    if len(videos) >= max_results:
                        break
        except Exception as e:
            self.logger.error(f"yt-dlp error fetching videos: {e}")
            
        return videos

    def get_video_details(self, video_ids: List[str]) -> List[VideoInfo]:
        """Fetch per-video metadata using yt-dlp for orchestrator compatibility."""
        if not video_ids:
            return []

        infos: List[VideoInfo] = []
        ydl_opts = {
            **self._base_ydl_opts(),
            "skip_download": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for video_id in video_ids:
                url = f"https://www.youtube.com/watch?v={video_id}"
                try:
                    detail = ydl.extract_info(url, download=False)
                    duration_iso = self._seconds_to_iso8601_duration(detail.get("duration"))
                    published_timestamp = detail.get("timestamp")
                    published_at = (
                        datetime.fromtimestamp(published_timestamp, tz=timezone.utc).isoformat() + "Z"
                        if published_timestamp
                        else ""
                    )
                    infos.append(
                        VideoInfo(
                            video_id=detail.get("id") or video_id,
                            title=detail.get("title") or "",
                            description=detail.get("description") or "",
                            published_at=published_at,
                            channel_id=detail.get("channel_id") or "",
                            channel_title=detail.get("uploader") or "",
                            duration=duration_iso,
                            view_count=detail.get("view_count"),
                        )
                    )
                except Exception as e:
                    self.logger.warning(f"yt-dlp failed to fetch video details for {video_id}: {e}")

        return infos


