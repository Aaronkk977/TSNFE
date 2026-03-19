#!/usr/bin/env python3
"""
Example: Complete pipeline walkthrough
Shows how to use the pipeline programmatically
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.pipeline.orchestrator import SignalPipeline
from tw_analyst_pipeline.utils.config import get_settings, get_pipeline_config
from tw_analyst_pipeline.utils.logging import setup_logging


def main():
    # Setup
    setup_logging(level="INFO")
    settings = get_settings()
    pipeline_config = get_pipeline_config()

    # Initialize pipeline
    pipeline = SignalPipeline(settings, pipeline_config)

    # Example 1: Process a single video
    print("\n" + "=" * 70)
    print("Example 1: Process Single Video")
    print("=" * 70)

    try:
        # Replace with a real Taiwan stock analyst video URL
        video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        analysis = pipeline.process_video(
            video_url=video_url,
            analyst_name="範例分析師",
        )

        print(f"\n✓ Extracted {len(analysis.signals)} signals")

        for i, signal in enumerate(analysis.signals, 1):
            print(f"\n{i}. {signal.stock_code} {signal.stock_name}")
            print(f"   Action: {signal.action.value}")
            print(f"   Confidence: {signal.confidence:.1%}")
            print(f"   Reasoning: {signal.reasoning}")

    except Exception as e:
        print(f"\n✗ Example 1 failed: {e}")
        print("  (This is expected if you haven't configured API keys)")

    # Example 2: Access raw configuration
    print("\n" + "=" * 70)
    print("Example 2: Configuration Access")
    print("=" * 70)

    print(f"Data directory: {settings.data_dir}")
    print(f"Log level: {settings.log_level}")
    print(f"Whisper model: {settings.whisper_model}")
    print(f"Whisper device: {settings.whisper_device}")
    print(f"LLM provider: {settings.llm_provider}")
    print(f"LLM model: {settings.llm_model}")

    # Pipeline config
    print(f"\nPipeline config:")
    print(f"  Max retries: {pipeline_config.get('pipeline.max_retries')}")
    print(f"  Transcription model: {pipeline_config.get('transcription.model')}")

    # Example 3: Stock validation
    print("\n" + "=" * 70)
    print("Example 3: Stock Code Validation")
    print("=" * 70)

    validator = pipeline.validator

    test_codes = ["2330", "台積電", "護國神山", "2454", "發哥", "0050"]

    print("\nStock code resolution:")
    for mention in test_codes:
        resolved = validator.resolve_stock_code(mention)
        if resolved:
            stock_name = validator.get_stock_name(resolved)
            print(f"  '{mention}' → {resolved} ({stock_name})")
        else:
            print(f"  '{mention}' → not found")

    # Example 4: Extract signals from text
    print("\n" + "=" * 70)
    print("Example 4: Direct Signal Extraction")
    print("=" * 70)

    sample_transcript = """
    今天我想討論台股的走勢。台積電最近表現不錯，我認為可以加碼，目標價在 1500。
    聯發科面臨技術面反壓，建議觀望。長榮運價指數下跌，應該減碼。
    """

    print(f"Sample transcript:\n{sample_transcript}\n")

    try:
        # Use LLM extractor directly
        analysis = pipeline.llm_extractor.extract_signals(
            transcript=sample_transcript,
            video_id="example",
            analyst_name="示範",
        )

        print(f"Extracted {len(analysis.signals)} signals:\n")

        for signal in analysis.signals:
            print(f"• {signal.stock_code} ({signal.stock_name})")
            print(f"  Action: {signal.action.value} | Confidence: {signal.confidence:.1%}")
            print(f"  Reason: {signal.reasoning}\n")

    except Exception as e:
        print(f"✗ Extraction failed: {e}")
        print("  (This is expected if OpenAI API key is not configured)")

    # Example 5: Pipeline statistics
    print("\n" + "=" * 70)
    print("Example 5: Pipeline Statistics")
    print("=" * 70)

    stats = pipeline.get_stats()
    print(f"Total API calls: {stats['api_calls']}")
    print(f"Total cost: ${stats['total_cost_usd']:.4f}")
    if stats['api_calls'] > 0:
        print(f"Average cost per video: ${stats['avg_cost_per_video']:.4f}")

    print("\n" + "=" * 70)
    print("Examples completed!")
    print("=" * 70)


if __name__ == "__main__":
    main()
