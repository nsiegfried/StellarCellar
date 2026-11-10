import html
import json
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

from . import ai, importer, qr, regions, zebra
from .db import get_session, init_db
from .logic import build_picks, effective_price, window_status
from .models import Bottle, Consumption, DrinkingWindow, Location, Setting, Tasting, Wine, WishItem, utcnow

SOON = int(os.environ.get("CELLAR_DRINK_SOON_MONTHS", "6"))
LOW = float(os.environ.get("CELLAR_PRICE_TIER_LOW", "50"))
HIGH = float(os.environ.get("CELLAR_PRICE_TIER_HIGH", "100"))
CURRENCY = os.environ.get("CELLAR_CURRENCY", "USD")
PUBLIC_BASE = os.environ.get("CELLAR_PUBLIC_BASE", "").strip().rstrip("/")
UNDO_SECONDS = 10
ZEBRA_HOST = os.environ.get("CELLAR_ZEBRA_HOST", "").strip()
ZEBRA_PORT = int(os.environ.get("CELLAR_ZEBRA_PORT", "9100") or 9100)
ZEBRA_DPI = int(os.environ.get("CELLAR_ZEBRA_DPI", "203") or 203)
ZEBRA_W = float(os.environ.get("CELLAR_ZEBRA_LABEL_W_MM", "25.4") or 25.4)
ZEBRA_H = float(os.environ.get("CELLAR_ZEBRA_LABEL_H_MM", "12.7") or 12.7)
ZEBRA_DARK = int(os.environ.get("CELLAR_ZEBRA_DARKNESS", "20") or 20)
ZEBRA_LAYOUT = os.environ.get("CELLAR_ZEBRA_LAYOUT", "compact").strip() or "compact"
ZEBRA_QR_SCALE = float(os.environ.get("CELLAR_ZEBRA_QR_SCALE", "1.0") or 1.0)
ZEBRA_KW = dict(width_mm=ZEBRA_W, height_mm=ZEBRA_H, dpi=ZEBRA_DPI, darkness=ZEBRA_DARK, qr_scale=ZEBRA_QR_SCALE)
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
        # imported journal entries: the export's inventory notes are bookkeeping, not where it was tasted
        for t in session.exec(select(Tasting).where(Tasting.source == "import", Tasting.where != "")).all():
            t.context = (t.context + " " + t.where).strip() if t.context else t.where
            t.where = ""
            session.add(t)
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
        "reserved_for": b.reserved_for or "",
        "reserved_note": b.reserved_note or "",
        "slot": b.slot or "",
        "location_code": loc.code if loc else None,
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


DEFAULT_FRIDGE = {"order": "top_down", "zones": ["Upper", "Lower"],
                  "racks": [{"name": c, "across": 5, "deep": 2, "zone": 0 if i < 5 else 1} for i, c in enumerate("ABCDEFGHIJK")]}


def parse_layout(loc: Location) -> Optional[dict]:
    if not loc.layout:
        return None
    try:
        return json.loads(loc.layout)
    except ValueError:
        return None


def layout_capacity(loc: Location) -> Optional[int]:
    lay = parse_layout(loc)
    if not lay:
        return None
    return sum(int(r.get("across", 0)) * int(r.get("deep", 1)) for r in lay.get("racks", []))


def validate_layout(lay: dict) -> dict:
    racks = lay.get("racks") or []
    if not 1 <= len(racks) <= 26:
        raise HTTPException(400, "A layout needs 1 to 26 racks")
    zones = [str(z).strip()[:20] for z in (lay.get("zones") or []) if str(z).strip()][:4]
    clean = []
    seen = set()
    for i, r in enumerate(racks):
        name = str(r.get("name") or chr(65 + i)).strip()[:4]
        if name in seen:
            raise HTTPException(400, f"Rack name {name} is used twice")
        seen.add(name)
        across, deep = int(r.get("across", 8)), int(r.get("deep", 1))
        if not 1 <= across <= 30 or not 1 <= deep <= 3:
            raise HTTPException(400, "Each rack holds 1 to 30 across and 1 to 3 deep")
        zone = int(r.get("zone", 0) or 0)
        if zones and not 0 <= zone < len(zones):
            zone = 0
        clean.append({"name": name, "across": across, "deep": deep, "zone": zone,
                      "bulk": bool(r.get("bulk", False))})
    return {"order": lay.get("order", "top_down"), "zones": zones, "racks": clean}


