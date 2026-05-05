#!/bin/bash
source local/conda_commands.sh
source /data1/yclin/miniconda3/bin/activate tw-analyst

export PIPELINE_OUTPUT_SUBFOLDER=history
export CUDA_VISIBLE_DEVICES=5
RUN_TAG=$(date +%Y%m%d_%H%M%S)

echo "========== [Worker 2] Transcriber Started =========="
if [ ! -f "local/cookies.txt" ]; then
    echo "[WARN] local/cookies.txt not found; YouTube CC may hit 429 rate limit."
fi
while true; do
    echo "[Transcriber] ================================"
    echo "[Transcriber] Processing from central registry..."
    
    # 1. Run the transcriber
    # transcription.provider 使用 config/config.yaml（勿在此覆寫）
    python scripts/03_generate_transcripts.py \
        --text-source auto \
        --limit 10 \
        --run-tag "$RUN_TAG"

    WAIT_SECONDS=$((20 + RANDOM % 21))
    echo "[Transcriber] Batch finished. Waiting ${WAIT_SECONDS}s..."
    sleep "$WAIT_SECONDS"
done
