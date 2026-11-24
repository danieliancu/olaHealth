from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_storage(settings, tmp_path):
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path / "private"), "base_url": None},
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    settings.EXPORT_ROOT = tmp_path / "exports"
    settings.OFF_BASE_URL = "https://off.test"
    settings.OFF_USER_AGENT = "olaHealth-tests/1.0 (test)"


@pytest.fixture(autouse=True)
def _no_real_embeddings(monkeypatch):
    """Never call the real embeddings API (the container may hold an OpenAI key): Magazine
    search uses full text unless a test installs fake embeddings."""
    monkeypatch.setattr("ai.embeddings._client", lambda: None)


@pytest.fixture(autouse=True)
def queued_tasks(monkeypatch):
    """django-q async_task is replaced by a recorder: tests run jobs explicitly."""
    calls: list[tuple] = []

    def fake_async_task(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return "test-task"

    monkeypatch.setattr("django_q.tasks.async_task", fake_async_task)
    return calls


@pytest.fixture
def member(db):
    """A signed-up user with a confirmed email address."""
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username="member", email="member@example.com", password="a-long-test-password-1"
    )
    EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
    return user


@pytest.fixture
def member_client(client, member):
    client.force_login(member)
    return client


@pytest.fixture
def off_payload():
    data = json.loads((FIXTURES / "off" / "product_yogurt.json").read_text(encoding="utf-8"))
    return lambda **changes: {**copy.deepcopy(data), **changes}


@pytest.fixture
def off_json(off_payload):
    return lambda **changes: {
        "status": 1,
        "code": off_payload()["code"],
        "product": off_payload(**changes),
    }


@pytest.fixture(autouse=True)
def _fresh_maintenance_banner():
    """The banner text is cached per process; tests must not see another test's banner."""
    from core.context_processors import _MAINTENANCE

    _MAINTENANCE.update(at=0.0, text="")
    yield
    _MAINTENANCE.update(at=0.0, text="")
