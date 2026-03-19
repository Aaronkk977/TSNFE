#!/usr/bin/env python3
"""
Quick transcription test with performance timing
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.transcription.whisper_engine import WhisperTranscriber
from tw_analyst_pipeline.utils.config import get_settings
from tw_analyst_pipeline.utils.logging import setup_logging


def main():
    setup_logging(level="INFO")
    settings = get_settings()

    print(f"\n{'='*70}")
    print("快速轉錄測試")
    print(f"{'='*70}\n")
    print(f"模型: {settings.whisper_model}")
    print(f"設備: {settings.whisper_device}")
    print(f"計算類型: {settings.whisper_compute_type}\n")

    # Check for downloaded audio
    audio_dir = Path("data/raw")
    audio_files = sorted(audio_dir.glob("*.wav"))

    if not audio_files:
        print("✗ 找不到音頻文件")
        return 1

    # Use first (smallest/shortest) file
    audio_file = audio_files[0]
    video_id = audio_file.stem
    
    file_size_mb = audio_file.stat().st_size / 1024 / 1024
    estimated_duration_min = file_size_mb / 18  # Rough estimate: ~18 MB per minute
    
    print(f"測試文件: {audio_file.name}")
    print(f"大小: {file_size_mb:.1f} MB")
    print(f"預估時長: ~{estimated_duration_min:.0f} 分鐘\n")

    transcriber = WhisperTranscriber(settings)

    # Check if already done
    if transcriber.is_transcribed(video_id):
        print(f"✓ 已有轉錄記錄，載入中...\n")
        transcript = transcriber.load_transcript(video_id)
        
        print(f"語言: {transcript.language}")
        print(f"時長: {transcript.duration_seconds:.1f} 秒")
        print(f"文本長度: {len(transcript.text)} 字符")
        print(f"\n{'='*70}")
        print("轉錄預覽:")
        print(f"{'='*70}\n")
        print(transcript.text[:300])
        print("\n...")
        return 0

    # Transcribe with timing
    print(f"{'='*70}")
    print("開始轉錄...")
    print(f"{'='*70}\n")
    
    if settings.whisper_model == "small":
        est_time = estimated_duration_min * 1.5
        print(f"⏱️  預計需要: ~{est_time:.0f} 分鐘 (CPU + small 模型)")
    else:
        est_time = estimated_duration_min * 2.5
        print(f"⏱️  預計需要: ~{est_time:.0f} 分鐘 (CPU + medium 模型)")
    
    print("💡 提示: 可以按 Ctrl+C 中斷，稍後繼續\n")

    start_time = time.time()
    transcript = transcriber.transcribe(audio_file, video_id)
    elapsed = time.time() - start_time

    if transcript:
        print(f"\n{'='*70}")
        print(f"✓ 轉錄完成！")
        print(f"{'='*70}\n")
        print(f"實際耗時: {elapsed/60:.1f} 分鐘")
        print(f"語言: {transcript.language}")
        print(f"時長: {transcript.duration_seconds:.1f} 秒")
        print(f"速度比: {transcript.duration_seconds/elapsed:.2f}x (1.0x = 實時)")
        print(f"文本長度: {len(transcript.text)} 字符")
        print(f"\n{'='*70}")
        print("轉錄預覽:")
        print(f"{'='*70}\n")
        print(transcript.text[:300])
        print("\n...")
        print(f"\n完整轉錄已保存: data/transcripts/{video_id}.json\n")
        return 0
    else:
        print("\n✗ 轉錄失敗")
        return 1


if __name__ == "__main__":
    sys.exit(main())
