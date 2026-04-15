import sys
import re

with open("src/tw_analyst_pipeline/youtube/fetcher.py", "r") as f:
    content = f.read()

# Replace googleapiclient imports with yt_dlp
content = content.replace("from googleapiclient.discovery import build\nfrom googleapiclient.errors import HttpError", "import yt_dlp")
content = content.replace("    def _init_youtube_client(self):\n        \"\"\"Initialize YouTube Data API client.\"\"\"\n        if not self.settings.youtube_api_key:\n            raise ValueError(\"YOUTUBE_API_KEY not set in environment\")\n\n        try:\n            self.youtube = build(\n                \"youtube\",\n                \"v3\",\n                developerKey=self.settings.youtube_api_key,\n            )\n            self.logger.info(\"YouTube Data API client initialized\")\n        except Exception as e:\n            self.logger.error(f\"Failed to initialize YouTube client: {e}\")\n            raise\n", "")

