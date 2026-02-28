import os

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="Set RUN_INTEGRATION=1 with a running Postgres to execute integration tests",
)


def test_integration_placeholder() -> None:
    assert True
