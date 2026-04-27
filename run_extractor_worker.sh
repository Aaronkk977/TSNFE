#!/bin/bash
source local/conda_commands.sh
source /data1/yclin/miniconda3/bin/activate tw-analyst

export PIPELINE_OUTPUT_SUBFOLDER=history
RUN_TAG=$(date +%Y%m%d_%H%M%S)

echo "========== [Worker 3] Extractor Started =========="
while true; do
    echo "[Extractor] ================================"
    echo "[Extractor] Processing from transcribed cache..."
    
    # Run the extractor
    export OPENAI_API_KEY="sk-hello-qwen-xyz"
    export OPENAI_API_BASE="http://localhost:8000/v1"
    
    python scripts/04_extract_signals.py \
        --limit 10 \
        --llm-provider qwen \
        --llm-model Qwen/Qwen2.5-14B-Instruct-AWQ

    echo "[Extractor] Batch finished. Waiting..."
    sleep 30
done
