"""Create the first Admin of a fresh install (after `alembic upgrade head`).

    python -m app.scripts.create_admin --username admin --full-name "Ada Admin"

The password is asked for twice (not echoed). For automation, pipe it in with
--password-stdin. Refuses if the property already has an active admin; then create
admins in the app instead. Never put the password on the command line: it would
end up in shell history and process listings.
"""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from app.core.config import get_settings
from app.core.passwords import MIN_LENGTH
from app.db.session import create_engine, create_session_factory
from app.services.bootstrap import create_first_admin
from app.services.errors import DomainError


def read_password(from_stdin: bool) -> str | None:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    print(f"Choose a password (at least {MIN_LENGTH} characters, not a common one).")
    first = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != first:
        print("The passwords do not match.", file=sys.stderr)
        return None
    return first


async def run(args: argparse.Namespace, password: str) -> int:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as db:
            user = await create_first_admin(
                db,
                username=args.username,
                full_name=args.full_name,
                email=args.email,
                password=password,
            )
    except DomainError as exc:
        print(f"Not created: {exc.message}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(f"Created admin {user.username!r}. Sign in and create the other users in the app.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the first admin of a fresh install.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--email", default=None)
    parser.add_argument(
        "--password-stdin", action="store_true", help="read the password from standard input"
    )
    args = parser.parse_args(argv)
    password = read_password(args.password_stdin)
    if password is None:
        return 1
    return asyncio.run(run(args, password))


if __name__ == "__main__":
    raise SystemExit(main())
