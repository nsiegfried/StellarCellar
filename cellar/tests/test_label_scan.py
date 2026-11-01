import os
import tempfile
from unittest.mock import MagicMock, patch

os.environ["CELLAR_DATA_DIR"] = tempfile.mkdtemp()

from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.main import app  # noqa: E402

REPLY = ('{"producer":"Ridge","name":"Monte Bello","vintage":"2018","color":"red","region":"Santa Cruz Mountains",'
         '"country":"USA","varietals":["Cabernet Sauvignon"],"drink_from":2026,"drink_to":"2045",'
         '"confidence":"high","note":""}')


def test_read_label_normalises_types():
    fake = MagicMock(); fake.content = [MagicMock(text=REPLY)]
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), patch("anthropic.Anthropic") as A:
        A.return_value.messages.create.return_value = fake
        data, err = ai.read_label("QUJD", "image/jpeg")
    assert err is None
    assert data["vintage"] == 2018 and data["drink_to"] == 2045 and data["color"] == "red"
    assert data["varietals"] == ["Cabernet Sauvignon"]


def test_read_label_without_key_and_bad_reply():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        assert ai.read_label("QUJD", "image/jpeg")[1].startswith("Label scanning needs")
    fake = MagicMock(); fake.content = [MagicMock(text="not json")]
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), patch("anthropic.Anthropic") as A:
        A.return_value.messages.create.return_value = fake
        data, err = ai.read_label("QUJD", "image/jpeg")
    assert data is None and "Could not read" in err


def test_scan_label_endpoint():
    with TestClient(app) as client:
        assert client.post("/api/scan-label", json={"image": "QUJD", "media_type": "image/gif"}).status_code == 400
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            assert client.post("/api/scan-label", json={"image": "QUJD"}).status_code == 503
        fake = MagicMock(); fake.content = [MagicMock(text=REPLY)]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), patch("anthropic.Anthropic") as A:
            A.return_value.messages.create.return_value = fake
            r = client.post("/api/scan-label", json={"image": "QUJD"})
        assert r.status_code == 200 and r.json()["producer"] == "Ridge"
