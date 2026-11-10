#!/bin/bash
# Setup script for local Ollama Qwen model integration
# Usage:
#   ./scripts/setup_ollama.sh [model_name]
# Example:
#   ./scripts/setup_ollama.sh qwen3.5:2b

set -e

MODEL="${1:-${OLLAMA_MODEL:-qwen3.5:2b}}"
OLLAMA_HOST="${OLLAMA_BASE_URL:-http://localhost:11434}"

echo "=========================================================="
echo "🦙 ChangSupport.AI - Ollama Qwen Integration Setup"
echo "   Target Model: $MODEL"
echo "   Ollama Host:  $OLLAMA_HOST"
echo "=========================================================="

if ! command -v ollama &> /dev/null; then
    echo "❌ Error: 'ollama' CLI is not found in PATH."
    echo "Please install Ollama from https://ollama.com or via Homebrew:"
    echo "   brew install ollama"
    exit 1
fi

echo "Checking if Ollama daemon is running at $OLLAMA_HOST..."
if ! curl -s "$OLLAMA_HOST/api/tags" &> /dev/null; then
    echo "⚠️  Ollama daemon is not responding at $OLLAMA_HOST."
    echo "Starting Ollama in the background (or run 'ollama serve' in another terminal)..."
    ollama serve &
    sleep 3
fi

echo "Pulling model '$MODEL' into Ollama..."
ollama pull "$MODEL"

echo ""
echo "✅ Successfully pulled $MODEL!"
echo ""
echo "To start ChangSupport.AI with this local model, run:"
echo "   CHANGSUPPORT_LLM_PROVIDER=ollama OLLAMA_MODEL=$MODEL ./start.sh"
echo "=========================================================="
