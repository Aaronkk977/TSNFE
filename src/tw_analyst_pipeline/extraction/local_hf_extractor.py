"""
Local Hugging Face Extractor for testing local LLMs.
"""

import json
import os
import time
from typing import List, Optional

import torch
from transformers import pipeline, AutoModelForCausalLM, AutoTokenizer

from .schemas import StockSignal, VideoAnalysis
from .llm_client import BaseLLMExtractor
from ..utils.config import PipelineConfig, Settings

class LocalHuggingFaceExtractor(BaseLLMExtractor):
    """Extract signals using a local Hugging Face model via transformers Pipeline."""

    def __init__(
        self,
        settings: Settings,
        pipeline_config: PipelineConfig,
    ):
        super().__init__(settings, pipeline_config)
        self.logger.info("Initializing LocalHuggingFaceExtractor...")
        
        # Use YAML first, then settings, then a sensible default
        self.model_id = (
            self.config.get("extraction.local_hf.model")
            or self.config.get("extraction.models.local_hf")
            or self.settings.llm_model
            or "Qwen/Qwen3.6-35B-A3B"
        )

        self.temperature = float(
            self.config.get("extraction.local_hf.temperature", self.settings.llm_temperature)
            or 0.0
        )
        self.max_new_tokens = int(
            self.config.get("extraction.local_hf.max_tokens", self.settings.llm_max_tokens)
            or self.settings.llm_max_tokens
        )
        self.device_map = self.config.get("extraction.local_hf.device_map", "auto")
        self.trust_remote_code = bool(self.config.get("extraction.local_hf.trust_remote_code", True))
        self.attn_implementation = self.config.get("extraction.local_hf.attn_implementation", None)
        self.hf_token = (
            getattr(self.settings, "hugging_face_api_key", None)
            or os.getenv("HF_TOKEN")
            or os.getenv("HUGGINGFACE_HUB_TOKEN")
        )

        if self.hf_token:
            os.environ.setdefault("HF_TOKEN", self.hf_token)
            os.environ.setdefault("HUGGINGFACE_HUB_TOKEN", self.hf_token)
            
        self.logger.info(f"Loading local model: {self.model_id}")
        
        tokenizer_kwargs = {
            "trust_remote_code": self.trust_remote_code,
        }
        if self.hf_token:
            tokenizer_kwargs["token"] = self.hf_token

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, **tokenizer_kwargs)
        
        # Build kwargs for model
        model_kwargs = {
            "device_map": self.device_map,
            "torch_dtype": torch.float16,
            "trust_remote_code": self.trust_remote_code,
        }
        if self.attn_implementation:
            model_kwargs["attn_implementation"] = self.attn_implementation
        if self.hf_token:
            model_kwargs["token"] = self.hf_token

        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            **model_kwargs
        )
        
        self.pipe = pipeline(
            "text-generation",
            model=self.model,
            tokenizer=self.tokenizer,
        )
        self.logger.info("Local model loaded successfully.")

    @staticmethod
    def _extract_generated_text(outputs) -> str:
        if not outputs:
            return ""

        first_output = outputs[0]
        if isinstance(first_output, dict):
            text = first_output.get("generated_text") or first_output.get("text") or ""
            return str(text).strip()

        return str(first_output).strip()

    def _get_system_prompt(self) -> str:
        """Get the Qwen-specific system prompt from config."""
        prompt = self.config.get("prompts.system_qwen")
        if not prompt:
            prompt = self.config.get("prompts.system", "你是台灣股市分析助手，從影片逐字稿中提取股票訊號。")
        return prompt

    def _get_extraction_prompt(self, transcript: str) -> str:
        """Get the Qwen-specific extraction prompt from config."""
        prompt_template = self.config.get("prompts.extraction_qwen", "")
        if not prompt_template:
            prompt_template = self.config.get("prompts.extraction", "")
        
        if not prompt_template:
            prompt_template = "從以下逐字稿提取台灣股票買賣訊號，返回 JSON 陣列。\n\n{transcript}"
            
        if "{transcript}" in prompt_template:
            return prompt_template.replace("{transcript}", transcript)
        return f"{prompt_template}\n\n{transcript}"

    def extract_signals(
        self,
        transcript: str,
        video_id: str,
        analyst_name: Optional[str] = None,
    ) -> VideoAnalysis:
        """Extract stock signals from transcript mapping the flow like Gemini API."""
        self.logger.info(f"Local text-based extraction from transcript: {video_id}")
        start_time = time.time()

        try:
            # 1. Grab Prompts
            system_prompt = self._get_system_prompt()
            user_prompt = self._get_extraction_prompt(transcript)
            
            # Format using chat template for instruct models
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )

            # 2. Run Inference
            self.logger.info(f"Running inference with {self.model_id}...")
            temperature = self.temperature
            do_sample = True if temperature > 0 else False
            
            # For backtesting, you might tune these parameters:
            outputs = self.pipe(
                prompt,
                max_new_tokens=self.max_new_tokens,
                temperature=temperature if do_sample else None,
                do_sample=do_sample,
                return_full_text=False, # Avoid returning the prompt
            )
            
            response_text = self._extract_generated_text(outputs)
            if not response_text:
                raise RuntimeError(
                    "Local model returned an empty response. "
                    f"Check model {self.model_id}, token limits, and prompt formatting."
                )
            self.logger.debug(f"Raw Model Output:\n{response_text}")

            # 3. Parse JSON using the base parser rules
            try:
                signals_data = self._safe_parse_json(response_text)
            except json.JSONDecodeError as parse_error:
                self.logger.error(
                    "Model output was not valid JSON. "
                    f"Parse error: {parse_error}; raw output starts with: {response_text[:300]!r}"
                )
                raise
            if not isinstance(signals_data, list):
                signals_data = [signals_data]

            # 4. Construct schemas
            signals = []
            for item in signals_data:
                stock_code = str(item.get("ticker", "") or item.get("stock_code", "")).strip()
                if stock_code.isdigit() and len(stock_code) < 4:
                    stock_code = stock_code.zfill(4)
                
                raw_label = item.get("label", "中立")
                # First try to get 'action' directly (often 'buy', 'sell', 'hold' from Qwen)
                action = item.get("action")
                if not action or action not in ["buy", "sell", "hold"]:
                    action = self._action_from_label(raw_label)
                normalized = self._action_from_label(raw_label) # Just reuse for normal label
                
                signals.append(
                    StockSignal(
                        stock_code=stock_code,
                        stock_name=item.get("stock_name", stock_code),
                        action=action,
                        confidence=float(item.get("confidence", 1.0)),
                        reasoning=item.get("reasoning", ""),
                        implied_label=raw_label,
                        normalized_label=raw_label,
                        label_reason=item.get("label_reason", ""),
                    )
                )

            processing_time = time.time() - start_time
            result = VideoAnalysis(
                video_id=video_id,
                analyst_name=analyst_name,
                signals=signals,
                transcript_length_chars=len(transcript),
                processing_duration_seconds=processing_time,
            )

            self.logger.info(
                f"Local extraction done in {processing_time:.1f}s "
                f"({len(result.signals)} signals)"
            )
            return result

        except Exception as e:
            self.logger.error(f"Local extraction failed: {e}")
            raise
