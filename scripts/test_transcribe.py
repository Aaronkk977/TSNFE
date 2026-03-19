#!/usr/bin/env python3
"""
Simple test script to transcribe downloaded audio files
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.transcription.whisper_engine import WhisperTranscriber
from tw_analyst_pipeline.utils.config import get_settings
from tw_analyst_pipeline.utils.logging import setup_logging


def main():
    setup_logging()
    settings = get_settings()

    print(f"\n{'='*70}")
    print("Whisper Transcription Test")
    print(f"{'='*70}\n")
    print(f"Device: {settings.whisper_device}")
    print(f"Model: {settings.whisper_model}")
    print(f"Compute type: {settings.whisper_compute_type}\n")

    transcriber = WhisperTranscriber(settings)

    # Check for downloaded audio files
    audio_dir = Path("data/raw")
    audio_files = list(audio_dir.glob("*.wav"))

    if not audio_files:
        print("✗ No audio files found in data/raw/")
        print("Please download a video first using:")
        print("  python scripts/fetch_channel_videos.py @win16888 --download\n")
        return 1

    print(f"Found {len(audio_files)} audio file(s):\n")
    for i, audio_file in enumerate(audio_files, 1):
        print(f"{i}. {audio_file.name} ({audio_file.stat().st_size / 1024 / 1024:.1f} MB)")

    # Transcribe first file
    print(f"\n{'='*70}")
    print(f"Transcribing: {audio_files[0].name}")
    print(f"{'='*70}\n")

    # Extract video ID from filename
    video_id = audio_files[0].stem

    # Check if already transcribed
    if transcriber.is_transcribed(video_id):
        print(f"⏭️  Already transcribed, loading from cache...\n")
        transcript = transcriber.load_transcript(video_id)
    else:
        print("🎤 Starting transcription (this may take several minutes)...")
        print("    Tip: This is running on CPU. For faster processing, fix CUDA setup.\n")
        transcript = transcriber.transcribe(audio_files[0], video_id)

    if transcript:
        print(f"\n✓ Transcription complete!")
        print(f"  Language: {transcript.language}")
        print(f"  Duration: {transcript.duration_seconds:.1f} seconds")
        print(f"  Text length: {len(transcript.text)} characters")
        print(f"  Segments: {len(transcript.segments)}")
        print(f"\n{'='*70}")
        print("Transcript Preview (first 500 chars):")
        print(f"{'='*70}\n")
        print(transcript.text[:500])
        print("\n...")
        print(f"\n{'='*70}")
        print(f"✓ Full transcript saved to: data/transcripts/{video_id}.json")
        print(f"{'='*70}\n")
        return 0
    else:
        print("\n✗ Transcription failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
