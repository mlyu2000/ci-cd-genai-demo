"""Integration: payments user service import & API surface.

Fails in fixture state `missing_import` (app/service.py uses Optional without
importing it -> NameError at import time) and passes once the import is added.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "app"))

from service import get_user, get_user_orders  # noqa: E402  (import = the test)


def test_service_get_user():
    assert get_user(1)["id"] == 1
    assert get_user(None) is not None


def test_service_orders():
    assert get_user_orders(1) == []
