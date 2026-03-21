"""
Unit tests for image analysis provider and confidence blending.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.providers.image_analysis import CLIPImageAnalysisProvider
from app.services.evaluation.service import _blend_confidence


class TestBlendConfidence:
    """Tests for the confidence blending function."""

    def test_no_image_analysis_returns_engine_score(self):
        assert _blend_confidence(0.82, None) == 0.82

    def test_empty_analysis_returns_engine_score(self):
        ctx = {"disaster_score": 0.0, "analysed_count": 0, "source": "clip_vit_base_patch32"}
        assert _blend_confidence(0.82, ctx) == 0.82

    def test_high_image_score_boosts_confidence(self):
        ctx = {"disaster_score": 0.95, "analysed_count": 1, "source": "clip_vit_base_patch32"}
        result = _blend_confidence(0.80, ctx)
        # 0.70 * 0.80 + 0.30 * 0.95 = 0.56 + 0.285 = 0.845
        assert result == pytest.approx(0.845, abs=0.01)

    def test_low_image_score_reduces_confidence(self):
        ctx = {"disaster_score": 0.10, "analysed_count": 1, "source": "clip_vit_base_patch32"}
        result = _blend_confidence(0.80, ctx)
        # 0.70 * 0.80 + 0.30 * 0.10 = 0.56 + 0.03 = 0.59
        assert result == pytest.approx(0.59, abs=0.01)

    def test_result_clamped_to_max_1(self):
        ctx = {"disaster_score": 1.0, "analysed_count": 1, "source": "clip_vit_base_patch32"}
        assert _blend_confidence(1.0, ctx) <= 1.0

    def test_result_clamped_to_min_0(self):
        ctx = {"disaster_score": 0.0, "analysed_count": 1, "source": "clip_vit_base_patch32"}
        assert _blend_confidence(0.0, ctx) >= 0.0


class TestCLIPImageAnalysisProvider:
    """Tests for the CLIP provider with mocked model."""

    @pytest.mark.asyncio
    async def test_empty_urls_returns_empty_result(self):
        provider = CLIPImageAnalysisProvider()
        result = await provider.analyse_photos([])
        assert result["analysed_count"] == 0
        assert result["disaster_score"] == 0.0

    @pytest.mark.asyncio
    async def test_failed_downloads_returns_empty(self):
        provider = CLIPImageAnalysisProvider()
        mock_download = AsyncMock(return_value=None)
        with patch("app.providers.image_analysis._download_image", mock_download):
            result = await provider.analyse_photos(["http://fake.example.com/img.jpg"])
        assert result["analysed_count"] == 0

    @pytest.mark.asyncio
    async def test_successful_analysis_returns_scores(self):
        fake_image = MagicMock()
        provider = CLIPImageAnalysisProvider()

        mock_download = AsyncMock(return_value=fake_image)
        with patch("app.providers.image_analysis._download_image", mock_download), \
             patch("app.providers.image_analysis._classify_image", return_value=(0.87, "fire", 0.72)):
            result = await provider.analyse_photos(["http://example.com/fire.jpg"])

        assert result["analysed_count"] == 1
        assert result["disaster_score"] == 0.87
        assert result["detected_type"] == "fire"
        assert result["type_confidence"] == 0.72
        assert result["source"] == "clip_vit_base_patch32"

    @pytest.mark.asyncio
    async def test_max_score_across_multiple_images(self):
        fake_image = MagicMock()
        provider = CLIPImageAnalysisProvider()

        mock_download = AsyncMock(return_value=fake_image)
        with patch("app.providers.image_analysis._download_image", mock_download), \
             patch("app.providers.image_analysis._classify_image", side_effect=[
                 (0.3, "storm", 0.4),
                 (0.9, "fire", 0.8),
                 (0.5, "flood", 0.6),
             ]):
            result = await provider.analyse_photos([
                "http://example.com/1.jpg",
                "http://example.com/2.jpg",
                "http://example.com/3.jpg",
            ])

        assert result["disaster_score"] == 0.9
        assert result["detected_type"] == "fire"
        assert result["analysed_count"] == 3
