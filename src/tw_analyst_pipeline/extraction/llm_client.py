"""
LLM Client for structured signal extraction using instructor + Pydantic
Supports OpenAI, Anthropic, and Google Generative AI
"""

import json
import time
import re
from typing import List, Optional, Type, Union

import instructor
from anthropic import Anthropic
from google.generativeai import GenerativeModel
from openai import OpenAI
from pydantic import BaseModel

from ..extraction.schemas import CostMetrics, StockSignal, VideoAnalysis, normalize_label
from ..utils.config import PipelineConfig, Settings
from ..utils.logging import LoggerMixin
from ..utils.retry import retry_with_backoff


class BaseLLMExtractor(LoggerMixin):
    """Base class for LLM-based signal extraction."""

    def __init__(
        self,
        settings: Settings,
        pipeline_config: PipelineConfig,
    ):
        self.settings = settings
        self.config = pipeline_config
        self.cost_metrics: List[CostMetrics] = []

    def extract_signals(
        self,
        transcript: str,
        video_id: str,
        analyst_name: Optional[str] = None,
    ) -> VideoAnalysis:
        """Extract stock signals from transcript."""
        raise NotImplementedError

    def _get_extraction_prompt(self, transcript: str) -> str:
        """Get the extraction prompt from config."""
        prompt_template = self.config.get("prompts.extraction", "")
        if not prompt_template:
            # Fallback to a simple prompt
            prompt_template = (
                "從以下逐字稿提取台灣股票買賣訊號，返回 JSON 格式。\n\n{transcript}"
            )
        return prompt_template.format(transcript=transcript)

    def _get_system_prompt(self) -> str:
        """Get the system prompt from config."""
        return self.config.get(
            "prompts.system",
            "你是台灣股市分析助手，從影片逐字稿中提取股票訊號。",
        )

    @staticmethod
    def _action_from_label(label: Optional[str]) -> str:
        normalized = normalize_label(label)
        if normalized == "買進":
            return "buy"
        if normalized == "賣出":
            return "sell"
        return "hold"

    @staticmethod
    def _safe_parse_json(text: str):
        """Parse JSON from plain text or markdown fenced code block."""
        cleaned = text.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise


class OpenAIExtractor(BaseLLMExtractor):
    """Extract signals using OpenAI GPT-4o-mini."""

    def __init__(
        self,
        settings: Settings,
        pipeline_config: PipelineConfig,
    ):
        super().__init__(settings, pipeline_config)

        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY not set")

        # Initialize instructor-patched OpenAI client
        self.client = instructor.from_openai(
            OpenAI(api_key=settings.openai_api_key)
        )
        self.logger.info("OpenAI client initialized")

    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    def extract_signals(
        self,
        transcript: str,
        video_id: str,
        analyst_name: Optional[str] = None,
    ) -> VideoAnalysis:
        """Extract signals using OpenAI with structured output."""

        self.logger.info(f"Extracting signals from {video_id} using OpenAI")
        start_time = time.time()

        try:
            # Prepare prompt
            system_prompt = self._get_system_prompt()
            user_prompt = self._get_extraction_prompt(transcript)

            # Call OpenAI with structured output (Pydantic mode)
            response = self.client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_model=list[StockSignal],
                temperature=self.settings.llm_temperature,
                max_tokens=self.settings.llm_max_tokens,
            )

            # Track costs
            processing_time = time.time() - start_time
            input_tokens = len(system_prompt.split()) + len(user_prompt.split())
            output_tokens = sum(len(sig.reasoning.split()) for sig in response)

            self._track_cost(
                video_id,
                input_tokens=int(input_tokens * 0.8),  # Rough estimate
                output_tokens=int(output_tokens * 0.8),
                signals_extracted=len(response),
                processing_time=processing_time,
            )

            # Create result
            result = VideoAnalysis(
                video_id=video_id,
                analyst_name=analyst_name,
                signals=response,
                transcript_length_chars=len(transcript),
                processing_duration_seconds=processing_time,
                confidence_score=self._calculate_confidence(response),
            )

            self.logger.info(
                f"Extracted {len(response)} signals in {processing_time:.1f}s"
            )
            return result

        except Exception as e:
            self.logger.error(f"Failed to extract signals: {str(e)}")
            raise

    def _track_cost(
        self,
        video_id: str,
        input_tokens: int,
        output_tokens: int,
        signals_extracted: int,
        processing_time: float,
    ):
        """Track API costs."""
        # GPT-4o-mini pricing: $0.15/$0.60 per 1M tokens
        input_cost = (input_tokens / 1_000_000) * 0.15
        output_cost = (output_tokens / 1_000_000) * 0.60
        total_cost = input_cost + output_cost

        metric = CostMetrics(
            video_id=video_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_usd=total_cost,
            processing_time_seconds=processing_time,
            signals_extracted=signals_extracted,
        )

        self.cost_metrics.append(metric)

        self.logger.debug(
            f"Cost tracking: {input_tokens} input + {output_tokens} output tokens "
            f"= ${total_cost:.4f}"
        )

    @staticmethod
    def _calculate_confidence(signals: List[StockSignal]) -> float:
        """Calculate overall confidence score."""
        if not signals:
            return 0.0
        return sum(sig.confidence for sig in signals) / len(signals)


