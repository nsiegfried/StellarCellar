from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp (required by SQLModel's datetime columns)."""
    return datetime.now(timezone.utc)


class Wine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    producer: str
    name: str = ""
    vintage: Optional[int] = None
    color: str = "red"
    region: str = ""
    country: str = ""
    special_occasion: bool = False
    notes: str = ""


class Location(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    code: str = Field(index=True, unique=True)
    kind: str = "shelf"        # shelf | fridge | rack
    layout: str = ""           # JSON: {"racks":[{"name":"A","across":8,"deep":2},...], "order":"top_down"}


class Bottle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    wine_id: int = Field(foreign_key="wine.id", index=True)
    location_id: Optional[int] = Field(default=None, foreign_key="location.id")
    status: str = "in_cellar"  # in_cellar | incoming | consumed | gifted
    tags: str = ""             # comma-separated, e.g. "collector,A+"
    reserved_for: str = ""     # person this bottle is held for; "" means it is ours to open
    reserved_note: str = ""    # occasion or reason
    slot: str = ""             # position inside a laid-out place, e.g. "A-3" or "A-3b" (b = back row)
    purchase_price: Optional[float] = None
    est_value: Optional[float] = None
    currency: str = "USD"
    added_at: datetime = Field(default_factory=utcnow)
    consumed_at: Optional[datetime] = None


class DrinkingWindow(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wine_id: int = Field(foreign_key="wine.id", index=True)
    drink_from: Optional[int] = None
    drink_to: Optional[int] = None
    source: str = "owner"  # owner | ai | import
    approved: bool = True


class Consumption(SQLModel, table=True):
    """A bottle leaving the cellar: opened or given away."""
    id: Optional[int] = Field(default=None, primary_key=True)
    bottle_id: int = Field(foreign_key="bottle.id", index=True)
    occurred_at: datetime = Field(default_factory=utcnow)
    reason: str = "drank"      # drank | gift
    recipient: str = ""        # for gifts
    occasion: str = ""
    rating: Optional[int] = None
    note: str = ""
    undone: bool = False


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""


class Tasting(SQLModel, table=True):
    """Journal entry: a wine someone in the household tasted, anywhere, owned or not."""
    id: Optional[int] = Field(default=None, primary_key=True)
    wine_id: Optional[int] = Field(default=None, foreign_key="wine.id", index=True)
    wine_text: str                     # name as recorded, kept even when linked to a Wine
    vintage: Optional[int] = None
    taster: str = ""
    score: Optional[float] = None      # 100-point scale
    band: str = ""                     # Exceptional | Strong | Good | Drinkable | No, or verdict text
    verdict: str = ""                  # favorite | dislike | ok, for unscored entries
    notes: str = ""
    where: str = ""
    tasted_on: Optional[datetime] = None
    source: str = "manual"             # manual | import
    history: str = ""                  # e.g. "92 -> 90: earlier enthusiasm value-driven"
    created_at: datetime = Field(default_factory=utcnow)


class WishItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wine_text: str
    vintage: Optional[int] = None
    status: str = "wishlist"           # wishlist | buy_again | purchased
    notes: str = ""
    reference_price: Optional[float] = None
    created_at: datetime = Field(default_factory=utcnow)
