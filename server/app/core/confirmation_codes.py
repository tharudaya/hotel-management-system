"""Reservation confirmation codes: random, not sequential, easy to read aloud.

Eight characters from an alphabet without look-alikes (no 0/O, 1/I/L): 31**8, about
8.5e11 codes. The database checks the format (ck_reservations_confirmation_code_format).
"""

import secrets
from typing import Final

ALPHABET: Final = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
LENGTH: Final = 8
# The same set as a PostgreSQL regular expression; keep the two in step (a test checks).
PATTERN: Final = "^[2-9A-HJKMNP-Z]{8}$"


def new_confirmation_code() -> str:
    """A fresh random code. Uniqueness is the database's job (retry on conflict)."""
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
