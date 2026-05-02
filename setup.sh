#!/bin/bash

# Reddit ZST to Parquet: One-Command Setup Script for macOS
set -e

echo "🚀 Starting Reddit ZST to Parquet Setup..."

# 1. Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo "❌ Homebrew not found. Please install it from https://brew.sh/ and run this script again."
    exit 1
fi

# 2. Install System Dependencies
echo "📦 Installing system dependencies via Homebrew..."
brew install uv zstd duckdb

# 3. Synchronize Python Environment
echo "🐍 Setting up Python environment with uv..."
uv sync

# 4. Initialize Local Directories and Dependencies
echo "📂 Preparing local temporary directories and dependencies..."
mkdir -p ~/reddit_parquet_temp
mkdir -p conversion_temp
mkdir -p deps

if [ ! -d "deps/arctic_shift" ]; then
    echo "📥 Cloning arctic_shift dependency (schemas/scripts)..."
    # Note: Using local path as default, but in a real repo this would be a GitHub URL
    git clone https://github.com/arctic-shift/arctic_shift.git deps/arctic_shift || \
    git clone ~/src/arctic_shift deps/arctic_shift
fi

# 5. Connectivity Check
echo "🔍 Checking NAS connectivity..."
# Try to load the IP from core/config.py
NAS_IP=$(grep "REMOTE_HOST =" core/config.py | cut -d'"' -f2)
if [ -n "$NAS_IP" ] && ping -c 1 "$NAS_IP" &> /dev/null; then
    echo "✅ NAS ($NAS_IP) is reachable."
else
    echo "⚠️  Note: NAS connectivity check skipped or NAS not reachable. Configure your IP in config.local.toml."
fi

echo "===================================================="
echo "✅ SETUP COMPLETE!"
echo "===================================================="
echo "To start the conversion process, run:"
echo "uv run main.py run"
echo ""
echo "To view a fleet progress report:"
echo "uv run main.py report"
echo "===================================================="
