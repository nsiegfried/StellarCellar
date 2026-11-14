import os
import tempfile

os.environ["CELLAR_DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_full_flow():
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"ok": True}
        loc = client.post("/api/locations", json={"name": "Kitchen fridge"}).json()
        assert loc["code"].startswith("L")

        r = client.post("/api/bottles", json={
            "producer": "Test Estate", "name": "Reserve", "vintage": 2019, "quantity": 2,
            "purchase_price": 42, "drink_from": 2024, "drink_to": 2030, "location_code": loc["code"],
        }).json()
        assert len(r["codes"]) == 2 and all(x.startswith("B") for x in r["codes"])
        code = r["codes"][0]

        s = client.get("/api/summary").json()
        assert s["total"] == 2 and s["ready"] == 2 and s["value"] == 84

        picks = client.get("/api/picks").json()
        assert len(picks["under_low"]) == 1 and picks["under_low"][0]["count"] == 2  # one card per wine

        assert client.get(f"/api/location/{loc['code']}").json()["bottles"][0]["location"] == "Kitchen fridge"
        assert client.get("/api/search?q=estate").json()[0]["code"] in r["codes"]

        labels = client.get(f"/api/labels?codes={code},{loc['code']}")
        assert labels.status_code == 200 and "<svg" in labels.text and code in labels.text

        landing = client.get(f"/q/{code.lower()}")
        assert landing.status_code == 200 and f"#/bottle/{code}" in landing.text

        rec = client.post("/api/recommend", json={"food": "steak"}).json()
        assert rec["source"] == "rules" and rec["picks"][0]["code"] in r["codes"]

        d = client.post(f"/api/bottle/{code}/drink", json={"rating": 4, "note": "Good"}).json()
        assert d["ok"] and client.get("/api/summary").json()["total"] == 1
        assert client.post(f"/api/bottle/{code}/drink", json={}).status_code == 409

        assert client.post(f"/api/bottle/{code}/undo").json()["ok"]
        assert client.get("/api/summary").json()["total"] == 2
        assert client.post(f"/api/bottle/{code}/undo").status_code == 409

        other = client.post("/api/locations", json={"name": "Rack A"}).json()
        mb = client.post("/api/move-batch", json={"bottle_codes": r["codes"], "location_code": other["code"]}).json()
        assert sorted(mb["moved"]) == sorted(r["codes"])
        assert client.get(f"/api/location/{other['code']}").json()["bottles"].__len__() == 2

        assert client.get("/api/bottle/BNOPE123").status_code == 404
        assert client.get("/").status_code == 200
        assert client.get("//").status_code == 200            # Ingress double-slash
        assert client.get("/api//health").json() == {"ok": True}


def test_label_formats():
    with TestClient(app) as client:
        loc = client.post("/api/locations", json={"name": "Shelf"}).json()
        r = client.post("/api/bottles", json={"producer": "Fmt", "quantity": 1}).json()
        code = r["codes"][0]
        compact = client.get(f"/api/labels?codes={code}").text          # default
        assert "width:20mm" in compact and f'data-payload="{code}"' in compact   # bare code in QR
        neck = client.get(f"/api/labels?codes={code},{loc['code']}&fmt=neck").text
        assert "width:54mm" in neck and loc["code"] in neck
        full = client.get(f"/api/labels?codes={code}&fmt=full").text
        assert f'data-payload="http://testserver/q/{code}"' in full
        assert client.get(f"/api/labels?codes={code}&fmt=huge").status_code == 400


