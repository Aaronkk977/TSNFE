"""
Gemini transcription engine.
Converts audio to transcript using Gemini 2.5 Flash.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi

from ..extraction.schemas import TranscriptResult
from ..utils.config import PipelineConfig, Settings
from ..utils.logging import LoggerMixin


class GeminiTranscriber(LoggerMixin):
    """Speech-to-text transcription using Gemini audio understanding."""

    def __init__(self, settings: Settings, pipeline_config: PipelineConfig | None = None):
        self.settings = settings
        self.output_dir = Path(settings.data_transcripts_dir)
        self.config = pipeline_config

        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY not set")

        self.client = genai.Client(api_key=settings.google_api_key)
        self.model_name = self._resolve_model_name()
        self.logger.info(f"Gemini transcriber initialized: {self.model_name}")

    def _resolve_model_name(self) -> str:
        if self.config is not None:
            model_name = (self.config.get("transcription.gemini_model") or "").strip()
            if model_name:
                return model_name

        return (self.settings.gemini_transcription_model or "gemini-2.5-flash").strip()

    @staticmethod
    def _current_date_folder(published_at: str = None) -> str:
        if published_at:
            try:
                from datetime import timezone, timedelta, datetime
                if isinstance(published_at, datetime):
                    dt = published_at
                else:
                    dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                tz_taipei = timezone(timedelta(hours=8))
                return dt.astimezone(tz_taipei).strftime("%Y-%m-%d")
            except Exception:
                pass
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d")

    def _dated_output_dir(self, published_at: str = None) -> Path:
        return self.output_dir / os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "daily") / self._current_date_folder(published_at)

    def _find_latest_transcript_file(self, video_id: str) -> Optional[Path]:
        candidates = sorted(
            self.output_dir.glob(f"{os.environ.get('PIPELINE_OUTPUT_SUBFOLDER', 'daily')}/*/{video_id}_*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def transcribe(self, audio_path: Path, video_id: Optional[str] = None, published_at: Optional[str] = None) -> TranscriptResult:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if video_id is None:
            video_id = audio_path.stem

        self.logger.info(f"Starting Gemini transcription: {video_id}")
        start_time = time.time()

        uploaded_file = None
        try:
            uploaded_file = self.client.files.upload(file=str(audio_path))

            self.logger.info("Waiting for uploaded audio to become active...")
            wait_start = time.time()
            while True:
                file_info = self.client.files.get(name=uploaded_file.name)
                if file_info.state.name == "ACTIVE":
                    break
                if file_info.state.name == "FAILED":
                    raise Exception("File processing failed on Gemini servers.")
                if time.time() - wait_start > 120:
                    raise Exception("Timeout waiting for file to become active.")
                time.sleep(2)

            prompt = (
                "請將這段中文語音完整轉成繁體中文逐字稿。"
                "保留金融術語、股票代碼與數字。"
                "只輸出逐字稿文字，不要輸出額外說明。"
            )
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, uploaded_file],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=8192,
                ),
            )
            transcript_text = (response.text or "").strip()

            processing_time = time.time() - start_time
            result = TranscriptResult(
                video_id=video_id,
                text=transcript_text,
                segments=[
                    {
                        "id": 0,
                        "start": 0.0,
                        "end": None,
                        "text": transcript_text,
                        "confidence": None,
                    }
                ],
                language="zh",
                duration_seconds=None,
                processing_time_seconds=processing_time,
            )

            self._save_transcript(result, published_at=published_at)
            self.logger.info(
                f"Gemini transcription completed in {processing_time:.1f}s "
                f"({len(transcript_text)} chars)"
            )
            return result

        except Exception as e:
            self.logger.error(f"Gemini transcription failed for {video_id}: {e}")
            raise

        finally:
            if uploaded_file is not None:
                try:
                    self.client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass

    def try_fast_track(self, video_id: str, published_at: Optional[str] = None) -> Optional[TranscriptResult]:
        """Fast-track: cache -> YouTube CC subtitle API. Return None on failure."""
        if not video_id:
            return None

        # Cache first
        cached = self.load_transcript(video_id)
        if cached and cached.text:
            self.logger.info(f"Fast-track cache hit: {video_id}")
            return cached

        start_time = time.time()
        try:
            languages = ["zh-Hant", "zh-TW", "zh-Hans", "zh", "en"]
            transcript_items = None
            cookie_path = self._resolve_youtube_cookie_path()

            if cookie_path:
                # Prefer cookie-authenticated subtitle request in cloud environments.
                try:
                    transcript_items = YouTubeTranscriptApi.get_transcript(
                        video_id,
                        languages=languages,
                        cookies=str(cookie_path),
                    )
                    self.logger.info(f"Fast-track using transcript cookies: {cookie_path}")
                except TypeError:
                    # Backward compatibility for youtube-transcript-api versions
                    # that do not support cookies parameter in get_transcript.
                    self.logger.warning(
                        "youtube-transcript-api does not accept 'cookies' in get_transcript; "
                        "fallback to fetch() without explicit cookies"
                    )

            if transcript_items is None:
                api = YouTubeTranscriptApi()
                transcript_items = api.fetch(
                    video_id,
                    languages=languages,
                )

            segments = []
            text_chunks = []

            for i, item in enumerate(transcript_items):
                if isinstance(item, dict):
                    seg_text = (item.get("text", "") or "").strip()
                    start_sec = float(item.get("start", 0.0) or 0.0)
                    duration = float(item.get("duration", 0.0) or 0.0)
                else:
                    seg_text = (getattr(item, "text", "") or "").strip()
                    start_sec = float(getattr(item, "start", 0.0) or 0.0)
                    duration = float(getattr(item, "duration", 0.0) or 0.0)

                if not seg_text:
                    continue
                segments.append(
                    {
                        "id": i,
                        "start": start_sec,
                        "end": start_sec + duration if duration > 0 else None,
                        "text": seg_text,
                        "confidence": None,
                    }
                )
                text_chunks.append(seg_text)

            full_text = "\n".join(text_chunks).strip()
            if not full_text:
                return None

            result = TranscriptResult(
                video_id=video_id,
                text=full_text,
                segments=segments,
                language="zh",
                duration_seconds=(segments[-1]["end"] if segments and segments[-1]["end"] else None),
                processing_time_seconds=time.time() - start_time,
            )
            self._save_transcript(result, published_at=published_at)
            self.logger.info(
                f"Fast-track transcript success: {video_id} "
                f"({len(full_text)} chars, {len(segments)} segments)"
            )
            return result

        except Exception as e:
            self.logger.info(f"Fast-track transcript unavailable for {video_id}: {e}")
            return None

    def _resolve_youtube_cookie_path(self) -> Optional[Path]:
        configured_cookie = (self.settings.yt_cookies_file or "").strip()
        cookie_candidates = []
        if configured_cookie:
            cookie_candidates.append(Path(configured_cookie))
        cookie_candidates.append(Path("local") / "cookies.txt")

        for cookie_path in cookie_candidates:
            if cookie_path.exists() and cookie_path.is_file():
                return cookie_path
        return None

    def _save_transcript(self, result: TranscriptResult, published_at: Optional[str] = None) -> Path:
        output_dir = self._dated_output_dir(published_at)
        output_file = output_dir / f"{result.video_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                data = {
                    "video_id": result.video_id,
                    "text": result.text,
                    "segments": result.segments,
                    "language": result.language,
                    "duration_seconds": result.duration_seconds,
                    "processing_time_seconds": result.processing_time_seconds,
                    "saved_at": datetime.utcnow().isoformat(),
                }
                json.dump(data, f, ensure_ascii=False, indent=2)
            return output_file
        except Exception as e:
            self.logger.warning(f"Failed to save transcript: {e}")
            return output_file

    def load_transcript(self, video_id: str) -> Optional[TranscriptResult]:
        cache_file = self._find_latest_transcript_file(video_id)
        if cache_file is None:
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return TranscriptResult(**json.load(f))
        except Exception as e:
            self.logger.warning(f"Failed to load transcript cache: {e}")
            return None

    def is_transcribed(self, video_id: str) -> bool:
        return self._find_latest_transcript_file(video_id) is not None
