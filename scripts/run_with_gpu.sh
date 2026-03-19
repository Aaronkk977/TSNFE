#!/bin/bash
# GPU-enabled transcription wrapper
# Sets CUDA environment to use CUDA 13 libraries

# Add CUDA 13 to library path (trick faster-whisper to use CUDA 13 as CUDA 12)
export LD_LIBRARY_PATH=/opt/cuda/lib64:$LD_LIBRARY_PATH

# Set these to allow CUDA 13 to be recognized
export CUDA_HOME=/opt/cuda
export CUDA_PATH=/opt/cuda

echo "=== CUDA Environment ==="
echo "CUDA_HOME: $CUDA_HOME"
echo "LD_LIBRARY_PATH: $LD_LIBRARY_PATH"
echo ""

# Try to force load CUDA 13 libraries
export CTRANSLATE2_USE_CUDA13=1

# Run the Python script with GPU settings
python "$@"