def test_holding_place_delete_and_settings():
    with TestClient(app) as client:
        locs = client.get("/api/locations").json()
        holding = next(l for l in locs if l["holding"])
        assert holding["name"] == "Needs a Home"

        # "Decide later" lands in the holding place, never nowhere
        r = client.post("/api/bottles", json={"producer": "Dup", "quantity": 4}).json()
        assert client.get(f"/api/bottle/{r['codes'][0]}").json()["location"] == "Needs a Home"
        assert client.get(f"/api/bottle/{r['codes'][0]}").json()["siblings_in_cellar"] == 4

        # delete one, then the rest
        assert client.delete(f"/api/bottle/{r['codes'][0]}").json()["ok"]
        assert client.get(f"/api/bottle/{r['codes'][0]}").status_code == 404
        wine_id = client.get(f"/api/bottle/{r['codes'][1]}").json()["wine_id"]
        assert client.delete(f"/api/wine/{wine_id}/bottles").json()["deleted"] == 3
        assert client.get("/api/search?q=dup") == [] or client.get("/api/search?q=dup").json() == []

        # default place setting
        rack = client.post("/api/locations", json={"name": "Rack B"}).json()
        assert client.get("/api/settings").json()["default_location_code"] == holding["code"]
        s = client.post("/api/settings", json={"default_location_code": rack["code"]}).json()
        assert s["default_location_code"] == rack["code"]
        assert client.post("/api/settings", json={"default_location_code": "LNOPE99"}).status_code == 404


def test_journal_and_import(tmp_path):
    import json, pathlib
    data = json.load(open(pathlib.Path(__file__).parent / "fixtures" / "wine_profile_sample.json"))
    with TestClient(app) as client:
        rep = client.post("/api/import/wine-profile", json={"data": data, "taster": "Jen"}).json()
        assert rep["wines"] == 2 and rep["bottles"] == 2 and rep["incoming"] == 1 and rep["tastings"] == 2
        assert rep["linked_tastings"] == 1
        assert client.post("/api/import/wine-profile", json={"data": data}).status_code == 409
        j = client.get("/api/journal").json()
        assert j[0]["score"] == 96 and j[0]["taster"] == "Jen"
        inc = client.get("/api/incoming").json()
        assert len(inc) == 1 and client.post(f"/api/bottle/{inc[0]['code']}/arrived").json()["ok"]
        assert client.get("/api/incoming").json() == []
        assert "Cabernet" in client.get("/api/settings").json()["taste_profile"]
        t = client.post("/api/journal", json={"wine": "New Thing", "score": 88, "notes": "good"}).json()
        assert t["band"] == "Strong" and t["taster"] == "Jen"
        assert client.delete(f"/api/journal/{t['id']}").json()["ok"]


def test_single_word_wine_does_not_attract_links():
    with TestClient(app) as client:
        client.post("/api/bottles", json={"producer": "Ridge", "name": "Vineyards", "quantity": 1})
        r = client.post("/api/journal", json={"wine": "Ridge Lytton Petite Sirah", "score": 75}).json()
        assert r["wine_id"] is None
        rl = client.post("/api/bottles", json={"producer": "Ridge Lytton", "name": "Petite Sirah", "quantity": 1}).json()
        assert client.post("/api/journal/relink").json()["changed"] >= 1
        linked = [x for x in client.get("/api/journal").json() if x["wine"] == "Ridge Lytton Petite Sirah"][0]
        assert linked["wine_id"] is not None
        # deleting the cellar wine unlinks but keeps the tasting
        client.delete(f"/api/bottle/{rl['codes'][0]}")
        kept = [x for x in client.get("/api/journal").json() if x["wine"] == "Ridge Lytton Petite Sirah"][0]
        assert kept["wine_id"] is None


def test_bottles_by_status():
    with TestClient(app) as client:
        client.post("/api/bottles", json={"producer": "StatusTest", "quantity": 2, "drink_from": 2020, "drink_to": 2040})
        ready = client.get("/api/bottles?status=ready").json()
        assert any(c["label"].startswith("StatusTest") for c in ready)
        assert all(c["status"] == "ready" for c in ready)
        assert len(client.get("/api/bottles").json()) >= len(ready)


