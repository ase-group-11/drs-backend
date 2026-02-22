"""
XGBoost severity classifier training script.

Usage:
    python scripts/train_model.py

Prerequisites:
    python scripts/generate_training_data.py   (produces data/synthetic.csv)

Output:
    models/severity_classifier_v1.joblib

The artifact contains:
    {"model": XGBClassifier, "label_encoder": LabelEncoder,
     "feature_names": FEATURE_NAMES, "version": "v1"}

Key metric to watch: CRITICAL class recall — target ≥ 0.70.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import numpy as np
import joblib
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import classification_report
from xgboost import XGBClassifier

from app.services.evaluation.features import FEATURE_NAMES

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_PATH = PROJECT_ROOT / "data" / "synthetic.csv"
MODEL_PATH = PROJECT_ROOT / "models" / "severity_classifier_v1.joblib"

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if not DATA_PATH.exists():
        print(f"ERROR: {DATA_PATH} not found.")
        print("Run:  python scripts/generate_training_data.py")
        sys.exit(1)

    print(f"Loading data from {DATA_PATH} …")
    df = pd.read_csv(DATA_PATH)
    print(f"  Loaded {len(df)} rows, {len(df.columns)} columns")

    # Feature matrix and label vector
    missing = [f for f in FEATURE_NAMES if f not in df.columns]
    if missing:
        print(f"ERROR: Missing feature columns in CSV: {missing}")
        sys.exit(1)

    X = df[FEATURE_NAMES].values.astype(float)
    y_raw = df["label"].values

    # Label encoding — always fit on all 4 classes
    le = LabelEncoder()
    all_classes = np.array(["CRITICAL", "HIGH", "LOW", "MEDIUM"])
    le.fit(all_classes)
    y = le.transform(y_raw)

    print(f"\nLabel distribution in full dataset:")
    for cls in all_classes:
        mask = y_raw == cls
        print(f"  {cls:8s}: {mask.sum():5d}  ({100*mask.mean():.1f}%)")

    # Stratified 80/20 split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )
    print(f"\nTrain size: {len(X_train)}, Test size: {len(X_test)}")

    # XGBoost classifier
    print("\nTraining XGBoost classifier …")
    clf = XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.1,
        eval_metric="mlogloss",
        random_state=42,
        verbosity=0,
    )
    clf.fit(X_train, y_train)
    print("  Training complete.")

    # Evaluation
    y_pred = clf.predict(X_test)
    print("\n" + "=" * 60)
    print("Classification Report (test set):")
    print("=" * 60)
    report = classification_report(
        y_test,
        y_pred,
        target_names=le.classes_,
        digits=3,
    )
    print(report)

    # Highlight CRITICAL recall
    from sklearn.metrics import recall_score
    critical_idx = list(le.classes_).index("CRITICAL")
    critical_recall = recall_score(y_test, y_pred, labels=[critical_idx], average="micro")
    target_met = "✓" if critical_recall >= 0.70 else "✗  (target: ≥ 0.70)"
    print(f"CRITICAL class recall: {critical_recall:.3f}  {target_met}")
    print("=" * 60)

    # Save artifact
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "model": clf,
        "label_encoder": le,
        "feature_names": FEATURE_NAMES,
        "version": "v1",
    }
    joblib.dump(artifact, MODEL_PATH)
    print(f"\nArtifact saved to {MODEL_PATH}")


if __name__ == "__main__":
    main()
