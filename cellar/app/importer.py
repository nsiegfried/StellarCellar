"""Import the ChatGPT wine-profile export (wine_profile.json) into Stellar Cellar."""
import re
from typing import Optional

from sqlmodel import Session, select

from .models import Bottle, DrinkingWindow, Tasting, Wine, WishItem

STOP = {"cabernet", "sauvignon", "pinot", "noir", "the", "wine", "cellars", "vineyard", "vineyards",
        "estate", "napa", "valley", "reserve", "red", "blend", "de", "del", "la", "le", "di", "of",
        "and", "&", "winery", "wines"}


def _f(v) -> Optional[float]:
    try:
        s = str(v).strip()
        return float(s) if s and s.lower() not in ("nan", "none", "unknown") else None
    except ValueError:
        return None


def _year(v) -> Optional[int]:
    f = _f(v)
    return int(f) if f and 1900 < f < 2100 else None


def _tokens(s: str) -> set:
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if t not in STOP and len(t) > 1}


def _split_name(text: str):
    """'Williams Selyem Foss Vineyard' -> producer 'Williams Selyem', name 'Foss Vineyard' (best effort)."""
    text = re.sub(r"\s+", " ", text.replace("\u2019", "'")).strip(" ,")
    parts = text.split(" ")
    if len(parts) <= 2:
        return text, ""
    return " ".join(parts[:2]), " ".join(parts[2:])


def _color(text: str, category: str = "") -> str:
    t = (text + " " + category).lower()
    if any(k in t for k in ("sauvignon blanc", "riesling", "chardonnay", "pinot grigio", "albari", "white")):
        return "white"
    if "rosé" in t or t.endswith(" rose") or " rose " in t:
        return "rose"
    if any(k in t for k in ("champagne", "sparkling", "prosecco", "cava")):
        return "sparkling"
    return "red"


def match_wine(session: Session, text: str, vintage: Optional[int]) -> Optional[Wine]:
    """Find an existing wine whose words overlap strongly with the tasting text."""
    want = _tokens(text)
    if not want:
        return None
    best, best_score = None, 0.0
    for w in session.exec(select(Wine)).all():
        have = _tokens(f"{w.producer} {w.name}")
        if not have:
            continue
        overlap = len(want & have) / min(len(want), len(have))
        if vintage and w.vintage and vintage != w.vintage:
            overlap -= 0.25
        if overlap > best_score:
            best, best_score = w, overlap
    return best if best_score >= 0.75 else None


def import_profile(session: Session, data: dict, holding_location_id: int, new_code,
                   taster: str = "Jen", currency: str = "USD") -> dict:
    tables = data.get("tables", data)
    report = {"wines": 0, "bottles": 0, "incoming": 0, "tastings": 0, "linked_tastings": 0,
              "wishlist": 0, "skipped": []}

    # 1. master cellar -> wines, windows, bottles
    for row in tables.get("master_cellar", []):
        text = (row.get("Wine") or "").strip()
        if not text:
            continue
        vintage = _year(row.get("Vintage"))
        category = row.get("Category") or ""
        producer, name = _split_name(text)
        wine = session.exec(select(Wine).where(Wine.producer == producer, Wine.name == name,
                                               Wine.vintage == vintage)).first()
        if not wine:
            wine = Wine(producer=producer, name=name, vintage=vintage, color=_color(text, category),
                        special_occasion=(row.get("Tier") or "").startswith("A") or "Collector" in category,
                        notes=(row.get("Notes") or "").strip())
            session.add(wine)
            session.commit()
            session.refresh(wine)
            report["wines"] += 1
        d_from, d_to = _year(row.get("Drink From")), _year(row.get("Drink To"))
        if (d_from or d_to) and not session.exec(select(DrinkingWindow).where(DrinkingWindow.wine_id == wine.id)).first():
            session.add(DrinkingWindow(wine_id=wine.id, drink_from=d_from, drink_to=d_to,
                                       source="import", approved=True))
        qty = int(_f(row.get("Qty")) or 1)
        incoming = "incoming" in category.lower()
        tags = ",".join(t for t in [(row.get("Tier") or "").strip(), category.strip().replace(" / ", "/")] if t)
        for _ in range(max(1, min(qty, 24))):
            session.add(Bottle(code=new_code(session, "B", Bottle), wine_id=wine.id,
                               location_id=holding_location_id, est_value=_f(row.get("Est Value")),
                               currency=currency, status="incoming" if incoming else "in_cellar", tags=tags))
            report["incoming" if incoming else "bottles"] += 1
        session.commit()

    # 2. rating history, keyed by wine text
    history = {}
    for r in tables.get("rating_history", []):
        key = _tokens(r.get("wine") or "")
        prev, cur = _f(r.get("previous_score")), _f(r.get("current_score"))
        history[frozenset(key)] = f"{prev:g} -> {cur:g}: " if prev and cur else f"{prev:g} -> unscored: " if prev else ""
        history[frozenset(key)] += (r.get("notes") or "").strip()

    # 3. tastings
    for row in tables.get("tastings", []):
        text = (row.get("Wine") or "").strip()
        if not text:
            continue
        vintage = _year(row.get("Vintage"))
        score = _f(row.get("Personal Score"))
        band = (row.get("Rating Band") or "").strip()
        notes = (row.get("Tasting Notes") or "").strip()
        verdict = ""
        low = (band + " " + notes).lower()
        if score is None:
            verdict = "favorite" if "favorite" in low or "love" in low else "dislike" if "dislike" in low or "not like" in low or "wasn't good" in low else "ok"
        elif score >= 90:
            band = band or "Exceptional"
        wine = match_wine(session, text, vintage)
        hist = next((h for k, h in history.items() if k and k <= _tokens(text)), "")
        session.add(Tasting(wine_id=wine.id if wine else None, wine_text=text, vintage=vintage,
                            taster=taster, score=score, band=band, verdict=verdict, notes=notes,
                            where=(row.get("Inventory / Context") or "").strip(), source="import",
                            history=hist))
        report["tastings"] += 1
        report["linked_tastings"] += 1 if wine else 0
    session.commit()

    # 4. wishlist + buy list
    for r in tables.get("collector_wishlist", []):
        text = (r.get("Wine / Goal") or "").strip()
        if not text:
            continue
        status = (r.get("Status") or "").lower()
        session.add(WishItem(wine_text=text, notes=(r.get("Notes") or "").strip(),
                             status="purchased" if "purchased" in status else "wishlist"))
        report["wishlist"] += 1
    for r in tables.get("buy_list", []):
        text = (r.get("Wine") or "").strip()
        if text:
            session.add(WishItem(wine_text=text, vintage=_year(r.get("Vintage")), status="buy_again",
                                 reference_price=_f(r.get("Reference Value")),
                                 notes=(r.get("Why It Qualifies") or "").strip()))
            report["wishlist"] += 1
    session.commit()

    # 5. taste profile text (palate_profile + preference facts) -> one string for Claude
    lines = [f"{r.get('Dimension')}: {r.get('Current Calibration')}" for r in tables.get("palate_profile", [])]
    lines += [f"{r.get('subject')}: {r.get('detail')}" for r in tables.get("supplemental_facts", [])
              if (r.get("category") or "").startswith(("preference", "serving"))]
    report["taste_profile"] = "\n".join(l for l in lines if l)
    return report
