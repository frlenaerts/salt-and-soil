from dataclasses import dataclass, field

# Sentinel stored in allowed_aliases to mean "every source". Admins always have
# full access regardless; this lets a non-admin be granted everything too.
ALL_ALIASES = "*"


@dataclass
class User:
    username: str
    password_hash: str
    is_admin: bool = False
    allowed_aliases: list[str] = field(default_factory=list)
    pw_version: int = 0          # bumped on every password change → invalidates that user's sessions
    created_at: str = ""

    def can_access(self, alias: str) -> bool:
        return self.is_admin or ALL_ALIASES in self.allowed_aliases or alias in self.allowed_aliases

    @property
    def has_all_access(self) -> bool:
        return self.is_admin or ALL_ALIASES in self.allowed_aliases
