import os
import sqlite3
import tempfile


def test_adds_missing_columns_to_old_db():
    d = tempfile.mkdtemp()
    os.environ["CELLAR_DATA_DIR"] = d
    # a 0.5.x-era bottle table without the tags column
    con = sqlite3.connect(os.path.join(d, "cellar.db"))
    con.execute("CREATE TABLE bottle (id INTEGER PRIMARY KEY, code VARCHAR, wine_id INTEGER, location_id INTEGER, "
                "status VARCHAR, purchase_price FLOAT, est_value FLOAT, currency VARCHAR, added_at DATETIME, consumed_at DATETIME)")
    con.execute("INSERT INTO bottle (code, wine_id, status, currency) VALUES ('BOLD001', 1, 'in_cellar', 'USD')")
    con.commit(); con.close()

    import importlib
    from app import db
    importlib.reload(db)
    db.init_db()
    con = sqlite3.connect(os.path.join(d, "cellar.db"))
    cols = {r[1] for r in con.execute("PRAGMA table_info(bottle)")}
    assert "tags" in cols
    assert con.execute("SELECT tags FROM bottle").fetchone()[0] == ""
