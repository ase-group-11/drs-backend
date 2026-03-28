# File: app/services/location_registry.py
"""
Location Registry — three Redis-backed stores.

1. USER LOCATION  (user_location:{user_id})
   TTL: 5 minutes, refreshed on every WS heartbeat.

2. DISASTER SUBSCRIPTIONS  (disaster_subscribers:{disaster_id})
   Redis SET of user_ids (reporter + assigned team).
   TTL: 24 hours.

3. OFFLINE ALERT QUEUE  (offline_alerts:{user_id})
   Redis LIST of JSON alerts for offline users.
   TTL: 24 hours. Capped at 50.

4. HAVERSINE GEO TARGETING — pure Python, no DB needed.

All functions SYNC — safe from blocking pika thread.
"""

import json
import logging
import math
from typing import Any, Dict, List, Optional, Set

import redis as sync_redis
from app.core.config import settings

logger = logging.getLogger("location_registry")

REDIS_URL          = settings.REDIS_URL
TTL_USER_LOCATION  = 5 * 60
TTL_DISASTER_SUBS  = 24 * 60 * 60
TTL_OFFLINE_ALERTS = 24 * 60 * 60
MAX_OFFLINE_ALERTS = 50

_client: Optional[sync_redis.Redis] = None


def _r() -> sync_redis.Redis:
    global _client
    if _client is None:
        _client = sync_redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_keepalive=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )
    return _client


# ── User location ─────────────────────────────────────────────

def set_user_location(user_id: str, lat: float, lon: float) -> None:
    try:
        _r().setex(
            f"user_location:{user_id}",
            TTL_USER_LOCATION,
            json.dumps({"lat": lat, "lon": lon}),
        )
    except Exception as exc:
        logger.error(f"set_user_location({user_id}): {exc}")


def delete_user_location(user_id: str) -> None:
    try:
        _r().delete(f"user_location:{user_id}")
    except Exception:
        pass


def is_user_online(user_id: str) -> bool:
    try:
        return bool(_r().exists(f"user_location:{user_id}"))
    except Exception:
        return False


def get_all_located_users() -> List[Dict[str, Any]]:
    try:
        keys = _r().keys("user_location:*")
        result = []
        for key in keys:
            raw = _r().get(key)
            if raw:
                uid = key.split("user_location:")[1]
                loc = json.loads(raw)
                result.append({"user_id": uid, "lat": loc["lat"], "lon": loc["lon"]})
        return result
    except Exception as exc:
        logger.error(f"get_all_located_users: {exc}")
        return []


# ── Disaster subscriptions ────────────────────────────────────

def subscribe_to_disaster(disaster_id: str, user_id: str) -> None:
    try:
        key = f"disaster_subscribers:{disaster_id}"
        _r().sadd(key, user_id)
        _r().expire(key, TTL_DISASTER_SUBS)
    except Exception as exc:
        logger.error(f"subscribe_to_disaster: {exc}")


def get_disaster_subscribers(disaster_id: str) -> Set[str]:
    try:
        return _r().smembers(f"disaster_subscribers:{disaster_id}") or set()
    except Exception as exc:
        logger.error(f"get_disaster_subscribers: {exc}")
        return set()


# ── Offline queue ─────────────────────────────────────────────

def queue_offline_alert(user_id: str, alert: Dict[str, Any]) -> None:
    try:
        key = f"offline_alerts:{user_id}"
        _r().lpush(key, json.dumps(alert, default=str))
        _r().ltrim(key, 0, MAX_OFFLINE_ALERTS - 1)
        _r().expire(key, TTL_OFFLINE_ALERTS)
    except Exception as exc:
        logger.error(f"queue_offline_alert({user_id}): {exc}")


def flush_offline_alerts(user_id: str) -> List[Dict[str, Any]]:
    try:
        key = f"offline_alerts:{user_id}"
        raw_list = _r().lrange(key, 0, -1)
        _r().delete(key)
        return [json.loads(r) for r in reversed(raw_list)]
    except Exception as exc:
        logger.error(f"flush_offline_alerts({user_id}): {exc}")
        return []


# ── Geo targeting ─────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def get_users_within_radius(
    disaster_lat: float, disaster_lon: float, radius_km: float
) -> Set[str]:
    nearby: Set[str] = set()
    for u in get_all_located_users():
        if _haversine_km(disaster_lat, disaster_lon, u["lat"], u["lon"]) <= radius_km:
            nearby.add(u["user_id"])
    return nearby


def resolve_targets(
    disaster_id: str,
    disaster_lat: float,
    disaster_lon: float,
    radius_km: float,
) -> Set[str]:
    direct = get_disaster_subscribers(disaster_id)
    geo    = get_users_within_radius(disaster_lat, disaster_lon, radius_km)
    return direct | geo