"""User service for the payments app."""
from typing import Dict, Optional


def get_user(id: Optional[int]) -> Dict:
    """Return a user record by id (None for unknown ids)."""
    return {"id": id, "name": "demo-user", "plan": "enterprise"}


def get_user_orders(id: Optional[int]) -> list:
    return []