def _racks(n, across, deep, zone=0, start=0, bulk_last=0):
    out = [{"name": chr(65 + start + i), "across": across, "deep": deep, "zone": zone} for i in range(n)]
    if bulk_last:
        out.append({"name": chr(65 + start + n), "across": bulk_last, "deep": 1, "zone": zone, "bulk": True})
    return out


LAYOUT_PRESETS = [
    {"id": "counter12", "name": "Countertop, 12 bottles", "note": "Typical: 4 racks of 3", "zones": [],
     "racks": _racks(4, 3, 1)},
    {"id": "compact24", "name": "Compact, 18 to 24 bottles", "note": "Typical: 6 racks of 4", "zones": [],
     "racks": _racks(6, 4, 1)},
    {"id": "under32", "name": "Under-counter dual zone, 28 to 32", "note": "Typical: 2 upper + 5 lower, 4 to 5 across",
     "zones": ["Upper", "Lower"], "racks": _racks(2, 4, 1, 0) + _racks(5, 4, 1, 1, start=2)},
    {"id": "dual46", "name": "24-inch dual zone, 46 to 52", "note": "Typical: 2 upper + 4 lower, 6 across x 2 deep",
     "zones": ["Upper", "Lower"], "racks": _racks(2, 6, 2, 0) + _racks(4, 6, 2, 1, start=2)},
    {"id": "free98", "name": "Freestanding, 76 to 98", "note": "Typical: 6 to 7 racks, 7 across x 2 deep, bulk floor",
     "zones": [], "racks": _racks(6, 7, 2, 0, bulk_last=12)},
    {"id": "tall116", "name": "Tall dual zone, 116 to 160", "note": "Typical: 5 upper + 6 lower, 5 across x 2 deep",
     "zones": ["Upper", "Lower"], "racks": _racks(5, 5, 2, 0) + _racks(6, 5, 2, 1, start=5)},
    {"id": "wallrack", "name": "Wall rack or cubbies", "note": "Grid, one deep", "zones": [],
     "racks": _racks(6, 6, 1)},
]


@app.get("/api/layout-presets")
def layout_presets():
    return [{**p, "capacity": sum(r["across"] * r["deep"] for r in p["racks"])} for p in LAYOUT_PRESETS]


def known_people(session: Session) -> list:
    """Names used before as gift recipients or holds, for the picklist."""
    names = set()
    for h in session.exec(select(Consumption).where(Consumption.recipient != "")).all():
        names.add(h.recipient)
    for b in session.exec(select(Bottle).where(Bottle.reserved_for != "")).all():
        names.add(b.reserved_for)
    return sorted(names)


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
    value = held_value = 0.0
    held = 0
    for b in bottles:
        c = candidate(session, b)
        counts[c["status"]] = counts.get(c["status"], 0) + 1
        value += c["price"] or 0
        if c["reserved_for"]:
            held += 1
            held_value += c["price"] or 0
    return {"total": len(bottles), **counts, "value": round(value, 2), "held": held,
            "held_value": round(held_value, 2), "own_value": round(value - held_value, 2),
            "currency": CURRENCY}


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
    cands = [c for c in (candidate(session, b) for b in in_cellar(session)) if not c["reserved_for"]]
    return {**build_picks(cands, LOW, HIGH), "thresholds": {"low": LOW, "high": HIGH},
            "currency": CURRENCY}


@app.post("/api/recommend")
def recommend(body: RecommendBody, session: Session = Depends(get_session)):
    cands = [c for c in (candidate(session, b) for b in in_cellar(session))
             if c["status"] in ("ready", "drink_soon") and not c["reserved_for"]]
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
        "history": [{"when": h.occurred_at.isoformat(), "reason": h.reason, "recipient": h.recipient,
                     "occasion": h.occasion, "rating": h.rating, "note": h.note} for h in history],
        "people": known_people(session),
    }


