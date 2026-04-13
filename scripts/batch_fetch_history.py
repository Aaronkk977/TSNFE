import argparse
import subprocess
from datetime import datetime, timedelta
import sys

def main():
    parser = argparse.ArgumentParser(description="Batch fetch historical analyst videos by generating daily reports for a date range.")
    parser.add_argument('--start-date', type=str, required=True, help="Start date YYYY-MM-DD")
    parser.add_argument('--end-date', type=str, required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--max-videos", type=int, default=50, help="Max videos per day to process")
    parser.add_argument("--max-videos-per-analyst", type=int, default=50, help="Max videos per analyst per day")
    parser.add_argument('--keep-audio', action='store_true', help="Keep audio file after processing (default is to delete them)")
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
            "python", "scripts/daily_analyst_table.py",
            "--target-date", target_date_str,
            "--max-videos", str(args.max_videos),
            "--max-videos-per-analyst", str(args.max_videos_per_analyst)
        ]
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
    print("1. All daily reports have been placed into data/reports/daily/")
    print("2. Run 'python scripts/build_dataset.py --days 5' to generate ML dataset")
    print("3. Run 'python scripts/train_eval_models.py --days 5' to view evaluation metrics")

if __name__ == "__main__":
    main()
