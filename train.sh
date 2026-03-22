#!/bin/bash
# =============================================================================
# train.sh — Fetch data + train the ML model
# Run this once before run_evaluation.sh, or whenever you want to retrain.
#
# Usage:
#   ./train.sh           — full pipeline (GDACS fetch + synthetic + train)
#   ./train.sh --skip-gdacs  — skip GDACS fetch (faster, synthetic only)
#   ./train.sh --force   — retrain even if the model artifact already exists
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="$SCRIPT_DIR/venv/bin/python"
MODEL="$SCRIPT_DIR/models/severity_classifier_v2.joblib"
SKIP_GDACS=false
FORCE=false

for arg in "$@"; do
    case $arg in
        --skip-gdacs) SKIP_GDACS=true ;;
        --force)      FORCE=true ;;
    esac
done

# ── Venv check ────────────────────────────────────────────────────────────────
if [ ! -f "$PYTHON" ]; then
    echo "ERROR: venv not found at $SCRIPT_DIR/venv"
    echo "Run:  python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

# ── Already trained? ──────────────────────────────────────────────────────────
if [ -f "$MODEL" ] && [ "$FORCE" = false ]; then
    echo "Model already exists: $MODEL"
    echo "Run with --force to retrain."
    exit 0
fi

mkdir -p models data

# ── 1. Fetch GDACS real-world events ─────────────────────────────────────────
if [ "$SKIP_GDACS" = false ]; then
    echo "[1/3] Fetching GDACS real-world events..."
    $PYTHON scripts/fetch_gdacs_data.py
else
    echo "[1/3] Skipping GDACS fetch (--skip-gdacs)"
fi

# ── 2. Generate synthetic training data ──────────────────────────────────────
echo "[2/3] Generating synthetic training data..."
$PYTHON scripts/generate_training_data.py

# ── 3. Train model ────────────────────────────────────────────────────────────
echo "[3/3] Training model..."
$PYTHON scripts/train_model.py

echo ""
echo "Done. Artifact: $MODEL"
echo "Run ./run_evaluation.sh to test it."
