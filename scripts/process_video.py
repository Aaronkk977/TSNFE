#!/usr/bin/env python3
"""
Process a single YouTube video through the entire pipeline
Usage: python scripts/process_video.py <video_url>
"""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.pipeline.orchestrator import SignalPipeline
from tw_analyst_pipeline.utils.config import get_settings, get_pipeline_config
from tw_analyst_pipeline.utils.logging import setup_logging


def main():
    parser = argparse.ArgumentParser(
        description="Process a YouTube video to extract stock signals"
    )
    parser.add_argument("video_url", help="YouTube video URL or video ID")
    parser.add_argument(
        "--analyst",
        type=str,
        default=None,
        help="Analyst name for metadata",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip audio download (use cached file)",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    # Create pipeline
    try:
        settings = get_settings()
        pipeline_config = get_pipeline_config()
        pipeline = SignalPipeline(settings, pipeline_config)

    except Exception as e:
        print(f"✗ Failed to initialize pipeline: {e}")
        return 1

    # Process video
    try:
        print(f"\n{'=' * 70}")
        print(f"Processing: {args.video_url}")
        print(f"{'=' * 70}\n")

        analysis = pipeline.process_video(
            video_url=args.video_url,
            analyst_name=args.analyst,
        )

        if analysis:
            print(f"\n{'=' * 70}")
            print(f"✓ RESULT: {len(analysis.signals)} signals extracted")
            print(f"{'=' * 70}\n")

            # Print signals
            for i, signal in enumerate(analysis.signals, 1):
                print(f"{i}. {signal.stock_code} {signal.stock_name}")
                print(f"   Action: {signal.action.value:6} | Confidence: {signal.confidence:.1%}")
                print(f"   Reason: {signal.reasoning}")
                if signal.mentioned_price:
                    print(f"   Target: ${signal.mentioned_price:.2f}")
                print()

            print(f"Market outlook: {analysis.market_outlook or 'N/A'}")
            print(f"Video views: {analysis.video_view_count if analysis.video_view_count is not None else 'N/A'}")
            print(f"Label: {analysis.normalized_label or '中立'}")
            print(f"Processing time: {analysis.processing_duration_seconds:.1f}s")
            print(f"Overall confidence: {analysis.confidence_score:.1%}")

            # Print stats
            stats = pipeline.get_stats()
            print(f"\nPipeline Statistics:")
            print(f"  Total API calls: {stats['api_calls']}")
            print(f"  Total cost: ${stats['total_cost_usd']:.4f}")
            if stats['api_calls'] > 0:
                print(f"  Average cost per video: ${stats['avg_cost_per_video']:.4f}")

            return 0
        else:
            return 1

    except KeyboardInterrupt:
        print("\n✗ Interrupted by user")
        return 130

    except Exception as e:
        print(f"\n✗ Processing failed: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
