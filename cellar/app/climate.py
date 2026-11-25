"""Wine fridge temperature and humidity, read from Home Assistant sensors.

The add-on talks to Home Assistant through the Supervisor proxy (homeassistant_api: true in config.yaml),
so no long-lived token is needed. Readings are cached briefly so a busy Home screen doesn't hammer HA.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

STALE_SECONDS = 3 * 3600       # a sensor silent this long is treated as not reporting
CACHE_SECONDS = 60
_cache = {"at": 0.0, "data": None}


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _num(name, default):
    try:
        return float(_env(name) or default)
    except ValueError:
        return float(default)


def settings():
    return {
        "temp_entity": _env("CELLAR_FRIDGE_TEMP_ENTITY", "sensor.wine_tt_mt_temperature"),
        "humidity_entity": _env("CELLAR_FRIDGE_HUMIDITY_ENTITY", "sensor.wine_tt_mt_humidity"),
        "temp_min": _num("CELLAR_FRIDGE_TEMP_MIN", 45), "temp_max": _num("CELLAR_FRIDGE_TEMP_MAX", 65),
        "rh_min": _num("CELLAR_FRIDGE_RH_MIN", 40), "rh_max": _num("CELLAR_FRIDGE_RH_MAX", 80),
    }


def fetch_state(entity: str):
    """One entity's state object from Home Assistant, or None if it can't be read."""
    token = _env("SUPERVISOR_TOKEN")
    if not token or not entity:
        return None
    base = _env("CELLAR_HA_URL", "http://supervisor/core/api").rstrip("/")
    req = urllib.request.Request(f"{base}/states/{entity}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _value(state):
    if not state:
        return None
    try:
        return float(state.get("state"))
    except (TypeError, ValueError):
        return None          # "unavailable", "unknown"


def _age_seconds(state, now):
    stamp = state.get("last_reported") or state.get("last_updated") or state.get("last_changed")
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - when).total_seconds()


def build(temp_state, rh_state, cfg, now=None):
    """Pure: turn two HA state objects into what the app shows."""
    now = now or datetime.now(timezone.utc)
    if not temp_state and not rh_state:
        return {"available": False}
    temp, rh = _value(temp_state), _value(rh_state)
    unit = ((temp_state or {}).get("attributes") or {}).get("unit_of_measurement") or "°F"
    ages = [a for a in (_age_seconds(s, now) for s in (temp_state, rh_state) if s) if a is not None]
    stale = bool(ages) and min(ages) > STALE_SECONDS
    problems = []
    if temp is not None:
        if temp > cfg["temp_max"]:
            problems.append(f"Warm: above {cfg['temp_max']:g}{unit}")
        elif temp < cfg["temp_min"]:
            problems.append(f"Cold: below {cfg['temp_min']:g}{unit}")
    if rh is not None:
        if rh < cfg["rh_min"]:
            problems.append(f"Dry: under {cfg['rh_min']:g}% humidity")
        elif rh > cfg["rh_max"]:
            problems.append(f"Damp: over {cfg['rh_max']:g}% humidity")
    if (temp is None and rh is None) or stale:
        status, message = "unknown", "Sensor not reporting"
    elif problems:
        status, message = "warn", "; ".join(problems)
    else:
        status, message = "ok", "In range"
    return {"available": True, "status": status, "message": message,
            "temp": round(temp, 1) if temp is not None else None, "unit": unit,
            "humidity": round(rh) if rh is not None else None,
            "range": f"{cfg['temp_min']:g} to {cfg['temp_max']:g}{unit}, {cfg['rh_min']:g} to {cfg['rh_max']:g}% humidity"}


def read(fetch=fetch_state, use_cache=True):
    """Current fridge climate, or None when the add-on can't reach Home Assistant at all."""
    if not _env("SUPERVISOR_TOKEN"):
        return None
    if use_cache and _cache["data"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    cfg = settings()
    data = build(fetch(cfg["temp_entity"]), fetch(cfg["humidity_entity"]), cfg)
    _cache.update(at=time.time(), data=data)
    return data
