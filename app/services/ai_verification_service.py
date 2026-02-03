"""
STUB FILE - To be implemented by Developer 2

AI-based severity verification service interface.
"""

from typing import List

class AIVerificationService:
    """
    Service for AI-powered disaster severity verification.

    IMPLEMENTATION REQUIRED BY DEVELOPER 2
    """

    def verify_severity(
        self,
        disaster_id: int,
        images: List[str],
        description: str
    ) -> str:
        """
        Verify disaster severity using AI analysis.

        Args:
            disaster_id: ID of disaster to verify
            images: List of image URLs to analyze
            description: Text description to analyze

        Returns:
            Verified severity level (low/medium/high/critical)

        TODO (Developer 2):
        - Implement OpenAI Vision API integration
        - Analyze images for severity indicators:
          * Flood depth, fire size, accident severity, medical urgency
        - Process text description with NLP
        - Compare with user-reported severity
        - Return severity recommendation
        - Log verification results to audit log
        - Consider updating disaster record if discrepancy > 1 level

        Example Implementation:
            import openai

            # Analyze images
            vision_response = openai.ChatCompletion.create(
                model="gpt-4-vision-preview",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Assess disaster severity..."},
                        {"type": "image_url", "image_url": images[0]}
                    ]
                }]
            )

            # Extract severity from response
            return severity_level
        """
        raise NotImplementedError("To be implemented by Developer 2")
