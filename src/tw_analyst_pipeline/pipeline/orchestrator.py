"""
Main pipeline orchestrator that coordinates all stages
End-to-end processing from YouTube URL to signal extraction
"""

import json
import os
import random
import time
from pathlib import Path
from typing import List, Optional

from ..extraction.llm_client import LLMExtractorFactory
from ..extraction.schemas import (
    ProcessingError,
    RecommendationFeature,
    RecommendationStock,
    VideoAnalysis,
    normalize_label,
)
from ..stock_data.validators import StockValidator
from ..transcription import TranscriberFactory
from ..transcription.whisper_engine import WhisperTranscriber
from ..utils.config import PipelineConfig, Settings
from ..utils.logging import LogContext, LoggerMixin
from ..youtube.downloader import AudioDownloader
from ..youtube.fetcher import YouTubeFetcher


class SignalPipeline(LoggerMixin):
    """Main processing pipeline for analyst videos."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        pipeline_config: Optional[PipelineConfig] = None,
    ):
        """Initialize pipeline with all components."""

        # Load settings if not provided
        if settings is None:
            from ..utils.config import get_settings

            settings = get_settings()

        if pipeline_config is None:
            from ..utils.config import get_pipeline_config

            pipeline_config = get_pipeline_config()

        self.settings = settings
        self.config = pipeline_config

        # Initialize components
        self.downloader = AudioDownloader(settings)
        self.transcriber = TranscriberFactory.create(settings)
        self.fallback_transcriber = None
        self.validator = StockValidator(settings)
        self.llm_extractor = LLMExtractorFactory.create(settings, pipeline_config)
        self.youtube_fetcher = None
        if settings.youtube_api_key:
            try:
                self.youtube_fetcher = YouTubeFetcher(settings)
            except Exception as e:
                self.logger.warning(f"YouTube metadata fetcher unavailable: {e}")
        self.error_log: List[ProcessingError] = []
        self._has_requested_download = False

    def process_video(
        self,
        video_url: str,
        video_id: Optional[str] = None,
        analyst_name: Optional[str] = None,
        skip_download: bool = False,
        mode: str = "audio",
        text_transcript_source: str = "auto",
    ) -> Optional[VideoAnalysis]:
        """
        Process a single video from URL to signal extraction.

        Args:
            video_url: YouTube video URL or video ID
            video_id: Optional explicit video ID
            analyst_name: Optional analyst name for metadata
            skip_download: Skip downloading audio if local file exists

        Returns:
            VideoAnalysis with extracted signals, or None if processing failed
        """

        pipeline_start = time.time()

        # Extract video ID
        if video_id is None:
            video_id = self.downloader._extract_video_id(video_url)

        # Use context logging
        with LogContext(video_id=video_id):
            self.logger.info(f"Starting pipeline for: {video_url}")

            try:
                llm_provider = (self.settings.llm_provider or "").lower()
                use_media_extraction = llm_provider in {"gemini", "google"}
                mode = (mode or "audio").lower()
                text_transcript_source = (text_transcript_source or "auto").lower()

                if mode not in {"audio", "url", "text"}:
                    raise ValueError(f"Unsupported mode: {mode}")
                if text_transcript_source not in {"auto", "cc", "gemini"}:
                    raise ValueError(
                        f"Unsupported text_transcript_source: {text_transcript_source}"
                    )

                view_count = None
                published_at = None
                if self.youtube_fetcher:
                    details = self.youtube_fetcher.get_video_details([video_id])
                    if details:
                        view_count = details[0].view_count
                        published_at = details[0].published_at

                if mode == "url":
                    if not hasattr(self.llm_extractor, "extract_signals_from_youtube_url"):
                        raise RuntimeError(
                            "Current provider does not support URL extraction. "
                            "Please use mode='audio' or mode='text'."
                        )
                    self.logger.info("Stage 1: Extracting signals directly from YouTube URL")
                    analysis = self.llm_extractor.extract_signals_from_youtube_url(
                        youtube_url=video_url,
                        video_id=video_id,
                        analyst_name=analyst_name,
                    )

                elif mode == "audio" and use_media_extraction:
                    self.logger.info("Stage 1: Handling audio for end-to-end multimodal extraction")
                    
                    if skip_download:
                        audio_path = os.path.join(self.settings.data_raw_dir, f"{video_id}.wav")
                        if not os.path.exists(audio_path):
                            raise RuntimeError(f"Skip download requested but file does not exist: {audio_path}")
                        self.logger.info(f"Using existing local audio: {audio_path}")
                    else:
                        self._sleep_between_download_requests()
                        audio_path = self.downloader.download(video_url)
                        if not audio_path:
                            raise RuntimeError("Failed to download audio")

                    self.logger.info("Stage 2: Extracting signals directly from media using LLM")
                    analysis = self.llm_extractor.extract_signals_from_media(
                        media_path=audio_path,
                        video_id=video_id,
                        analyst_name=analyst_name,
                    )
                else:
                    # Stage 1: Fast-track transcript (cache/YouTube CC)
                    transcript_result = None
                    should_try_cc = text_transcript_source in {"auto", "cc"}
                    if should_try_cc and hasattr(self.transcriber, "try_fast_track"):
                        self.logger.info("Stage 1: Fast-track transcript (cache/YouTube CC)")
                        transcript_result = self.transcriber.try_fast_track(video_id)

                    # Stage 2: Audio pipeline fallback
                    should_transcribe = text_transcript_source in {"auto", "gemini"}
                    if not transcript_result and should_transcribe:
                        self.logger.info("Stage 2: Handling audio")
                        
                        if skip_download:
                            audio_path = os.path.join(self.settings.data_raw_dir, f"{video_id}.wav")
                            if not os.path.exists(audio_path):
                                raise RuntimeError(f"Skip download requested but file does not exist: {audio_path}")
                            self.logger.info(f"Using existing local audio: {audio_path}")
                        else:
                            self._sleep_between_download_requests()
                            audio_path = self.downloader.download(video_url)
                            if not audio_path:
                                raise RuntimeError("Failed to download audio")

                        self.logger.info("Stage 3: Transcribing audio")
                        try:
                            transcript_result = self.transcriber.transcribe(audio_path, video_id)
                        except Exception as e:
                            self.logger.warning(f"Primary transcriber failed, fallback to Whisper: {e}")
                            if text_transcript_source == "auto":
                                if self.fallback_transcriber is None:
                                    self.fallback_transcriber = WhisperTranscriber(self.settings)
                                transcript_result = self.fallback_transcriber.transcribe(audio_path, video_id)
                    if not transcript_result or not transcript_result.text:
                        raise RuntimeError("Failed to transcribe audio")

                    self.logger.info(
                        f"Transcript ready: {len(transcript_result.text)} chars, "
                        f"{len(transcript_result.segments)} segments"
                    )

                    # Stage 4: Extract signals
                    self.logger.info("Stage 4: Extracting signals using LLM")
                    analysis = self.llm_extractor.extract_signals(
                        transcript=transcript_result.text,
                        video_id=video_id,
                        analyst_name=analyst_name,
                    )

                # Stage 5: Validate
                self.logger.info("Stage 5: Validating signals")
                if self.settings.validate_stock_codes:
                    analysis.signals = self.validator.resolve_signals(analysis.signals)

                analysis.video_view_count = view_count
                analysis.video_published_at = published_at
                labels = [
                    normalize_label(sig.normalized_label or sig.implied_label)
                    for sig in analysis.signals
                ]
                analysis.normalized_label = self._majority_label(labels)
                analysis.recommendation_feature = self._build_recommendation_feature(analysis)

                # Stage 6: Save results
                self.logger.info("Stage 6: Saving results")
                self._save_analysis(analysis)

                # Total processing time
                total_time = time.time() - pipeline_start
                analysis.processing_duration_seconds = total_time

                self.logger.info(
                    f"✓ Pipeline completed in {total_time:.1f}s "
                    f"({len(analysis.signals)} signals extracted)"
                )

                return analysis

            except Exception as e:
                self.logger.error(f"✗ Pipeline failed: {str(e)}")
                self._log_error(video_id, str(e))
                raise

    def process_multiple(
        self,
        video_urls: List[str],
        skip_existing: bool = True,
    ) -> List[Optional[VideoAnalysis]]:
        """
        Process multiple videos.

        Args:
            video_urls: List of YouTube URLs or video IDs
            skip_existing: Skip already processed videos

        Returns:
            List of VideoAnalysis results (None for failed videos)
        """

        results = []

        for i, url in enumerate(video_urls, 1):
            self.logger.info(f"Processing {i}/{len(video_urls)}: {url}")

            try:
                result = self.process_video(url)
                results.append(result)

            except Exception as e:
                self.logger.warning(f"Failed to process {url}: {e}")
                results.append(None)

        return results

    def _sleep_between_download_requests(self) -> None:
        if self._has_requested_download:
            delay_seconds = random.uniform(15, 45)
            self.logger.info(
                f"Sleeping {delay_seconds:.1f}s before next download request"
            )
            time.sleep(delay_seconds)
        self._has_requested_download = True

    def _save_analysis(self, analysis: VideoAnalysis) -> Path:
        """Save analysis result to JSON file."""
        output_file = self.settings.data_signals_dir / f"{analysis.video_id}.json"

        try:
            with open(output_file, "w", encoding="utf-8") as f:
                # Convert to dict for JSON serialization
                data = {
                    "video_id": analysis.video_id,
                    "analyst_name": analysis.analyst_name,
                    "signals": [sig.model_dump() for sig in analysis.signals],
                    "market_outlook": analysis.market_outlook,
                    "processed_at": analysis.processed_at.isoformat(),
                    "processing_duration_seconds": analysis.processing_duration_seconds,
                    "transcript_length_chars": analysis.transcript_length_chars,
                    "confidence_score": analysis.confidence_score,
                    "sentiment_score": analysis.sentiment_score,
                    "urgency": analysis.urgency,
                    "implied_label": analysis.implied_label,
                    "normalized_label": analysis.normalized_label,
                    "video_view_count": analysis.video_view_count,
                    "video_published_at": analysis.video_published_at,
                    "recommendation_feature": (
                        analysis.recommendation_feature.model_dump(mode="json")
                        if analysis.recommendation_feature
                        else None
                    ),
                }
                json.dump(data, f, ensure_ascii=False, indent=2)

            self._update_recommendation_list(analysis)

            self.logger.debug(f"Analysis saved to {output_file}")
            return output_file

        except Exception as e:
            self.logger.warning(f"Failed to save analysis: {e}")
            return output_file

    def _log_error(self, video_id: str, error_message: str):
        """Log processing error."""
        error = ProcessingError(
            video_id=video_id,
            stage="pipeline",
            error_type="Exception",
            error_message=error_message,
            is_recoverable=True,
        )

        self.error_log.append(error)

        # Save to file
        try:
            error_file = self.settings.data_errors_dir / "failed_processing.json"
            errors = []

            if error_file.exists():
                with open(error_file, "r", encoding="utf-8") as f:
                    errors = json.load(f)

            errors.append({
                "video_id": error.video_id,
                "stage": error.stage,
                "error_type": error.error_type,
                "error_message": error.error_message,
                "timestamp": error.timestamp.isoformat(),
            })

            with open(error_file, "w", encoding="utf-8") as f:
                json.dump(errors, f, ensure_ascii=False, indent=2)

        except Exception as log_error:
            self.logger.warning(f"Failed to log error: {log_error}")

    def _build_recommendation_feature(self, analysis: VideoAnalysis) -> RecommendationFeature:
        recommended = []
        for signal in analysis.signals:
            stock_label = signal.normalized_label or normalize_label(signal.implied_label)
            recommended.append(
                RecommendationStock(
                    stock_code=signal.stock_code,
                    stock_name=signal.stock_name,
                    label=stock_label,
                )
            )

        return RecommendationFeature(
            timestamp=analysis.processed_at,
            view_count=analysis.video_view_count or 0,
            recommended_stocks=recommended,
            label=analysis.normalized_label or "中立",
        )

    def _filter_ambiguous_signals(self, signals):
        filtered = [
            signal for signal in signals
            if normalize_label(signal.normalized_label or signal.implied_label) != "模糊"
        ]
        removed = len(signals) - len(filtered)
        if removed > 0:
            self.logger.info(f"Filtered {removed} ambiguous signals in post-processing")
        return filtered

    @staticmethod
    def _majority_label(labels: List[str]) -> str:
        if not labels:
            return "中立"
        counts = {"買進": 0, "中立": 0, "賣出": 0, "模糊": 0}
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
        return max(counts.items(), key=lambda item: item[1])[0]

    def _update_recommendation_list(self, analysis: VideoAnalysis):
        list_file = self.settings.data_signals_dir / "recommendation_list.json"
        existing = []

        if list_file.exists():
            try:
                with open(list_file, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                existing = payload.get("items", []) if isinstance(payload, dict) else []
            except Exception:
                existing = []

        feature = analysis.recommendation_feature or self._build_recommendation_feature(analysis)
        item = {
            "video_id": analysis.video_id,
            "timestamp": feature.timestamp.isoformat(),
            "view_count": feature.view_count,
            "recommended_stocks": [stock.model_dump() for stock in feature.recommended_stocks],
            "label": feature.label,
        }

        existing = [entry for entry in existing if entry.get("video_id") != analysis.video_id]
        existing.append(item)
        existing.sort(key=lambda entry: entry.get("timestamp", ""), reverse=True)

        with open(list_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "count": len(existing),
                    "items": existing,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    def get_stats(self) -> dict:
        """Get pipeline statistics."""
        total_cost = sum(
            m.estimated_usd for m in self.llm_extractor.cost_metrics
        )

        return {
            "total_errors": len(self.error_log),
            "api_calls": len(self.llm_extractor.cost_metrics),
            "total_cost_usd": total_cost,
            "avg_cost_per_video": (
                total_cost / len(self.llm_extractor.cost_metrics)
                if self.llm_extractor.cost_metrics
                else 0.0
            ),
        }
