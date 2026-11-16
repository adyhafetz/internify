"""Counting what each user costs, in one place. Every AI call and every paid scraping call goes through ``count``, which hands
it to ``sink`` if the hosted version has set one (bot/services/plans.py). In single mode there is no sink and nothing is counted
or stored, so a normal copy is unaffected."""
from __future__ import annotations

from typing import Callable, Optional

sink: Optional[Callable[[str], None]] = None


def count(kind: str) -> None:
    """Note one call of this kind ("ai" or "scrape") for the acting user. Never raises: counting must not break the work."""
    if sink is None:
        return
    try:
        sink(kind)
    except Exception:
        pass
