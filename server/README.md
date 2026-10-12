# server

FastAPI back end: Pydantic v2, SQLAlchemy 2.x (async, asyncpg), Alembic, pytest, ruff, mypy.

## Setup

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/). uv manages the virtualenv and
installs exactly what `uv.lock` pins. Install uv with `pip install uv` (or the official
installer) if you don't have it.

From the repo root, start the database and create `.env` first (see the root README). Then:

```sh
cd server
uv sync                      # create .venv and install app + dev dependencies
uv run alembic upgrade head  # apply migrations to DATABASE_URL
```

Settings are read from the repo-root `.env`, then `server/.env` if present; real
environment variables override both.

## Seed development data

```sh
uv run python -m app.scripts.seed          # refuses if data exists
uv run python -m app.scripts.seed --reset  # wipe app tables, then seed
```

Creates 40 rooms with a mix of vacant, occupied, arriving, departing and out-of-order
rooms for today, plus one user per role. Dev logins (development only):

| role | username | password |
|---|---|---|
| Admin | admin | admin-dev-pass |
| General manager | manager | manager-dev-pass |
| Front office manager | fomanager | fomanager-dev-pass |
| Receptionist | frontdesk | frontdesk-dev-pass |
| Housekeeping supervisor | housekeeping | housekeeping-dev-pass |
| Room attendant | attendant | attendant-dev-pass |

## Run

```sh
uv run uvicorn app.main:create_app --factory --reload
```

- API: http://localhost:8000/api/health
- Interactive docs: http://localhost:8000/docs

## Test

```sh
uv run pytest
```

Tests use `TEST_DATABASE_URL` (a separate `hotel_test` database). It is created if missing
and migrated to head at the start of each run. The database container must be running.

## Lint, format, type-check

```sh
uv run ruff check .          # lint (add --fix to auto-fix)
uv run ruff format .         # format
uv run mypy app tests alembic/env.py
```

## Migrations

```sh
uv run alembic revision --autogenerate -m "add rooms table"
uv run alembic upgrade head
uv run alembic downgrade -1
```

Import every new model module in `app/models/__init__.py` so autogenerate can see it.

## Layout

```
app/
  main.py          create_app(): lifespan (engine), CORS, routers
  core/config.py   Settings (pydantic-settings)
  db/session.py    async engine, session factory, SessionDep
  api/             routers (thin: parse, call service, return)
  services/        business logic
  models/          SQLAlchemy models (Base with naming convention)
  schemas/         Pydantic request/response models
  realtime/        WebSocket broadcasting (not implemented yet)
alembic/           migrations (env.py reads DATABASE_URL from settings)
tests/             pytest (conftest.py sets up the test database)
```
