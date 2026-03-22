"""Pytest fixtures for Swarm integration tests."""
import os
import sys

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import comparator_backend as cb  # noqa: E402


@pytest.fixture
def model_dirs() -> list[str]:
    """Default model directories used by pytest for integration tests."""
    return ["C:\\AI\\Models", "C:\\Users\\Public\\AI\\Models"]


@pytest.fixture(autouse=True)
def reset_global_rate_limiter():
    """Keep the backend's process-global limiter from leaking across tests."""
    with cb._rate_limiter._lock:
        cb._rate_limiter._hits.clear()
    yield
    with cb._rate_limiter._lock:
        cb._rate_limiter._hits.clear()