class EditBottle(BaseModel):
    producer: Optional[str] = None
    name: Optional[str] = None
    vintage: Optional[int] = None
    clear_vintage: bool = False
    color: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    special_occasion: Optional[bool] = None
    notes: Optional[str] = None
    drink_from: Optional[int] = None
    drink_to: Optional[int] = None
    clear_window: bool = False
    purchase_price: Optional[float] = None
    est_value: Optional[float] = None
    clear_purchase_price: bool = False
    clear_est_value: bool = False


@app.patch("/api/bottle/{code}")
def edit_bottle(code: str, body: EditBottle, session: Session = Depends(get_session)):
    """Edit a bottle. Wine fields apply to every bottle of that wine; prices apply to this bottle only."""
    b = get_bottle_or_404(session, code)
    wine = session.get(Wine, b.wine_id)
    if body.producer is not None:
        if not body.producer.strip():
            raise HTTPException(400, "Producer is required")
        wine.producer = body.producer.strip()
    if body.name is not None:
        wine.name = body.name.strip()
    if body.clear_vintage:
        wine.vintage = None
    elif body.vintage is not None:
        wine.vintage = body.vintage
    if body.color is not None:
        if body.color not in ("red", "white", "rose", "sparkling", "dessert", "fortified"):
            raise HTTPException(400, "Unknown color")
        wine.color = body.color
    if body.region is not None:
        wine.region = body.region.strip()
    if body.country is not None:
        wine.country = body.country.strip()
    if body.special_occasion is not None:
        wine.special_occasion = body.special_occasion
    if body.notes is not None:
        wine.notes = body.notes.strip()[:1000]
    session.add(wine)

    if body.clear_window:
        session.exec(sql_delete(DrinkingWindow).where(DrinkingWindow.wine_id == wine.id))
    elif body.drink_from is not None or body.drink_to is not None:
        win = best_window(session, wine.id)
        if win:
            if body.drink_from is not None:
                win.drink_from = body.drink_from
            if body.drink_to is not None:
                win.drink_to = body.drink_to
            win.source = "owner"
            win.approved = True
            session.add(win)
        else:
            session.add(DrinkingWindow(wine_id=wine.id, drink_from=body.drink_from, drink_to=body.drink_to,
                                       source="owner", approved=True))

    if body.clear_purchase_price:
        b.purchase_price = None
    elif body.purchase_price is not None:
        b.purchase_price = body.purchase_price
    if body.clear_est_value:
        b.est_value = None
    elif body.est_value is not None:
        b.est_value = body.est_value
    session.add(b)
    session.commit()
    return bottle_detail(code, session)


@app.post("/api/bottle/{code}/drink")
def drink(code: str, body: DrinkBody = DrinkBody(), session: Session = Depends(get_session)):
    b = get_bottle_or_404(session, code)
    if b.status != "in_cellar":
        raise HTTPException(409, "This bottle is not in the cellar")
    now = utcnow()
    b.status = "consumed"
    b.consumed_at = now
    b.reserved_for = ""
    b.reserved_note = ""
    session.add(b)
    session.add(Consumption(bottle_id=b.id, occurred_at=now, reason="drank", rating=body.rating, note=body.note))
    session.commit()
    wine = session.get(Wine, b.wine_id)
    return {"ok": True, "label": wine_label(wine), "undo_seconds": UNDO_SECONDS}


class GiftBody(BaseModel):
    to: str
    occasion: str = ""
    note: str = ""


@app.post("/api/bottle/{code}/gift")
def gift(code: str, body: GiftBody, session: Session = Depends(get_session)):
    b = get_bottle_or_404(session, code)
    if b.status != "in_cellar":
        raise HTTPException(409, "This bottle is not in the cellar")
    if not body.to.strip():
        raise HTTPException(400, "Who is it for?")
    now = utcnow()
    b.status = "gifted"
    b.consumed_at = now
    b.reserved_for = ""
    b.reserved_note = ""
    session.add(b)
    session.add(Consumption(bottle_id=b.id, occurred_at=now, reason="gift", recipient=body.to.strip()[:80],
                            occasion=body.occasion.strip()[:120], note=body.note.strip()[:500]))
    session.commit()
    wine = session.get(Wine, b.wine_id)
    return {"ok": True, "label": wine_label(wine), "to": body.to.strip(), "undo_seconds": UNDO_SECONDS}


