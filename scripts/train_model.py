"""
XGBoost severity classifier training script (v2).

Usage:
    python scripts/train_model.py

Prerequisites:
    python scripts/generate_training_data.py   (produces data/synthetic.csv)
    python scripts/fetch_gdacs_data.py         (produces data/gdacs_events.csv, optional)

Output:
    models/severity_classifier_v2.joblib

The artifact contains:
    {"model": XGBClassifier, "label_encoder": LabelEncoder,
     "feature_names": FEATURE_NAMES, "version": "v2"}

Key metric to watch: CRITICAL class recall — target >= 0.70.

Training pipeline:
    1. Load synthetic.csv (always required) + gdacs_events.csv (if present)
    2. Stratified 80/20 hold-out split
    3. SMOTE on training fold only to balance CRITICAL class
    4. Stratified 5-fold cross-validation on resampled training set
    5. Final model trained on full resampled training set
    6. Evaluate on held-out test set: classification report, confusion matrix, MCC
    7. Save artifact as severity_classifier_v2.joblib
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import joblib
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    matthews_corrcoef,
    recall_score,
)
from xgboost import XGBClassifier

try:
    from imblearn.over_sampling import SMOTE
except ImportError:
    print("ERROR: imbalanced-learn not installed.")
    print("Run:  pip install imbalanced-learn")
    sys.exit(1)

from app.services.evaluation.features import FEATURE_NAMES

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SYNTHETIC_PATH = PROJECT_ROOT / "data" / "synthetic.csv"
GDACS_PATH = PROJECT_ROOT / "data" / "gdacs_events.csv"
MODEL_PATH = PROJECT_ROOT / "models" / "severity_classifier_v2.joblib"

ALL_CLASSES = np.array(["CRITICAL", "HIGH", "LOW", "MEDIUM"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_data() -> pd.DataFrame:
    """Load synthetic data (required) and GDACS data (optional), concatenate."""
    if not SYNTHETIC_PATH.exists():
        print(f"ERROR: {SYNTHETIC_PATH} not found.")
        print("Run:  python scripts/generate_training_data.py")
        sys.exit(1)

    print(f"Loading synthetic data from {SYNTHETIC_PATH} …")
    df = pd.read_csv(SYNTHETIC_PATH)
    print(f"  {len(df)} rows loaded")

    if GDACS_PATH.exists():
        print(f"Loading GDACS data from {GDACS_PATH} …")
        gdacs_df = pd.read_csv(GDACS_PATH)
        print(f"  {len(gdacs_df)} rows loaded")
        df = pd.concat([df, gdacs_df], ignore_index=True)
        print(f"  Combined dataset: {len(df)} rows")
    else:
        print(f"NOTE: {GDACS_PATH} not found — training on synthetic data only.")
        print("      Run scripts/fetch_gdacs_data.py to add real-world events.")

    return df


def print_confusion_matrix(cm: np.ndarray, class_names: list[str]) -> None:
    """Print confusion matrix as an ASCII table."""
    col_width = max(len(n) for n in class_names) + 2
    header = " " * col_width + "".join(n.ljust(col_width) for n in class_names)
    print(header)
    print(" " * col_width + "-" * (col_width * len(class_names)))
    for i, row_name in enumerate(class_names):
        row = row_name.ljust(col_width) + "".join(str(v).ljust(col_width) for v in cm[i])
        print(row)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    df = load_data()

    # Validate feature columns
    missing = [f for f in FEATURE_NAMES if f not in df.columns]
    if missing:
        print(f"ERROR: Missing feature columns in CSV: {missing}")
        sys.exit(1)

    X = df[FEATURE_NAMES].values.astype(float)
    y_raw = df["label"].values

    # Label encoding — always fit on all 4 classes
    le = LabelEncoder()
    le.fit(ALL_CLASSES)
    y = le.transform(y_raw)

    print(f"\nLabel distribution in full dataset ({len(df)} rows):")
    for cls in ALL_CLASSES:
        mask = y_raw == cls
        print(f"  {cls:8s}: {mask.sum():5d}  ({100 * mask.mean():.1f}%)")

    # Stratified 80/20 hold-out split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )
    print(f"\nTrain size: {len(X_train)}, Test size: {len(X_test)}")

    # SMOTE on training fold only
    print("\nApplying SMOTE to training fold …")
    smote = SMOTE(random_state=42)
    X_train_res, y_train_res = smote.fit_resample(X_train, y_train)
    print(f"  Resampled training set: {len(X_train_res)} rows")
    for i, cls in enumerate(le.classes_):
        count = (y_train_res == i).sum()
        print(f"    {cls:8s}: {count}")

    # Stratified 5-fold cross-validation on resampled training set
    print("\nRunning stratified 5-fold cross-validation …")
    clf_cv = XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.1,
        eval_metric="mlogloss",
        random_state=42,
        verbosity=0,
    )
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(
        clf_cv, X_train_res, y_train_res,
        cv=skf, scoring="f1_weighted", n_jobs=-1
    )
    print(f"  CV weighted F1 per fold: {[round(s, 3) for s in cv_scores]}")
    print(f"  Mean: {cv_scores.mean():.3f}  |  Std: {cv_scores.std():.3f}")

    # Final model on full resampled training set
    print("\nTraining final model on full resampled training set …")
    clf = XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.1,
        eval_metric="mlogloss",
        random_state=42,
        verbosity=0,
    )
    clf.fit(X_train_res, y_train_res)
    print("  Training complete.")

    # Evaluate on held-out test set
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

    # Confusion matrix
    print("Confusion Matrix (rows=actual, cols=predicted):")
    cm = confusion_matrix(y_test, y_pred)
    print_confusion_matrix(cm, list(le.classes_))
    print()

    # Matthews Correlation Coefficient
    mcc = matthews_corrcoef(y_test, y_pred)
    print(f"Matthews Correlation Coefficient (MCC): {mcc:.3f}")
    print("  (MCC=1.0 is perfect, 0.0 is random, -1.0 is inverse)")

    # CRITICAL class recall
    critical_idx = list(le.classes_).index("CRITICAL")
    critical_recall = recall_score(
        y_test, y_pred, labels=[critical_idx], average="micro"
    )
    target_met = "[PASS]" if critical_recall >= 0.70 else "[FAIL] (target: >= 0.70)"
    print(f"\nCRITICAL class recall: {critical_recall:.3f}  {target_met}")
    print("=" * 60)

    # Save artifact
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "model": clf,
        "label_encoder": le,
        "feature_names": FEATURE_NAMES,
        "version": "v2",
    }
    joblib.dump(artifact, MODEL_PATH)
    print(f"\nArtifact saved to {MODEL_PATH}")


if __name__ == "__main__":
    main()
