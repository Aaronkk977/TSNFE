"""
YouTube Data API v3 integration
Fetch videos from analyst channels
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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
        """Initialize YouTube Data API client."""
        if not self.settings.youtube_api_key:
            raise ValueError("YOUTUBE_API_KEY not set in environment")

        try:
            self.youtube = build(
                "youtube",
                "v3",
                developerKey=self.settings.youtube_api_key,
            )
            self.logger.info("YouTube Data API client initialized")
        except Exception as e:
            self.logger.error(f"Failed to initialize YouTube client: {e}")
            raise

    def get_channel_id_from_handle(self, handle: str) -> Optional[str]:
        """
        Get channel ID from channel handle (e.g., @win16888).

        Args:
            handle: Channel handle with or without @

        Returns:
            Channel ID or None if not found
        """
        # Remove @ if present
        if handle.startswith("@"):
            handle = handle[1:]

        try:
            # Search for the channel
            request = self.youtube.search().list(
                part="snippet",
                q=handle,
                type="channel",
                maxResults=1,
            )
            response = request.execute()

            if response["items"]:
                channel_id = response["items"][0]["snippet"]["channelId"]
                channel_title = response["items"][0]["snippet"]["title"]
                self.logger.info(f"Found channel: {channel_title} ({channel_id})")
                return channel_id
            else:
                self.logger.warning(f"Channel not found: @{handle}")
                return None

        except HttpError as e:
            self.logger.error(f"HTTP error fetching channel: {e}")
            return None

    @retry_with_backoff(max_attempts=3, exceptions=(HttpError,))
    def get_channel_videos(
        self,
        channel_id: str,
        max_results: int = 10,
        days_back: Optional[int] = 7,
    ) -> List[VideoInfo]:
        """
        Get recent videos from a channel.

        Args:
            channel_id: YouTube channel ID
            max_results: Maximum number of videos to fetch
            days_back: Only fetch videos from last N days (None = all)

        Returns:
            List of VideoInfo objects
        """
        self.logger.info(f"Fetching videos from channel: {channel_id}")

        # Calculate date threshold
        if days_back:
            published_after = (
                datetime.utcnow() - timedelta(days=days_back)
            ).isoformat() + "Z"
        else:
            published_after = None

        try:
            # Get channel's uploads playlist ID
            channel_request = self.youtube.channels().list(
                part="contentDetails,snippet",
                id=channel_id,
            )
            channel_response = channel_request.execute()

            if not channel_response["items"]:
                self.logger.error(f"Channel not found: {channel_id}")
                return []

            channel_title = channel_response["items"][0]["snippet"]["title"]
            uploads_playlist_id = channel_response["items"][0]["contentDetails"][
                "relatedPlaylists"
            ]["uploads"]

            self.logger.info(f"Channel: {channel_title}")
            self.logger.info(f"Uploads playlist: {uploads_playlist_id}")

            # Get videos from uploads playlist
            videos = []
            next_page_token = None

            while len(videos) < max_results:
                playlist_request = self.youtube.playlistItems().list(
                    part="snippet,contentDetails",
                    playlistId=uploads_playlist_id,
                    maxResults=min(50, max_results - len(videos)),
                    pageToken=next_page_token,
                )
                playlist_response = playlist_request.execute()

                for item in playlist_response["items"]:
                    snippet = item["snippet"]
                    video_id = item["contentDetails"]["videoId"]
                    published_at = snippet["publishedAt"]

                    # Check date filter
                    if published_after and published_at < published_after:
                        continue

                    video_info = VideoInfo(
                        video_id=video_id,
                        title=snippet["title"],
                        description=snippet.get("description", ""),
                        published_at=published_at,
                        channel_id=snippet["channelId"],
                        channel_title=snippet["channelTitle"],
                    )
                    videos.append(video_info)

                    if len(videos) >= max_results:
                        break

                # Check for more pages
                next_page_token = playlist_response.get("nextPageToken")
                if not next_page_token:
                    break

            self.logger.info(f"Found {len(videos)} videos")

            if videos:
                detail_map = {
                    detail.video_id: detail
                    for detail in self.get_video_details([video.video_id for video in videos])
                }
                for video in videos:
                    detail = detail_map.get(video.video_id)
                    if detail:
                        video.duration = detail.duration
                        video.view_count = detail.view_count

            return videos

        except HttpError as e:
            self.logger.error(f"HTTP error fetching videos: {e}")
            raise

    def get_video_details(self, video_ids: List[str]) -> List[VideoInfo]:
        """
        Get detailed information for specific videos.

        Args:
            video_ids: List of video IDs

        Returns:
            List of VideoInfo objects with duration
        """
        if not video_ids:
            return []

        try:
            # YouTube API allows up to 50 IDs per request
            video_infos = []

            for i in range(0, len(video_ids), 50):
                batch_ids = video_ids[i : i + 50]
                request = self.youtube.videos().list(
                    part="snippet,contentDetails,statistics",
                    id=",".join(batch_ids),
                )
                response = request.execute()

                for item in response["items"]:
                    snippet = item["snippet"]
                    content = item["contentDetails"]
                    stats = item.get("statistics", {})

                    video_info = VideoInfo(
                        video_id=item["id"],
                        title=snippet["title"],
                        description=snippet.get("description", ""),
                        published_at=snippet["publishedAt"],
                        channel_id=snippet["channelId"],
                        channel_title=snippet["channelTitle"],
                        duration=content["duration"],
                        view_count=int(stats.get("viewCount", 0)),
                    )
                    video_infos.append(video_info)

            return video_infos

        except HttpError as e:
            self.logger.error(f"HTTP error fetching video details: {e}")
            return []

    def save_video_list(self, videos: List[VideoInfo], output_file: Path):
        """Save video list to JSON file."""
        try:
            data = {
                "fetched_at": datetime.utcnow().isoformat(),
                "count": len(videos),
                "videos": [v.to_dict() for v in videos],
            }

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self.logger.info(f"Saved video list to {output_file}")

        except Exception as e:
            self.logger.error(f"Failed to save video list: {e}")
