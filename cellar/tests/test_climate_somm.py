import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("CELLAR_DATA_DIR", tempfile.mkdtemp())
os.environ["ANTHROPIC_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import climate  # noqa: E402
from app.main import app  # noqa: E402

CFG = {"temp_min": 45, "temp_max": 65, "rh_min": 40, "rh_max": 80}
NOW = datetime(2026, 11, 25, 22, 0, tzinfo=timezone.utc)


def st(value, unit="°F", minutes_ago=5):
    stamp = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return {"state": str(value), "attributes": {"unit_of_measurement": unit}, "last_reported": stamp}


def test_in_range():
    c = climate.build(st("57.56"), st("55.71", "%"), CFG, NOW)
    assert c["status"] == "ok" and c["temp"] == 57.6 and c["humidity"] == 56 and c["unit"] == "°F"


def test_out_of_range_names_the_problem():
    assert climate.build(st(67), st(55, "%"), CFG, NOW)["message"].startswith("Warm")
    assert climate.build(st(57), st(35, "%"), CFG, NOW)["message"].startswith("Dry")


def test_unavailable_or_silent_sensor():
    assert climate.build(st("unavailable"), st("unavailable", "%"), CFG, NOW)["status"] == "unknown"
    assert climate.build(st(57, minutes_ago=400), st(55, "%", minutes_ago=400), CFG, NOW)["status"] == "unknown"
    assert climate.build(None, None, CFG, NOW) == {"available": False}


def test_read_without_supervisor_is_off(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert climate.read(fetch=lambda e: st(57)) is None


def test_read_uses_configured_entities(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    asked = []
    def fake(entity):
        asked.append(entity)
        return st(57) if "temp" in entity else st(55, "%")
    c = climate.read(fetch=fake, use_cache=False)
    assert asked == ["sensor.wine_tt_mt_temperature", "sensor.wine_tt_mt_humidity"]
    assert c["available"] and c["status"] in ("ok", "unknown")
    monkeypatch.delenv("SUPERVISOR_TOKEN")


def test_sommelier_rules_fallback_picks_only_ready_unheld_bottles():
    with TestClient(app) as client:
        loc = client.post("/api/locations", json={"name": "Somm fridge"}).json()
        ok = client.post("/api/bottles", json={"producer": "Somm Ready", "name": "Pinot", "vintage": 2021,
                                               "purchase_price": 40, "drink_from": 2024, "drink_to": 2032,
                                               "location_code": loc["code"]}).json()["codes"][0]
        held = client.post("/api/bottles", json={"producer": "Somm Held", "vintage": 2020, "purchase_price": 30,
                                                 "drink_from": 2024, "drink_to": 2032,
                                                 "location_code": loc["code"]}).json()["codes"][0]
        client.post(f"/api/bottle/{held}/reserve", json={"reserved_for": "Jen"})
        pricey = client.post("/api/bottles", json={"producer": "Somm Pricey", "vintage": 2019, "purchase_price": 300,
                                                   "drink_from": 2024, "drink_to": 2032,
                                                   "location_code": loc["code"]}).json()["codes"][0]

        assert client.post("/api/sommelier", json={}).status_code == 400
        r = client.post("/api/sommelier", json={"dish": "Risotto", "flavor": "Earthy", "budget_max": 100}).json()
        codes = [r["pick"]["code"]] + [a["code"] for a in r["also"]]
        assert held not in codes and pricey not in codes
        assert r["source"] == "rules" and r["pick"]["label"]
        assert r["pick"]["status"] in ("ready", "drink_soon") and ok


def test_sommelier_claude_reply_is_checked_against_the_cellar(monkeypatch):
    from app import ai

    class Msg:
        def __init__(self, text):
            self.content = [type("B", (), {"text": text})()]

    class Client:
        def __init__(self, text):
            self.messages = type("M", (), {"create": lambda _s, **kw: Msg(text)})()

    cands = [{"code": "B1", "label": "Pinot 2023", "color": "red", "region": "Carneros", "price": 60, "status": "ready",
              "score": 95, "taster": "Jen", "location": "Wine Fridge", "slot": "B-1", "location_code": "L1"},
             {"code": "B2", "label": "Malbec 2020", "color": "red", "region": "Mendoza", "price": 35, "status": "ready",
              "score": None, "taster": None, "location": "Needs a Home", "slot": "", "location_code": "L2"}]
    good = ('{"pick": {"code": "B1", "why": "Earthy Pinot for earthy mushrooms.", "open": "15 min ahead", '
            '"serve": "55 to 60°F"}, "also": [{"code": "B2", "note": "fuller"}, {"code": "B9", "note": "fake"}], '
            '"dish": "Mushroom risotto", "poor_fit": false}')
    monkeypatch.setattr(ai, "_client", lambda: Client(good))
    out, err = ai.sommelier({"dish": "Risotto"}, cands)
    assert err is None and out["pick"]["code"] == "B1" and out["pick"]["slot"] == "B-1"
    assert [a["code"] for a in out["also"]] == ["B2"]          # invented code dropped
    monkeypatch.setattr(ai, "_client", lambda: Client('{"pick": {"code": "B9"}}'))
    out, err = ai.sommelier({"dish": "Risotto"}, cands)
    assert out is None and "isn't in your cellar" in err
