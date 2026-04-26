import os
"""
YouTube video audio download module using yt-dlp
Handles downloading audio from YouTube videos with error handling and retry logic
"""

import json
from datetime import datetime
from pathlib import Path
import shutil
import sys
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

    @staticmethod
    def _current_date_folder(published_at: str = None) -> str:
        if published_at:
            try:
                from datetime import timezone, timedelta, datetime
                import dateutil.parser
                # Parse ISO format
                if isinstance(published_at, datetime):
                    dt = published_at
                else:
                    dt_str = str(published_at)
                    if dt_str.endswith("+00:00Z"):
                        dt_str = dt_str[:-1]  # Remove trailing Z
                    dt = dateutil.parser.parse(dt_str)
                tz_taipei = timezone(timedelta(hours=8))
                return dt.astimezone(tz_taipei).strftime("%Y-%m-%d")
            except Exception:
                pass
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d")

    def _dated_output_dir(self, published_at: str = None) -> Path:
        return self.output_dir / os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "daily") / self._current_date_folder(published_at)

    def _find_latest_audio_file(self, video_id: str) -> Optional[Path]:
        candidates = sorted(
            self.output_dir.rglob(f"*{video_id}*.wav"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _get_ydl_opts(self, published_at: str = None) -> dict:
        """Get yt-dlp options for audio extraction."""
        output_dir = self._dated_output_dir(published_at)
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
            "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
            
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
            "remote_components": ["ejs:github"],
        }

        js_runtimes = self._detect_js_runtimes()
        if js_runtimes:
            opts["js_runtimes"] = js_runtimes

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

    @staticmethod
    def _detect_js_runtimes() -> dict:
        runtimes = {}
        runtime_order = ("node", "bun", "deno", "quickjs")
        for runtime in runtime_order:
            executable = shutil.which(runtime)
            if executable:
                runtimes[runtime] = {"path": executable}

        # Fallback for conda env binaries when current PATH differs.
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
    def download(self, video_url: str, published_at: str = None) -> Optional[Path]:
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

        ydl_opts = self._get_ydl_opts(published_at)
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
                max_keep = int(os.environ.get("AUDIO_CACHE_MAX_KEEP", "20") or "20")
                self.maintain_audio_cache(max_keep=max_keep)
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
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "no_warnings": True, "no_warnings": True}) as ydl:
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
        return self._find_latest_audio_file(video_id)

    @staticmethod
    def _extract_video_id_from_audio_file(audio_file: Path) -> str:
        stem = audio_file.stem
        if "_" in stem:
            return stem.split("_", 1)[0]
        return stem

    def _list_audio_files_sorted(self, subfolder: str) -> list[Path]:
        files = [
            p
            for p in self.output_dir.glob(f"{subfolder}/**/*.wav")
            if p.is_file()
        ]
        return sorted(files, key=lambda p: p.stat().st_mtime)

    def _get_transcribed_video_ids(self, subfolder: str) -> set[str]:
        ids: set[str] = set()
        transcript_root = Path(self.settings.data_transcripts_dir) / subfolder
        if not transcript_root.exists():
            return ids

        for path in transcript_root.glob("**/*.json"):
            if not path.is_file():
                continue
            stem = path.stem
            if not stem:
                continue
            if "_" in stem:
                ids.add(stem.split("_", 1)[0])
            else:
                ids.add(stem)
        return ids

    def cleanup_empty_audio_dirs(self, subfolder: Optional[str] = None) -> int:
        """Remove empty date folders after wav deletion."""
        removed = 0
        target_subfolder = subfolder or os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "daily")
        root = self.output_dir / target_subfolder
        if not root.exists():
            return removed

        dirs = sorted(
            [p for p in root.glob("**/*") if p.is_dir()],
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for dir_path in dirs:
            try:
                next(dir_path.iterdir())
            except StopIteration:
                dir_path.rmdir()
                removed += 1
            except Exception:
                continue

        return removed

    def maintain_audio_cache(self, max_keep: int = 20):
        """Keep at most N audio files, deleting oldest untranscribed first."""
        if max_keep <= 0:
            return

        subfolder = os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "daily")
        audio_files = self._list_audio_files_sorted(subfolder)
        if len(audio_files) <= max_keep:
            return

        transcribed_ids = self._get_transcribed_video_ids(subfolder)
        deleted = 0

        for audio_file in audio_files:
            if len(audio_files) - deleted <= max_keep:
                break

            video_id = self._extract_video_id_from_audio_file(audio_file)
            if video_id in transcribed_ids:
                continue

            try:
                audio_file.unlink()
                deleted += 1
                self.logger.info(f"Deleted old untranscribed audio cache: {audio_file}")
            except Exception as e:
                self.logger.warning(f"Failed to delete audio cache {audio_file}: {e}")

        removed_dirs = self.cleanup_empty_audio_dirs(subfolder=subfolder)
        if deleted > 0 or removed_dirs > 0:
            self.logger.info(
                f"Audio cache maintenance complete: deleted_files={deleted}, "
                f"removed_empty_dirs={removed_dirs}"
            )

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

        for file_path in self.output_dir.glob(f"{os.environ.get('PIPELINE_OUTPUT_SUBFOLDER', 'daily')}/**/*.wav"):
            if file_path.is_file():
                file_age = current_time - file_path.stat().st_mtime
                if file_age > max_age_seconds:
                    try:
                        file_path.unlink()
                        self.logger.info(f"Deleted old file: {file_path}")
                    except Exception as e:
                        self.logger.warning(f"Failed to delete {file_path}: {e}")

        removed_dirs = self.cleanup_empty_audio_dirs()
        if removed_dirs > 0:
            self.logger.info(f"Removed {removed_dirs} empty audio directories")
