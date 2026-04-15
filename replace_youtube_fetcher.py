import re

with open("src/tw_analyst_pipeline/youtube/fetcher.py", "r") as f:
    text = f.read()

# Add yt_dlp import
if "import yt_dlp" not in text:
    text = text.replace("from googleapiclient.discovery import build\nfrom googleapiclient.errors import HttpError", "import yt_dlp\nfrom urllib.error import HTTPError as HttpError")

# Replace _init_youtube_client
text = re.sub(
    r"    def _init_youtube_client\(self\):.*?raise\s+", 
    "    def _init_youtube_client(self):\n        pass  # using yt-dlp instead\n", 
    text, flags=re.DOTALL
)

# Rewrite get_channel_id_from_handle
new_get_channel_id = """    def get_channel_id_from_handle(self, handle: str) -> Optional[str]:
        handle = handle.strip()
        if not handle: return None
        if not handle.startswith("http"):
            handle = f"https://www.youtube.com/@{handle.lstrip('@')}"

        ydl_opts = {
            'extract_flat': True,
            'quiet': True,
            'playlistend': 1,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(handle, download=False)
                channel_id = info.get('channel_id') or info.get('id')
                if channel_id:
                    self.logger.info(f"Found channel ID: {channel_id}")
                    return channel_id
        except Exception as e:
            self.logger.error(f"yt-dlp error fetching channel: {e}")
        return None"""
text = re.sub(
    r"    def get_channel_id_from_handle\(self, handle: str\) -> Optional\[str\]:.*?return None\s+(?=    @)",
    new_get_channel_id + "\n\n",
    text, flags=re.DOTALL
)

# Rewrite get_channel_videos
new_get_channel_videos = """    def get_channel_videos(
        self, channel_id: str, max_results: int = 10, days_back: Optional[int] = 7,
        exclude_shorts: bool = False, min_duration_seconds: Optional[int] = None,
        published_after_dt: Optional[datetime] = None, published_before_dt: Optional[datetime] = None,
    ) -> List[VideoInfo]:
        self.logger.info(f"Fetching videos from channel: {channel_id}")
        if published_after_dt:
            after_dt = published_after_dt
        elif days_back:
            after_dt = datetime.utcnow() - timedelta(days=days_back)
        else:
            after_dt = None
            
        before_dt = published_before_dt
        url = f"https://www.youtube.com/channel/{channel_id}/videos"
        ydl_opts = {
            'extract_flat': True,
            'quiet': True,
            'playlistend': max_results * 2, # Fetch more to account for filters
        }
        videos = []
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                entries = info.get('entries', [])
                for entry in entries:
                    if not entry: continue
                    pub_timestamp = entry.get('timestamp')
                    if pub_timestamp:
                        dt = datetime.utcfromtimestamp(pub_timestamp)
                        if after_dt and dt < after_dt: continue
                        if before_dt and dt > before_dt: continue
                        pub_str = dt.isoformat() + "Z"
                    else:
                        pub_str = ""
                    
                    duration = entry.get('duration')
                    if duration:
                        if exclude_shorts and duration <= 180: continue
                        if min_duration_seconds and duration <= min_duration_seconds: continue

                    videos.append(VideoInfo(
                        video_id=entry.get('id'),
                        title=entry.get('title'),
                        description=entry.get('description', ''),
                        published_at=pub_str,
                        channel_id=info.get('channel_id', channel_id),
                        channel_title=info.get('uploader', ''),
                        duration=str(duration) if duration else None,
                        view_count=entry.get('view_count')
                    ))
                    if len(videos) >= max_results:
                        break
        except Exception as e:
            self.logger.error(f"yt-dlp error fetching videos: {e}")
            
        return videos"""
text = re.sub(
    r"    @retry_with_backoff.*?(?=class |$)",
    new_get_channel_videos + "\n\n",
    text, flags=re.DOTALL
)

with open("src/tw_analyst_pipeline/youtube/fetcher.py", "w") as f:
    f.write(text)

