import os

from sqlmodel import Session, SQLModel, create_engine

DATA_DIR = os.environ.get("CELLAR_DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "cellar.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def init_db():
    from . import models  # noqa: F401  registers tables
    SQLModel.metadata.create_all(engine)
    migrate_columns()


def migrate_columns():
    """create_all never alters existing tables. Add any column a model gained since the DB was created."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                coltype = col.type.compile(engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "arg", None) is not None \
                        and not callable(col.default.arg):
                    v = col.default.arg
                    default = f" DEFAULT {repr(v) if isinstance(v, str) else int(v) if isinstance(v, bool) else v}"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}{default}'))


def get_session():
    with Session(engine) as session:
        yield session
