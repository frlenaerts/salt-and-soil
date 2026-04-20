from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

_PH = PasswordHasher()

MIN_PASSWORD_LENGTH = 8


def hash_password(plain: str) -> str:
    return _PH.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _PH.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _PH.check_needs_rehash(hashed)
    except Exception:
        return False
