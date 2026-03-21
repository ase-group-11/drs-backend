"""
Image analysis provider using OpenAI CLIP (zero-shot classification).

Two-pass classification:
  Pass 1 — Disaster vs. Normal scene → disaster_score (0.0–1.0)
  Pass 2 — Which disaster type?     → mapped to DisasterType enum values

Model: openai/clip-vit-base-patch32 (loaded once, reused across requests).
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from PIL import Image

logger = logging.getLogger(__name__)

# ── Pass 1: Disaster vs Normal ──────────────────────────────────────
_DISASTER_LABEL = "a photo of a real disaster or emergency such as fire, flood, earthquake, storm, or structural collapse"
_NORMAL_LABEL = "a photo of a normal everyday scene with no emergency or disaster"
_BINARY_LABELS = [_DISASTER_LABEL, _NORMAL_LABEL]

# ── Pass 2: Disaster type → DisasterType enum mapping ───────────────
# Each key is the CLIP text prompt; each value is the DisasterType.value
# that the evaluation service already uses (lowercase enum values).
_TYPE_LABELS: Dict[str, str] = {
    "a photo of a fire, flames, or burning building": "fire",
    "a photo of a flood, rising water, or submerged area": "flood",
    "a photo of earthquake damage, collapsed or cracked buildings": "earthquake",
    "a photo of a hurricane or cyclone with strong winds and rain": "hurricane",
    "a photo of a tornado or funnel cloud": "tornado",
    "a photo of a tsunami or massive ocean wave hitting land": "tsunami",
    "a photo of a severe storm with lightning, hail, or heavy rain": "storm",
    "a photo of extreme heat, drought, or dry cracked earth": "drought",
    "a photo of a heatwave with scorching sun and heat haze": "heatwave",
    "a photo of extreme cold, blizzard, ice, or freezing conditions": "coldwave",
}
_TYPE_PROMPT_LIST = list(_TYPE_LABELS.keys())

# Lazy-loaded singleton — avoids importing torch at module level
_classifier = None


def _get_classifier():
    """Load the CLIP zero-shot image classifier once (lazy singleton)."""
    global _classifier
    if _classifier is None:
        from transformers import pipeline

        _classifier = pipeline(
            "zero-shot-image-classification",
            model="openai/clip-vit-base-patch32",
        )
        logger.info("CLIP image classifier loaded (openai/clip-vit-base-patch32)")
    return _classifier


async def _download_image(url: str, timeout: float = 10.0) -> Optional[Image.Image]:
    """Download an image from a URL and return a PIL Image."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    logger.warning("Image download failed (HTTP %d): %s", resp.status, url)
                    return None
                data = await resp.read()
                return Image.open(BytesIO(data)).convert("RGB")
    except Exception:
        logger.warning("Image download error: %s", url, exc_info=True)
        return None


def _classify_image(image: Image.Image) -> Tuple[float, str, float]:
    """
    Run two-pass CLIP classification on a single image.

    Returns:
        (disaster_score, detected_type, type_confidence)
        - disaster_score: probability of being a disaster (0.0–1.0)
        - detected_type: DisasterType enum value (e.g. "fire", "flood")
        - type_confidence: confidence for the detected type (0.0–1.0)
    """
    classifier = _get_classifier()

    # Pass 1: disaster vs normal
    binary_results = classifier(image, candidate_labels=_BINARY_LABELS)
    disaster_score = 0.0
    for r in binary_results:
        if r["label"] == _DISASTER_LABEL:
            disaster_score = float(r["score"])
            break

    # Pass 2: which disaster type (only if it looks like a disaster)
    detected_type = "other"
    type_confidence = 0.0

    if disaster_score >= 0.40:
        type_results = classifier(image, candidate_labels=_TYPE_PROMPT_LIST)
        if type_results:
            top = type_results[0]  # highest scoring label
            detected_type = _TYPE_LABELS.get(top["label"], "other")
            type_confidence = float(top["score"])

    return disaster_score, detected_type, type_confidence


class CLIPImageAnalysisProvider:
    """
    Analyses disaster report photos using CLIP zero-shot classification.

    Returns a dict with:
        - disaster_score: float (0.0–1.0) — max disaster confidence across all photos
        - detected_type: str — DisasterType enum value (e.g. "fire", "flood")
        - type_confidence: float — confidence for the detected type
        - per_image: list of per-image detail dicts
        - analysed_count: how many images were successfully analysed
        - source: "clip_vit_base_patch32"
    """

    async def analyse_photos(
        self, image_urls: List[str]
    ) -> Dict[str, Any]:
        """
        Download and classify photos in parallel.

        CPU-bound CLIP inference is run in a thread pool to avoid
        blocking the async event loop.
        """
        if not image_urls:
            return self._empty_result()

        # Download all images in parallel
        images = await asyncio.gather(
            *[_download_image(url) for url in image_urls]
        )

        # Filter out failed downloads
        valid_images = [img for img in images if img is not None]
        if not valid_images:
            logger.warning("No images could be downloaded for analysis")
            return self._empty_result()

        # Run CLIP inference in thread pool (CPU-bound)
        loop = asyncio.get_running_loop()
        results = await asyncio.gather(
            *[loop.run_in_executor(None, _classify_image, img) for img in valid_images]
        )

        # Find the image with the highest disaster score
        best_idx = max(range(len(results)), key=lambda i: results[i][0])
        best_disaster_score, best_type, best_type_conf = results[best_idx]

        per_image = [
            {
                "disaster_score": round(score, 4),
                "detected_type": dtype,
                "type_confidence": round(tconf, 4),
            }
            for score, dtype, tconf in results
        ]

        return {
            "disaster_score": round(best_disaster_score, 4),
            "detected_type": best_type,
            "type_confidence": round(best_type_conf, 4),
            "per_image": per_image,
            "analysed_count": len(valid_images),
            "source": "clip_vit_base_patch32",
        }

    @staticmethod
    def _empty_result() -> Dict[str, Any]:
        return {
            "disaster_score": 0.0,
            "detected_type": "other",
            "type_confidence": 0.0,
            "per_image": [],
            "analysed_count": 0,
            "source": "clip_vit_base_patch32",
        }
