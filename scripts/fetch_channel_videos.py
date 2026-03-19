#!/usr/bin/env python3
"""
Fetch videos from a YouTube channel and optionally download/transcribe them
No LLM processing - just transcript generation
"""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.youtube.fetcher import YouTubeFetcher
from tw_analyst_pipeline.youtube.downloader import AudioDownloader
from tw_analyst_pipeline.transcription import TranscriberFactory
from tw_analyst_pipeline.utils.config import get_settings
from tw_analyst_pipeline.utils.logging import setup_logging


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and process videos from YouTube analyst channel"
    )
    parser.add_argument(
        "channel",
        help="Channel handle (e.g., @win16888) or URL",
    )
    parser.add_argument(
        "--max-videos",
        type=int,
        default=5,
        help="Maximum number of videos to fetch (default: 5)",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=7,
        help="Only fetch videos from last N days (default: 7)",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download audio files",
    )
    parser.add_argument(
        "--transcribe",
        action="store_true",
        help="Transcribe audio to text (implies --download)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Setup
    setup_logging(level=args.log_level)
    settings = get_settings()

    print("\n" + "=" * 70)
    print("YouTube Channel Video Fetcher")
    print("=" * 70 + "\n")

    # Extract channel handle from URL if needed
    channel = args.channel
    if "youtube.com/@" in channel:
        channel = channel.split("@")[1].split("/")[0]
    elif not channel.startswith("@"):
        channel = f"@{channel}"

    print(f"📺 Channel: {channel}")
    print(f"📊 Max videos: {args.max_videos}")
    print(f"📅 Days back: {args.days_back}")
    print()

    # Initialize YouTube fetcher
    try:
        fetcher = YouTubeFetcher(settings)
    except Exception as e:
        print(f"✗ Failed to initialize YouTube fetcher: {e}")
        print("\nMake sure YOUTUBE_API_KEY is set in .env file")
        return 1

    # Get channel ID
    print(f"🔍 Looking up channel ID for {channel}...")
    channel_id = fetcher.get_channel_id_from_handle(channel)

    if not channel_id:
        print(f"✗ Could not find channel: {channel}")
        return 1

    print(f"✓ Channel ID: {channel_id}\n")

    # Fetch videos
    print(f"📥 Fetching recent videos...")
    videos = fetcher.get_channel_videos(
        channel_id=channel_id,
        max_results=args.max_videos,
        days_back=args.days_back,
    )

    if not videos:
        print("✗ No videos found")
        return 1

    print(f"✓ Found {len(videos)} videos\n")

    # Display videos
    print("=" * 70)
    print("Videos:")
    print("=" * 70 + "\n")

    for i, video in enumerate(videos, 1):
        print(f"{i}. {video.title}")
        print(f"   ID: {video.video_id}")
        print(f"   Published: {video.published_at}")
        print(f"   Views: {video.view_count if video.view_count is not None else 'N/A'}")
        print(f"   URL: https://youtube.com/watch?v={video.video_id}")
        print()

    # Save video list
    output_file = Path("data") / "video_list.json"
    fetcher.save_video_list(videos, output_file)
    print(f"💾 Video list saved to: {output_file}\n")

    # Optional: Download and transcribe
    if args.transcribe:
        args.download = True  # Transcribe implies download

    if args.download or args.transcribe:
        print("=" * 70)
        print("Processing Videos")
        print("=" * 70 + "\n")

        downloader = AudioDownloader(settings) if args.download else None
        transcriber = TranscriberFactory.create(settings) if args.transcribe else None

        for i, video in enumerate(videos, 1):
            video_url = f"https://youtube.com/watch?v={video.video_id}"
            print(f"\n[{i}/{len(videos)}] Processing: {video.title[:50]}...")

            try:
                # Download
                if args.download:
                    print(f"  📥 Downloading audio...")
                    audio_path = downloader.download(video_url)
                    if audio_path:
                        print(f"  ✓ Audio downloaded: {audio_path.name}")
                    else:
                        print(f"  ✗ Download failed")
                        continue

                # Transcribe
                if args.transcribe:
                    # Check if already transcribed
                    if transcriber.is_transcribed(video.video_id):
                        print(f"  ⏭️  Already transcribed, loading from cache...")
                        transcript = transcriber.load_transcript(video.video_id)
                    else:
                        print(f"  🎤 Transcribing audio (this may take a few minutes)...")
                        transcript = transcriber.transcribe(audio_path, video.video_id)

                    if transcript:
                        print(f"  ✓ Transcript ready: {len(transcript.text)} chars")
                        print(f"  📝 Preview: {transcript.text[:100]}...")
                    else:
                        print(f"  ✗ Transcription failed")

            except Exception as e:
                print(f"  ✗ Error: {e}")
                continue

    print("\n" + "=" * 70)
    print("✓ Done!")
    print("=" * 70 + "\n")

    if not (args.download or args.transcribe):
        print("💡 Tip: Use --transcribe to download and transcribe videos")
        print("   Example: python scripts/fetch_channel_videos.py @win16888 --transcribe\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
