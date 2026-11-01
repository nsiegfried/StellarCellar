from datetime import date

RANK = {"drink_soon": 3, "ready": 2, "unknown": 1}


def window_status(drink_from, drink_to, today: date, soon_months: int = 6) -> str:
    """Status for a bottle in the cellar, computed from today's date."""
    if drink_from is None and drink_to is None:
        return "unknown"
    if drink_from is not None and today.year < drink_from:
        return "too_young"
    if drink_to is not None:
        if today.year > drink_to:
            return "past_peak"
        months_left = (date(drink_to, 12, 31) - today).days / 30.44
        if months_left <= soon_months:
            return "drink_soon"
    return "ready"


def effective_price(est_value, purchase_price):
    return est_value if est_value is not None else purchase_price


def _best(items, n=3):
    """Window status first, then the household's own score, then the sooner-closing window."""
    return sorted(items, key=lambda c: (-RANK.get(c["status"], 0), -(c.get("score") or 0),
                                        c["window_to"] or 9999))[:n]


def dedupe_by_wine(cands):
    """One entry per wine label, carrying how many bottles share it."""
    seen = {}
    for c in cands:
        key = c["label"]
        if key in seen:
            seen[key]["count"] += 1
            if RANK.get(c["status"], 0) > RANK.get(seen[key]["status"], 0):
                seen[key].update({k: v for k, v in c.items() if k != "count"})
        else:
            seen[key] = {**c, "count": 1}
    return list(seen.values())


def build_picks(cands, low, high, n=3):
    """Rule-based picks. No AI call, so opening the page is free and predictable."""
    cands = dedupe_by_wine(cands)
    tier_ok = [c for c in cands if c["status"] in ("ready", "drink_soon", "unknown")]
    priced = [c for c in tier_ok if c["price"] is not None]
    under_low = [c for c in priced if c["price"] <= low]
    under_high = [c for c in priced if c["price"] <= high]
    special = [
        c for c in cands
        if c["status"] == "ready"
        and (c["special"] or (c["price"] is not None and c["price"] >= high))
    ]
    use_soon = [c for c in cands if c["status"] == "drink_soon"]
    hold = [c for c in cands if c["status"] == "too_young"]
    return {
        "under_low": _best(under_low, n),
        "under_high": _best(under_high, n),
        "special": sorted(special, key=lambda c: (-(c.get("score") or 0), -(c["window_to"] or 0)))[:n],
        "use_soon": sorted(use_soon, key=lambda c: c["window_to"] or 9999)[: n * 2],
        "hold": sorted(hold, key=lambda c: c["window_from"] or 9999)[: n * 2],
    }
