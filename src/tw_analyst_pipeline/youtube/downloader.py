"""
YouTube video audio download module using yt-dlp
Handles downloading audio from YouTube videos with error handling and retry logic
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import yt_dlp

from ..utils.config import Settings
from ..utils.logging import LoggerMixin, logger
from ..utils.retry import retry_with_backoff


class AudioDownloader(LoggerMixin):
    """Download audio from YouTube videos."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.output_dir = Path(settings.data_raw_dir)
        self.failed_downloads_log = settings.data_errors_dir / "failed_downloads.json"

    def _get_ydl_opts(self) -> dict:
        """Get yt-dlp options for audio extraction."""
        opts = {
            # Format selection
            "format": "bestaudio/best",
            
            # Post-processing (audio extraction)
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "wav",  # wav, mp3, m4a
                    "preferredquality": "192",
                }
            ],
            
            # Output template
            "outtmpl": str(self.output_dir / "%(id)s.%(ext)s"),
            
            # Audio-specific options
            "keepvideo": False,  # Don't keep video file
            
            # Logging
            "quiet": False,
            "no_warnings": False,
            
            # Network
            "socket_timeout": 30,
            "skip_unavailable_fragments": True,
            
            # Retry
            "retries": 5,
            "fragment_retries": 5,
            
            # Progress
            "progress_hooks": [self._progress_hook],
        }

        configured_cookie = (self.settings.yt_cookies_file or "").strip()
        cookie_candidates = []
        if configured_cookie:
            cookie_candidates.append(Path(configured_cookie))
        cookie_candidates.append(Path("local") / "cookies.txt")

        for cookie_path in cookie_candidates:
            if cookie_path and cookie_path.exists() and cookie_path.is_file():
                opts["cookiefile"] = str(cookie_path)
                self.logger.info(f"Using yt-dlp cookies file: {cookie_path}")
                break

        return opts

    def _progress_hook(self, d):
        """Progress hook for yt-dlp."""
        if d["status"] == "downloading":
            percent = d.get("_percent_str", "unknown")
            self.logger.debug(f"Downloading: {percent}")
        elif d["status"] == "finished":
            self.logger.info(f"Download finished: {d.get('filename', 'unknown')}")

    @staticmethod
    def _is_format_unavailable_error(error: Exception) -> bool:
        """Return True when yt-dlp reports an unavailable format selector."""
        message = str(error).lower()
        return "requested format is not available" in message

    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    def download(self, video_url: str) -> Optional[Path]:
        """
        Download audio from YouTube video.

        Args:
            video_url: YouTube video URL or video ID

        Returns:
            Path to the downloaded audio file, or None if failed

        Raises:
            ValueError: If video_url is invalid
            Exception: If download fails after retries
        """

        # Validate and normalize URL
        if not video_url:
            raise ValueError("video_url cannot be empty")

        if "youtube.com" not in video_url and "youtu.be" not in video_url:
            # Assume it's a video ID
            video_url = f"https://www.youtube.com/watch?v={video_url}"

        video_id = self._extract_video_id(video_url)
        self.logger.info(f"Downloading audio from video: {video_id}")

        ydl_opts = self._get_ydl_opts()
        format_fallbacks = [
            "bestaudio/best",
            "best",
            "bv*+ba/b",
        ]

        # Keep order while removing duplicates if defaults already changed upstream.
        deduped_fallbacks = []
        for fmt in format_fallbacks:
            if fmt not in deduped_fallbacks:
                deduped_fallbacks.append(fmt)

        try:
            # Download using yt-dlp with format fallback for videos with atypical stream manifests.
            info = None
            last_error = None

            for index, format_selector in enumerate(deduped_fallbacks):
                current_opts = {**ydl_opts, "format": format_selector}
                try:
                    with yt_dlp.YoutubeDL(current_opts) as ydl:
                        info = ydl.extract_info(video_url, download=True)
                        video_id = (info or {}).get("id", video_id)
                    break
                except Exception as e:
                    last_error = e
                    is_last_attempt = index == len(deduped_fallbacks) - 1
                    if self._is_format_unavailable_error(e) and not is_last_attempt:
                        next_format = deduped_fallbacks[index + 1]
                        self.logger.warning(
                            "Format '%s' unavailable for %s; retrying with '%s'",
                            format_selector,
                            video_id,
                            next_format,
                        )
                        continue
                    raise

            if info is None:
                if last_error is not None:
                    raise last_error
                raise RuntimeError(f"Failed to download {video_id}: unknown yt-dlp error")

            # Find the downloaded file
            audio_file = self._find_audio_file(video_id)
            if audio_file and audio_file.exists():
                self.logger.info(f"Successfully downloaded: {audio_file}")
                return audio_file
            else:
                raise FileNotFoundError(f"Audio file not found for video {video_id}")

        except Exception as e:
            self.logger.error(f"Failed to download audio from {video_url}: {str(e)}")
            self._log_failed_download(video_url, str(e))
            raise

    def _extract_video_id(self, video_url: str) -> str:
        """Extract video ID from URL."""
        # Use yt-dlp's built-in ID extraction
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                info = ydl.extract_info(video_url, download=False)
                return info.get("id", video_url)
        except Exception:
            # Fallback: simple parsing
            if "v=" in video_url:
                return video_url.split("v=")[1].split("&")[0]
            if "youtu.be/" in video_url:
                return video_url.split("youtu.be/")[1].split("?")[0]
            return video_url

    def _find_audio_file(self, video_id: str) -> Optional[Path]:
        """Find the downloaded audio file."""
        for ext in ["wav", "mp3", "m4a", "opus", "webm"]:
            file_path = self.output_dir / f"{video_id}.{ext}"
            if file_path.exists():
                return file_path
        return None

    def _log_failed_download(self, video_url: str, error: str):
        """Log failed download to file."""
        try:
            failed_list = []
            if self.failed_downloads_log.exists():
                with open(self.failed_downloads_log, "r", encoding="utf-8") as f:
                    failed_list = json.load(f)

            failed_list.append({
                "video_url": video_url,
                "error": error,
                "timestamp": datetime.utcnow().isoformat(),
            })

            with open(self.failed_downloads_log, "w", encoding="utf-8") as f:
                json.dump(failed_list, f, ensure_ascii=False, indent=2)
        except Exception as log_error:
            self.logger.warning(f"Failed to log download error: {log_error}")

    def cleanup_old_files(self, max_age_days: int = 7):
        """
        Clean up old downloaded files.

        Args:
            max_age_days: Maximum age of files to keep
        """
        import time

        current_time = time.time()
        max_age_seconds = max_age_days * 24 * 3600

        for file_path in self.output_dir.glob("*.*"):
            if file_path.is_file():
                file_age = current_time - file_path.stat().st_mtime
                if file_age > max_age_seconds:
                    try:
                        file_path.unlink()
                        self.logger.info(f"Deleted old file: {file_path}")
                    except Exception as e:
                        self.logger.warning(f"Failed to delete {file_path}: {e}")
