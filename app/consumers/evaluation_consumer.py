# File: app/consumers/evaluation_consumer.py
"""
Evaluation Consumer - Listens to evaluation_queue.

Processes disaster.reported events:
  1. Reads disaster data from the message
  2. Calculates severity score based on factors
  3. Determines impact radius
  4. Logs evaluation results
  5. (Future) Updates DB and publishes disaster.evaluated

Run:
    python -m app.consumers.evaluation_consumer
"""

import os
import sys
import logging

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

from app.consumers.base_consumer import BaseConsumer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("evaluation_consumer")


# Severity scoring weights
SEVERITY_SCORES = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}

# Impact radius by disaster type (meters)
IMPACT_RADIUS = {
    "FLOOD": 1000,
    "FIRE": 500,
    "EARTHQUAKE": 2000,
    "STORM": 1500,
    "GAS_LEAK": 300,
    "BUILDING_COLLAPSE": 400,
    "TRAFFIC_ACCIDENT": 200,
    "OTHER": 500,
}


class EvaluationConsumer(BaseConsumer):
    """Processes disaster reports and evaluates severity."""

    def __init__(self):
        super().__init__(queue_name="evaluation_queue")

    def process_message(self, data: dict):
        """
        Evaluate disaster severity and impact.

        Scoring factors:
          - Base severity (LOW=1, MEDIUM=2, HIGH=3, CRITICAL=4)
          - People affected (0-10: +0, 11-50: +1, 51-200: +2, 200+: +3)
          - Multiple casualties: +2
          - Structural damage: +1
          - Road blocked: +1
        """
        event_type = data.get("event_type", "")

        if event_type != "disaster.reported":
            print(f"  Skipping event: {event_type} (not disaster.reported)")
            return

        disaster_id = data.get("disaster_id")
        disaster_type = data.get("type", "OTHER")
        severity = data.get("severity", "LOW")
        people_affected = data.get("people_affected", 0)
        multiple_casualties = data.get("multiple_casualties", False)
        structural_damage = data.get("structural_damage", False)
        road_blocked = data.get("road_blocked", False)
        location = data.get("location", {})

        # ── Calculate severity score ──
        score = SEVERITY_SCORES.get(severity, 1)

        if people_affected > 200:
            score += 3
        elif people_affected > 50:
            score += 2
        elif people_affected > 10:
            score += 1

        if multiple_casualties:
            score += 2
        if structural_damage:
            score += 1
        if road_blocked:
            score += 1

        # Determine final severity level
        if score >= 8:
            final_severity = "CRITICAL"
        elif score >= 5:
            final_severity = "HIGH"
        elif score >= 3:
            final_severity = "MEDIUM"
        else:
            final_severity = "LOW"

        # ── Determine impact radius ──
        impact_radius = IMPACT_RADIUS.get(disaster_type, 500)

        # Scale radius by severity
        if final_severity == "CRITICAL":
            impact_radius = int(impact_radius * 2.0)
        elif final_severity == "HIGH":
            impact_radius = int(impact_radius * 1.5)

        # ── Determine required services ──
        required_services = []
        if disaster_type in ["FIRE", "GAS_LEAK"]:
            required_services.append("FIRE")
        if multiple_casualties or people_affected > 10:
            required_services.append("MEDICAL")
        if road_blocked or disaster_type in ["TRAFFIC_ACCIDENT"]:
            required_services.append("POLICE")
        if disaster_type in ["EARTHQUAKE", "BUILDING_COLLAPSE", "FLOOD"]:
            required_services.append("FIRE")
            required_services.append("MEDICAL")
            required_services.append("POLICE")

        required_services = list(set(required_services))  # dedupe

        # ── Print evaluation results ──
        print(f"\n  {'─'*50}")
        print(f"  📊 EVALUATION RESULTS")
        print(f"  {'─'*50}")
        print(f"  Disaster ID:      {disaster_id}")
        print(f"  Type:             {disaster_type}")
        print(f"  Reported Severity:{severity}")
        print(f"  Severity Score:   {score}/12")
        print(f"  Final Severity:   {final_severity}")
        print(f"  Impact Radius:    {impact_radius}m")
        print(f"  People Affected:  {people_affected}")
        print(f"  Casualties:       {'Yes' if multiple_casualties else 'No'}")
        print(f"  Structural Dmg:   {'Yes' if structural_damage else 'No'}")
        print(f"  Road Blocked:     {'Yes' if road_blocked else 'No'}")
        print(f"  Required Services:{', '.join(required_services) if required_services else 'None'}")
        print(f"  Location:         {location.get('lat', 'N/A')}, {location.get('lon', 'N/A')}")
        print(f"  {'─'*50}")

        # ── Future: Update DB + publish disaster.evaluated ──
        # TODO: Update disasters table with evaluation results
        # TODO: Publish disaster.evaluated to RabbitMQ
        #   publish_disaster_evaluated({
        #       "disaster_id": disaster_id,
        #       "severity": final_severity,
        #       "impact_radius": impact_radius,
        #       "required_services": required_services,
        #   })

        logger.info(
            f"Evaluated disaster {disaster_id}: "
            f"score={score}, severity={final_severity}, "
            f"radius={impact_radius}m, services={required_services}"
        )


if __name__ == "__main__":
    consumer = EvaluationConsumer()
    consumer.start()