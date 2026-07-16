from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    """The real repo, so tests exercise the actually-committed config and state."""
    return Path(__file__).resolve().parent.parent
