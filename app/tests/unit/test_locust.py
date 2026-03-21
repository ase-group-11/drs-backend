"""
locustfile.py — Locust load test for the ReRoute Service.

Run with:
  locust -f app/tests/unit/test_locust.py --host http://localhost:8000

Two user types:
  SocketIOUser        — simulates connected vehicle clients listening for alerts
  DisasterTriggerUser — simulates the Disaster Evaluation Service triggering reroutes
"""
import time
import uuid
import random
import requests
from locust import User, HttpUser, task, events, between


# ---------------------------------------------------------------------------
# Socket.IO User — simulates a connected browser/app client
# ---------------------------------------------------------------------------

class SocketIOUser(User):
    """
    Each Locust user represents one vehicle connected via Socket.IO.
    Connects to the reroute:{regionId} room and listens for traffic alerts.
    """

    wait_time = between(1, 3)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sio = None
        self.user_id = f"locust-user-{uuid.uuid4().hex[:8]}"
        self.region_id = "region-dublin-m50"

    def on_start(self):
        """Connect to Socket.IO server."""
        import socketio as sio_client
        start = time.monotonic()
        try:
            self.sio = sio_client.SimpleClient()
            self.sio.connect(
                self.host,
                transports=["websocket", "polling"],
                socketio_path="socket.io",
                wait_timeout=10,
            )
            self.sio.emit("join_region", {"region_id": self.region_id})
            elapsed_ms = (time.monotonic() - start) * 1000
            events.request.fire(
                request_type="socket.io",
                name="connect",
                response_time=elapsed_ms,
                response_length=0,
                exception=None,
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            events.request.fire(
                request_type="socket.io",
                name="connect",
                response_time=elapsed_ms,
                response_length=0,
                exception=e,
            )

    def on_stop(self):
        try:
            if self.sio:
                self.sio.disconnect()
        except Exception:
            pass

    @task(3)
    def listen_for_reroute_alert(self):
        """
        Wait for reroute_alert event — SLA target is < 5 seconds.
        """
        if not self.sio:
            return
        start = time.monotonic()
        try:
            event = self.sio.receive(timeout=5.0)
            elapsed_ms = (time.monotonic() - start) * 1000
            if event and event[0] == "reroute_alert":
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
                    exception=TimeoutError("No reroute_alert within SLA"),
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
    def listen_for_updated_recommendation(self):
        """Listen for updated_recommendation during monitoring cycles."""
        if not self.sio:
            return
        start = time.monotonic()
        try:
            event = self.sio.receive(timeout=3.0)
            elapsed_ms = (time.monotonic() - start) * 1000
            event_name = "updated_recommendation_received" if (event and event[0] == "updated_recommendation") else "no_update_received"
            events.request.fire(
                request_type="socket.io",
                name=event_name,
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
        """Listen for all_clear when roads are restored."""
        if not self.sio:
            return
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
# Disaster Trigger User — simulates Disaster Evaluation Service
# ---------------------------------------------------------------------------

class DisasterTriggerUser(HttpUser):
    """
    Activates scenarios and triggers the full reroute pipeline.
    Measures end-to-end disaster → reroute latency.
    """

    wait_time = between(30, 60)  # Fire a disaster every 30–60 seconds to avoid TomTom 429

    def on_start(self):
        """Seed vehicles once on startup."""
        try:
            self.client.post("/api/v1/scenarios/seed-vehicles?count=200")
        except Exception:
            pass

    @task
    def trigger_full_reroute_pipeline(self):
        """
        Activate a scenario then immediately trigger reroute.
        Measures full pipeline latency.
        """
        scenario = random.choice(["m50_flooding", "port_tunnel_closure"])
        region_id = "region-dublin-m50"

        # Step 1 — activate scenario
        start = time.monotonic()
        try:
            activate_resp = self.client.post(
                "/api/v1/scenarios/activate",
                json={"scenario_type": scenario},
                name="/api/v1/scenarios/activate",
            )
            # accept both 200 and 201
            if activate_resp.status_code not in (200, 201):
                events.request.fire(
                    request_type="HTTP",
                    name="scenario_activate",
                    response_time=(time.monotonic() - start) * 1000,
                    response_length=0,
                    exception=Exception(f"activate failed: {activate_resp.status_code}"),
                )
                return

            disaster_id = activate_resp.json().get("disaster_id")
            if not disaster_id:
                return

        except Exception as e:
            events.request.fire(
                request_type="HTTP",
                name="scenario_activate",
                response_time=(time.monotonic() - start) * 1000,
                response_length=0,
                exception=e,
            )
            return

        # Step 2 — trigger reroute and measure SLA
        trigger_start = time.monotonic()
        try:
            trigger_resp = self.client.post(
                "/api/v1/reroute/trigger",
                json={
                    "disaster_id": disaster_id,
                    "region_id": region_id,
                },
                name="/api/v1/reroute/trigger",
            )
            elapsed_ms = (time.monotonic() - trigger_start) * 1000

            if trigger_resp.status_code == 200:
                data = trigger_resp.json()
                events.request.fire(
                    request_type="HTTP",
                    name="reroute_pipeline_e2e",
                    response_time=elapsed_ms,
                    response_length=len(trigger_resp.content),
                    exception=None if elapsed_ms < 5000 else TimeoutError(f"SLA breach: {elapsed_ms:.0f}ms"),
                )
            else:
                events.request.fire(
                    request_type="HTTP",
                    name="reroute_pipeline_e2e",
                    response_time=elapsed_ms,
                    response_length=0,
                    exception=Exception(f"trigger failed: {trigger_resp.status_code}"),
                )

        except Exception as e:
            events.request.fire(
                request_type="HTTP",
                name="reroute_pipeline_e2e",
                response_time=(time.monotonic() - trigger_start) * 1000,
                response_length=0,
                exception=e,
            )