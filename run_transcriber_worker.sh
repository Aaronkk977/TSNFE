#!/bin/bash
source local/conda_commands.sh
source /data1/yclin/miniconda3/bin/activate tw-analyst

export PIPELINE_OUTPUT_SUBFOLDER=history
export CUDA_VISIBLE_DEVICES=5
RUN_TAG=$(date +%Y%m%d_%H%M%S)

echo "========== [Worker 2] Transcriber Started =========="
while true; do
    echo "[Transcriber] ================================"
    echo "[Transcriber] Processing from central registry..."
    
    # 1. Run the transcriber
    python scripts/03_generate_transcripts.py \
        --text-source auto \
        --transcription-provider whisper \
        --limit 10 \
        --run-tag "$RUN_TAG"

    echo "[Transcriber] Batch finished. Waiting..."
    sleep 30
done
