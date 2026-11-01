import html
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, delete as sql_delete, select

from . import ai, importer, qr
from .db import get_session, init_db
from .logic import build_picks, effective_price, window_status
from .models import Bottle, Consumption, DrinkingWindow, Location, Setting, Tasting, Wine, WishItem, utcnow

SOON = int(os.environ.get("CELLAR_DRINK_SOON_MONTHS", "6"))
LOW = float(os.environ.get("CELLAR_PRICE_TIER_LOW", "50"))
HIGH = float(os.environ.get("CELLAR_PRICE_TIER_HIGH", "100"))
CURRENCY = os.environ.get("CELLAR_CURRENCY", "USD")
PUBLIC_BASE = os.environ.get("CELLAR_PUBLIC_BASE", "").strip().rstrip("/")
UNDO_SECONDS = 10
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no I, L, O, U


HOLDING_CODE = "LNEWARR"   # built-in holding place for bottles without a decided spot


@asynccontextmanager
async def lifespan(_app):
    init_db()
    from .db import engine
    with Session(engine) as session:
        holding = session.exec(select(Location).where(Location.code == HOLDING_CODE)).first()
        if not holding:
            holding = Location(name="New arrivals", code=HOLDING_CODE)
            session.add(holding)
            session.commit()
            session.refresh(holding)
        # bottles saved before the holding place existed ("Decide later" left them nowhere)
        for b in session.exec(select(Bottle).where(Bottle.location_id == None)).all():  # noqa: E711
            b.location_id = holding.id
            session.add(b)
        session.commit()
    yield


