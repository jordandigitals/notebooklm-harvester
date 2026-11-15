"""Guard for tests that open a real listener on the laptop's LAN address (phone listening, D-026).

On Windows the first listen on a network address makes Windows Security ask whether to allow
the program through the firewall, and answering writes firewall rules. A test run must never
put that question on someone's screen, so on Windows these tests run only when NLH_UI_LAN=1
(CI sets it; its runners never prompt). On Mac and Linux they run as before.
"""

import os
import sys

import pytest

LAN_TESTS_ALLOWED = sys.platform != "win32" or os.environ.get("NLH_UI_LAN") == "1"

needs_lan = pytest.mark.skipif(
    not LAN_TESTS_ALLOWED,
    reason="opens a real LAN listener; on Windows set NLH_UI_LAN=1 (Windows asks about the firewall)",
)
