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
from ..utils.transcript_repetition import dedupe_transcript_text


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

    def _resolve_max_output_tokens(self) -> int:
        """Gemini 2.5 Flash allows up to ~64k output tokens; 8192 was truncating long transcripts."""
        cap = 65536
        if self.config is None:
            return cap
        raw = self.config.get("transcription.gemini_max_output_tokens")
        if raw is None:
            return cap
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return cap
        return max(1024, min(n, cap))

    def _model_supports_logit_penalties(self) -> bool:
        """Gemini API returns 400 'Penalty is not enabled' for e.g. gemini-2.5-flash."""
        m = (self.model_name or "").lower()
        if "flash" in m:
            return False
        return True

    def _cfg_float(self, key: str, default: float) -> float:
        if self.config is None:
            return default
        raw = self.config.get(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    def _cfg_int(self, key: str, default: int) -> int:
        if self.config is None:
            return default
        raw = self.config.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def _build_transcription_generation_kwargs(self, max_out: int) -> dict:
        """Sampling params for transcribe; omits frequency/presence penalty on Flash."""
        gen_kw: dict = {
            "temperature": self._cfg_float("transcription.gemini_temperature", 0.0),
            "max_output_tokens": max_out,
            "top_p": self._cfg_float("transcription.gemini_top_p", 0.95),
            "top_k": self._cfg_int("transcription.gemini_top_k", 64),
        }
        if self._model_supports_logit_penalties():
            fp = self.config.get("transcription.gemini_frequency_penalty") if self.config else None
            pp = self.config.get("transcription.gemini_presence_penalty") if self.config else None
            if fp is not None:
                try:
                    gen_kw["frequency_penalty"] = float(fp)
                except (TypeError, ValueError):
                    pass
            if pp is not None:
                try:
                    gen_kw["presence_penalty"] = float(pp)
                except (TypeError, ValueError):
                    pass
        else:
            if self.config and (
                self.config.get("transcription.gemini_frequency_penalty") is not None
                or self.config.get("transcription.gemini_presence_penalty") is not None
            ):
                self.logger.info(
                    "Omitting frequency/presence penalty for {} (not supported on this model)",
                    self.model_name,
                )
        return gen_kw

    def _generate_content_transcription(self, prompt: str, uploaded_file, gen_kw: dict):
        """Call Gemini; retry once without penalties if API rejects Penalty on this model."""
        try:
            return self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, uploaded_file],
                config=types.GenerateContentConfig(**gen_kw),
            )
        except Exception as exc:
            err_s = str(exc).lower()
            if (
                "penalty" in err_s
                and ("invalid" in err_s or "400" in err_s or "not enabled" in err_s)
                and ("frequency_penalty" in gen_kw or "presence_penalty" in gen_kw)
            ):
                self.logger.warning(
                    "Gemini rejected penalty params ({}); retrying without frequency/presence penalty",
                    exc,
                )
                stripped = {
                    k: v
                    for k, v in gen_kw.items()
                    if k not in ("frequency_penalty", "presence_penalty")
                }
                return self.client.models.generate_content(
                    model=self.model_name,
                    contents=[prompt, uploaded_file],
                    config=types.GenerateContentConfig(**stripped),
                )
            raise

    @staticmethod
    def _primary_finish_reason(response) -> str:
        try:
            cands = getattr(response, "candidates", None) or []
            if not cands:
                return ""
            return str(getattr(cands[0], "finish_reason", "") or "")
        except Exception:
            return ""

    @staticmethod
    def _gemini_response_text_safe(response) -> str:
        """Prefer aggregate .text; fallback to candidates[].content.parts (SDK varies)."""
        try:
            text = getattr(response, "text", None)
            if text:
                return str(text).strip()
        except Exception:
            pass
        chunks: list[str] = []
        for cand in getattr(response, "candidates", []) or []:
            content = getattr(cand, "content", None)
            # content.parts may exist but be None on some SDK responses → not iterable
            raw_parts = getattr(content, "parts", None) if content else None
            parts = raw_parts if raw_parts is not None else []
            for part in parts:
                part_text = getattr(part, "text", None)
                if not part_text:
                    part_text = getattr(part, "thought", None)
                if part_text:
                    chunks.append(str(part_text))
        return "\n".join(chunks).strip()

    def _resolve_transcription_safety_settings(self) -> list:
        """Default Gemini filters often block finance/news speech (empty candidates). Loguru: use f-strings, not printf."""
        allowed = {"BLOCK_ONLY_HIGH", "BLOCK_NONE", "OFF", "BLOCK_LOW_AND_ABOVE", "BLOCK_MEDIUM_AND_ABOVE"}
        threshold = "BLOCK_ONLY_HIGH"
        if self.config is not None:
            raw = (self.config.get("transcription.gemini_transcription_safety_threshold") or "").strip().upper()
            if raw in {"BLOCK_NONE", "NONE"}:
                threshold = "BLOCK_NONE"
            elif raw in allowed:
                threshold = raw
        categories = [
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT",
        ]
        try:
            return [
                types.SafetySetting(category=c, threshold=threshold)
                for c in categories
            ]
        except Exception as exc:
            self.logger.warning(f"Could not build Gemini safety_settings ({exc}); using API defaults")
            return []

    def _resolve_thinking_config_for_transcription(self):
        """Gemini 2.5 defaults to dynamic thinking; long audio can spend 30k+ thought tokens and return empty text."""
        if not hasattr(types, "ThinkingConfig"):
            return None
        budget = 0
        if self.config is not None:
            raw = self.config.get("transcription.gemini_thinking_budget")
            if raw is not None:
                try:
                    budget = int(raw)
                except (TypeError, ValueError):
                    budget = 0
        try:
            return types.ThinkingConfig(thinking_budget=budget)
        except Exception as exc:
            self.logger.warning(f"Could not build ThinkingConfig ({exc}); omitting")
            return None

    def _log_gemini_empty_response(self, response) -> None:
        try:
            pf = getattr(response, "prompt_feedback", None)
            block_reason = getattr(pf, "block_reason", None) if pf is not None else None
            self.logger.error(f"Gemini returned empty text; prompt_feedback={pf!r} block_reason={block_reason!r}")
            usage = getattr(response, "usage_metadata", None)
            self.logger.error(f"Gemini usage_metadata={usage!r}")
            if usage is not None:
                ttc = getattr(usage, "thoughts_token_count", None)
                if ttc:
                    self.logger.error(
                        f"Gemini thoughts_token_count={ttc}: 2.5 Flash often uses this for internal reasoning; "
                        f"set transcription.gemini_thinking_budget: 0 in config.yaml for ASR."
                    )
            cands = getattr(response, "candidates", None) or []
            self.logger.error(f"Gemini candidates count={len(cands)}")
            for i, cand in enumerate(cands):
                fr = getattr(cand, "finish_reason", None)
                fr_s = str(fr) if fr is not None else ""
                if "RECITATION" in fr_s.upper():
                    self.logger.error(
                        "Gemini finish_reason=RECITATION: output withheld (recitation / overlap with "
                        "policy training-data matching). Not fixed by thinking_budget. "
                        "Use YouTube captions, whisper provider, or pipeline auto→Whisper fallback."
                    )
                self.logger.error(
                    f"Gemini candidate[{i}]: finish_reason={fr!r} "
                    f"safety_ratings={getattr(cand, 'safety_ratings', None)!r} "
                    f"content={getattr(cand, 'content', None)!r}"
                )

            debug_dir = Path(self.settings.data_debug_dir)
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_path = debug_dir / "last_gemini_transcribe_empty.json"
            payload = {}
            try:
                if hasattr(response, "model_dump"):
                    payload = response.model_dump(mode="json")
                elif hasattr(response, "to_dict"):
                    payload = response.to_dict()  # type: ignore[assignment]
                else:
                    payload = {"repr": repr(response)}
            except Exception as dump_err:
                payload = {"dump_error": str(dump_err), "repr": repr(response)}
            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            self.logger.error(f"Wrote raw Gemini response dump to {debug_path}")
        except Exception as exc:
            self.logger.warning(f"Could not log Gemini empty-response diagnostics: {exc}")

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

    def transcribe(
        self,
        audio_path: Path,
        video_id: Optional[str] = None,
        published_at: Optional[str] = None,
        *,
        persist_to_disk: bool = True,
    ) -> TranscriptResult:
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
            max_out = self._resolve_max_output_tokens()
            gen_kw = self._build_transcription_generation_kwargs(max_out)
            safety = self._resolve_transcription_safety_settings()
            if safety:
                gen_kw["safety_settings"] = safety
            think_cfg = self._resolve_thinking_config_for_transcription()
            if think_cfg is not None:
                gen_kw["thinking_config"] = think_cfg
            response = self._generate_content_transcription(prompt, uploaded_file, gen_kw)
            transcript_text = self._gemini_response_text_safe(response)
            try:
                cand0 = response.candidates[0] if getattr(response, "candidates", None) else None
                fr = getattr(cand0, "finish_reason", None) if cand0 is not None else None
                fr_s = str(fr) if fr is not None else ""
                if "MAX" in fr_s.upper() and "TOKEN" in fr_s.upper():
                    self.logger.error(
                        f"Gemini transcript hit output token limit (finish_reason={fr_s!r}, "
                        f"max_output_tokens={max_out}). If already 65536, split long audio or use Whisper."
                    )
            except Exception:
                pass

            if not transcript_text:
                self._log_gemini_empty_response(response)
                fr_empty = self._primary_finish_reason(response)
                if "RECITATION" in fr_empty.upper():
                    raise ValueError(
                        "Gemini withheld the transcript (finish_reason=RECITATION): output blocked by "
                        "recitation policy (common for broadcast/finance audio matching policy triggers). "
                        "This is not fixed by gemini_thinking_budget. Use YouTube CC (e.g. ETL cc path), "
                        "set transcription.provider to whisper, or execution.text_transcript_source: auto "
                        "for Whisper fallback in SignalPipeline. "
                        "See data/processing/debug/last_gemini_transcribe_empty.json."
                    )
                raise ValueError(
                    "Gemini returned an empty transcript. For 2.5 Flash, high thoughts_token_count with "
                    "empty parts often means internal 'thinking' used the budget — set "
                    "transcription.gemini_thinking_budget: 0. If finish_reason is RECITATION, use CC/Whisper. "
                    "See logs and data/processing/debug/last_gemini_transcribe_empty.json."
                )

            before_dedupe_len = len(transcript_text)
            cfg = self.config
            do_far = True
            if cfg is not None and cfg.get("transcription.gemini_dedupe_far_repeats", True) is False:
                do_far = False
            if do_far:
                if cfg is not None:
                    raw_a = cfg.get("transcription.gemini_far_repeat_anchor", "我是主持人")
                    try:
                        min_sig = int(
                            cfg.get("transcription.gemini_far_repeat_min_significant_chars", 2500) or 2500
                        )
                    except (TypeError, ValueError):
                        min_sig = 2500
                else:
                    raw_a, min_sig = "我是主持人", 2500
                if raw_a is False:
                    anchor = None
                else:
                    anchor = (str(raw_a).strip() or None) if raw_a is not None else "我是主持人"
                min_sig = max(400, min_sig)
                transcript_text = dedupe_transcript_text(
                    transcript_text,
                    far_repeat_anchor=anchor,
                    min_far_repeat_sig_chars=min_sig,
                    do_far_repeat=anchor is not None,
                )
                if len(transcript_text) < before_dedupe_len - 200:
                    self.logger.info(
                        "Gemini transcript de-junk: removed {} chars ({} -> {}); far-repeat anchor={!r}",
                        before_dedupe_len - len(transcript_text),
                        before_dedupe_len,
                        len(transcript_text),
                        anchor,
                    )

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

            if persist_to_disk:
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
                except (TypeError, AttributeError):
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
