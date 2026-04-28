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
import time
import random
from typing import List, Optional
from urllib.parse import unquote, urlparse

 
import yt_dlp
from urllib.error import HTTPError as HttpError
from googleapiclient.discovery import build

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

        self.api_key = settings.youtube_api_key 
        self._init_youtube_client()

    def _init_youtube_client(self):
        # Keep this method for compatibility; channel/video fetch now uses yt-dlp.
        api_key = self.settings.youtube_api_key
        if api_key:
            self.youtube = build("youtube", "v3", developerKey=api_key)
            self.logger.info("YouTube Data API v3 client initialized.")
        else:
            self.youtube = None
            self.logger.warning("No YouTube API Key found, official API features will be disabled.")

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
        handle = handle.strip().lstrip('@')
        if not self.youtube:
            return None

        try:
            # 使用 forHandle 參數查詢
            request = self.youtube.channels().list(
                part="id",
                forHandle=handle
            )
            response = request.execute()
            items = response.get("items", [])
            if items:
                channel_id = items[0]["id"]
                self.logger.info(f"Found channel ID via API: {channel_id}")
                return channel_id
        except Exception as e:
            self.logger.error(f"API error fetching channel ID: {e}")
        return None

    def get_channel_videos(
        self, channel_id: str, max_results: int = 10, days_back: Optional[int] = 7,
        exclude_shorts: bool = False, min_duration_seconds: Optional[int] = None,
        published_after_dt: Optional[datetime] = None, published_before_dt: Optional[datetime] = None,
        **kwargs
    ) -> List[VideoInfo]:
        if not self.youtube:
            return []

        uploads_playlist_id = channel_id.replace("UC", "UU", 1)

        if published_after_dt:
            after_dt = published_after_dt.astimezone(timezone.utc)
        elif days_back:
            after_dt = datetime.now(timezone.utc) - timedelta(days=days_back)
        else:
            after_dt = None

        before_dt = published_before_dt.astimezone(timezone.utc) if published_before_dt else None

        self.logger.info(f"Fetching via Playlist (2 points/page) from: {channel_id}")
        videos = []
        next_page_token = None
        
        try:
            while len(videos) < max_results:
                # 1. 抓取基本清單 (消耗 1 點)
                request = self.youtube.playlistItems().list(
                    part="snippet",
                    playlistId=uploads_playlist_id,
                    maxResults=50,
                    pageToken=next_page_token
                )
                response = request.execute()
                items = response.get("items", [])
                
                if not items:
                    break

                # ======= 【新增：批次查詢補充資料 (消耗 1 點)】 =======
                # 收集這 50 支影片的 ID
                video_ids = [item["snippet"]["resourceId"]["videoId"] for item in items]
                details_map = {}
                
                if video_ids:
                    stats_req = self.youtube.videos().list(
                        part="contentDetails,statistics",
                        id=",".join(video_ids)
                    )
                    stats_res = stats_req.execute()
                    for d in stats_res.get("items", []):
                        details_map[d["id"]] = {
                            "duration_iso": d["contentDetails"]["duration"], # 例如 PT15M33S
                            "view_count": int(d.get("statistics", {}).get("viewCount", 0))
                        }
                # ========================================================

                for item in items:
                    snippet = item["snippet"]
                    pub_str = snippet["publishedAt"]
                    video_id = snippet["resourceId"]["videoId"]
                    video_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))

                    if after_dt and video_dt < after_dt:
                        self.logger.info("Reached videos older than target start_date. Stopping pagination.")
                        return videos
                    if before_dt and video_dt > before_dt:
                        continue

                    # 取出剛才查到的細節
                    detail = details_map.get(video_id, {})
                    duration_iso = detail.get("duration_iso", "")
                    view_count = detail.get("view_count")

                    # 將 ISO 格式 (PT1H2M3S) 轉成秒數
                    duration_sec = 0
                    if duration_iso:
                        # 使用正則表達式解析 YouTube 的時間格式
                        import re
                        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_iso)
                        if match:
                            h = int(match.group(1) or 0)
                            m = int(match.group(2) or 0)
                            s = int(match.group(3) or 0)
                            duration_sec = h * 3600 + m * 60 + s

                    # 執行原本的 Shorts 與長度過濾邏輯
                    if exclude_shorts and duration_sec > 0 and duration_sec <= 180:
                        continue
                    if min_duration_seconds and duration_sec > 0 and duration_sec <= min_duration_seconds:
                        continue

                    videos.append(VideoInfo(
                        video_id=video_id,
                        title=snippet["title"],
                        description=snippet["description"],
                        published_at=pub_str,
                        channel_id=channel_id,
                        channel_title=snippet["channelTitle"],
                        duration=str(duration_sec) if duration_sec > 0 else None,
                        view_count=view_count
                    ))

                    if len(videos) >= max_results:
                        break

                next_page_token = response.get("nextPageToken")
                if not next_page_token:
                    break

        except Exception as e:
            self.logger.error(f"API error fetching playlist items: {e}")

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


