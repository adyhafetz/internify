"""What has worked before: the bullet points that were on resumes which got you an interview or an offer.

The bot records it when you mark an application in the tracker; the Archivist gives those bullet points a small
nudge up when it ranks your experience for a new job. The nudge is capped, so relevance to the job still decides."""
from __future__ import annotations

import json
import os
import threading

from service import config

PATH = os.getenv("OUTCOMES_PATH", os.path.join(os.path.dirname(config.FACT_STORE_PATH) or ".", "outcomes.json"))
STEP, CAP = 0.03, 0.09
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, PATH)


def record(app_key: str, fact_ids: list[str], good: bool) -> None:
    """Remember (or forget) that this application's bullet points led somewhere good. Idempotent per application."""
    with _lock:
        data = _load()
        wins = data.setdefault("wins", {})
        if good and fact_ids:
            wins[str(app_key)] = sorted(set(fact_ids))
        else:
            wins.pop(str(app_key), None)
        _save(data)


def boosts() -> dict[str, float]:
    """fact id -> the small extra score it earns from past interviews."""
    counts: dict[str, int] = {}
    for ids in _load().get("wins", {}).values():
        for fid in ids:
            counts[fid] = counts.get(fid, 0) + 1
    return {fid: min(CAP, STEP * n) for fid, n in counts.items()}