class AnthropicExtractor(BaseLLMExtractor):
    """Extract signals using Anthropic Claude."""

    def __init__(
        self,
        settings: Settings,
        pipeline_config: PipelineConfig,
    ):
        super().__init__(settings, pipeline_config)

        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.logger.info("Anthropic client initialized")

    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    def extract_signals(
        self,
        transcript: str,
        video_id: str,
        analyst_name: Optional[str] = None,
    ) -> VideoAnalysis:
        """Extract signals using Anthropic Claude."""

        self.logger.info(f"Extracting signals from {video_id} using Anthropic")
        start_time = time.time()

        try:
            system_prompt = self._get_system_prompt()
            user_prompt = self._get_extraction_prompt(transcript)

            # Call Anthropic API
            message = self.client.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=self.settings.llm_max_tokens,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt},
                ],
            )

            # Parse response as JSON
            response_text = message.content[0].text
            signals_data = json.loads(response_text)

            if not isinstance(signals_data, list):
                signals_data = [signals_data]

            signals = [StockSignal(**item) for item in signals_data]

            processing_time = time.time() - start_time

            result = VideoAnalysis(
                video_id=video_id,
                analyst_name=analyst_name,
                signals=signals,
                transcript_length_chars=len(transcript),
                processing_duration_seconds=processing_time,
                confidence_score=self._calculate_confidence(signals),
            )

            self.logger.info(
                f"Extracted {len(signals)} signals in {processing_time:.1f}s"
            )
            return result

        except Exception as e:
            self.logger.error(f"Failed to extract signals: {str(e)}")
            raise

    @staticmethod
    def _calculate_confidence(signals: List[StockSignal]) -> float:
        """Calculate overall confidence score."""
        if not signals:
            return 0.0
        return sum(sig.confidence for sig in signals) / len(signals)


