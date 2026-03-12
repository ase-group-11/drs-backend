"""
locustfile.py — Locust load test for Socket.IO concurrent user simulation.
Section 10 + Section 13.3.

Run with:
  locust -f locustfile.py --headless -u 500 -r 50 --run-time 60s

Simulates 500 users connecting via Socket.IO, receiving reroute alerts,
and verifying that all users get updated routes within the 5-second SLA.
"""
import time
import uuid
import random
from locust import User, task, events, between
import socketio


# ---------------------------------------------------------------------------
# Socket.IO User — simulates a connected browser client
# ---------------------------------------------------------------------------

class SocketIOUser(User):
    """
    Each Locust user represents one vehicle/app connected via Socket.IO.
    Connects to the reroute:{regionId} room and listens for traffic alerts.
    """

    wait_time = between(1, 3)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sio = socketio.SimpleClient()
        self.user_id = f"locust-user-{uuid.uuid4().hex[:8]}"
        self.region_id = random.choice(
            ["region-dublin-m50", "region-dublin-city", "region-dublin-port"]
        )
        self.reroute_received = False
        self.reroute_time = None

    def on_start(self):
        """Called when a Locust user starts — connect to Socket.IO server."""
        try:
            self.sio.connect(
                self.host,
                headers={"X-User-Id": self.user_id},
            )
            # Subscribe to regional room
            self.sio.emit("join_region", {"region_id": self.region_id})
        except Exception as e:
            events.request.fire(
                request_type="socket.io",
                name="connect",
                response_time=0,
                response_length=0,
                exception=e,
            )

    def on_stop(self):
        """Disconnect when the Locust user finishes."""
        try:
            self.sio.disconnect()
        except Exception:
            pass

    @task(3)
    def listen_for_reroute_alert(self):
        """
        Wait for a reroute_alert event (up to 5 seconds — the SLA target).
        Fires a Locust request event recording the wait time.
        """
        start = time.monotonic()
        try:
            event = self.sio.receive(timeout=5.0)
            elapsed_ms = (time.monotonic() - start) * 1000

            if event and event[0] == "reroute_alert":
                self.reroute_received = True
                self.reroute_time = elapsed_ms
                events.request.fire(
                    request_type="socket.io",
                    name="reroute_alert_received",
                    response_time=elapsed_ms,
                    response_length=len(str(event[1])),
                    exception=None,
                )
            else:
                events.request.fire(
                    request_type="socket.io",
                    name="reroute_alert_received",
                    response_time=elapsed_ms,
                    response_length=0,
                    exception=TimeoutError("No reroute_alert received within SLA"),
                )
        except Exception as e:
            events.request.fire(
                request_type="socket.io",
                name="reroute_alert_received",
                response_time=(time.monotonic() - start) * 1000,
                response_length=0,
                exception=e,
            )

    @task(1)
    def receive_updated_recommendation(self):
        """Listen for updated_reroute_recommendation during monitoring cycles."""
        start = time.monotonic()
        try:
            event = self.sio.receive(timeout=3.0)
            elapsed_ms = (time.monotonic() - start) * 1000
            name = "updated_recommendation_received" if event else "no_update_received"
            events.request.fire(
                request_type="socket.io",
                name=name,
                response_time=elapsed_ms,
                response_length=len(str(event)) if event else 0,
                exception=None,
            )
        except Exception as e:
            events.request.fire(
                request_type="socket.io",
                name="updated_recommendation_received",
                response_time=(time.monotonic() - start) * 1000,
                response_length=0,
                exception=e,
            )

    @task(1)
    def listen_for_all_clear(self):
        """Listen for the all_clear event when roads are restored."""
        start = time.monotonic()
        try:
            event = self.sio.receive(timeout=3.0)
            elapsed_ms = (time.monotonic() - start) * 1000
            if event and event[0] == "all_clear":
                events.request.fire(
                    request_type="socket.io",
                    name="all_clear_received",
                    response_time=elapsed_ms,
                    response_length=len(str(event[1])),
                    exception=None,
                )
        except Exception as e:
            events.request.fire(
                request_type="socket.io",
                name="all_clear_received",
                response_time=(time.monotonic() - start) * 1000,
                response_length=0,
                exception=e,
            )


# ---------------------------------------------------------------------------
# Scenario trigger — admin task to fire a disaster and measure reroute latency
# ---------------------------------------------------------------------------

class DisasterTriggerUser(User):
    """
    One admin user who triggers disasters and measures end-to-end reroute latency.
    Corresponds to the Disaster Scenario Engine (Phase 0).
    """

    wait_time = between(10, 20)  # Fire a disaster every 10–20 seconds

    @task
    def trigger_disaster_scenario(self):
        scenario = random.choice(["m50_flooding", "city_center_fire", "port_tunnel_closure"])
        start = time.monotonic()

        with self.client.post(
            "/api/v1/scenarios/activate",
            json={
                "scenario_type": scenario,
                "region_id": "region-dublin-m50",
                "severity": random.choice(["low", "medium", "high"]),
            },
            catch_response=True,
        ) as response:
            elapsed_ms = (time.monotonic() - start) * 1000
            if response.status_code == 200:
                response.success()
                events.request.fire(
                    request_type="HTTP",
                    name=f"disaster_trigger_{scenario}",
                    response_time=elapsed_ms,
                    response_length=len(response.content),
                    exception=None,
                )
            else:
                response.failure(f"Disaster trigger failed: {response.status_code}")