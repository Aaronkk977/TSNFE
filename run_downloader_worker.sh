#!/bin/bash
source local/conda_commands.sh
source /data1/yclin/miniconda3/bin/activate tw-analyst
export PIPELINE_OUTPUT_SUBFOLDER=history
START_DATE=2025-05-01
END_DATE=2025-05-31
RUN_TAG=$(date +%Y%m%d_%H%M%S)

echo "========== [Worker 1] Downloader Started =========="

# Fetch metadata for last 30 days once a day
# python scripts/01_fetch_video_list.py --start-date "$START_DATE" --end-date "$END_DATE" --analysts-file config/analysts.yaml --max-videos 1000

while true; do
  # 2. Download media based on the central registry
  python scripts/02_download_media.py --max-audio-cache 0 --start-date "$START_DATE" --end-date "$END_DATE" --run-tag "$RUN_TAG"
  echo "[Downloader] Sleeping before next batch..."
  sleep 30
done

echo "========== [Worker 1] Downloader Finished =========="
