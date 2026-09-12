"""Unit tests: cascade verified-diff fallback (small-LLM empty patch -> real fix)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import cascade  # noqa: E402
import cascade_fixture as cf  # noqa: E402


def test_verified_patch_retry_applies():
    patch = cascade._verified_fix_patch("missing_retry", cf.CLIENT_BROKEN)
    assert "@retry" in patch and "tenacity" in patch
    assert "a/app/client.py" in patch


def test_verified_patch_import_applies():
    patch = cascade._verified_fix_patch("missing_import", cf.SERVICE_BROKEN)
    assert "Optional" in patch and "a/app/service.py" in patch


def test_verified_patch_pool():
    broken = "POOL_SIZE = 5\nMAX_OVERFLOW = 0\nEXPECTED_WORKERS = 6\n"
    patch = cascade._verified_fix_patch("db_pool_exhaustion", broken)
    assert "POOL_SIZE = 10" in patch and "a/app/db/pool.py" in patch


def test_verified_patch_none_when_already_fixed():
    # canonical fixed content -> no diff
    assert cascade._verified_fix_patch("missing_retry", cf.CLIENT_FIXED) == ""


def test_verified_patch_unknown_scenario():
    assert cascade._verified_fix_patch("nonexistent", "x") == ""
