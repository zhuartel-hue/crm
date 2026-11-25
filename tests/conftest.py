import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(__file__).parent / "test.db"))
os.environ["ADMIN_PASSWORD"] = "test-haslo"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["MAIL_SYNC_MINUTES"] = "0"
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.db import Base, engine
    from app.main import app

    _reset(engine, Base)
    with TestClient(app) as c:
        r = c.post("/login", data={"login": "admin", "password": "test-haslo"})
        assert r.status_code in (200, 303)
        yield c
    _reset(engine, Base)


def _reset(engine, Base):
    from sqlalchemy import text

    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
