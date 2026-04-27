import os
import math 
import opencc
"""
Whisper transcription engine using faster-whisper
Converts audio to text with GPU acceleration
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import List, Optional

from faster_whisper import WhisperModel

from ..extraction.schemas import TranscriptResult
from ..utils.config import PipelineConfig, Settings
from ..utils.logging import LoggerMixin


class WhisperTranscriber(LoggerMixin):
    @staticmethod
    def _is_cuda_oom_error(error: Exception) -> bool:
        message = str(error).lower()
        return "out of memory" in message and "cuda" in message

    """Speech-to-text transcription using faster-whisper."""

    def __init__(self, settings: Settings, pipeline_config: PipelineConfig | None = None):
        self.settings = settings
        self.config = pipeline_config
        self.output_dir = Path(settings.data_transcripts_dir)
        self.model_name = self._resolve_model_name()
        self.device = self._resolve_device()
        self.compute_type = self._resolve_compute_type()
        self.model = self._load_model()

    def _resolve_model_name(self) -> str:
        if self.config is not None:
            model_name = (self.config.get_model_name("whisper") or "").strip()
            if model_name:
                return model_name
        return (self.settings.whisper_model or "medium").strip()

    def _resolve_device(self) -> str:
        if self.config is not None:
            configured = (self.config.get("transcription.device") or "").strip().lower()
            if configured in {"cuda", "cpu"}:
                return configured
        return (self.settings.whisper_device or "cuda").strip().lower()

    def _resolve_compute_type(self) -> str:
        if self.config is not None:
            configured = (self.config.get("transcription.compute_type") or "").strip().lower()
            if configured in {"float16", "float32", "int8"}:
                return configured
        return (self.settings.whisper_compute_type or "float16").strip().lower()

    def _resolve_initial_prompt(self) -> Optional[str]:
        if self.config is None:
            return None

        prompt = (self.config.get("prompts.whisper_initial_prompt") or "").strip()
        return prompt or None

    @staticmethod
    def _current_date_folder(published_at: str = None) -> str:
        if published_at:
            try:
                from datetime import timezone, timedelta, datetime
                import dateutil.parser
                if isinstance(published_at, datetime):
                    dt = published_at
                else:
                    dt_str = str(published_at)
                if dt_str.endswith("+00:00Z"):
                    dt_str = dt_str[:-1]
                dt = dateutil.parser.parse(dt_str)
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

    def _transcribe_file(self, audio_path: Path) -> tuple[List[dict], Optional[float], str]:
        segments, info = self.model.transcribe(
            str(audio_path),
            language="zh",
            beam_size=5,
            best_of=5,
            patience=1.0,
            temperature=(0.0, 0.2, 0.4),
            no_speech_threshold=0.6,
            vad_filter=True,
            initial_prompt=self._resolve_initial_prompt(),

            condition_on_previous_text=False,
            compression_ratio_threshold=2.4,
            # logprob_threshold=-1.0,
            vad_parameters={
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 400,
                "threshold": 0.4,
            },
        )

        converter = opencc.OpenCC('s2twp')

        segment_list = []
        full_text = []

        for segment in segments:

            # if hasattr(segment, "avg_logprob"):
            #     conf = math.exp(segment.avg_logprob)
            # else:
            #     conf = 0.0

            segment.text = converter.convert(segment.text)

            segment_list.append({
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "confidence": segment.confidence if hasattr(segment, "confidence") else 0.0,
            })
            full_text.append(segment.text)

        duration_seconds = info.duration if hasattr(info, "duration") else None
        return segment_list, duration_seconds, " ".join(full_text)

    def _load_model(self) -> WhisperModel:
        """Load Whisper model with specified configuration."""
        self.logger.info(
            "Loading Whisper model: {} (device={}, compute_type={})",
            self.model_name,
            self.device,
            self.compute_type,
        )

        model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            num_workers=1,  # Single worker for stability
        )

        self.logger.info(f"Whisper model loaded on {self.device}")
        return model

    def transcribe(self, audio_path: Path, video_id: Optional[str] = None, published_at: Optional[str] = None) -> TranscriptResult:
        """
        Transcribe audio file to text.

        Args:
            audio_path: Path to audio file
            video_id: Optional video ID for logging

        Returns:
            TranscriptResult containing the full text and segments

        Raises:
            FileNotFoundError: If audio file doesn't exist
            Exception: If transcription fails
        """

        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if video_id is None:
            video_id = audio_path.stem

        self.logger.info(f"Starting transcription: {video_id}")

        try:
            import time

            start_time = time.time()
            segment_list, duration_seconds, transcript_text = self._transcribe_file(audio_path)

            processing_time = time.time() - start_time

            result = TranscriptResult(
                video_id=video_id,
                text=transcript_text,
                segments=segment_list,
                language="zh",
                duration_seconds=duration_seconds,
                processing_time_seconds=processing_time,
            )

            self.logger.info(
                f"Transcription completed in {processing_time:.1f}s "
                f"({len(result.text)} chars, {len(segment_list)} segments)"
            )

            # Save to cache
            self._save_transcript(result, published_at=published_at)

            return result

        except Exception as e:
            if self.device == "cuda" and self._is_cuda_oom_error(e):
                self.logger.error(
                    "CUDA OOM for %s, mark failed and skip this item (no CPU fallback)",
                    video_id,
                )
            self.logger.error(f"Transcription failed for {video_id}: {str(e)}")
            raise

    def _save_transcript(self, result: TranscriptResult, published_at: Optional[str] = None) -> Path:
        """Save transcript to JSON file."""
        output_dir = self._dated_output_dir(published_at)
        now_utc = datetime.now(UTC)
        output_file = output_dir / f"{result.video_id}_{now_utc.strftime('%Y%m%d_%H%M%S')}.json"

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                # Convert to dict for JSON serialization
                data = {
                    "video_id": result.video_id,
                    "text": result.text,
                    "segments": result.segments,
                    "language": result.language,
                    "duration_seconds": result.duration_seconds,
                    "processing_time_seconds": result.processing_time_seconds,
                    "saved_at": now_utc.isoformat(),
                }
                json.dump(data, f, ensure_ascii=False, indent=2)

            self.logger.debug(f"Transcript saved to {output_file}")
            return output_file

        except Exception as e:
            self.logger.warning(f"Failed to save transcript: {e}")
            return output_file

    def load_transcript(self, video_id: str) -> Optional[TranscriptResult]:
        """Load cached transcript from file."""
        cache_file = self._find_latest_transcript_file(video_id)

        if cache_file is None:
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            return TranscriptResult(**data)

        except Exception as e:
            self.logger.warning(f"Failed to load transcript cache: {e}")
            return None

    def is_transcribed(self, video_id: str) -> bool:
        """Check if video has been transcribed."""
        return self._find_latest_transcript_file(video_id) is not None
