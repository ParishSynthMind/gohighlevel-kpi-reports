import os

import pytest

from ghl_kpi.client import GHLClient
from ghl_kpi.db import make_session_factory
from ghl_kpi.mock_api import MOCK_TOKEN, MockGHLSession


@pytest.fixture
def mock_session():
    return MockGHLSession()


@pytest.fixture
def client(mock_session):
    return GHLClient(MOCK_TOKEN, mock_session.location_id, session=mock_session, sleep=lambda s: None)


@pytest.fixture
def Session(tmp_path):
    """SQLite by default. Set TEST_DATABASE_URL (e.g. a Postgres URL) to run the same tests on that database."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return make_session_factory(f"sqlite:///{tmp_path / 'test.db'}")
    from sqlalchemy import create_engine
    from ghl_kpi.db import Base
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    return make_session_factory(url)


@pytest.fixture(autouse=True)
def no_external_alerts(monkeypatch):
    for var in ("SMTP_HOST", "REPORT_EMAIL_TO", "ALERT_WEBHOOK_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