app = FastAPI(title="Stellar Cellar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def collapse_slashes(request: Request, call_next):
    """Home Assistant Ingress can hand us paths like '//' or '//api/picks'. Normalise them."""
    path = request.scope.get("path", "")
    if "//" in path:
        request.scope["path"] = re.sub(r"/{2,}", "/", path)
        request.scope["raw_path"] = request.scope["path"].encode()
    return await call_next(request)


# ---------- helpers ----------

def new_code(session: Session, prefix: str, model) -> str:
    while True:
        code = prefix + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not session.exec(select(model).where(model.code == code)).first():
            return code


def wine_label(w: Wine) -> str:
    parts = [w.producer, w.name, str(w.vintage) if w.vintage else "NV"]
    return " ".join(p for p in parts if p)


def best_window(session: Session, wine_id: int) -> Optional[DrinkingWindow]:
    return session.exec(
        select(DrinkingWindow)
        .where(DrinkingWindow.wine_id == wine_id, DrinkingWindow.approved == True)  # noqa: E712
        .order_by(DrinkingWindow.id.desc())
    ).first()


def candidate(session: Session, b: Bottle) -> dict:
    wine = session.get(Wine, b.wine_id)
    loc = session.get(Location, b.location_id) if b.location_id else None
    win = best_window(session, wine.id)
    status = window_status(
        win.drink_from if win else None,
        win.drink_to if win else None,
        date.today(),
        SOON,
    ) if b.status == "in_cellar" else b.status
    best = session.exec(
        select(Tasting).where(Tasting.wine_id == wine.id, Tasting.score != None)  # noqa: E711
        .order_by(Tasting.id.desc())
    ).first()
    return {
        "code": b.code,
        "label": wine_label(wine),
        "status": status,
        "score": best.score if best else None,
        "taster": best.taster if best else None,
        "price": effective_price(b.est_value, b.purchase_price),
        "special": wine.special_occasion,
        "color": wine.color,
        "window_from": win.drink_from if win else None,
        "window_to": win.drink_to if win else None,
        "location": loc.name if loc else "Unplaced",
    }


def get_bottle_or_404(session: Session, code: str) -> Bottle:
    b = session.exec(select(Bottle).where(Bottle.code == code.upper())).first()
    if not b:
        raise HTTPException(404, "Bottle not found")
    return b


def get_location_or_404(session: Session, code: str) -> Location:
    loc = session.exec(select(Location).where(Location.code == code.upper())).first()
    if not loc:
        raise HTTPException(404, "Place not found")
    return loc


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row else default


def set_setting(session: Session, key: str, value: str):
    row = session.get(Setting, key) or Setting(key=key)
    row.value = value
    session.add(row)


def journal_for_wine(session: Session, wine_id: int) -> list:
    rows = session.exec(select(Tasting).where(Tasting.wine_id == wine_id).order_by(Tasting.id.desc())).all()
    return [tasting_out(t) for t in rows]


def tasting_out(t: Tasting) -> dict:
    return {"id": t.id, "wine_id": t.wine_id, "wine": t.wine_text, "vintage": t.vintage, "taster": t.taster,
            "score": t.score, "band": t.band, "verdict": t.verdict, "notes": t.notes, "where": t.where,
            "tasted_on": t.tasted_on.isoformat() if t.tasted_on else None, "history": t.history}


def in_cellar(session: Session):
    return session.exec(select(Bottle).where(Bottle.status == "in_cellar")).all()


# ---------- request bodies ----------

class AddBottles(BaseModel):
    producer: str
    name: str = ""
    vintage: Optional[int] = None
    color: str = "red"
    region: str = ""
    country: str = ""
    special_occasion: bool = False
    quantity: int = 1
    purchase_price: Optional[float] = None
    est_value: Optional[float] = None
    drink_from: Optional[int] = None
    drink_to: Optional[int] = None
    location_code: Optional[str] = None


class DrinkBody(BaseModel):
    rating: Optional[int] = None
    note: str = ""


class MoveBody(BaseModel):
    location_code: str


class MoveBatchBody(BaseModel):
    bottle_codes: List[str]
    location_code: str


class LocationBody(BaseModel):
    name: str


class LabelScanBody(BaseModel):
    image: str          # base64, no data: prefix
    media_type: str = "image/jpeg"


class RecommendBody(BaseModel):
    food: str = ""
    occasion: str = ""
    budget_max: Optional[float] = None


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/q/{code}", response_class=HTMLResponse)
def qr_landing(code: str):
    """Target of every printed label. Sends the phone to the right screen in the app."""
    code = code.upper()
    if code.startswith("B"):
        kind = "bottle"
    elif code.startswith("L"):
        kind = "location"
    else:
        raise HTTPException(404, "Unknown label")
    target = f"../#/{kind}/{code}"
    return HTMLResponse(
        '<!doctype html><meta charset="utf-8">'
        f'<script>location.replace("{target}")</script>'
        f'<a href="{target}">Open in Cellar</a>'
    )


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------- summary, search, picks ----------

@app.get("/api/summary")
def summary(session: Session = Depends(get_session)):
    bottles = in_cellar(session)
    counts = {"too_young": 0, "ready": 0, "drink_soon": 0, "past_peak": 0, "unknown": 0}
    value = 0.0
    for b in bottles:
        c = candidate(session, b)
        counts[c["status"]] = counts.get(c["status"], 0) + 1
        value += c["price"] or 0
    return {"total": len(bottles), **counts, "value": round(value, 2), "currency": CURRENCY}


@app.get("/api/search")
def search(q: str = "", session: Session = Depends(get_session)):
    term = q.strip().lower()
    if not term:
        return []
    like = f"%{term}%"
    wines = session.exec(
        select(Wine).where(
            func.lower(Wine.producer).like(like) | func.lower(Wine.name).like(like)
        )
    ).all()
    ids = [w.id for w in wines]
    if not ids:
        return []
    bottles = session.exec(
        select(Bottle).where(Bottle.wine_id.in_(ids), Bottle.status == "in_cellar")
    ).all()
    return [candidate(session, b) for b in bottles]


@app.get("/api/bottles")
def list_bottles(status: str = "all", session: Session = Depends(get_session)):
    """Every bottle in the cellar, optionally filtered by window status (ready, drink_soon, too_young, past_peak, unknown)."""
    cands = [candidate(session, b) for b in in_cellar(session)]
    if status != "all":
        cands = [c for c in cands if c["status"] == status]
    order = {"drink_soon": 0, "ready": 1, "unknown": 2, "too_young": 3, "past_peak": 4}
    cands.sort(key=lambda c: (order.get(c["status"], 9), -(c.get("score") or 0), c["label"]))
    return cands


@app.get("/api/picks")
def picks(session: Session = Depends(get_session)):
    cands = [candidate(session, b) for b in in_cellar(session)]
    return {**build_picks(cands, LOW, HIGH), "thresholds": {"low": LOW, "high": HIGH},
            "currency": CURRENCY}


@app.post("/api/recommend")
def recommend(body: RecommendBody, session: Session = Depends(get_session)):
    cands = [c for c in (candidate(session, b) for b in in_cellar(session))
             if c["status"] in ("ready", "drink_soon")]
    if body.budget_max is not None:
        cands = [c for c in cands if c["price"] is None or c["price"] <= body.budget_max]
    return ai.recommend(body.food, body.occasion, cands, get_setting(session, "taste_profile"))


@app.post("/api/scan-label")
def scan_label(body: LabelScanBody):
    if body.media_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "Send a JPEG, PNG or WebP image")
    if len(body.image) > 7_000_000:  # ~5 MB decoded; the phone downsizes first
        raise HTTPException(413, "Image too large")
    data, err = ai.read_label(body.image, body.media_type)
    if err:
        raise HTTPException(503, err)
    return data