def test_edit_bottle():
    with TestClient(app) as client:
        r = client.post("/api/bottles", json={"producer": "Edit Me", "quantity": 2, "purchase_price": 20}).json()
        a, b2 = r["codes"]
        e = client.patch(f"/api/bottle/{a}", json={"producer": "Edited", "vintage": 2019, "region": "Paso",
                                                   "drink_from": 2025, "drink_to": 2035, "est_value": 55,
                                                   "special_occasion": True}).json()
        assert e["producer"] == "Edited" and e["vintage"] == 2019 and e["window_to"] == 2035 and e["est_value"] == 55
        other = client.get(f"/api/bottle/{b2}").json()
        assert other["producer"] == "Edited" and other["window_to"] == 2035      # wine fields shared
        assert other["est_value"] is None                                        # price is per bottle
        e2 = client.patch(f"/api/bottle/{a}", json={"clear_window": True, "clear_est_value": True}).json()
        assert e2["window_to"] is None and e2["est_value"] is None
        assert client.patch(f"/api/bottle/{a}", json={"producer": "  "}).status_code == 400
        assert client.patch(f"/api/bottle/{a}", json={"color": "purple"}).status_code == 400


def test_gift_hold_and_split():
    with TestClient(app) as client:
        r = client.post("/api/bottles", json={"producer": "GiftWine", "quantity": 2, "purchase_price": 100,
                                              "drink_from": 2020, "drink_to": 2040}).json()
        a, b2 = r["codes"]
        before = client.get("/api/summary").json()
        # hold one
        h = client.post(f"/api/bottle/{a}/reserve", json={"reserved_for": "Colin", "note": "Napa payback"}).json()
        assert h["reserved_for"] == "Colin"
        s = client.get("/api/summary").json()
        assert s["held"] >= 1 and s["held_value"] >= 100 and abs(s["own_value"] + s["held_value"] - s["value"]) < 0.01
        held = client.get("/api/held").json()
        assert any(g["person"] == "Colin" and any(x["code"] == a for x in g["bottles"]) for g in held)
        # held bottle stays out of picks and recommendations
        picks = client.get("/api/picks").json()
        codes = [x["code"] for tier in ("under_low", "under_high", "special") for x in picks[tier]]
        assert a not in codes
        rec = client.post("/api/recommend", json={"food": "steak"}).json()
        assert all(p["code"] != a for p in rec["picks"])
        # gift it, undo, gift again
        g = client.post(f"/api/bottle/{a}/gift", json={"to": "Colin", "occasion": "Birthday"}).json()
        assert g["ok"] and g["to"] == "Colin"
        assert client.get(f"/api/bottle/{a}").json()["bottle_status"] == "gifted"
        assert client.post(f"/api/bottle/{a}/undo").json()["ok"]
        assert client.get(f"/api/bottle/{a}").json()["bottle_status"] == "in_cellar"
        client.post(f"/api/bottle/{a}/gift", json={"to": "Colin"})
        gifts = client.get("/api/gifts").json()
        assert gifts[0]["to"] == "Colin" and gifts[0]["code"] == a
        assert "Colin" in client.get(f"/api/bottle/{b2}").json()["people"]
        assert client.post(f"/api/bottle/{a}/gift", json={"to": "X"}).status_code == 409
        assert client.post(f"/api/bottle/{b2}/gift", json={"to": " "}).status_code == 400


def test_static_scanner_library_served():
    with TestClient(app) as client:
        r = client.get("/static/jsQR.js")
        assert r.status_code == 200 and "jsQR" in r.text[:2000]


