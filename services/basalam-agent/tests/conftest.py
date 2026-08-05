import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Isolated test environment BEFORE app.config is imported anywhere.
_tmp = tempfile.mkdtemp(prefix="bdsk-ai-test-")
os.environ.update({
    "BDSK_AI_DB_PATH": f"{_tmp}/test.db",
    "BDSK_AI_ADMIN_TOKEN": "test-admin-token",
    "BDSK_AI_FERNET_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    "BDSK_AI_CHATWOOT_WEBHOOK_SECRET": "test-chatwoot-secret",
    "BDSK_AI_HUB_WEBHOOK_SECRET": "test-hub-secret",
    "BDSK_AI_TELEGRAM_WEBHOOK_SECRET": "test-telegram-secret",
    "BDSK_AI_CHATWOOT_BOT_TOKEN": "bot-token",
    "BDSK_AI_CHATWOOT_USER_TOKEN": "user-token",
    "BDSK_AI_CHATWOOT_BASE_URL": "http://chatwoot.test",
    "BDSK_AI_HUB_BASE_URL": "http://hub.test",
    "BDSK_AI_OPENROUTER_API_KEY": "test-openrouter-key",
    "BDSK_AI_TELEGRAM_BOT_TOKEN": "",  # bot disabled in tests
})

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db import init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
