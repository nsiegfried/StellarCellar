import os
import tempfile
from unittest.mock import patch

from app import zebra


def test_zpl_dimensions_203dpi():
    z = zebra.label_zpl("B7K3QX9", "Jessup Pinot Noir 2023", "B7K3QX9", width_mm=25.4, height_mm=12.7, dpi=203)
    assert z.startswith("^XA") and z.endswith("^XZ")
    assert "^PW203^LL102" in z           # 1 x 0.5 inch at 203 dpi
    assert "^BQN,2," in z and "^FDQA,B7K3QX9^FS" in z
    assert "B7K3QX9" in z and "Jessup Pinot" in z


def test_zpl_strips_control_chars_and_fits_300dpi():
    z = zebra.label_zpl("L4M9TQ2", "Rack ^A ~ test", "L4M9TQ2", width_mm=50.8, height_mm=19, dpi=300)
    assert "^A ~" not in z and "^PW600^LL224" in z


def test_print_endpoints():
    os.environ["CELLAR_DATA_DIR"] = tempfile.mkdtemp()
    from fastapi.testclient import TestClient
    from app import main
    with TestClient(main.app) as client:
        assert client.get("/api/printer").json() == {"configured": False}
        assert client.post("/api/print", json={"codes": ["BXXXXXX"]}).status_code == 409
        with patch.object(main, "ZEBRA_HOST", "192.0.2.10"), patch("app.zebra.send") as send, \
             patch("app.zebra.status", return_value=True):
            r = client.post("/api/bottles", json={"producer": "Zebra Test", "quantity": 2}).json()
            out = client.post("/api/print", json={"codes": r["codes"], "copies": 2}).json()
            assert out == {"ok": True, "printed": 4}
            assert send.call_args[0][0] == "192.0.2.10" and send.call_args[0][1].count("^XA") == 4
            assert client.get("/api/printer").json()["online"] is True
            assert client.post("/api/print", json={"codes": ["BNOPE99"]}).status_code == 404
            send.side_effect = OSError("refused")
            assert client.post("/api/print/test").status_code == 503
