"""UI tests start real servers and browsers: run them only with NLH_UI_TESTS=1 (own CI job)."""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("NLH_UI_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="set NLH_UI_TESTS=1 to run browser tests")
    for item in items:
        if "tests/ui/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)
