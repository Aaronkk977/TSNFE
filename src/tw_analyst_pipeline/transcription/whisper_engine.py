"""
Whisper transcription engine using faster-whisper
Converts audio to text with GPU acceleration
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from faster_whisper import WhisperModel

from ..extraction.schemas import TranscriptResult
from ..utils.config import Settings
from ..utils.logging import LoggerMixin


class WhisperTranscriber(LoggerMixin):
    """Speech-to-text transcription using faster-whisper."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.output_dir = Path(settings.data_transcripts_dir)
        self.model = self._load_model()

    def _load_model(self) -> WhisperModel:
        """Load Whisper model with specified configuration."""
        self.logger.info(f"Loading Whisper model: {self.settings.whisper_model}")

        model = WhisperModel(
            self.settings.whisper_model,
            device=self.settings.whisper_device,
            compute_type=self.settings.whisper_compute_type,
            num_workers=1,  # Single worker for stability
        )

        self.logger.info(f"Whisper model loaded on {self.settings.whisper_device}")
        return model

    def transcribe(self, audio_path: Path, video_id: Optional[str] = None) -> TranscriptResult:
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
            # Transcribe audio
            import time

            start_time = time.time()

            segments, info = self.model.transcribe(
                str(audio_path),
                language="zh",
                beam_size=5,
                best_of=5,
                patience=1.0,
                temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                vad_filter=True,
                vad_parameters={
                    "min_silence_duration_ms": 500,
                    "speech_pad_ms": 400,
                    "threshold": 0.4,
                },
            )

            # Collect segments
            segment_list = []
            full_text = []

            for segment in segments:
                segment_list.append({
                    "id": segment.id,
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text,
                    "confidence": segment.confidence if hasattr(segment, "confidence") else 0.0,
                })
                full_text.append(segment.text)

            processing_time = time.time() - start_time

            # Create result
            result = TranscriptResult(
                video_id=video_id,
                text=" ".join(full_text),
                segments=segment_list,
                language="zh",
                duration_seconds=info.duration if hasattr(info, "duration") else None,
                processing_time_seconds=processing_time,
            )

            self.logger.info(
                f"Transcription completed in {processing_time:.1f}s "
                f"({len(result.text)} chars, {len(segment_list)} segments)"
            )

            # Save to cache
            self._save_transcript(result)

            return result

        except Exception as e:
            self.logger.error(f"Transcription failed for {video_id}: {str(e)}")
            raise

    def _save_transcript(self, result: TranscriptResult) -> Path:
        """Save transcript to JSON file."""
        output_file = self.output_dir / f"{result.video_id}.json"

        try:
            with open(output_file, "w", encoding="utf-8") as f:
                # Convert to dict for JSON serialization
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

            self.logger.debug(f"Transcript saved to {output_file}")
            return output_file

        except Exception as e:
            self.logger.warning(f"Failed to save transcript: {e}")
            return output_file

    def load_transcript(self, video_id: str) -> Optional[TranscriptResult]:
        """Load cached transcript from file."""
        cache_file = self.output_dir / f"{video_id}.json"

        if not cache_file.exists():
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
        return (self.output_dir / f"{video_id}.json").exists()
