from dataclasses import dataclass


@dataclass
class AuthUser:
    username: str
    password_hash: str
    session_secret: str
    created_at: str = ""
