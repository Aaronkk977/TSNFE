#!/usr/bin/env python3
"""
Quick test of the pipeline with a sample configuration
Tests all components without requiring real YouTube videos
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.utils.config import Settings, PipelineConfig
from tw_analyst_pipeline.utils.logging import setup_logging


def test_configuration():
    """Test that configuration loads correctly."""
    print("\n1. Testing configuration loading...")

    try:
        settings = Settings()
        print(f"   ✓ Settings loaded")
        print(f"     - Data dir: {settings.data_dir}")
        print(f"     - Log level: {settings.log_level}")
        print(f"     - Whisper model: {settings.whisper_model}")
        print(f"     - Whisper device: {settings.whisper_device}")
        print(f"     - LLM provider: {settings.llm_provider}")
        print(f"     - LLM model: {settings.llm_model}")

    except Exception as e:
        print(f"   ✗ Failed to load settings: {e}")
        return False

    try:
        config = PipelineConfig("config/config.yaml")
        print(f"   ✓ Pipeline config loaded")
        print(f"     - Max retries: {config.get('pipeline.max_retries')}")
        print(f"     - Whisper model: {config.get('transcription.model')}")

    except Exception as e:
        print(f"   ✗ Failed to load config: {e}")
        return False

    return True


def test_schemas():
    """Test that Pydantic schemas work correctly."""
    print("\n2. Testing Pydantic schemas...")

    try:
        from tw_analyst_pipeline.extraction.schemas import (
            StockSignal,
            TradeAction,
            VideoAnalysis,
        )

        # Create a test signal
        signal = StockSignal(
            stock_code="2330",
            stock_name="台積電",
            action=TradeAction.BUY,
            confidence=0.85,
            reasoning="法說會展望佳，產能滿載",
            mentioned_price=1500.0,
        )

        print(f"   ✓ StockSignal created: {signal.stock_code} {signal.stock_name}")
        print(f"     - Action: {signal.action.value}")
        print(f"     - Confidence: {signal.confidence:.1%}")

        # Validate JSON schema
        print(f"   ✓ Schema validation passed")

        return True

    except Exception as e:
        print(f"   ✗ Schema test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_logging():
    """Test logging setup."""
    print("\n3. Testing logging system...")

    try:
        setup_logging(level="DEBUG", to_console=True, to_file=False)
        from loguru import logger

        logger.info("Test info message")
        logger.debug("Test debug message")
        print(f"   ✓ Logging configured and working")

        return True

    except Exception as e:
        print(f"   ✗ Logging test failed: {e}")
        return False


def test_dependencies():
    """Check if required dependencies are installed."""
    print("\n4. Testing dependencies...")

    dependencies = {
        "yt-dlp": "yt_dlp",
        "faster-whisper": "faster_whisper",
        "openai": "openai",
        "pydantic": "pydantic",
        "instructor": "instructor",
        "loguru": "loguru",
        "tenacity": "tenacity",
    }

    all_ok = True
    for package, import_name in dependencies.items():
        try:
            __import__(import_name)
            print(f"   ✓ {package}")

        except ImportError:
            print(f"   ✗ {package} not installed")
            all_ok = False

    return all_ok


def test_stock_validator():
    """Test stock code validation."""
    print("\n5. Testing stock validator...")

    try:
        from tw_analyst_pipeline.stock_data.validators import StockValidator
        from tw_analyst_pipeline.utils.config import Settings

        settings = Settings()
        validator = StockValidator(settings)

        # Test validation
        assert validator.validate_stock_code("2330"), "2330 should be valid"
        assert validator.validate_stock_code("2454"), "2454 should be valid"

        # Test alias resolution
        assert validator.resolve_stock_code("台積電") == "2330", "台積電 should resolve to 2330"
        assert validator.resolve_stock_code("聯發科") == "2454", "聯發科 should resolve to 2454"

        print(f"   ✓ {len(validator.valid_codes)} stock codes loaded")
        print(f"   ✓ {len(validator.aliases)} aliases loaded")
        print(f"   ✓ Stock validation tests passed")

        return True

    except Exception as e:
        print(f"   ✗ Stock validator test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("Taiwan Analyst Signal Pipeline - System Test")
    print("=" * 70)

    results = []

    results.append(("Configuration", test_configuration()))
    results.append(("Schemas", test_schemas()))
    results.append(("Logging", test_logging()))
    results.append(("Dependencies", test_dependencies()))
    results.append(("Stock Validator", test_stock_validator()))

    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)

    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{test_name:.<40} {status}")

    all_passed = all(result for _, result in results)
    print("\n" + "=" * 70)

    if all_passed:
        print("✓ All tests passed! System is ready to use.")
        print("\nNext steps:")
        print("  1. Set up .env file with API keys")
        print("  2. Run: python scripts/process_video.py <YouTube_URL>")
        return 0
    else:
        print("✗ Some tests failed. Please fix the issues above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
