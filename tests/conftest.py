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
def queued_tasks(monkeypatch):
    """django-q async_task is replaced by a recorder: tests run jobs explicitly."""
    calls: list[tuple] = []

    def fake_async_task(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return "test-task"

    monkeypatch.setattr("django_q.tasks.async_task", fake_async_task)
    return calls


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
