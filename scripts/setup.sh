#!/bin/bash

# Setup script for Taiwan Analyst Signal Pipeline
# Installs dependencies and configures the environment

set -e

echo "=========================================="
echo "Taiwan Analyst Signal Pipeline - Setup"
echo "=========================================="

# Check Python version
python_version=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
echo "✓ Python version: $python_version"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
fi

# Activate virtual environment
source venv/bin/activate
echo "✓ Virtual environment activated"

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip setuptools wheel
echo "✓ pip upgraded"

# Install dependencies
echo "Installing dependencies..."
pip install -e .
echo "✓ Dependencies installed"

# Copy .env.example to .env if it doesn't exist
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✓ Created .env file (please edit with your API keys)"
else
    echo "✓ .env file already exists"
fi

# Create necessary directories
mkdir -p data/{raw,transcripts,signals,checkpoints,stock_codes}
mkdir -p logs
echo "✓ Data directories created"

# Run system test
echo ""
echo "Running system tests..."
python3 scripts/test_system.py

echo ""
echo "=========================================="
echo "✓ Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Edit .env file and add your API keys:"
echo "     - YOUTUBE_API_KEY"
echo "     - OPENAI_API_KEY"
echo "  2. Test the pipeline:"
echo "     python3 scripts/test_system.py"
echo "  3. Process a video:"
echo "     python3 scripts/process_video.py <YouTube_URL>"
echo ""
