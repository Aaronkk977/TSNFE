#!/bin/bash
source local/conda_commands.sh
source /tmp2/b12902115/miniconda3/bin/activate vllm_env
CUDA_VISIBLE_DEVICES=2 python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-14B-Instruct-AWQ \
  --quantization awq \
  --gpu-memory-utilization 0.85 \
  --max-model-len 32768 \
  --port 8000 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder
