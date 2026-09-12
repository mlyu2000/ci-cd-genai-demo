"""User service for the payments app.

Fixture state for scenario `missing_import`: `Optional` is used in a signature
but NOT imported from typing, so importing this module raises NameError. The
GenAI agent's fix is to add the missing import (one-line patch).
"""
from typing import Dict, Optional


def get_user(id: Optional[int]) -> Dict:
    """Return a user record by id (None for unknown ids)."""
    return {"id": id, "name": "demo-user", "plan": "enterprise"}


def get_user_orders(id: Optional[int]) -> list:
    return []
