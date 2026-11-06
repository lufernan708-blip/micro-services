import shutil
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1] / "microservicos-fastapi"
sys.path.insert(0, str(SERVICE_DIR))

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="microservices-tests-"))
os.environ["USERS_DATABASE_PATH"] = str(TEST_DATA_DIR / "users.db")
os.environ["ORDERS_DATABASE_PATH"] = str(TEST_DATA_DIR / "orders.db")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolate_sqlite_data():
    import order_service
    import user_service

    test_id = uuid4().hex
    user_service.DATABASE_PATH = str(TEST_DATA_DIR / f"{test_id}-users.db")
    order_service.DATABASE_PATH = str(TEST_DATA_DIR / f"{test_id}-orders.db")
    user_service.init_db()
    order_service.init_db()
