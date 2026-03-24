#!/usr/bin/env python3
"""
Process a single YouTube video through the entire pipeline
Usage: python scripts/process_video.py <video_url>
"""

import argparse
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.pipeline.orchestrator import SignalPipeline
from tw_analyst_pipeline.utils.config import get_settings, get_pipeline_config
from tw_analyst_pipeline.utils.logging import setup_logging


def _extract_video_id_no_network(video_url: str) -> str:
    parsed = urlparse(video_url)

    if parsed.netloc in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/")
        return candidate or video_url

    if "youtube.com" in parsed.netloc:
        query_id = parse_qs(parsed.query).get("v", [""])[0]
        if query_id:
            return query_id
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 2 and path_parts[0] in {"shorts", "live"}:
            return path_parts[1]

    return video_url


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
    parser.add_argument(
        "--mode",
        type=str,
        default=None,
        choices=["audio", "url", "text"],
        help="Processing mode: audio (default), url, text",
    )
    parser.add_argument(
        "--text-source",
        type=str,
        default=None,
        choices=["auto", "cc", "gemini"],
        help="Transcript source for text mode: auto, cc, gemini",
    )
    parser.add_argument(
        "--direct-youtube",
        action="store_true",
        help="Backward-compatible alias of --mode url",
    )
    parser.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help="Override LLM model (e.g. gemini-2.5-pro)",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    # Create pipeline
    try:
        settings = get_settings()
        if args.llm_model:
            settings.llm_model = args.llm_model
            try:
                settings.model_fields_set.add("llm_model")
            except Exception:
                pass
        pipeline_config = get_pipeline_config()
        pipeline = SignalPipeline(settings, pipeline_config)

        default_mode = str(pipeline_config.get("execution.mode", "audio") or "audio").lower()
        default_text_source = str(
            pipeline_config.get("execution.text_transcript_source", "auto") or "auto"
        ).lower()

        mode = (args.mode or default_mode).lower()
        if args.direct_youtube:
            mode = "url"

        text_source = (args.text_source or default_text_source).lower()

        if mode not in {"audio", "url", "text"}:
            raise ValueError(f"Invalid mode: {mode}")
        if text_source not in {"auto", "cc", "gemini"}:
            raise ValueError(f"Invalid text source: {text_source}")

    except Exception as e:
        print(f"✗ Failed to initialize pipeline: {e}")
        return 1

    # Process video
    try:
        print(f"\n{'=' * 70}")
        print(f"Processing: {args.video_url}")
        print(f"Mode: {mode}")
        if mode == "text":
            print(f"Text source: {text_source}")
        resolved_model = settings.llm_model
        extractor = getattr(pipeline, "llm_extractor", None)
        if extractor and hasattr(extractor, "_resolve_gemini_model_name"):
            try:
                resolved_model = extractor._resolve_gemini_model_name()
            except Exception:
                pass
        print(f"LLM model: {resolved_model}")
        print(f"{'=' * 70}\n")

        analysis = pipeline.process_video(
            video_url=args.video_url,
            video_id=_extract_video_id_no_network(args.video_url),
            analyst_name=args.analyst,
            skip_download=args.skip_download,
            mode=mode,
            text_transcript_source=text_source,
        )

        if analysis:
            print(f"\n{'=' * 70}")
            print(f"✓ RESULT: {len(analysis.signals)} signals extracted")
            print(f"{'=' * 70}\n")

            # Print signals
            for i, signal in enumerate(analysis.signals, 1):
                print(f"{i}. {signal.stock_code} {signal.stock_name}")
                print(f"   Action: {signal.action.value:6}")
                print(f"   Reason: {signal.reasoning}")
                if signal.mentioned_price:
                    print(f"   Target: ${signal.mentioned_price:.2f}")
                print()

            print(f"Market outlook: {analysis.market_outlook or 'N/A'}")
            print(f"Video views: {analysis.video_view_count if analysis.video_view_count is not None else 'N/A'}")
            print(f"Processing time: {analysis.processing_duration_seconds:.1f}s")

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
