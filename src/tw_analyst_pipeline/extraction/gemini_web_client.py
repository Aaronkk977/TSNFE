"""Gemini web automation client using Playwright.

This module automates gemini.google.com with a persistent browser profile,
sends an @YouTube prompt, and extracts structured JSON from the response.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from ..utils.logging import LoggerMixin


class GeminiWebClient(LoggerMixin):
    """Automate Gemini web UI and extract JSON output."""

    def __init__(self, user_data_dir: Path, headless: bool = False, cdp_url: Optional[str] = None):
        self.user_data_dir = Path(user_data_dir)
        self.headless = headless
        self.cdp_url = cdp_url

    def run_youtube_prompt(
        self,
        video_url: str,
        prompt: str,
        timeout_seconds: int = 180,
    ) -> Any:
        """Run an @YouTube prompt on Gemini web and return parsed JSON."""

        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            raise RuntimeError(
                "Playwright not installed. Run `pip install playwright` and `playwright install chromium`."
            ) from e

        final_prompt = self._build_prompt(video_url, prompt)

        with sync_playwright() as p:
            if self.cdp_url:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
            else:
                self.user_data_dir.mkdir(parents=True, exist_ok=True)
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.user_data_dir),
                    headless=self.headless,
                    viewport={"width": 1440, "height": 900},
                )
                page = context.new_page()

            page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=90_000)
            self._wait_for_input_ready(page, timeout_seconds=timeout_seconds)
            self._send_prompt(page, final_prompt)

            deadline = time.time() + timeout_seconds
            last_error: Optional[str] = None
            while time.time() < deadline:
                try:
                    candidates = self._collect_response_candidates(page)
                    parsed = self._parse_candidates(candidates)
                    if parsed is not None:
                        page.close()
                        if not self.cdp_url:
                            context.close()
                        return parsed
                except Exception as e:
                    last_error = str(e)
                page.wait_for_timeout(3000)

            page.close()
            if not self.cdp_url:
                context.close()
            raise RuntimeError(
                f"Timed out waiting for JSON response from Gemini web. Last parser error: {last_error}"
            )

    @staticmethod
    def _build_prompt(video_url: str, prompt: str) -> str:
        return f"@YouTube {video_url}\n\n{prompt}".strip()

    def _wait_for_input_ready(self, page, timeout_seconds: int):
        selectors = [
            "textarea",
            "div[contenteditable='true']",
            "rich-textarea div[contenteditable='true']",
        ]
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            for selector in selectors:
                locator = page.locator(selector)
                if locator.count() > 0:
                    try:
                        locator.first.wait_for(state="visible", timeout=1500)
                        return
                    except Exception:
                        continue
            page.wait_for_timeout(1000)

        raise RuntimeError(
            "Gemini input box not found. Please confirm you are logged in. "
            "If this is first run, execute with --headless false and complete login manually."
        )

    def _send_prompt(self, page, prompt: str):
        input_box = None
        for selector in [
            "textarea",
            "rich-textarea div[contenteditable='true']",
            "div[contenteditable='true']",
        ]:
            locator = page.locator(selector)
            if locator.count() > 0:
                input_box = locator.first
                break

        if input_box is None:
            raise RuntimeError("No editable input found on Gemini page")

        try:
            input_box.click()
            input_box.fill(prompt)
        except Exception:
            input_box.click()
            page.keyboard.type(prompt)

        page.keyboard.press("Enter")

    @staticmethod
    def _collect_response_candidates(page) -> list[str]:
        return page.evaluate(
            """
            () => {
              const texts = [];
              const push = (s) => { if (s && typeof s === 'string' && s.trim().length > 0) texts.push(s.trim()); };

              document.querySelectorAll('pre').forEach(el => push(el.innerText));
              document.querySelectorAll('code').forEach(el => push(el.innerText));
              document.querySelectorAll('model-response, article, .markdown, .response-content').forEach(el => push(el.innerText));
              push(document.body ? document.body.innerText : '');

              return texts;
            }
            """
        )

    def _parse_candidates(self, candidates: list[str]) -> Optional[Any]:
        for text in reversed(candidates):
            parsed = self._try_parse_json_like(text)
            if parsed is not None:
                return parsed
        return None

    def _try_parse_json_like(self, text: str) -> Optional[Any]:
        cleaned = (text or "").strip()
        if not cleaned:
            return None

        fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, flags=re.IGNORECASE)
        for block in reversed(fenced):
            try:
                return json.loads(block)
            except Exception:
                continue

        try:
            return json.loads(cleaned)
        except Exception:
            pass

        for start_char, end_char in [("[", "]"), ("{", "}")]:
            start_idx = cleaned.find(start_char)
            end_idx = cleaned.rfind(end_char)
            if start_idx == -1 or end_idx <= start_idx:
                continue
            block = cleaned[start_idx:end_idx + 1]
            try:
                return json.loads(block)
            except Exception:
                continue

        return None
