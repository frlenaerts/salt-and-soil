from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def snapshot_id() -> str:
    """Filesystem-safe ISO timestamp for snapshot filenames."""
    return utc_now().strftime("%Y-%m-%dT%H-%M-%S")
