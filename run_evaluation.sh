#!/bin/bash
# =============================================================================
# Disaster Evaluation — Setup & Run
# Edit the VALUES section below, then run:  ./run_evaluation.sh
# =============================================================================

# =============================================================================
# YOUR VALUES — edit these
# =============================================================================
DISASTER_TYPE="fire"         # flood | fire | earthquake | hurricane | tornado
                             # tsunami | drought | heatwave | coldwave | storm | other
SEVERITY="high"              # low | medium | high | critical
PEOPLE_AFFECTED=80           # number of people affected
DESCRIPTION="Large building fire with casualties"

MULTIPLE_CASUALTIES=true     # true | false
STRUCTURAL_DAMAGE=true       # true | false
ROAD_BLOCKED=true            # true | false
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="$SCRIPT_DIR/venv/bin/python"
PIP="$SCRIPT_DIR/venv/bin/pip"
MODEL="$SCRIPT_DIR/models/severity_classifier_v2.joblib"

# ── 1. Virtual environment ────────────────────────────────────────────────────
if [ ! -f "$PYTHON" ]; then
    echo "[1/4] Creating virtual environment..."
    python3 -m venv venv
else
    echo "[1/4] Virtual environment already exists."
fi

# ── 2. Dependencies ───────────────────────────────────────────────────────────
echo "[2/4] Checking dependencies..."

# Only install what the evaluation pipeline actually needs
MISSING=()
for pkg in xgboost joblib numpy scikit-learn pydantic fastapi; do
    if ! $PIP show "$pkg" &>/dev/null 2>&1; then
        MISSING+=("$pkg")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "      Installing: ${MISSING[*]}"
    $PIP install -q "${MISSING[@]}"
else
    echo "      All dependencies present."
fi

# XGBoost needs OpenMP on macOS
if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! brew list libomp &>/dev/null 2>&1; then
        echo "      Installing libomp (required for XGBoost on macOS)..."
        brew install libomp
    fi
fi

# ── 3. Fetch real data + train model ─────────────────────────────────────────
if [ ! -f "$MODEL" ]; then
    echo "[3/4] Model not found — fetching data and training..."
    mkdir -p models data
    $PYTHON scripts/fetch_gdacs_data.py
    $PYTHON scripts/generate_training_data.py
    $PYTHON scripts/train_model.py
else
    echo "[3/4] Model already trained."
fi

# ── 4. Run evaluation ─────────────────────────────────────────────────────────
echo "[4/4] Running evaluation..."
echo ""

$PYTHON - <<EOF
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, "$SCRIPT_DIR")

from app.db.models.enums import DisasterSeverity
from app.services.evaluation.base import EvaluationContext
from app.services.evaluation.xgboost_strategy import XGBoostStrategy
from datetime import datetime, timezone

async def main():
    strategy = XGBoostStrategy(model_path="$MODEL")
    strategy.load()

    context = EvaluationContext(
        report_id="manual-run",
        disaster_type="$DISASTER_TYPE",
        severity=DisasterSeverity("$SEVERITY"),
        description="$DESCRIPTION",
        people_affected=$PEOPLE_AFFECTED,
        multiple_casualties=$( [ "$MULTIPLE_CASUALTIES" = "true" ] && echo "True" || echo "False" ),
        structural_damage=$( [ "$STRUCTURAL_DAMAGE" = "true" ] && echo "True" || echo "False" ),
        road_blocked=$( [ "$ROAD_BLOCKED" = "true" ] && echo "True" || echo "False" ),
        lat=53.35,
        lon=-6.26,
        hour_of_day=datetime.now(timezone.utc).hour,
        traffic_context=None,
        weather_context=None,
    )

    print("Input")
    print(f"  type         : {context.disaster_type}")
    print(f"  severity     : {context.severity.name}")
    print(f"  people       : {context.people_affected}")
    print(f"  casualties   : {context.multiple_casualties}")
    print(f"  structural   : {context.structural_damage}")
    print(f"  road_blocked : {context.road_blocked}")
    if context.description:
        print(f"  description  : {context.description}")

    result = await strategy.evaluate(context)

    print("")
    print("Output")
    print(json.dumps({
        "severity":             result.severity,
        "confidence":           round(result.confidence, 3),
        "strategy_used":        result.strategy_used,
        "recommended_services": result.recommended_services,
        "trigger_deploy":       result.trigger_deploy,
        "trigger_reroute":      result.trigger_reroute,
        "trigger_evacuation":   result.trigger_evacuation,
        "flag":                 result.flag,
    }, indent=4))

asyncio.run(main())
EOF
