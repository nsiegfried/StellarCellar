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
        assert holding["name"] == "New arrivals"

        # "Decide later" lands in the holding place, never nowhere
        r = client.post("/api/bottles", json={"producer": "Dup", "quantity": 4}).json()
        assert client.get(f"/api/bottle/{r['codes'][0]}").json()["location"] == "New arrivals"
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
