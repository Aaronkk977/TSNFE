"""
Gemini transcription engine.
Converts audio to transcript using Gemini 2.5 Flash.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import google.generativeai as genai
from youtube_transcript_api import YouTubeTranscriptApi

from ..extraction.schemas import TranscriptResult
from ..utils.config import Settings
from ..utils.logging import LoggerMixin


class GeminiTranscriber(LoggerMixin):
    """Speech-to-text transcription using Gemini audio understanding."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.output_dir = Path(settings.data_transcripts_dir)

        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY not set")

        genai.configure(api_key=settings.google_api_key)
        self.model_name = settings.gemini_transcription_model
        self.logger.info(f"Gemini transcriber initialized: {self.model_name}")

    def transcribe(self, audio_path: Path, video_id: Optional[str] = None) -> TranscriptResult:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if video_id is None:
            video_id = audio_path.stem

        self.logger.info(f"Starting Gemini transcription: {video_id}")
        start_time = time.time()

        uploaded_file = None
        try:
            uploaded_file = genai.upload_file(path=str(audio_path))
            model = genai.GenerativeModel(self.model_name)
            prompt = (
                "請將這段中文語音完整轉成繁體中文逐字稿。"
                "保留金融術語、股票代碼與數字。"
                "只輸出逐字稿文字，不要輸出額外說明。"
            )
            response = model.generate_content([prompt, uploaded_file])
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

            self._save_transcript(result)
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
                    genai.delete_file(uploaded_file.name)
                except Exception:
                    pass

    def try_fast_track(self, video_id: str) -> Optional[TranscriptResult]:
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
            self._save_transcript(result)
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

    def _save_transcript(self, result: TranscriptResult) -> Path:
        output_file = self.output_dir / f"{result.video_id}.json"
        try:
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
        cache_file = self.output_dir / f"{video_id}.json"
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return TranscriptResult(**json.load(f))
        except Exception as e:
            self.logger.warning(f"Failed to load transcript cache: {e}")
            return None

    def is_transcribed(self, video_id: str) -> bool:
        return (self.output_dir / f"{video_id}.json").exists()
