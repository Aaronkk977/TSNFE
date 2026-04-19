#!/bin/bash
# 啟動 Qwen 作為背景 OpenAI 相容伺服器
# 依據 35B 模型大小約 70GB, 我們分配 4 張顯示卡來進行 Tensor Parallelism
# 確保已經切換環境：conda activate tw-analyst

python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen3.6-35B-A3B \
    --tensor-parallel-size 4 \
    --host 0.0.0.0 \
    --port 8000 \
    --trust-remote-code \
    --max-model-len 4096
