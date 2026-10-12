# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## Project

A hotel management system. The first feature is the **front desk dashboard**:

- Front desk staff log in with a username and password.
- After login they see a live dashboard of all rooms showing:
  - occupancy status
  - housekeeping status
  - current guest
  - flags (e.g. maintenance, VIP, do-not-disturb; defined per room)
- The dashboard updates in real time as room status changes.

Status: server has data model + dev seed, auth (`/api/auth/*`), dashboard reads (`GET /api/dashboard/rooms`, `/summary`) and actions (`POST /api/reservations/{id}/check-in|check-out`, `PATCH /api/rooms/{id}/housekeeping-status`) publishing to an in-process event bus, and the WebSocket hub at `/ws`. Not yet: client. `client/` holds only a README.

## Architecture

Monorepo:

```
/client   React single-page app
/server   FastAPI backend
docker-compose.yml   PostgreSQL (and later other services)
```

### Client (`/client`)
- React + TypeScript, built with Vite
- Tailwind CSS for styling
- React Router for routing
- TanStack Query for server state (fetching, caching, invalidation)
- WebSocket messages update the TanStack Query cache; do not keep a second copy of server state in component state

### Server (`/server`)
- Python, FastAPI
- Pydantic for request/response schemas and settings
- SQLAlchemy 2.x, async (`AsyncSession`, asyncpg driver; 2.0-style typed ORM: `Mapped[...]`, `mapped_column`, `select()`)
- Alembic for migrations; every schema change ships with a migration. New model modules must be imported in `app/models/__init__.py`.
- uv for dependencies (`pyproject.toml` + `uv.lock`); ruff for lint/format; mypy strict
- Layout:
  ```
  server/app/
    main.py          create_app() factory: lifespan creates the engine, CORS, router registration
    core/config.py   Settings (pydantic-settings), get_settings()
    db/session.py    async engine/session factory, SessionDep dependency
    api/             HTTP + WebSocket endpoints (thin: parse, call service, return); api/router.py mounts under /api
    services/        business logic (all of it lives here)
    models/          SQLAlchemy models (Base has a constraint naming convention)
    schemas/         Pydantic models
    realtime/        WebSocket broadcasting
  server/tests/      pytest tests (conftest.py creates + migrates the test database)
  server/alembic/    migrations
  ```
- Data model rules: room occupancy is derived (a `checked_in` reservation assigned to the room), not stored. Reservation dates are plain `DATE`s, half-open `[check_in, check_out)`. `rooms.version` is SQLAlchemy optimistic locking (`StaleDataError` on conflict). Usernames are stored lowercase.
- Business date: "today" is always `hotel_today(settings.hotel_tz)` (`HOTEL_TIMEZONE`, default Europe/Stockholm), never `date.today()`. Routes get it via `HotelTodayDep`; tests override `get_hotel_today`.
- Dashboard occupancy derivation lives in `services/dashboard.derive_occupancy` (pure, unit-tested). Precedence: in-house guest (occupied / departing_today, incl. overstays) > out_of_order > arriving_today > vacant; `has_arrival_today` flags turnover rooms. Dashboard reads use a fixed number of queries (tests assert the count); relationships are `lazy="raise"` to catch N+1.
- Mutations (pattern in `services/reservations.py`, `services/rooms.py`): one transaction per action; client sends the room `version` it saw (`ensure_version`), SQLAlchemy's version check catches races at commit (`room_changes.commit` -> `VersionConflictError`); call `touch(room)` when an action changes a room's dashboard state without changing a room column; write an audit row (`record_event`, details with `before`/`after`) in the same transaction; after commit call `publish_room_changes` for every affected room. Read anything needed for error messages before committing (rollback expires loaded objects).
- Services raise `services/errors.py` `DomainError` subclasses; `main.py` maps them to `{"detail", "code"}` with 404 (NotFoundError) or 409 (everything else). Allowed housekeeping transitions are the `HOUSEKEEPING_TRANSITIONS` table in `services/rooms.py`.
- Event bus (`app/realtime/bus.py`, on `app.state.event_bus`) is in-process only; multiple instances need Redis pub/sub or Postgres LISTEN/NOTIFY (see the scaling note in `app/realtime/hub.py`).
- WebSocket `/ws` (protocol documented at the top of `app/realtime/hub.py`): messages are `{type, seq, payload}`; `seq` increments only for broadcast events (`room_status_changed`, `summary_changed`); `hello`/`ping` carry the latest seq. Client must answer `ping` with `{"type":"pong"}`. Close codes: 4401 unauthenticated/session ended, 4403 forbidden (role or Origin), 4408 heartbeat timeout, 1013 too slow. Dashboard roles only. Clients should connect first, then fetch REST, then apply events; refetch on a seq gap or reconnect.
- Mutations publish room events then one `summary_changed`; logout/re-login publish an internal `session_ended` the hub uses to close that session's sockets.
- Driver note: use asyncpg, not psycopg async; psycopg's async mode does not work with the default Windows event loop.