def test_fridge_layout_and_slots():
    with TestClient(app) as client:
        loc = client.post("/api/locations", json={"name": "Wine Fridge"}).json()
        lay = {"order": "top_down", "racks": [{"name": "A", "across": 8, "deep": 2}, {"name": "B", "across": 7, "deep": 2}]}
        r = client.post(f"/api/location/{loc['code']}/layout", json={"kind": "fridge", "layout": lay}).json()
        assert r["capacity"] == 30 and r["kind"] == "fridge"
        assert client.post(f"/api/location/{loc['code']}/layout", json={"layout": {"racks": [{"name": "A", "across": 99}]}}).status_code == 400

        b = client.post("/api/bottles", json={"producer": "Slot Test", "quantity": 2}).json()
        a, b2 = b["codes"]
        assert client.post(f"/api/bottle/{a}/slot", json={"location_code": loc["code"], "slot": "a-3"}).json()["slot"] == "A-3"
        assert client.post(f"/api/bottle/{a}/slot", json={"location_code": loc["code"], "slot": "A-9"}).status_code == 400
        assert client.post(f"/api/bottle/{a}/slot", json={"location_code": loc["code"], "slot": "A-3C"}).status_code == 400  # deep is 2
        # second bottle takes the slot; first gets bumped to slot unknown
        client.post(f"/api/bottle/{b2}/slot", json={"location_code": loc["code"], "slot": "A-3"})
        det = client.get(f"/api/location/{loc['code']}").json()
        slots = {x["code"]: x["slot"] for x in det["bottles"]}
        assert slots[b2] == "A-3" and slots[a] == ""
        assert det["capacity"] == 30 and det["layout"]["racks"][1]["across"] == 7
        # moving to a plain place clears the slot
        other = client.post("/api/locations", json={"name": "Pantry2"}).json()
        client.post(f"/api/bottle/{b2}/move", json={"location_code": other["code"]})
        assert client.get(f"/api/bottle/{b2}").json()["slot"] == ""
        # clearing the layout clears slots and kind
        r2 = client.post(f"/api/location/{loc['code']}/layout", json={"layout": None}).json()
        assert r2["layout"] is None and r2["kind"] == "shelf"


def test_export_import_db():
    import base64, os, tempfile
    with TestClient(app) as client:
        client.post("/api/bottles", json={"producer": "Export Me", "quantity": 3})
        total = client.get("/api/summary").json()["total"]
        exported = client.get("/api/export/db").content
        assert exported[:15] == b"SQLite format 3"
        # importing into a non-empty install is refused
        r = client.post("/api/import/db", json={"db_base64": base64.b64encode(exported).decode()})
        assert r.status_code == 409
    # fresh empty install accepts it
    os.environ["CELLAR_DATA_DIR"] = tempfile.mkdtemp()
    import importlib
    from app import db as dbmod, main as mainmod
    importlib.reload(dbmod); importlib.reload(mainmod)
    with TestClient(mainmod.app) as client2:
        assert client2.get("/api/summary").json()["total"] == 0
        r = client2.post("/api/import/db", json={"db_base64": base64.b64encode(exported).decode()}).json()
        assert r["ok"] and r["bottles"] >= total   # raw rows include opened and gifted bottles
        assert client2.get("/api/summary").json()["total"] == total
        assert client2.post("/api/import/db", json={"db_base64": base64.b64encode(b"nope").decode()}).status_code in (400, 409)


def test_presets_zones_and_bulk():
    with TestClient(app) as client:
        presets = client.get("/api/layout-presets").json()
        tall = next(p for p in presets if p["id"] == "tall116")
        assert tall["capacity"] == 110 and tall["zones"] == ["Upper", "Lower"] and len(tall["racks"]) == 11
        loc = client.post("/api/locations", json={"name": "NewAir"}).json()
        lay = {"zones": ["Upper", "Lower"], "racks": tall["racks"] + [{"name": "Z", "across": 6, "deep": 1, "zone": 1, "bulk": True}]}
        r = client.post(f"/api/location/{loc['code']}/layout", json={"kind": "fridge", "layout": lay}).json()
        assert r["capacity"] == 116 and r["layout"]["racks"][-1]["bulk"] is True
        b = client.post("/api/bottles", json={"producer": "Bulk Test", "quantity": 2}).json()
        # bulk rack takes a bare rack name, slotted racks need a position
        assert client.post(f"/api/bottle/{b['codes'][0]}/slot", json={"location_code": loc["code"], "slot": "Z"}).json()["slot"] == "Z"
        assert client.post(f"/api/bottle/{b['codes'][1]}/slot", json={"location_code": loc["code"], "slot": "Z"}).json()["slot"] == "Z"
        assert client.post(f"/api/bottle/{b['codes'][1]}/slot", json={"location_code": loc["code"], "slot": "A"}).status_code == 400
        det = client.get(f"/api/location/{loc['code']}").json()
        assert sum(1 for x in det["bottles"] if x["slot"] == "Z") == 2   # bulk does not bump


