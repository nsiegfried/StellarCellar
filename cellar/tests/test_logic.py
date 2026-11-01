from datetime import date

from app.logic import build_picks, window_status

TODAY = date(2026, 11, 1)


def test_window_status():
    assert window_status(None, None, TODAY) == "unknown"
    assert window_status(2028, 2035, TODAY) == "too_young"
    assert window_status(2020, 2025, TODAY) == "past_peak"
    assert window_status(2020, 2026, TODAY) == "drink_soon"   # closes in 2 months
    assert window_status(2020, 2027, TODAY) == "ready"        # 14 months left
    assert window_status(2020, None, TODAY) == "ready"        # open-ended window


def c(code, status, price, special=False, wto=None, wfrom=None, score=None, label=None):
    return {"code": code, "status": status, "price": price, "special": special,
            "window_to": wto, "window_from": wfrom, "score": score, "label": label or code}


def test_build_picks_tiers():
    cands = [
        c("B1", "ready", 30), c("B2", "drink_soon", 45, wto=2026), c("B3", "ready", 80),
        c("B4", "ready", 150), c("B5", "ready", 20, special=True), c("B6", "past_peak", 10),
        c("B7", "too_young", 60, wfrom=2029), c("B8", "unknown", None),
    ]
    p = build_picks(cands, 50, 100)
    assert [x["code"] for x in p["under_low"]] == ["B2", "B1", "B5"]   # drink_soon ranks first
    assert len(p["under_high"]) == 3 and "B4" not in [x["code"] for x in p["under_high"]]
    assert {x["code"] for x in p["special"]} == {"B4", "B5"}          # over high tier or flagged
    assert [x["code"] for x in p["use_soon"]] == ["B2"]
    assert [x["code"] for x in p["hold"]] == ["B7"]
    assert "B6" not in [x["code"] for tier in p.values() if isinstance(tier, list) for x in tier]


def test_picks_dedupe_and_score():
    cands = [c("B1", "ready", 60, score=96, label="Jessup"), c("B2", "ready", 60, score=96, label="Jessup"),
             c("B3", "ready", 40, label="Malbec"), c("B4", "ready", 45, score=88, label="Kirkland")]
    p = build_picks(cands, 50, 100)
    assert [x["label"] for x in p["under_high"]] == ["Jessup", "Kirkland", "Malbec"]
    assert p["under_high"][0]["count"] == 2
    assert [x["label"] for x in p["under_low"]] == ["Kirkland", "Malbec"]