class ReserveBody(BaseModel):
    reserved_for: str = ""
    note: str = ""


@app.post("/api/bottle/{code}/reserve")
def reserve(code: str, body: ReserveBody, session: Session = Depends(get_session)):
    """Hold a bottle for someone (or clear the hold with an empty name)."""
    b = get_bottle_or_404(session, code)
    if b.status not in ("in_cellar", "incoming"):
        raise HTTPException(409, "This bottle is not in the cellar")
    b.reserved_for = body.reserved_for.strip()[:80]
    b.reserved_note = body.note.strip()[:120] if b.reserved_for else ""
    session.add(b)
    session.commit()
    return {"ok": True, "reserved_for": b.reserved_for, "note": b.reserved_note}


@app.get("/api/held")
def held(session: Session = Depends(get_session)):
    rows = session.exec(select(Bottle).where(Bottle.reserved_for != "",
                                             Bottle.status.in_(["in_cellar", "incoming"]))).all()
    groups = {}
    for b in rows:
        c = candidate(session, b)
        g = groups.setdefault(b.reserved_for, {"person": b.reserved_for, "bottles": [], "value": 0.0})
        g["bottles"].append(c)
        g["value"] += c["price"] or 0
    return sorted(groups.values(), key=lambda g: g["person"].lower())


@app.get("/api/gifts")
def gifts(session: Session = Depends(get_session)):
    rows = session.exec(select(Consumption).where(Consumption.reason == "gift", Consumption.undone == False)  # noqa: E712
                        .order_by(Consumption.occurred_at.desc())).all()
    out = []
    for h in rows:
        b = session.get(Bottle, h.bottle_id)
        wine = session.get(Wine, b.wine_id) if b else None
        out.append({"code": b.code if b else None, "label": wine_label(wine) if wine else "(deleted)",
                    "to": h.recipient, "occasion": h.occasion, "note": h.note, "when": h.occurred_at.isoformat(),
                    "value": effective_price(b.est_value, b.purchase_price) if b else None})
    return out


