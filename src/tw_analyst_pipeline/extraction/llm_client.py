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
from pydantic import BaseModel, ValidationError

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

    def extract_signals_from_media(
        self,
        media_path,
        video_id: str,
        analyst_name: Optional[str] = None,
    ) -> VideoAnalysis:
        """Extract stock signals directly from media file."""
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
        if normalized == "模糊":
            return "hold"
        return "hold"

    @staticmethod
    def _safe_parse_json(text: str):
        """Parse JSON from plain text or markdown fenced code block."""
        cleaned = (text or "").strip()
        if not cleaned:
            raise json.JSONDecodeError("Empty response", "", 0)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            try:
                repaired = BaseLLMExtractor._escape_newlines_in_json_strings(cleaned)
                return json.loads(repaired)
            except Exception:
                pass

            match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
            if match:
                return json.loads(match.group(1))

            array_candidate = BaseLLMExtractor._extract_balanced_json_block(cleaned, "[")
            if array_candidate:
                return json.loads(array_candidate)

            object_candidate = BaseLLMExtractor._extract_balanced_json_block(cleaned, "{")
            if object_candidate:
                return json.loads(object_candidate)

            for opening_char in ("[", "{"):
                start = cleaned.find(opening_char)
                if start == -1:
                    continue
                try:
                    parsed_obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
                    return parsed_obj
                except Exception:
                    continue

            raise

    @staticmethod
    def _extract_balanced_json_block(text: str, opening_char: str) -> Optional[str]:
        if opening_char not in {"[", "{"}:
            return None

        closing_char = "]" if opening_char == "[" else "}"
        start = text.find(opening_char)
        if start == -1:
            return None

        depth = 0
        in_string = False
        escaped = False

        for index in range(start, len(text)):
            char = text[index]

            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue

            if char == opening_char:
                depth += 1
            elif char == closing_char:
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]

        return None

    def _get_api_timeout_seconds(self) -> int:
        timeout = self.config.get("extraction.api_timeout_seconds", 300)
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = 300
        return max(30, timeout)

    @staticmethod
    def _escape_newlines_in_json_strings(text: str) -> str:
        out = []
        in_string = False
        escaped = False
        for ch in text:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = not in_string
                continue
            if in_string and ch in ("\n", "\r"):
                out.append("\\n")
                continue
            out.append(ch)
        return "".join(out)

    @staticmethod
    def _get_response_text_safe(response) -> str:
        try:
            text = response.text
            if text:
                return text.strip()
        except Exception:
            pass

        chunks = []
        for cand in getattr(response, "candidates", []) or []:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", []) if content else []
            for part in parts:
                part_text = getattr(part, "text", None)
                if part_text:
                    chunks.append(part_text)
        return "\n".join(chunks).strip()

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
        """Transcript mode is intentionally disabled for GoogleExtractor."""
        self.logger.error(
            "GoogleExtractor.extract_signals() is disabled. "
            "Use extract_signals_from_media(media_path=...) for end-to-end multimodal extraction."
        )
        raise RuntimeError(
            "Google transcript extraction pipeline is disabled. "
            "Please call extract_signals_from_media()."
        )

    def _fallback_extract_signals_data(
        self,
        model,
        system_prompt: str,
        transcript: str,
        seed_tickers: List[str],
    ):
        import google.generativeai as genai

        seed_text = ", ".join(seed_tickers[:20]) if seed_tickers else "無"
        fallback_prompt = f"""
請根據逐字稿提取台股標的訊號，輸出 JSON 陣列。

每筆格式：
{{
  "ticker": "代號",
  "stock_name": "名稱",
  "sentiment_score": 0-10,
  "urgency": 0-10,
  "label": "買進|賣出|中立（持有）|模糊",
  "label_reason": "標籤理由",
  "reasoning": "簡短依據"
}}

已偵測代號（參考）：{seed_text}

逐字稿：
{transcript}

只輸出 JSON，不要其他說明。
""".strip()

        fallback_response = model.generate_content(
            [system_prompt, fallback_prompt],
            generation_config=genai.types.GenerationConfig(
                temperature=self.settings.llm_temperature,
                max_output_tokens=max(1200, int(self.settings.llm_max_tokens * 0.7)),
            ),
        )

        fallback_text = self._get_response_text_safe(fallback_response)
        if not fallback_text:
            self.logger.warning("Fallback extraction still returned empty response")
            return []

        return self._safe_parse_json(fallback_text)

    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    def extract_signals_from_media(
        self,
        media_path,
        video_id: str,
        analyst_name: Optional[str] = None,
    ) -> VideoAnalysis:
        """Extract signals directly from uploaded media file (no transcript stage)."""

        self.logger.info(f"Direct multimodal extraction from media: {video_id}")
        start_time = time.time()
        uploaded_file = None

        try:
            import google.generativeai as genai

            model_name = (
                self.config.get("extraction.models.gemini")
                or self.settings.llm_model
                or "gemini-2.5-flash"
            )
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=self._get_system_prompt()
            )
            timeout_seconds = self._get_api_timeout_seconds()

            self.logger.info(
                "Uploading media file for multimodal extraction: {}",
                str(media_path),
            )
            uploaded_file = genai.upload_file(path=str(media_path))
            
            # Wait for the file to be processed with timeout
            self.logger.info("Waiting for uploaded media to become active...")
            wait_start = time.time()
            while True:
                f = genai.get_file(uploaded_file.name)
                if f.state.name == "ACTIVE":
                    break
                if f.state.name == "FAILED":
                    raise Exception("File processing failed on Gemini servers.")
                if time.time() - wait_start > 120:
                    raise Exception("Timeout waiting for file to become active.")
                time.sleep(2)

            self.logger.info(
                "Media upload completed and active, requesting Gemini inference (timeout={}s, max_tokens={}, mode={})",
                timeout_seconds,
                self.settings.llm_max_tokens,
                model_name
            )
            
            gen_config = genai.types.GenerationConfig(
                temperature=self.settings.llm_temperature,
                max_output_tokens=self.settings.llm_max_tokens,
            )
            
            response = model.generate_content(
                [uploaded_file, self._get_multimodal_prompt()],
                generation_config=gen_config,
                request_options={"timeout": timeout_seconds},
            )

            # --- Log raw response to a debug file to answer user request ---
            try:
                import json
                debug_log_path = "logs/last_gemini_multimodal_response.json"
                import os
                os.makedirs("logs", exist_ok=True)
                with open(debug_log_path, "w", encoding="utf-8") as f:
                    # Attempt to dump the raw to_dict() if available, else str()
                    resp_dict = getattr(response, "to_dict", lambda: {"raw": str(response)})()
                    json.dump(resp_dict, f, ensure_ascii=False, indent=2)
                self.logger.info(f"Raw Gemini response dumped to {debug_log_path}")
            except Exception as dump_err:
                self.logger.warning(f"Could not dump raw gemini response: {dump_err}")
            # ---------------------------------------------------------------

            response_text = self._get_response_text_safe(response)
            
            if not response_text:
                finish_reason = ""
                safety_ratings = ""
                try:
                    finish_reason = response.candidates[0].finish_reason if getattr(response, "candidates", None) else ""
                    safety_ratings = response.candidates[0].safety_ratings if getattr(response, "candidates", None) else ""
                except Exception:
                    pass
                self.logger.warning(
                    f"Empty response from Gemini. finish_reason: {finish_reason}, safety_ratings: {safety_ratings}, prompt_feedback: {getattr(response, 'prompt_feedback', '')}"
                )

            self.logger.info(
                "Gemini multimodal response received ({} chars)",
                len(response_text or ""),
            )

            signals_data = self._safe_parse_json(response_text)
            if not isinstance(signals_data, list):
                signals_data = [signals_data]

            result = self._build_analysis_from_signals_data(
                signals_data=signals_data,
                video_id=video_id,
                analyst_name=analyst_name,
                processing_time=time.time() - start_time,
                transcript_length_chars=None,
            )

            self.logger.info(
                f"Direct multimodal extraction done in {result.processing_duration_seconds:.1f}s "
                f"({len(result.signals)} signals)"
            )
            return result

        except Exception as e:
            self.logger.error(f"Direct multimodal extraction failed: {e}")
            raise
        finally:
            if uploaded_file is not None:
                try:
                    genai.delete_file(uploaded_file.name)
                except Exception:
                    pass

    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    def extract_signals_from_youtube_url(
        self,
        youtube_url: str,
        video_id: str,
        analyst_name: Optional[str] = None,
    ) -> VideoAnalysis:
        """Extract signals directly from YouTube URL using Gemini URL understanding."""

        self.logger.info(f"Direct multimodal extraction from YouTube URL: {video_id}")
        start_time = time.time()

        try:
            import google.generativeai as genai

            model_name = (
                self.config.get("extraction.models.gemini")
                or self.settings.llm_model
                or "gemini-2.5-flash"
            )
            model = genai.GenerativeModel(model_name)
            timeout_seconds = self._get_api_timeout_seconds()

            self.logger.info(
                "Requesting Gemini URL multimodal extraction (timeout={}s)",
                timeout_seconds,
            )
            response = model.generate_content(
                [
                    self._get_system_prompt(),
                    self._get_youtube_url_multimodal_prompt(youtube_url=youtube_url),
                ],
                generation_config=genai.types.GenerationConfig(
                    temperature=self.settings.llm_temperature,
                    max_output_tokens=self.settings.llm_max_tokens,
                    response_mime_type="application/json",
                ),
                request_options={"timeout": timeout_seconds},
            )

            response_text = self._get_response_text_safe(response)
            self.logger.info(
                "Gemini URL response received ({} chars)",
                len(response_text or ""),
            )
            signals_data = self._safe_parse_json(response_text)
            if not isinstance(signals_data, list):
                signals_data = [signals_data]

            result = self._build_analysis_from_signals_data(
                signals_data=signals_data,
                video_id=video_id,
                analyst_name=analyst_name,
                processing_time=time.time() - start_time,
                transcript_length_chars=None,
            )

            self.logger.info(
                f"YouTube URL multimodal extraction done in {result.processing_duration_seconds:.1f}s "
                f"({len(result.signals)} signals)"
            )
            return result

        except Exception as e:
            self.logger.error(f"YouTube URL multimodal extraction failed: {e}")
            raise

    def _build_analysis_from_signals_data(
        self,
        signals_data: List[dict],
        video_id: str,
        analyst_name: Optional[str],
        processing_time: float,
        transcript_length_chars: Optional[int],
    ) -> VideoAnalysis:
        signals = []
        overall_sentiment = []
        overall_urgency = []
        labels = []

        for item in signals_data:
            stock_code = str(item.get("ticker", "")).strip().upper()
            if stock_code.isdigit():
                stock_code = stock_code.zfill(4)

            raw_label = item.get("label") or item.get("implied_label") or "模糊"
            confidence = 1.0 # Set default confidence to 1.0 as requested
            normalized = normalize_label(raw_label)

            labels.append(normalized)

            try:
                signals.append(
                    StockSignal(
                        stock_code=stock_code,
                        stock_name=item.get("stock_name") or stock_code,
                        action=self._action_from_label(raw_label),
                        confidence=confidence,
                        reasoning=item.get("reasoning", ""),
                        sentiment_score=None,
                        urgency=None,
                        implied_label=raw_label,
                        normalized_label=normalized,
                        label_reason=item.get("label_reason") or item.get("reasoning", ""),
                    )
                )
            except ValidationError as ve:
                self.logger.warning(
                    "Skip invalid multimodal signal: {} | item={}"
                    , str(ve), str(item)[:200]
                )

        normalized_label = self._majority_label(labels)

        return VideoAnalysis(
            video_id=video_id,
            analyst_name=analyst_name,
            signals=signals,
            transcript_length_chars=transcript_length_chars,
            processing_duration_seconds=processing_time,
            confidence_score=self._calculate_confidence(signals),
            sentiment_score=None,
            urgency=None,
            implied_label=normalized_label,
            normalized_label=normalized_label,
        )

    @staticmethod
    def _normalize_ticker(raw_ticker: str) -> str:
        ticker = str(raw_ticker or "").strip().upper()
        if ticker.isdigit() and len(ticker) <= 4:
            return ticker.zfill(4)
        return ticker

    def _extract_ticker_mentions(self, transcript: str) -> List[str]:
        pattern = re.compile(r"(?<!\d)(\d{4,5}[A-Z]?)(?!\d)")
        seen = set()
        ordered = []
        upper_text = transcript.upper()
        for found in pattern.finditer(upper_text):
            match = found.group(1)
            if self._is_non_ticker_numeric_context(upper_text, found.start(1), found.end(1)):
                continue
            ticker = self._normalize_ticker(match)
            if ticker and self._is_plausible_tw_ticker(ticker) and ticker not in seen:
                seen.add(ticker)
                ordered.append(ticker)
        return ordered[:30]

    @staticmethod
    def _is_non_ticker_numeric_context(text: str, start: int, end: int) -> bool:
        window_left = text[max(0, start - 6):start]
        window_right = text[end:min(len(text), end + 6)]
        around = window_left + window_right

        non_ticker_tokens = [
            "點", "PTS", "%", "％", "萬", "億", "元", "分鐘", "秒",
            "電話", "專線", "LINE", "TELEGRAM", "小老鼠", "@",
        ]
        if any(token in around for token in non_ticker_tokens):
            return True

        # Exclude numbers that are obviously part of phone strings like 0800-668085
        if "-" in window_left or "-" in window_right:
            return True

        return False

    @staticmethod
    def _is_plausible_tw_ticker(ticker: str) -> bool:
        if re.fullmatch(r"\d{4}", ticker):
            return True
        if re.fullmatch(r"00\d{3}[A-Z]?", ticker):
            return True
        return False

    def _extract_candidates_with_llm(
        self,
        model,
        system_prompt: str,
        transcript: str,
        seed_tickers: List[str],
    ) -> List[dict]:
        import google.generativeai as genai

        response = model.generate_content(
            [system_prompt, self._get_candidate_prompt(transcript, seed_tickers)],
            generation_config=genai.types.GenerationConfig(
                temperature=0.0,
                max_output_tokens=1200,
                response_mime_type="application/json",
            ),
        )

        response_text = self._get_response_text_safe(response)

        candidate_data = self._safe_parse_json(response_text)
        if not isinstance(candidate_data, list):
            candidate_data = [candidate_data]

        merged = []
        seen = set()
        for ticker in seed_tickers:
            normalized = self._normalize_ticker(ticker)
            if normalized and normalized not in seen:
                seen.add(normalized)
                merged.append({"ticker": normalized, "stock_name": ""})

        for item in candidate_data:
            normalized = self._normalize_ticker(item.get("ticker", ""))
            if (
                not normalized
                or not self._is_plausible_tw_ticker(normalized)
                or normalized in seen
            ):
                continue
            seen.add(normalized)
            merged.append({"ticker": normalized, "stock_name": item.get("stock_name", "")})

        self.logger.info("Candidate pass found {} candidate tickers", len(merged))
        return merged[:40]

    def _ensure_candidate_coverage(
        self,
        model,
        system_prompt: str,
        transcript: str,
        candidates: List[dict],
        signals_data: List[dict],
    ) -> List[dict]:
        candidate_by_ticker = {
            self._normalize_ticker(item.get("ticker", "")): item
            for item in candidates
            if self._normalize_ticker(item.get("ticker", ""))
        }
        signal_by_ticker = {
            self._normalize_ticker(item.get("ticker", "")): item
            for item in signals_data
            if self._normalize_ticker(item.get("ticker", ""))
        }

        missing = [
            ticker for ticker in candidate_by_ticker.keys()
            if ticker not in signal_by_ticker
        ]

        if missing:
            self.logger.warning(
                "Initial scoring missed {} candidates, running completion pass",
                len(missing),
            )
            filled = self._score_missing_candidates_with_llm(
                model=model,
                system_prompt=system_prompt,
                transcript=transcript,
                missing_candidates=[candidate_by_ticker[ticker] for ticker in missing],
            )
            for item in filled:
                ticker = self._normalize_ticker(item.get("ticker", ""))
                if ticker and ticker not in signal_by_ticker:
                    signal_by_ticker[ticker] = item

        final = []
        for ticker, candidate in candidate_by_ticker.items():
            item = signal_by_ticker.get(ticker)
            if not item:
                item = {
                    "ticker": ticker,
                    "stock_name": candidate.get("stock_name", "") or ticker,
                    "sentiment_score": 5.0,
                    "urgency": 3.0,
                    "reasoning": "候選標的已提及，但本輪模型未產出明確判斷。",
                    "label": "模糊",
                    "label_reason": "模型未提供可判定的明確態度，暫列模糊。",
                }
            else:
                if not item.get("stock_name"):
                    item["stock_name"] = candidate.get("stock_name", "") or ticker
                if not item.get("label") and not item.get("implied_label"):
                    item["label"] = "模糊"
                if not item.get("label_reason"):
                    item["label_reason"] = "逐字稿態度不夠明確，先標示為模糊。"
            final.append(item)

        return final

    def _score_missing_candidates_with_llm(
        self,
        model,
        system_prompt: str,
        transcript: str,
        missing_candidates: List[dict],
    ) -> List[dict]:
        if not missing_candidates:
            return []

        import google.generativeai as genai

        response = model.generate_content(
            [
                system_prompt,
                self._get_missing_candidate_prompt(
                    transcript=transcript,
                    missing_candidates=missing_candidates,
                ),
            ],
            generation_config=genai.types.GenerationConfig(
                temperature=0.0,
                max_output_tokens=self.settings.llm_max_tokens,
                response_mime_type="application/json",
            ),
        )

        response_text = self._get_response_text_safe(response)

        data = self._safe_parse_json(response_text)
        if not isinstance(data, list):
            data = [data]
        return data

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
        counts = {"買進": 0, "中立": 0, "賣出": 0, "模糊": 0}
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
        return max(counts.items(), key=lambda item: item[1])[0]

    def _get_candidate_prompt(self, transcript: str, seed_tickers: List[str]) -> str:
        seed_text = ", ".join(seed_tickers) if seed_tickers else "無"
        return f"""
請先做「候選標的提取」，從逐字稿抓出所有明確提及的可交易台股/ETF/槓反ETF。

輸出規則：
- 只輸出 JSON 陣列
- 每個物件格式：{{"ticker":"代號","stock_name":"名稱"}}
- 若有代號就填代號；沒有明確代號就不要猜
- 去重後輸出

已由規則抽到的代號（參考，不可新增猜測代號）：
{seed_text}

逐字稿：
{transcript}
""".strip()

    def _get_quant_prompt(self, transcript: str, candidates: List[dict], seed_tickers: List[str]) -> str:
        candidate_json = json.dumps(candidates, ensure_ascii=False)
        seed_text = ", ".join(seed_tickers) if seed_tickers else "無"
        return f"""
作為一名專業的量化交易員，請觀看此分析師影片並進行分析。

任務：
1) 請針對「候選標的清單」逐一評估，不可遺漏。
2) 對每個標的評分：
   - sentiment_score: 0-10（看好程度）
   - urgency: 0-10（推薦迫切性）
3) 請輸出 JSON 陣列；每個物件格式如下：
{{
  "ticker": "股票代號",
    "stock_name": "標的名稱",
  "sentiment_score": 8.5,
  "urgency": 9.0,
  "reasoning": "分析師提到營收創新高且突破盤整區...",
    "label": "買進",
    "label_reason": "明確說可加碼且語氣強烈"
}}

候選標的清單（必須覆蓋所有可判斷者）：
{candidate_json}

逐字稿正則提取到的代號（僅供校對）：
{seed_text}

注意：
- 這是**全量提取任務**，不要只挑一檔代表。
- 對候選清單中的**每一檔**都要輸出一筆結果，不可遺漏。
- 若態度不明，label 必須填「模糊」，不可省略該檔。
- 若同一檔被多次提及，保留最後一次明確態度。
- 即使分析師沒有直接說「買進」，若大量正向論述財報/產業前景，應給高分。
- label 僅可為：買進、賣出、中立（持有）、模糊。
- 每筆都必須提供 label_reason，解釋為何判定該 label（20~60 字）。
- reasoning 請精簡（最多 60 字），避免過長。
- ticker 優先輸出可交易代碼（4-5 位數，可含尾碼字母，例如 00662U）。
- 如果完全沒有可提取標的，請輸出空陣列 []。
- 僅輸出 JSON，不要加入額外說明。

逐字稿如下：
{transcript}
""".strip()

    def _get_missing_candidate_prompt(self, transcript: str, missing_candidates: List[dict]) -> str:
        missing_json = json.dumps(missing_candidates, ensure_ascii=False)
        return f"""
以下候選標的在第一次結果中缺失，請逐一補齊評分。

缺失清單（每一檔都必須輸出一筆）：
{missing_json}

輸出 JSON 陣列，每筆格式：
{{
    "ticker": "股票代號",
    "stock_name": "標的名稱",
    "reasoning": "簡短依據",
    "label": "買進|賣出|中立（持有）|模糊",
    "label_reason": "判定標籤原因"
}}

規則：
- 不可新增清單外 ticker。
- 若資訊不足，label 請填模糊。
- 只輸出 JSON。

逐字稿：
{transcript}
""".strip()

    def _get_multimodal_prompt(self) -> str:
        prompt_template = self.config.get("prompts.multimodal_prompt", "")
        if not prompt_template:
            prompt_template = """
你將直接觀看/聆聽上傳的影片或音訊，請一次性完成台股標的結構化評分。

請輸出 JSON 陣列，每筆格式：
{
    "ticker": "股票代號(4~5碼，可含尾碼字母)",
    "stock_name": "標的名稱",
    "reasoning": "簡短依據（<=40字）",
    "label": "買進|賣出|中立（持有）|模糊",
    "label_reason": "標籤理由（20~40字）"
}

規則：
- 全量提取所有被提及可交易標的（股票/ETF/槓反ETF）。
- 若態度不明，label 必須為「模糊」，不能省略。
- 嚴格排除「年份/年代/價格/數量」等數字（例如 1987、1970、1000）作為 ticker。
- 若 stock_name 與 ticker 對不上（例如 2002 卻填威剛），該筆不得輸出。
- 僅輸出 JSON，不要其他文字。
"""
        return prompt_template.strip()

    def _get_youtube_url_multimodal_prompt(self, youtube_url: str) -> str:
        prompt_template = self.config.get("prompts.youtube_url_multimodal_prompt", "")
        if not prompt_template:
            prompt_template = """
請直接觀看/理解以下 YouTube 影片網址內容，並完成台股標的結構化評分：
{youtube_url}

請輸出 JSON 陣列，每筆格式：
{{
    "ticker": "股票代號(4~5碼，可含尾碼字母)",
    "stock_name": "標的名稱",
    "reasoning": "簡短依據（<=40字）",
    "label": "買進|賣出|中立（持有）|模糊",
    "label_reason": "標籤理由（20~40字）"
}}

規則：
- 全量提取所有被提及可交易標的（股票/ETF/槓反ETF）。
- 若態度不明，label 必須為「模糊」，不能省略。
- 嚴格排除「年份/年代/價格/數量」等數字（例如 1987、1970、1000）作為 ticker。
- 若 stock_name 與 ticker 對不上（例如 2002 卻填威剛），該筆不得輸出。
- 僅輸出 JSON，不要其他文字。
"""
        return prompt_template.format(youtube_url=youtube_url).strip()


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
