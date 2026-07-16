from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    """The real repo, so tests exercise the actually-committed config and state."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test may call a real model.

    Not a nicety. When stage 2 was first wired in, the previous build's gate
    tests — which merely asserted that a run day is a run day — started spawning
    real researchers doing real oncology web research: minutes of wall clock and
    real money, per test, and the suite hung. run_researcher refuses when this
    is set unless a fake runner is injected, so the same mistake now fails in
    milliseconds with a message saying what to do instead.

    The one live test opts out explicitly. See tests/test_live_researcher.py.
    """
    monkeypatch.setenv("RESEARCHSWARM_OFFLINE", "1")


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run tests that call real models. Costs money, takes minutes.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls a real model; needs --live")
