"""User service for the payments app.

(Import of Optional is missing in this fixture state — scenario
`missing_import`; the module raises NameError at import time.)
"""
from typing import Dict


def get_user(id: Optional[int]) -> Dict:
    """Return a user record by id (None for unknown ids)."""
    return {"id": id, "name": "demo-user", "plan": "enterprise"}


def get_user_orders(id: Optional[int]) -> list:
    return []
