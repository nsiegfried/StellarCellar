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


class Bottle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    wine_id: int = Field(foreign_key="wine.id", index=True)
    location_id: Optional[int] = Field(default=None, foreign_key="location.id")
    status: str = "in_cellar"  # in_cellar | consumed
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
    id: Optional[int] = Field(default=None, primary_key=True)
    bottle_id: int = Field(foreign_key="bottle.id", index=True)
    occurred_at: datetime = Field(default_factory=utcnow)
    rating: Optional[int] = None
    note: str = ""
    undone: bool = False


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""
