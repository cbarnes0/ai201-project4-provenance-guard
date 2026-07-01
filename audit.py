import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_log: dict = {}


def log_entry(content_id: str, entry: dict) -> None:
    with _lock:
        _log[content_id] = entry


def get_log() -> dict:
    with _lock:
        return dict(_log)


def add_appeal(content_id: str, reasoning: str) -> bool:
    """
    Appends an appeal to an existing log entry and sets status to 'under_review'.
    Returns False if content_id is not found.
    """
    with _lock:
        if content_id not in _log:
            return False
        entry = _log[content_id]
        appeal = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "creator_reasoning": reasoning,
        }
        entry.setdefault("appeals", []).append(appeal)
        entry["status"] = "under_review"
        return True
