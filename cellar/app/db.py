import os

from sqlmodel import Session, SQLModel, create_engine

DATA_DIR = os.environ.get("CELLAR_DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "cellar.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def init_db():
    from . import models  # noqa: F401  registers tables
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
