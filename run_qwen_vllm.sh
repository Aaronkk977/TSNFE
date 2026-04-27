#!/bin/bash
source local/conda_commands.sh
# 確保使用當前的 conda 環境，並請確保已在該環境安裝 vllm (pip install vllm)
# 如果你有獨立的 vllm_env，請替換為 source /data1/yclin/miniconda3/bin/activate vllm_env
source /data1/yclin/miniconda3/bin/activate tw-analyst

CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-14B-Instruct-AWQ \
  --quantization awq \
  --gpu-memory-utilization 0.80 \
  --max-model-len 8192 \
  --port 8000 \
  --api-key "sk-hello-qwen-xyz" \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder
