#!/usr/bin/env python3
"""
Demo: CLIP zero-shot disaster image classification.

Two-pass classification:
  Pass 1 → Disaster vs Normal (disaster_score 0.0–1.0)
  Pass 2 → Which DisasterType enum? (fire, flood, earthquake, etc.)

Usage:
    # Single image
    venv/bin/python scripts/demo_clip_inference.py data/kaggle_disaster_images/fire/001.jpg

    # Entire Kaggle dataset folder (picks one image per category)
    venv/bin/python scripts/demo_clip_inference.py data/kaggle_disaster_images/
"""

import os
import sys
import time
from pathlib import Path
from PIL import Image

# ── Load model ──────────────────────────────────────────────────────
print("Loading CLIP model (openai/clip-vit-base-patch32)...")
t0 = time.time()

from app.providers.image_analysis import (
    _get_classifier,
    _classify_image,
    _TYPE_LABELS,
)

_get_classifier()  # force load
print(f"Model loaded in {time.time() - t0:.1f}s\n")

# ── Our DisasterType enums for reference ────────────────────────────
VALID_TYPES = {"fire", "flood", "earthquake", "hurricane", "tornado",
               "tsunami", "drought", "heatwave", "coldwave", "storm", "other"}


def classify_file(path: str) -> None:
    """Run two-pass CLIP classification on a single image file."""
    img = Image.open(path).convert("RGB")

    t0 = time.time()
    disaster_score, detected_type, type_confidence = _classify_image(img)
    elapsed = time.time() - t0

    print(f"  File: {os.path.basename(path)}")
    print(f"  Inference: {elapsed:.2f}s")
    print(f"  Pass 1 — disaster_score:  {disaster_score:.4f}  ({disaster_score*100:.1f}%)")
    print(f"  Pass 2 — detected_type:   {detected_type}")
    print(f"           type_confidence: {type_confidence:.4f}  ({type_confidence*100:.1f}%)")
    print(f"           maps to enum:    DisasterType.{detected_type.upper()}")

    # Show confidence blend example (70% engine + 30% CLIP → effective 50/20/30 three-way)
    engine = 0.80
    blended = 0.70 * engine + 0.30 * disaster_score
    print(f"  Blend example: engine=0.80 → final={blended:.4f} (50% rules + 20% XGB + 30% CLIP)")
    print()


def classify_folder(folder: str) -> None:
    """Pick the first image from each subfolder and classify it."""
    folder_path = Path(folder)
    subfolders = sorted([d for d in folder_path.iterdir() if d.is_dir()])

    if not subfolders:
        # No subfolders — just find images directly
        images = sorted(folder_path.glob("*.jpg")) + sorted(folder_path.glob("*.png"))
        if not images:
            print(f"No images found in {folder}")
            return
        for img_path in images[:5]:  # first 5
            classify_file(str(img_path))
        return

    print(f"Found {len(subfolders)} categories: {[d.name for d in subfolders]}\n")
    print("=" * 60)

    for subfolder in subfolders:
        images = sorted(subfolder.glob("*.jpg")) + sorted(subfolder.glob("*.png")) + sorted(subfolder.glob("*.jpeg"))
        if not images:
            print(f"\n[{subfolder.name}] — no images found, skipping")
            continue

        print(f"\n[Category: {subfolder.name}]")
        # Classify first image in this category
        classify_file(str(images[0]))


# ── Main ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  venv/bin/python scripts/demo_clip_inference.py <image_or_folder>")
        print()
        print("Examples:")
        print("  venv/bin/python scripts/demo_clip_inference.py data/kaggle_disaster_images/")
        print("  venv/bin/python scripts/demo_clip_inference.py path/to/fire_photo.jpg")
        sys.exit(1)

    target = sys.argv[1]

    if os.path.isdir(target):
        classify_folder(target)
    elif os.path.isfile(target):
        classify_file(target)
    else:
        print(f"Not found: {target}")
        sys.exit(1)
