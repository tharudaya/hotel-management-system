"""Password hashing (Argon2 via pwdlib) and session tokens.

Never log or store plaintext passwords or session tokens.
"""

import hashlib
import hmac
import secrets
from functools import lru_cache

from pwdlib import PasswordHash

_password_hash: PasswordHash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def verify_and_update_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    """Verify; if the hash uses outdated parameters, also return a fresh hash to store."""
    return _password_hash.verify_and_update(password, password_hash)


@lru_cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    """Spend the same time as a real verification, for usernames that don't exist.

    Keeps response time from revealing whether a username is registered.
    """
    _password_hash.verify(password, _dummy_hash())


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str, secret: str) -> str:
    """Keyed hash of a session token, as stored in the sessions table."""
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
