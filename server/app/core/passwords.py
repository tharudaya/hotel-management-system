"""Password rules for new passwords (activation and reset).

- At least 12 characters (and at most 1024, so hashing stays cheap).
- Not a common password. The list is SecLists' 10k most common passwords
  (data/common_passwords.txt, MIT licence in data/common_passwords.LICENSE.txt).
  Almost none of those are 12+ characters, so the check also strips leading and
  trailing digits and symbols ("password2024!" -> "password", "123qwerty123456" ->
  "qwerty") and compares what is left.
- Not one repeated character, and not containing the username.
"""

import re
from functools import lru_cache
from pathlib import Path

MIN_LENGTH: int = 12
MAX_LENGTH: int = 1024
_COMMON_FILE: Path = Path(__file__).parent / "data" / "common_passwords.txt"
_EDGE_NOISE: re.Pattern[str] = re.compile(r"^[\d\W_]+|[\d\W_]+$")


class PasswordPolicyError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@lru_cache
def common_passwords() -> frozenset[str]:
    lines = _COMMON_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip().lower() for line in lines if line.strip())


def _looks_common(password: str) -> bool:
    lowered = password.lower()
    common = common_passwords()
    if lowered in common:
        return True
    core = _EDGE_NOISE.sub("", lowered)
    # Only meaningful cores: "a" or "12" stripped out of a long password says nothing.
    return len(core) >= 4 and core in common


def check_new_password(password: str, *, username: str) -> None:
    """Raise PasswordPolicyError if the password breaks a rule."""
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(
            f"Use at least {MIN_LENGTH} characters.", code="password_too_short"
        )
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"Use at most {MAX_LENGTH} characters.", code="password_too_long")
    if len(set(password)) == 1 or _looks_common(password):
        raise PasswordPolicyError(
            "This password is too common or easy to guess. Choose something less predictable.",
            code="password_too_common",
        )
    if username and username.lower() in password.lower():
        raise PasswordPolicyError(
            "The password must not contain your username.", code="password_contains_username"
        )