# ---------- bottles ----------

@app.post("/api/bottles")
def add_bottles(body: AddBottles, session: Session = Depends(get_session)):
    if not 1 <= body.quantity <= 24:
        raise HTTPException(400, "Quantity must be between 1 and 24")
    if not body.producer.strip():
        raise HTTPException(400, "Producer is required")
    loc = get_location_or_404(session, body.location_code or HOLDING_CODE)

    wine = session.exec(
        select(Wine).where(
            Wine.producer == body.producer.strip(),
            Wine.name == body.name.strip(),
            Wine.vintage == body.vintage,
        )
    ).first()
    if not wine:
        wine = Wine(
            producer=body.producer.strip(), name=body.name.strip(), vintage=body.vintage,
            color=body.color, region=body.region, country=body.country,
            special_occasion=body.special_occasion,
        )
        session.add(wine)
        session.commit()
        session.refresh(wine)
    if body.drink_from is not None or body.drink_to is not None:
        session.add(DrinkingWindow(wine_id=wine.id, drink_from=body.drink_from,
                                   drink_to=body.drink_to, source="owner", approved=True))

    label = wine_label(wine)
    wine_id = wine.id
    codes = []
    for _ in range(body.quantity):
        code = new_code(session, "B", Bottle)
        session.add(Bottle(code=code, wine_id=wine_id, location_id=loc.id,
                           purchase_price=body.purchase_price, est_value=body.est_value,
                           currency=CURRENCY))
        codes.append(code)
    session.commit()
    return {"wine": label, "codes": codes}


@app.get("/api/bottle/{code}")
def bottle_detail(code: str, session: Session = Depends(get_session)):
    b = get_bottle_or_404(session, code)
    wine = session.get(Wine, b.wine_id)
    win = best_window(session, wine.id)
    history = session.exec(
        select(Consumption).where(Consumption.bottle_id == b.id, Consumption.undone == False)  # noqa: E712
        .order_by(Consumption.id.desc())
    ).all()
    siblings = len(session.exec(
        select(Bottle).where(Bottle.wine_id == wine.id, Bottle.status == "in_cellar")
    ).all())
    return {
        **candidate(session, b),
        "wine_id": wine.id, "siblings_in_cellar": siblings, "tags": b.tags,
        "bottle_status": b.status, "wine_notes": wine.notes,
        "journal": journal_for_wine(session, wine.id),
        "producer": wine.producer, "name": wine.name, "vintage": wine.vintage,
        "region": wine.region, "country": wine.country, "color": wine.color,
        "special_occasion": wine.special_occasion,
        "window_source": win.source if win else None,
        "purchase_price": b.purchase_price, "est_value": b.est_value, "currency": b.currency,
        "added_at": b.added_at.isoformat(),
        "history": [{"when": h.occurred_at.isoformat(), "rating": h.rating, "note": h.note}
                    for h in history],
    }