@app.post("/api/bottle/{code}/undo")
def undo_drink(code: str, session: Session = Depends(get_session)):
    b = get_bottle_or_404(session, code)
    last = session.exec(
        select(Consumption).where(Consumption.bottle_id == b.id, Consumption.undone == False)  # noqa: E712
        .order_by(Consumption.id.desc())
    ).first()
    if (not last or b.status not in ("consumed", "gifted")
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
    if b.location_id != loc.id:
        b.slot = ""
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
            if b.location_id != loc.id:
                b.slot = ""
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


# ---------- map ----------

@app.get("/api/map")
def cellar_map(session: Session = Depends(get_session)):
    """Pins for every place the cellar's wines come from, plus wines we can't place yet."""
    pins, unplaced = {}, []
    for b in in_cellar(session):
        wine = session.get(Wine, b.wine_id)
        lat, lon, precision = regions.locate(wine.region, wine.country)
        c = candidate(session, b)
        if lat is None:
            unplaced.append({"wine_id": wine.id, "label": c["label"]})
            continue
        key = (wine.region or wine.country or "").strip() or "Unknown"
        p = pins.setdefault(key, {"name": key, "country": wine.country, "lat": lat, "lon": lon,
                                  "precision": precision, "count": 0, "colors": {}, "bottles": []})
        p["count"] += 1
        p["colors"][wine.color] = p["colors"].get(wine.color, 0) + 1
        p["bottles"].append(c)
    seen, ul = set(), []
    for u in unplaced:
        if u["wine_id"] not in seen:
            seen.add(u["wine_id"])
            ul.append(u)
    return {"pins": sorted(pins.values(), key=lambda p: -p["count"]), "unplaced": ul}


@app.post("/api/regions/suggest")
def regions_suggest(session: Session = Depends(get_session)):
    """Ask Claude for region and country of wines that have no region yet. Nothing is saved here."""
    wines = session.exec(select(Wine)).all()
    todo = [w for w in wines if not (w.region or "").strip()
            and session.exec(select(Bottle).where(Bottle.wine_id == w.id, Bottle.status == "in_cellar")).first()]
    if not todo:
        return {"suggestions": []}
    out, err = ai.suggest_regions([{"id": w.id, "label": wine_label(w)} for w in todo])
    if err:
        raise HTTPException(503, err)
    by_id = {w.id: w for w in todo}
    return {"suggestions": [{**s, "label": wine_label(by_id[s["id"]]),
                             "mappable": regions.locate(s["region"], s["country"])[2] is not None}
                            for s in out if s["id"] in by_id]}


class RegionApply(BaseModel):
    items: List[dict]   # [{id, region, country}]


@app.post("/api/regions/apply")
def regions_apply(body: RegionApply, session: Session = Depends(get_session)):
    n = 0
    for it in body.items[:200]:
        w = session.get(Wine, int(it.get("id", 0)))
        if not w:
            continue
        if str(it.get("region") or "").strip():
            w.region = str(it["region"]).strip()[:60]
        if str(it.get("country") or "").strip():
            w.country = str(it["country"]).strip()[:40]
        session.add(w)
        n += 1
    session.commit()
    return {"updated": n}


@app.get("/api/region-about")
def region_about(region: str = "", country: str = "", session: Session = Depends(get_session)):
    key = f"about:{region.strip().lower()}|{country.strip().lower()}"
    cached = get_setting(session, key)
    if cached and not cached.rstrip().endswith((".", "!", "?")):
        cached = ""          # an earlier reply was cut off mid-sentence; ask again
    lat, lon, precision = regions.locate(region, country)
    if not cached:
        cached = ai.about_region(region or country, country) or ""
        if cached:
            set_setting(session, key, cached)
            session.commit()
    return {"region": region, "country": country, "about": cached, "lat": lat, "lon": lon, "precision": precision}


# ---------- backup: whole database in and out ----------

@app.get("/api/export/db")
def export_db():
    """The SQLite file itself, for moving to a new install or keeping a copy."""
    from .db import DB_PATH, engine
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    return FileResponse(DB_PATH, media_type="application/octet-stream", filename="stellar-cellar.db")


class ImportDbBody(BaseModel):
    db_base64: str


@app.post("/api/import/db")
def import_db(body: ImportDbBody):
    """Replace the database with an exported one. Only allowed while this install is empty."""
    import base64
    import sqlite3
    from .db import DB_PATH, engine
    from sqlalchemy import text
    with engine.begin() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM bottle")).scalar()
    if n:
        raise HTTPException(409, f"This install already has {n} bottles. Import only into an empty install.")
    raw = base64.b64decode(body.db_base64)
    if raw[:16] != b"SQLite format 3\x00":
        raise HTTPException(400, "That is not a SQLite database")
    tmp = DB_PATH + ".incoming"
    with open(tmp, "wb") as fh:
        fh.write(raw)
    try:
        con = sqlite3.connect(tmp)
        bottles = con.execute("SELECT COUNT(*) FROM bottle").fetchone()[0]
        con.close()
    except sqlite3.DatabaseError as exc:
        os.remove(tmp)
        raise HTTPException(400, f"Database could not be read ({exc})")
    engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(DB_PATH + suffix):
            os.remove(DB_PATH + suffix)
    os.replace(tmp, DB_PATH)
    init_db()   # applies any newer columns to the imported file
    return {"ok": True, "bottles": bottles}


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
        bottles = session.exec(
            select(Bottle).where(Bottle.location_id == loc.id, Bottle.status == "in_cellar")
        ).all()
        ready = sum(1 for b in bottles if candidate(session, b)["status"] in ("ready", "drink_soon"))
        cap = layout_capacity(loc)
        out.append({"code": loc.code, "name": loc.name, "count": len(bottles), "ready": ready,
                    "holding": loc.code == HOLDING_CODE, "kind": loc.kind, "capacity": cap})
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


class LayoutBody(BaseModel):
    kind: str = "fridge"
    layout: Optional[dict] = None     # None clears the layout
    name: Optional[str] = None


@app.post("/api/location/{code}/layout")
def set_layout(code: str, body: LayoutBody, session: Session = Depends(get_session)):
    loc = get_location_or_404(session, code)
    if body.name is not None and body.name.strip():
        loc.name = body.name.strip()
    if body.layout is None:
        loc.layout = ""
        loc.kind = "shelf"
        for b in session.exec(select(Bottle).where(Bottle.location_id == loc.id)).all():
            b.slot = ""
            session.add(b)
    else:
        loc.layout = json.dumps(validate_layout(body.layout))
        loc.kind = body.kind if body.kind in ("fridge", "rack") else "fridge"
    session.add(loc)
    session.commit()
    return {"ok": True, "layout": parse_layout(loc), "capacity": layout_capacity(loc), "kind": loc.kind}


class SlotBody(BaseModel):
    location_code: str
    slot: str = ""           # "" = somewhere on this place, position unknown


@app.post("/api/bottle/{code}/slot")
def set_slot(code: str, body: SlotBody, session: Session = Depends(get_session)):
    """Place a bottle at a slot (e.g. A-3 or A-3b) in a laid-out place; also moves it there."""
    b = get_bottle_or_404(session, code)
    loc = get_location_or_404(session, body.location_code)
    slot = body.slot.strip().upper()
    if slot:
        lay = parse_layout(loc)
        if not lay:
            raise HTTPException(400, "This place has no rack layout")
        mm = re.fullmatch(r"([A-Z0-9]{1,4})(?:-(\d{1,2})([A-C]?))?", slot)
        if not mm:
            raise HTTPException(400, "Slot looks like A-3 or A-3B")
        rack = next((r for r in lay["racks"] if r["name"].upper() == mm.group(1)), None)
        if not rack:
            raise HTTPException(400, f"No rack {mm.group(1)} in {loc.name}")
        if mm.group(2) is None:
            if not rack.get("bulk"):
                raise HTTPException(400, "Pick a position on that rack, like A-3")
        elif not 1 <= int(mm.group(2)) <= rack["across"] or (mm.group(3) and "ABC".index(mm.group(3)) >= rack["deep"]):
            raise HTTPException(400, f"No slot {slot} in {loc.name}")
        occupant = None if (rack.get("bulk") and mm.group(2) is None) else session.exec(
            select(Bottle).where(Bottle.location_id == loc.id, Bottle.slot == slot,
                                 Bottle.status == "in_cellar", Bottle.id != b.id)).first()
        if occupant:
            occupant.slot = ""          # bumped to "somewhere on this place"; the UI tells the user
            session.add(occupant)
    b.location_id = loc.id
    b.slot = slot
    session.add(b)
    session.commit()
    return {"ok": True, "location": loc.name, "slot": slot}


@app.get("/api/location/{code}")
def location_detail(code: str, session: Session = Depends(get_session)):
    loc = get_location_or_404(session, code)
    bottles = session.exec(
        select(Bottle).where(Bottle.location_id == loc.id, Bottle.status == "in_cellar")
    ).all()
    return {"code": loc.code, "name": loc.name, "kind": loc.kind, "layout": parse_layout(loc),
            "capacity": layout_capacity(loc), "bottles": [candidate(session, b) for b in bottles]}


# ---------- printable labels ----------

LABEL_FORMATS = {
    # full: URL in the QR, scannable by the phone's own camera once the public route exists
    "full": {"qr_mm": 22, "w_mm": 40, "h_mm": 30, "font_pt": 8, "border": 4, "payload": "url"},
    # compact: bare code in the QR, in-app scanner only, smallest reliable size
    "compact": {"qr_mm": 12, "w_mm": 20, "h_mm": 14, "font_pt": 5, "border": 2, "payload": "code"},
    # neck: fits Brother DK-11204 17 x 54 mm die-cut strips, wraps the bottle neck
    "neck": {"qr_mm": 13, "w_mm": 54, "h_mm": 17, "font_pt": 7, "border": 2, "payload": "code"},
}


class PrintBody(BaseModel):
    codes: List[str]
    copies: int = 1


def _label_target(session: Session, code: str):
    """Returns (title, subtitle, object, fields) for a bottle or place code."""
    if code.startswith("B"):
        b = session.exec(select(Bottle).where(Bottle.code == code)).first()
        if not b:
            return None, "", None, {}
        wine = session.get(Wine, b.wine_id)
        win = best_window(session, wine.id)
        if win and (win.drink_from or win.drink_to):
            today_y = date.today().year
            if win.drink_from and win.drink_from > today_y:
                window = f"Hold until {win.drink_from}"
            elif win.drink_from and win.drink_to:
                window = f"Drink {win.drink_from} to {win.drink_to}"
            elif win.drink_to:
                window = f"Drink by {win.drink_to}"
            else:
                window = f"Drink from {win.drink_from}"
        else:
            window = ""
        best = session.exec(select(Tasting).where(Tasting.wine_id == wine.id, Tasting.score != None)  # noqa: E711
                            .order_by(Tasting.id.desc())).first()
        foot = [wine.region] if wine.region else []
        if best:
            foot.append(f"{best.score:g} pts")
        if b.reserved_for:
            foot.append(f"Held for {b.reserved_for}")
        fields = {"vintage": str(wine.vintage) if wine.vintage else "NV", "window": window,
                  "footer": " · ".join(foot)}
        name = " ".join(x for x in [wine.producer, wine.name] if x)
        return name, fields["footer"], b, fields
    if code.startswith("L"):
        loc = session.exec(select(Location).where(Location.code == code)).first()
        return (loc.name if loc else None), "Place", loc, {"footer": "Place"}
    return None, "", None, {}


@app.get("/api/printer")
def printer_info():
    """Tells the app whether a network label printer is configured and reachable."""
    if not ZEBRA_HOST:
        return {"configured": False}
    return {"configured": True, "host": ZEBRA_HOST, "online": zebra.status(ZEBRA_HOST, ZEBRA_PORT),
            "label_mm": [ZEBRA_W, ZEBRA_H], "dpi": ZEBRA_DPI, "layout": ZEBRA_LAYOUT}


@app.post("/api/print")
def print_labels(body: PrintBody, session: Session = Depends(get_session)):
    """Send labels straight to the Zebra. Bare code in the QR, same as the compact sheet."""
    if not ZEBRA_HOST:
        raise HTTPException(409, "No label printer configured. Set zebra_host in the add-on options.")
    if not 1 <= body.copies <= 10:
        raise HTTPException(400, "Copies must be 1 to 10")
    zpl = []
    for raw in body.codes[:100]:
        code = raw.strip().upper()
        title, sub, obj, fields = _label_target(session, code)
        if not obj:
            raise HTTPException(404, f"Code {code} not found")
        zpl.append(zebra.render(ZEBRA_LAYOUT, code, title, code, subtitle=sub, fields=fields, **ZEBRA_KW)
                   * body.copies)
    try:
        zebra.send(ZEBRA_HOST, "".join(zpl), ZEBRA_PORT)
    except OSError as exc:
        raise HTTPException(503, f"Printer at {ZEBRA_HOST} did not answer ({exc.__class__.__name__}).")
    return {"ok": True, "printed": len(zpl) * body.copies}


@app.post("/api/print/test")
def print_test():
    if not ZEBRA_HOST:
        raise HTTPException(409, "No label printer configured.")
    zpl = zebra.render(ZEBRA_LAYOUT, "BTEST01", "Stellar Cellar test label", "BTEST01",
                       subtitle="Scan me with the app",
                       fields={"vintage": "2018", "window": "Drink 2026 to 2033", "footer": "Napa Valley · 96 pts"},
                       **ZEBRA_KW)
    try:
        zebra.send(ZEBRA_HOST, zpl, ZEBRA_PORT)
    except OSError as exc:
        raise HTTPException(503, f"Printer at {ZEBRA_HOST} did not answer ({exc.__class__.__name__}).")
    return {"ok": True}


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
