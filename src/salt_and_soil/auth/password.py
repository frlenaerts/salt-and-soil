import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

_PH = PasswordHasher()

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024      # bounds the work one request can ask for

_dummy_hash: str | None = None


def hash_password(plain: str) -> str:
    return _PH.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _PH.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:
        return False


def prepare_dummy_hash() -> None:
    """Create the hash dummy_verify() works against (once per process)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _PH.hash(secrets.token_urlsafe(16))


def dummy_verify(plain: str) -> None:
    """Spend the same effort as a real verification, and always fail. Used
    when the user name does not exist, so that the response time does not
    tell valid user names from invalid ones."""
    prepare_dummy_hash()
    verify_password(plain, _dummy_hash)


def needs_rehash(hashed: str) -> bool:
    try:
        return _PH.check_needs_rehash(hashed)
    except Exception:
        return False