@app.post("/api/bottle/{code}/drink")
def drink(code: str, body: DrinkBody = DrinkBody(), session: Session = Depends(get_session)):
    b = get_bottle_or_404(session, code)
    if b.status != "in_cellar":
        raise HTTPException(409, "This bottle is not in the cellar")
    now = utcnow()
    b.status = "consumed"
    b.consumed_at = now
    session.add(b)
    session.add(Consumption(bottle_id=b.id, occurred_at=now, rating=body.rating, note=body.note))
    session.commit()
    wine = session.get(Wine, b.wine_id)
    return {"ok": True, "label": wine_label(wine), "undo_seconds": UNDO_SECONDS}


@app.post("/api/bottle/{code}/undo")
def undo_drink(code: str, session: Session = Depends(get_session)):
    b = get_bottle_or_404(session, code)
    last = session.exec(
        select(Consumption).where(Consumption.bottle_id == b.id, Consumption.undone == False)  # noqa: E712
        .order_by(Consumption.id.desc())
    ).first()
    if (not last or b.status != "consumed"
            or utcnow() - last.occurred_at > timedelta(seconds=UNDO_SECONDS)):
        raise HTTPException(409, "Nothing to undo")
    last.undone = True
    b.status = "in_cellar"
    b.consumed_at = None
    session.add_all([last, b])
    session.commit()
    return {"ok": True}


@app.post("/api/bottle/{code}/move")
def move(code: str, body: MoveBody, session: Session = Depends(get_session)):
    b = get_bottle_or_404(session, code)
    loc = get_location_or_404(session, body.location_code)
    b.location_id = loc.id
    session.add(b)
    session.commit()
    return {"ok": True, "location": loc.name}


@app.post("/api/move-batch")
def move_batch(body: MoveBatchBody, session: Session = Depends(get_session)):
    loc = get_location_or_404(session, body.location_code)
    moved = []
    for raw in body.bottle_codes:
        b = session.exec(select(Bottle).where(Bottle.code == raw.upper())).first()
        if b:
            b.location_id = loc.id
            session.add(b)
            moved.append(b.code)
    session.commit()
    return {"ok": True, "moved": moved, "location": loc.name}


@app.delete("/api/bottle/{code}")
def delete_bottle(code: str, session: Session = Depends(get_session)):
    b = get_bottle_or_404(session, code)
    wine_id = b.wine_id
    session.exec(sql_delete(Consumption).where(Consumption.bottle_id == b.id))
    session.delete(b)
    session.commit()
    _prune_wine(session, wine_id)
    return {"ok": True}


@app.delete("/api/wine/{wine_id}/bottles")
def delete_wine_bottles(wine_id: int, session: Session = Depends(get_session)):
    """Remove every unopened bottle of a wine (for accidental duplicates)."""
    bottles = session.exec(
        select(Bottle).where(Bottle.wine_id == wine_id, Bottle.status == "in_cellar")
    ).all()
    for b in bottles:
        session.exec(sql_delete(Consumption).where(Consumption.bottle_id == b.id))
        session.delete(b)
    session.commit()
    _prune_wine(session, wine_id)
    return {"ok": True, "deleted": len(bottles)}


def _prune_wine(session: Session, wine_id: int):
    """Drop a wine record once it has no bottles at all (so it stops appearing in search)."""
    if not session.exec(select(Bottle).where(Bottle.wine_id == wine_id)).first():
        session.exec(sql_delete(DrinkingWindow).where(DrinkingWindow.wine_id == wine_id))
        for t in session.exec(select(Tasting).where(Tasting.wine_id == wine_id)).all():
            t.wine_id = None          # journal entries outlive the cellar record
            session.add(t)
        wine = session.get(Wine, wine_id)
        if wine:
            session.delete(wine)
        session.commit()


