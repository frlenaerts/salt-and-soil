from pathlib import Path


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


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
