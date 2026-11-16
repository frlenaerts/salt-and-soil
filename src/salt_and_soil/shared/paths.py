import contextlib
import os
import tempfile
from pathlib import Path


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_private(path: str | Path, text: str) -> None:
    """Atomically write `text` to `path`, readable by the owner only (0600).

    The content goes to a temporary file in the same directory, which then
    replaces the target. Readers never see a half-written file, a crash
    leaves the previous version intact, and the result is private whatever
    the process umask or the mode of the file it replaces."""
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def relative_to(path: Path, root: Path) -> str:
    """Return POSIX relative path string."""
    return path.relative_to(root).as_posix()


def is_plain_name(name: object) -> bool:
    """True if `name` is usable as exactly one path component: a non-empty
    string other than '.' or '..' with no '/' or NUL in it. Joining such a
    name onto a directory always yields a direct child of that directory."""
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and "/" not in name
        and "\x00" not in name
    )


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