# ---------- journal and wish list ----------

class TastingBody(BaseModel):
    wine: str
    vintage: Optional[int] = None
    taster: str = ""
    score: Optional[float] = None
    notes: str = ""
    where: str = ""
    verdict: str = ""
    bottle_code: Optional[str] = None   # link to a cellar wine via one of its bottles


def band_for(score: Optional[float]) -> str:
    if score is None:
        return ""
    return "Exceptional" if score >= 90 else "Strong" if score >= 86 else "Good" if score >= 80 else "Drinkable" if score >= 70 else "No"


@app.get("/api/journal")
def journal(q: str = "", session: Session = Depends(get_session)):
    rows = session.exec(select(Tasting).order_by(Tasting.score.desc().nullslast(), Tasting.id.desc())).all()
    term = q.strip().lower()
    out = [tasting_out(t) for t in rows if not term or term in (t.wine_text + " " + t.notes).lower()]
    return out


@app.post("/api/journal")
def add_tasting(body: TastingBody, session: Session = Depends(get_session)):
    if not body.wine.strip():
        raise HTTPException(400, "Wine is required")
    if body.score is not None and not 0 <= body.score <= 100:
        raise HTTPException(400, "Score is on a 100-point scale")
    wine = None
    if body.bottle_code:
        wine = session.get(Wine, get_bottle_or_404(session, body.bottle_code).wine_id)
    else:
        wine = importer.match_wine(session, body.wine, body.vintage)
    t = Tasting(wine_id=wine.id if wine else None, wine_text=body.wine.strip(), vintage=body.vintage,
                taster=body.taster.strip() or get_setting(session, "default_taster", ""),
                score=body.score, band=band_for(body.score), verdict=body.verdict, notes=body.notes.strip(),
                where=body.where.strip(), tasted_on=utcnow())
    session.add(t)
    session.commit()
    session.refresh(t)
    return tasting_out(t)


@app.post("/api/journal/relink")
def relink_journal(session: Session = Depends(get_session)):
    """Re-run wine matching for every journal entry. Manual links (via bottle code) are kept."""
    changed = 0
    for t in session.exec(select(Tasting)).all():
        if t.source != "import" and t.wine_id is not None:
            continue
        w = importer.match_wine(session, t.wine_text, t.vintage)
        new_id = w.id if w else None
        if new_id != t.wine_id:
            t.wine_id = new_id
            session.add(t)
            changed += 1
    session.commit()
    linked = len(session.exec(select(Tasting).where(Tasting.wine_id != None)).all())  # noqa: E711
    return {"changed": changed, "linked": linked}


@app.delete("/api/journal/{tasting_id}")
def delete_tasting(tasting_id: int, session: Session = Depends(get_session)):
    t = session.get(Tasting, tasting_id)
    if not t:
        raise HTTPException(404, "Not found")
    session.delete(t)
    session.commit()
    return {"ok": True}


@app.get("/api/wishlist")
def wishlist(session: Session = Depends(get_session)):
    rows = session.exec(select(WishItem).order_by(WishItem.status, WishItem.id)).all()
    return [{"id": w.id, "wine": w.wine_text, "vintage": w.vintage, "status": w.status, "notes": w.notes,
             "reference_price": w.reference_price} for w in rows]


class WishBody(BaseModel):
    wine: str
    vintage: Optional[int] = None
    status: str = "wishlist"
    notes: str = ""


@app.post("/api/wishlist")
def add_wish(body: WishBody, session: Session = Depends(get_session)):
    w = WishItem(wine_text=body.wine.strip(), vintage=body.vintage, status=body.status, notes=body.notes)
    session.add(w)
    session.commit()
    return {"ok": True, "id": w.id}