class GoogleExtractor(BaseLLMExtractor):
    """Extract signals using Google Generative AI."""

    def __init__(
        self,
        settings: Settings,
        pipeline_config: PipelineConfig,
    ):
        super().__init__(settings, pipeline_config)

        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY not set")

        import google.generativeai as genai

        genai.configure(api_key=settings.google_api_key)
        self.logger.info("Google Generative AI configured")

    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    def extract_signals(
        self,
        transcript: str,
        video_id: str,
        analyst_name: Optional[str] = None,
    ) -> VideoAnalysis:
        """Extract signals using Google Generative AI."""

        self.logger.info(f"Extracting signals from {video_id} using Google AI")
        start_time = time.time()

        try:
            import google.generativeai as genai

            system_prompt = self._get_system_prompt()
            user_prompt = self._get_quant_prompt(transcript)

            model_name = (
                self.config.get("extraction.models.gemini")
                or self.settings.llm_model
                or "gemini-2.5-flash"
            )
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                [system_prompt, user_prompt],
                generation_config=genai.types.GenerationConfig(
                    temperature=self.settings.llm_temperature,
                    max_output_tokens=self.settings.llm_max_tokens,
                ),
            )

            # Parse response
            response_text = response.text
            signals_data = self._safe_parse_json(response_text)

            if not isinstance(signals_data, list):
                signals_data = [signals_data]

            signals = []
            overall_sentiment = []
            overall_urgency = []
            labels = []
            for item in signals_data:
                stock_code = str(item.get("ticker", "")).strip().zfill(4)
                raw_label = item.get("implied_label", "Neutral")
                sentiment = float(item.get("sentiment_score", 5.0))
                urgency = float(item.get("urgency", 5.0))
                confidence = max(0.0, min(1.0, (sentiment + urgency) / 20.0))
                normalized = normalize_label(raw_label)
                labels.append(normalized)
                overall_sentiment.append(sentiment)
                overall_urgency.append(urgency)

                signals.append(
                    StockSignal(
                        stock_code=stock_code,
                        stock_name=item.get("stock_name") or item.get("ticker") or stock_code,
                        action=self._action_from_label(raw_label),
                        confidence=confidence,
                        reasoning=item.get("reasoning", ""),
                        sentiment_score=sentiment,
                        urgency=urgency,
                        implied_label=raw_label,
                        normalized_label=normalized,
                    )
                )

            processing_time = time.time() - start_time
            normalized_label = self._majority_label(labels)

            result = VideoAnalysis(
                video_id=video_id,
                analyst_name=analyst_name,
                signals=signals,
                transcript_length_chars=len(transcript),
                processing_duration_seconds=processing_time,
                confidence_score=self._calculate_confidence(signals),
                sentiment_score=(sum(overall_sentiment) / len(overall_sentiment)) if overall_sentiment else None,
                urgency=(sum(overall_urgency) / len(overall_urgency)) if overall_urgency else None,
                implied_label=normalized_label,
                normalized_label=normalized_label,
            )

            self.logger.info(
                f"Extracted {len(signals)} signals in {processing_time:.1f}s"
            )
            return result

        except Exception as e:
            self.logger.error(f"Failed to extract signals: {str(e)}")
            raise

    @staticmethod
    def _calculate_confidence(signals: List[StockSignal]) -> float:
        """Calculate overall confidence score."""
        if not signals:
            return 0.0
        return sum(sig.confidence for sig in signals) / len(signals)

    @staticmethod
    def _majority_label(labels: List[str]) -> str:
        if not labels:
            return "中立"
        counts = {"買進": 0, "中立": 0, "賣出": 0}
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
        return max(counts.items(), key=lambda item: item[1])[0]

    def _get_quant_prompt(self, transcript: str) -> str:
        return f"""
作為一名專業的量化交易員，請觀看此分析師影片並進行分析。

任務：
1) 先簡述分析師對股票的論點（技術面、基本面）。
2) 評分：
   - sentiment_score: 0-10（看好程度）
   - urgency: 0-10（推薦迫切性）
3) 請輸出 JSON 陣列，每個物件格式如下：
{{
  "ticker": "股票代號",
  "sentiment_score": 8.5,
  "urgency": 9.0,
  "reasoning": "分析師提到營收創新高且突破盤整區...",
  "implied_label": "Strong Buy"
}}

注意：
- 即使分析師沒有直接說「買進」，若大量正向論述財報/產業前景，應給高分。
- ticker 必須為台股 4 位數代碼。
- 僅輸出 JSON，不要加入額外說明。

逐字稿如下：
{transcript}
""".strip()


class LLMExtractorFactory:
    """Factory for creating LLM extractors based on settings."""

    _extractors = {
        "openai": OpenAIExtractor,
        "anthropic": AnthropicExtractor,
        "gemini": GoogleExtractor,
        "google": GoogleExtractor,
    }

    @classmethod
    def create(
        cls,
        settings: Settings,
        pipeline_config: PipelineConfig,
        provider: Optional[str] = None,
    ) -> BaseLLMExtractor:
        """
        Create an LLM extractor based on provider.

        Args:
            settings: Application settings
            pipeline_config: Pipeline configuration
            provider: LLM provider (openai, anthropic, gemini)

        Returns:
            Configured LLM extractor instance

        Raises:
            ValueError: If provider is not supported
        """

        provider = provider or settings.llm_provider
        extractor_class = cls._extractors.get(provider.lower())

        if not extractor_class:
            raise ValueError(
                f"Unsupported LLM provider: {provider}. "
                f"Supported: {', '.join(cls._extractors.keys())}"
            )

        return extractor_class(settings, pipeline_config)
