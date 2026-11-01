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
        assert len(picks["under_low"]) == 2

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