@app.delete("/api/wishlist/{item_id}")
def delete_wish(item_id: int, session: Session = Depends(get_session)):
    w = session.get(WishItem, item_id)
    if not w:
        raise HTTPException(404, "Not found")
    session.delete(w)
    session.commit()
    return {"ok": True}


@app.get("/api/incoming")
def incoming(session: Session = Depends(get_session)):
    rows = session.exec(select(Bottle).where(Bottle.status == "incoming")).all()
    return [candidate(session, b) for b in rows]


@app.post("/api/bottle/{code}/arrived")
def arrived(code: str, session: Session = Depends(get_session)):
    b = get_bottle_or_404(session, code)
    if b.status != "incoming":
        raise HTTPException(409, "This bottle is not marked incoming")
    b.status = "in_cellar"
    session.add(b)
    session.commit()
    return {"ok": True}


# ---------- import ----------

class ImportBody(BaseModel):
    data: dict
    taster: str = "Jen"


@app.post("/api/import/wine-profile")
def import_wine_profile(body: ImportBody, session: Session = Depends(get_session)):
    """One-shot import of the ChatGPT wine_profile.json export."""
    if session.exec(select(Tasting).where(Tasting.source == "import")).first():
        raise HTTPException(409, "This export has already been imported")
    holding = session.exec(select(Location).where(Location.code == HOLDING_CODE)).first()
    report = importer.import_profile(session, body.data, holding.id, new_code, taster=body.taster,
                                     currency=CURRENCY)
    if report.get("taste_profile"):
        set_setting(session, "taste_profile", report["taste_profile"])
    set_setting(session, "default_taster", body.taster)
    session.commit()
    return report


# ---------- settings ----------

class SettingsBody(BaseModel):
    default_location_code: Optional[str] = None
    taste_profile: Optional[str] = None
    default_taster: Optional[str] = None


@app.get("/api/settings")
def read_settings(session: Session = Depends(get_session)):
    return {
        "default_location_code": get_setting(session, "default_location_code", HOLDING_CODE),
        "taste_profile": get_setting(session, "taste_profile", ""),
        "default_taster": get_setting(session, "default_taster", ""),
        "holding_code": HOLDING_CODE,
        "currency": CURRENCY, "drink_soon_months": SOON,
        "price_tier_low": LOW, "price_tier_high": HIGH,
    }


@app.post("/api/settings")
def write_settings(body: SettingsBody, session: Session = Depends(get_session)):
    if body.default_location_code is not None:
        get_location_or_404(session, body.default_location_code)
        set_setting(session, "default_location_code", body.default_location_code.upper())
    if body.taste_profile is not None:
        set_setting(session, "taste_profile", body.taste_profile.strip()[:6000])
    if body.default_taster is not None:
        set_setting(session, "default_taster", body.default_taster.strip()[:40])
    session.commit()
    return read_settings(session)


# ---------- locations ----------

@app.get("/api/locations")
def list_locations(session: Session = Depends(get_session)):
    out = []
    for loc in session.exec(select(Location).order_by(Location.name)).all():
        count = len(session.exec(
            select(Bottle).where(Bottle.location_id == loc.id, Bottle.status == "in_cellar")
        ).all())
        out.append({"code": loc.code, "name": loc.name, "count": count,
                    "holding": loc.code == HOLDING_CODE})
    return out


