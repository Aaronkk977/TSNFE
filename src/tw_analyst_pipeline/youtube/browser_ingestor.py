"""
Browser-based media ingestion for YouTube.
Uses Playwright to run a real browser session and capture media stream URLs.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

import requests

from ..utils.config import Settings
from ..utils.logging import LoggerMixin


class BrowserMediaIngestor(LoggerMixin):
    """Capture media file from YouTube via real browser automation."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.output_dir = Path(settings.data_raw_dir)

    def capture(self, video_url: str, video_id: str) -> Path:
        media_urls = self._collect_media_urls(video_url)
        if not media_urls:
            raise RuntimeError("No media stream URL captured from browser session")

        selected_url = self._pick_best_media_url(media_urls)
        output_path = self._download_stream(selected_url, video_id)
        self.logger.info(f"Browser ingestion completed: {output_path}")
        return output_path

    def _collect_media_urls(self, video_url: str) -> List[str]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            raise RuntimeError(
                "Playwright not available. Install playwright and run 'playwright install chromium'."
            ) from e

        captured: List[str] = []

        def on_response(response):
            try:
                url = response.url
                if "googlevideo.com" not in url:
                    return
                if response.status != 200:
                    return
                if "mime=audio" in url or "mime=video" in url:
                    captured.append(url)
            except Exception:
                return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.on("response", on_response)

            page.goto(video_url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(8_000)

            context.close()
            browser.close()

        unique = []
        seen = set()
        for url in captured:
            if url not in seen:
                seen.add(url)
                unique.append(url)

        self.logger.info(f"Browser captured {len(unique)} media URLs")
        return unique

    def _pick_best_media_url(self, urls: List[str]) -> str:
        audio_first = [u for u in urls if "mime=audio" in u]
        ranked = audio_first or urls
        ranked.sort(key=lambda u: ("clen=" not in u, -self._content_length_hint(u)))
        return ranked[0]

    @staticmethod
    def _content_length_hint(url: str) -> int:
        try:
            q = parse_qs(urlparse(url).query)
            return int((q.get("clen") or ["0"])[0])
        except Exception:
            return 0

    def _download_stream(self, url: str, video_id: str) -> Path:
        extension = "m4a" if "mime=audio" in url else "mp4"
        output = self.output_dir / f"{video_id}.{extension}"

        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with open(output, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)

        return output
