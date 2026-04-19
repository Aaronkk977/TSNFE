import argparse
import subprocess
from datetime import datetime, timedelta
import sys

def main():
    import os
    os.environ["PIPELINE_OUTPUT_SUBFOLDER"] = "history"
    parser = argparse.ArgumentParser(description="Batch fetch historical analyst videos by generating daily reports for a date range.")
    parser.add_argument('--start-date', type=str, required=True, help="Start date YYYY-MM-DD")
    parser.add_argument('--end-date', type=str, required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--max-videos", type=int, default=10, help="Max videos per day to process")
    parser.add_argument("--max-videos-per-analyst", type=int, default=5, help="Max videos per analyst per day")
    parser.add_argument('--keep-audio', action='store_true', help="Keep audio file after processing (default is to delete them)")
    parser.add_argument(
        "--llm-provider",
        type=str,
        default=None,
        choices=["openai", "anthropic", "gemini", "google", "qwen", "local_hf"],
        help="Override LLM provider passed through to daily processing",
    )
    parser.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help="Override LLM model passed through to daily processing",
    )
    parser.add_argument(
        "--llm-temperature",
        type=float,
        default=None,
        help="Override LLM temperature passed through to daily processing",
    )
    parser.add_argument(
        "--llm-max-tokens",
        type=int,
        default=None,
        help="Override maximum tokens passed through to daily processing",
    )
    args = parser.parse_args()
    
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
    
    if end_date < start_date:
        print("End date must be after start date.")
        sys.exit(1)
        
    current_date = start_date
    
    print(f"Batch processing from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    
    while current_date <= end_date:
        target_date_str = current_date.strftime('%Y-%m-%d')
        print(f"\n{'='*50}\nProcessing for target date: {target_date_str}\n{'='*50}")
        
        cmd = [
            sys.executable, "scripts/daily_analyst_table.py",
            "--target-date", target_date_str,
            "--max-videos", str(args.max_videos),
            "--max-videos-per-analyst", str(args.max_videos_per_analyst)
        ]
        if args.llm_provider:
            cmd.extend(["--llm-provider", args.llm_provider])
        if args.llm_model:
            cmd.extend(["--llm-model", args.llm_model])
        if args.llm_temperature is not None:
            cmd.extend(["--llm-temperature", str(args.llm_temperature)])
        if args.llm_max_tokens is not None:
            cmd.extend(["--llm-max-tokens", str(args.llm_max_tokens)])
        if not args.keep_audio:
            cmd.append("--cleanup-audio")
            
        print(f"Running command: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error processing {target_date_str}: {e}")
            # Continue to next day even if one fails
            
        current_date += timedelta(days=1)
    
    print("\n--- ML Pipeline Execution Steps ---")
    print("1. All historical reports have been placed into data/reports/history/")
    print("2. Run 'python scripts/build_dataset.py --days 5' to generate ML dataset")
    print("3. Run 'python scripts/train_eval_models.py --days 5' to view evaluation metrics")

if __name__ == "__main__":
    main()