@app.post("/api/locations")
def create_location(body: LocationBody, session: Session = Depends(get_session)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    loc = Location(name=name, code=new_code(session, "L", Location))
    session.add(loc)
    session.commit()
    return {"code": loc.code, "name": loc.name}


@app.get("/api/location/{code}")
def location_detail(code: str, session: Session = Depends(get_session)):
    loc = get_location_or_404(session, code)
    bottles = session.exec(
        select(Bottle).where(Bottle.location_id == loc.id, Bottle.status == "in_cellar")
    ).all()
    return {"code": loc.code, "name": loc.name,
            "bottles": [candidate(session, b) for b in bottles]}


# ---------- printable labels ----------

LABEL_FORMATS = {
    # full: URL in the QR, scannable by the phone's own camera once the public route exists
    "full": {"qr_mm": 22, "w_mm": 40, "h_mm": 30, "font_pt": 8, "border": 4, "payload": "url"},
    # compact: bare code in the QR, in-app scanner only, smallest reliable size
    "compact": {"qr_mm": 12, "w_mm": 20, "h_mm": 14, "font_pt": 5, "border": 2, "payload": "code"},
    # neck: fits Brother DK-11204 17 x 54 mm die-cut strips, wraps the bottle neck
    "neck": {"qr_mm": 13, "w_mm": 54, "h_mm": 17, "font_pt": 7, "border": 2, "payload": "code"},
}


@app.get("/api/labels", response_class=HTMLResponse)
def labels(codes: str, request: Request, fmt: str = "compact",
           session: Session = Depends(get_session)):
    if fmt not in LABEL_FORMATS:
        raise HTTPException(400, f"fmt must be one of {', '.join(LABEL_FORMATS)}")
    f = LABEL_FORMATS[fmt]
    base = PUBLIC_BASE or str(request.base_url).rstrip("/")
    cards = []
    for raw in codes.split(","):
        code = raw.strip().upper()
        if not code:
            continue
        if code.startswith("B"):
            obj = session.exec(select(Bottle).where(Bottle.code == code)).first()
            title = wine_label(session.get(Wine, obj.wine_id)) if obj else None
        elif code.startswith("L"):
            obj = session.exec(select(Location).where(Location.code == code)).first()
            title = obj.name if obj else None
        else:
            raise HTTPException(400, f"Unknown code {code}")
        if not obj:
            raise HTTPException(404, f"Code {code} not found")
        payload = f"{base}/q/{code}" if f["payload"] == "url" else code
        cards.append(
            f'<div class="label" data-payload="{html.escape(payload)}">'
            f'<div class="qr">{qr.svg(payload, border=f["border"])}</div>'
            f'<div class="text"><div class="code">{code}</div>'
            f'<div class="title">{html.escape(title or "")}</div></div>'
            "</div>"
        )
    warning = ""
    if fmt == "full" and not PUBLIC_BASE:
        warning = ('<p class="warn">Set "Public base URL" in the add-on options so these codes '
                   "work when scanned from outside your home.</p>")
    horizontal = f["w_mm"] > f["h_mm"] * 1.6
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<title>Stellar Cellar labels ({fmt})</title>
<style>
body{{font-family:system-ui,sans-serif;margin:12px}}
.sheet{{display:flex;flex-wrap:wrap;gap:4px}}
.label{{width:{f["w_mm"]}mm;height:{f["h_mm"]}mm;border:1px dashed #bbb;box-sizing:border-box;
  display:flex;flex-direction:{"row" if horizontal else "column"};align-items:center;justify-content:center;
  gap:1.5mm;padding:1mm;overflow:hidden;page-break-inside:avoid}}
.qr svg{{width:{f["qr_mm"]}mm;height:{f["qr_mm"]}mm;display:block}}
.text{{text-align:{"left" if horizontal else "center"};min-width:0}}
.code{{font-family:monospace;font-size:{f["font_pt"]}pt;letter-spacing:.5px}}
.title{{font-size:{f["font_pt"]}pt;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;max-width:{f["w_mm"] - (f["qr_mm"] + 5 if horizontal else 2)}mm}}
.warn{{color:#b00}}
@media print{{body{{margin:0}} .warn,.bar{{display:none}} .label{{border:0}}}}
</style></head><body>
<div class="bar"><button onclick="window.print()">Print</button>
&nbsp; Format: <a href="?codes={html.escape(codes)}&fmt=compact">compact</a> ·
<a href="?codes={html.escape(codes)}&fmt=neck">neck strip</a> ·
<a href="?codes={html.escape(codes)}&fmt=full">full (camera link)</a></div>{warning}
<div class="sheet">{''.join(cards)}</div></body></html>""")
