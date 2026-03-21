#!/bin/bash
# =============================================================================
# setup.sh — One-shot setup for the DRS evaluation pipeline
#
# Clones-friendly: run this after pulling the repo and it does everything.
#
# Usage:
#   ./setup.sh               — full setup (venv, deps, data, train, smoke test)
#   ./setup.sh --skip-gdacs  — skip GDACS network fetch (synthetic data only)
#   ./setup.sh --skip-tests  — skip the pytest smoke test at the end
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="python3"
PYTHON="$SCRIPT_DIR/venv/bin/python"
PIP="$SCRIPT_DIR/venv/bin/pip"
PYTEST="$SCRIPT_DIR/venv/bin/pytest"
MODEL="$SCRIPT_DIR/models/severity_classifier_v2.joblib"

SKIP_GDACS=false
SKIP_TESTS=false

for arg in "$@"; do
    case $arg in
        --skip-gdacs) SKIP_GDACS=true ;;
        --skip-tests) SKIP_TESTS=true ;;
    esac
done

echo "=============================================="
echo " DRS Evaluation Pipeline — Setup"
echo "=============================================="
echo ""

# ── 1. Python version check ───────────────────────────────────────────────────
echo "[1/6] Checking Python version..."
PYTHON_VERSION=$($PYTHON_BIN --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]; }; then
    echo "ERROR: Python 3.11+ required (found $PYTHON_VERSION)"
    exit 1
fi
echo "      Python $PYTHON_VERSION OK"

# ── 2. Virtual environment ────────────────────────────────────────────────────
echo "[2/6] Setting up virtual environment..."
if [ ! -f "$PYTHON" ]; then
    $PYTHON_BIN -m venv venv
    echo "      Created venv"
else
    echo "      venv already exists"
fi

# ── 3. Dependencies ───────────────────────────────────────────────────────────
echo "[3/6] Installing dependencies..."
$PIP install -q --upgrade pip

# Install only what the evaluation pipeline needs.
# The full requirements.txt includes asyncpg==0.29.0 which fails to build on
# Python 3.13/arm64 — that's a known issue requiring a manual asyncpg==0.31.0
# override and is only needed when running the full FastAPI app.
$PIP install -q \
    "xgboost>=2.0.0" \
    "scikit-learn>=1.4.0" \
    "numpy>=1.26.0" \
    "pandas>=2.2.0" \
    "joblib>=1.3.0" \
    "imbalanced-learn>=0.12.0" \
    "gdacs-api>=1.0.0" \
    "pydantic>=2.5.3" \
    "pydantic-settings>=2.1.0" \
    "aiohttp>=3.9.0" \
    "fastapi>=0.109.0" \
    "python-dotenv>=1.0.0"

echo "      All dependencies installed"

# macOS: XGBoost requires OpenMP
if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! brew list libomp &>/dev/null 2>&1; then
        echo "      Installing libomp (required for XGBoost on macOS)..."
        brew install libomp
    else
        echo "      libomp already installed"
    fi
fi

# ── 4. Training data ──────────────────────────────────────────────────────────
echo "[4/6] Preparing training data..."
mkdir -p models data

if [ "$SKIP_GDACS" = false ]; then
    echo "      Fetching GDACS real-world events (--skip-gdacs to skip)..."
    $PYTHON scripts/fetch_gdacs_data.py || echo "      GDACS fetch failed or timed out — continuing with synthetic data only"
else
    echo "      Skipping GDACS fetch"
fi

echo "      Generating synthetic training data..."
$PYTHON scripts/generate_training_data.py

# ── 5. Train model ────────────────────────────────────────────────────────────
echo "[5/6] Training model..."
$PYTHON scripts/train_model.py

# ── 6. Smoke test ─────────────────────────────────────────────────────────────
if [ "$SKIP_TESTS" = false ]; then
    echo "[6/6] Running evaluation unit tests..."
    $PYTEST app/tests/unit/test_rules_engine.py \
            app/tests/unit/test_features.py \
            app/tests/unit/test_xgboost_strategy.py \
            app/tests/unit/test_openweathermap_provider.py \
            -q --tb=short 2>&1
else
    echo "[6/6] Skipping tests (--skip-tests)"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo " Setup complete"
echo "=============================================="
echo ""
echo " Model : $MODEL"
echo ""
echo " Next steps:"
echo "   Edit the values at the top of run_evaluation.sh, then:"
echo "   ./run_evaluation.sh"
echo ""
echo " To add a real weather API key:"
echo "   Add OPENWEATHER_API_KEY=<your_key> to your .env file"
echo "   Get a free key at https://openweathermap.org/api"
echo ""