### Database
- PostgreSQL, run locally via `docker-compose`

### Auth
- Our own credential login. No third-party identity provider.
- Passwords are stored hashed with a modern password hashing algorithm (e.g. Argon2 or bcrypt). Never store or log plaintext passwords.
- On login the server issues a session in a secure, HTTP-only cookie (`Secure`, `HttpOnly`, appropriate `SameSite`). The client never reads or stores the token in JavaScript.
- Each user has roles; endpoints check roles on the server. The client hides UI by role only as a convenience, never as the enforcement.
- The WebSocket connection is authenticated with the same session cookie.
- Implementation (server): Argon2 via pwdlib (`app/core/security.py`). Sessions live in the `sessions` table; the cookie holds a random token and the DB stores only its HMAC keyed by `SESSION_SECRET`. Logout/revocation sets `revoked_at`.
- Protect routes with `CurrentUser` or `Depends(require_role(...))` from `app/api/deps.py`; resolve a session token to a user with `services.auth.get_user_for_session` (reuse it for the WebSocket).
- Failed logins return one generic 401 message. Throttling (429) counts `auth.login_failed` rows in `audit_events` per username and per IP over a window (`LOGIN_*` settings). Auth events are written to `audit_events`.

### Realtime
- A WebSocket endpoint pushes room status changes to connected dashboards.
- **All mutations go through REST.** The WebSocket only sends from server to client; it never accepts writes.
- After a REST mutation succeeds, the service layer publishes the change so it is broadcast over the WebSocket.

## Conventions

- **Library versions:** use the current stable version of every library. Check the official docs for the installed version rather than assuming APIs from memory, especially for SQLAlchemy 2.x, Pydantic v2, TanStack Query, React Router and Tailwind, whose APIs have changed across major versions.
- **TypeScript:** strict mode on. No `any`, including implicit `any`; use `unknown` and narrow it instead.
- **Python:** type hints on every function, method and module-level variable. Tests use pytest.
- **Business logic** lives in `server/app/services`. Routers stay thin and contain no business rules or direct query logic.
- **Tests:** every endpoint (REST and WebSocket) has tests, covering the success path, auth/role failures and validation errors.
- **Commits:** small, one feature or fix per commit, with tests in the same commit.
- **Secrets:** never put secrets in code or commit them. Configuration comes from `.env` files, which are gitignored. Keep a committed `.env.example` listing every variable with placeholder values, and update it whenever a new variable is added.

## Commands

Database (from the repo root; needs a `.env` copied from `.env.example`):
- start: `docker compose up -d db`
- check health: `docker compose ps` (STATUS should show `healthy`)
- psql shell: `docker compose exec db psql -U hotel -d hotel`
- stop: `docker compose down` (add `-v` to delete the data volume)

Server (from `server/`; uv may need to be invoked as `python -m uv` if its Scripts dir is not on PATH):
- install: `uv sync`
- migrate: `uv run alembic upgrade head`; new migration: `uv run alembic revision --autogenerate -m "..."`
- seed dev data: `uv run python -m app.scripts.seed` (`--reset` wipes app tables first); dev logins are `<username>` / `<username>-dev-pass` for frontdesk, housekeeping, manager, admin
- run: `uv run uvicorn app.main:create_app --factory --reload` (http://localhost:8000)
- test: `uv run pytest` (needs the db container; uses TEST_DATABASE_URL). API tests share one rolled-back transaction via the `app`/`client`/`db_session` fixtures; use `make_user` to create users. WebSocket tests need committed data: use `live_db` + `async with live_clients(test_settings) as make:` (not a fixture, because of anyio task-group rules)
- lint/format/types: `uv run ruff check .`, `uv run ruff format .`, `uv run mypy app tests alembic/env.py`

Not set up yet; record here once they exist:
- install and run the client, type-check, lint, build