def test_map_and_region_apply():
    with TestClient(app) as client:
        r = client.post("/api/bottles", json={"producer": "MapTest Winery", "quantity": 2}).json()
        m = client.get("/api/map").json()
        assert any(u["label"].startswith("MapTest") and u["count"] == 2 for u in m["unplaced"])
        tot = m["totals"]
        assert tot["on_map"] + tot["to_verify"] == tot["bottles"]
        wine_id = client.get(f"/api/bottle/{r['codes'][0]}").json()["wine_id"]
        assert client.post("/api/regions/apply", json={"items": [{"id": wine_id, "region": "Paso Robles", "country": "USA"}]}).json()["updated"] == 1
        m2 = client.get("/api/map").json()
        pin = next(p for p in m2["pins"] if p["name"] == "Paso Robles")
        assert pin["count"] >= 2 and abs(pin["lat"] - 35.63) < 0.01 and pin["precision"] == "region"
        a = client.get("/api/region-about?region=Paso%20Robles&country=USA").json()
        assert a["precision"] == "region"   # about text is empty without an API key, coordinates still resolve


def test_home_rate_and_glance():
    with TestClient(app) as client:
        r = client.post("/api/bottles", json={"producer": "HomeTest", "quantity": 2}).json()
        h = client.get("/api/home").json()
        assert h["arrivals"] >= 2 and h["fridge"] is not None and "total" in h
        client.post(f"/api/bottle/{r['codes'][0]}/drink", json={})
        h2 = client.get("/api/home").json()
        assert h2["rate"] and h2["rate"]["code"] == r["codes"][0]
        client.post("/api/journal", json={"wine": "HomeTest", "score": 88, "bottle_code": r["codes"][1]})
        assert client.get("/api/home").json()["rate"] is None   # rated, so the nudge goes away
        assert client.post("/api/winelist", json={"image": "QUJD", "media_type": "image/gif"}).status_code == 400


def test_window_sources_and_apply():
    with TestClient(app) as client:
        a = client.post("/api/bottles", json={"producer": "WinEst", "drink_from": 2026, "drink_to": 2030, "window_from_label": True}).json()
        assert client.get(f"/api/bottle/{a['codes'][0]}").json()["window_source"] == "ai"
        b = client.post("/api/bottles", json={"producer": "WinOwn", "drink_from": 2026, "drink_to": 2030}).json()
        bot = client.get(f"/api/bottle/{b['codes'][0]}").json()
        assert bot["window_source"] == "owner"
        assert client.post("/api/windows/apply", json={"items": [{"id": bot["wine_id"], "drink_from": 2027, "drink_to": 2035}]}).json()["updated"] == 1
        after = client.get(f"/api/bottle/{b['codes'][0]}").json()
        assert (after["window_from"], after["window_to"], after["window_source"]) == (2027, 2035, "reviewed")
        assert client.post("/api/windows/review").status_code == 503   # no API key in tests


def test_delete_location():
    with TestClient(app) as client:
        loc = client.post("/api/locations", json={"name": "Old Pantry"}).json()
        b = client.post("/api/bottles", json={"producer": "Mover", "location_code": loc["code"]}).json()
        r = client.delete(f"/api/location/{loc['code']}").json()
        assert r["ok"] and r["moved_to_new_arrivals"] == 1
        assert client.get(f"/api/bottle/{b['codes'][0]}").json()["location_code"] == "LNEWARR"
        assert client.get(f"/api/location/{loc['code']}").status_code == 404
        assert client.delete("/api/location/LNEWARR").status_code == 400


def test_rack_orientation_pattern():
    with TestClient(app) as client:
        loc = client.post("/api/locations", json={"name": "Orient Fridge"}).json()
        lay = {"racks": [{"name": "A", "across": 5, "deep": 2, "orient": "bnb"}, {"name": "B", "across": 3, "deep": 2, "orient": "XNNNNNN"}]}
        r = client.post(f"/api/location/{loc['code']}/layout", json={"kind": "fridge", "layout": lay}).json()
        assert r["layout"]["racks"][0]["orient"] == "BNBBB"     # uppercased, padded with base-out
        assert r["layout"]["racks"][1]["orient"] == "NNN"       # junk dropped, trimmed to across
