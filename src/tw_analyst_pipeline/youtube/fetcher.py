"""
YouTube Data API v3 integration
Fetch videos from analyst channels
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
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

    def get_channel_id_from_handle(self, handle: str) -> Optional[str]:
        handle = handle.strip()
        if not handle: return None
        if not handle.startswith("http"):
            handle = f"https://www.youtube.com/@{handle.lstrip('@')}"

        ydl_opts = {
            'extract_flat': True,
            'quiet': True,
            'no_warnings': True,
            'no_warnings': True,
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
            after_dt = published_after_dt
        elif days_back:
            after_dt = datetime.utcnow() - timedelta(days=days_back)
        else:
            after_dt = None
            
        before_dt = published_before_dt
        url = f"https://www.youtube.com/channel/{channel_id}/videos"
        ydl_opts = {
            'extract_flat': True,
            'quiet': True,
            'no_warnings': True,
            'no_warnings': True,
            'playlistend': 2000 if published_after_dt else (max_results * 2), # Increased for historical fetch
        }
        videos = []
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                entries = info.get('entries', [])
                if before_dt and entries and entries[0].get('timestamp') is None:
                    self.logger.info("Using binary search for date range (yt-dlp returned no timestamps)")
                    from datetime import timezone
                    low, high = 0, len(entries) - 1
                    start_idx = 0
                    def fetch_dt(idx):
                        e = entries[idx]
                        if not e: return None
                        vid_url = f"https://www.youtube.com/watch?v={e.get('id')}"
                        try:
                            opts = {'quiet': True,
            'no_warnings': True,
            'no_warnings': True, 'extract_flat': 'in_playlist'}
                             
                            with yt_dlp.YoutubeDL(opts) as inner_ydl:
                                d = inner_ydl.extract_info(vid_url, download=False)
                                ts = d.get('timestamp')
                                if ts:
                                    return datetime.utcfromtimestamp(ts).replace(tzinfo=timezone.utc)
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
            'no_warnings': True,
            'no_warnings': True, 'extract_flat': 'in_playlist'}
                             
                            with yt_dlp.YoutubeDL(opts) as inner_ydl:
                                d = inner_ydl.extract_info(vid_url, download=False)
                                pub_timestamp = d.get('timestamp')
                        except:
                            pass
                    if pub_timestamp:
                        dt = datetime.utcfromtimestamp(pub_timestamp)
                        if dt.tzinfo is None:
                            from datetime import timezone
                            dt = dt.replace(tzinfo=timezone.utc)
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
            "quiet": True, "no_warnings": True, "no_warnings": True,
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
                        datetime.utcfromtimestamp(published_timestamp).isoformat() + "Z"
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


