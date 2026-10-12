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

Status: server has data model + dev seed, auth (`/api/auth/*`), dashboard reads (`GET /api/dashboard/rooms`, `/summary`) and actions (`POST /api/reservations/{id}/check-in|check-out`, `PATCH /api/rooms/{id}/housekeeping-status`) publishing to an in-process event bus, and the WebSocket hub at `/ws`. Client has login, protected routes, and the dashboard page (summary, filters, room grid, detail panel with actions). The dashboard is live over the WebSocket.

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
- Versions: React 19, React Router 8 (data mode: `createBrowserRouter` from `react-router`, `RouterProvider` from `react-router/dom`; there is no `react-router-dom`), Tailwind v4 via `@tailwindcss/vite` (`@import "tailwindcss"`, no config file), Vitest 5, ESLint 10 flat config. TypeScript pinned to 6.0 (typescript-eslint does not support 7).
- All requests go through `src/api/client.ts` `apiFetch` (relative `/api/...` paths, `credentials: 'include'`, throws `ApiError` with `status`/`code`; 401 redirects to /login unless `redirectOnUnauthorized: false`). Response types come from generated `src/api/schema.ts` via `src/api/types.ts`; never hand-write API types.
- Routing: `src/routes.tsx` is the route table (browser router in `main.tsx`, memory router in tests). Signed-in pages sit under `ProtectedRoute` (redirects to /login on 401 only, remembering `from`) and `AppLayout` (header + sign out); read the user with `useAuthenticatedUser()`.
- Dashboard (`src/dashboard/`): data hooks and query keys in `queries.ts` (`useRoomAction` updates the rooms cache from the response and invalidates `['dashboard']` + `['reservations']`); filters live in the URL (`filters.ts`, params `floor,type,occupancy,hk,q,view` plus `room` for the open panel); status labels/icons/colors and tile accessible labels in `status.ts` (never color alone); `housekeeping.ts` mirrors the server transition table for UI only. UI is shown/hidden by permission with `can(user, P.X)` from `src/auth/permissions.ts` (`/api/auth/me` returns `role` and `permissions`); never check role names in the client.
- Realtime (`src/realtime/`): `connection.ts` is a framework-free `RealtimeConnection` (pong replies, per-connection seq baseline from `hello`, ignores seq <= last, resyncs on gaps, 50 ms batching, exponential backoff with jitter capped at 30 s, no retry after 4401/4403). `cache.ts` `applyEvents` patches the rooms/summary caches (version-guarded; unchanged rooms keep identity so memoised tiles skip renders). `useRealtime` (mounted once in `AppLayout`, only with `dashboard.view`) refetches the snapshot on every `hello` and replays events received during that refetch. The header shows `ConnectionIndicator`.
- Client tests: `src/test/renderApp.tsx` renders the real routes at a path; `mockApi({'GET /api/...': () => [status, body]})` fakes the backend and fails on unexpected requests. Query by role/label (accessibility-first). A `FakeWebSocket` is installed globally in test setup (drive it with `FakeWebSocket.latest().hello()/receive()/serverClose()`). Shared data in `src/test/fixtures.ts` (`ROOMS` covers every status; `dashboardHandlers()` for pages that just need the dashboard to load).

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
- Services raise `services/errors.py` `DomainError` subclasses; `main.py` maps them to `{"detail", "code"}` with 404 (NotFoundError) or 409 (everything else). Allowed housekeeping transitions are the `HOUSEKEEPING_TRANSITIONS` table in `services/rooms.py`. Staff-facing housekeeping names differ from the stored values: `clean` is shown as **Inspection** (awaiting inspection) and `inspected` as **Ready** (both can be checked in); keep `HOUSEKEEPING_LABELS`/`HOUSEKEEPING_PHRASES` (server `services/rooms.py`) and `HOUSEKEEPING` (client `dashboard/status.ts`) in sync.
- Event bus (`app/realtime/bus.py`, on `app.state.event_bus`) is in-process only; multiple instances need Redis pub/sub or Postgres LISTEN/NOTIFY (see the scaling note in `app/realtime/hub.py`).
- WebSocket `/ws` (protocol documented at the top of `app/realtime/hub.py`): messages are `{type, seq, payload}`; `seq` increments only for broadcast events (`room_status_changed`, `summary_changed`); `hello`/`ping` carry the latest seq. Client must answer `ping` with `{"type":"pong"}`. Close codes: 4401 unauthenticated/session ended, 4403 forbidden (missing `dashboard.view`, or Origin), 4408 heartbeat timeout, 1013 too slow. Requires `dashboard.view`. Clients should connect first, then fetch REST, then apply events; refetch on a seq gap or reconnect.
- Mutations publish room events then one `summary_changed`; logout/re-login publish an internal `session_ended` the hub uses to close that session's sockets; `permissions_changed` (internal) makes the hub re-check access.
- Security (server): `app/core/security_middleware.py` refuses state-changing `/api` requests whose `Origin` is not in `CORS_ORIGINS` (or same origin) and adds security headers + `Cache-Control: no-store` on `/api`. `Settings` refuses unsafe production values (insecure cookie, weak `SESSION_SECRET`, `*` origin); production hides `/docs` and `/openapi.json`.
- Every REST operation must be listed in `ACCESS` in `tests/test_security.py` (the permission it needs, or public / any signed-in user); that test fails for unclassified endpoints and checks 401/403 for every default role.
- Driver note: use asyncpg, not psycopg async; psycopg's async mode does not work with the default Windows event loop.

### Database
- PostgreSQL, run locally via `docker-compose`

### Auth
- Our own credential login. No third-party identity provider.
- Passwords are stored hashed with a modern password hashing algorithm (e.g. Argon2 or bcrypt). Never store or log plaintext passwords.
- On login the server issues a session in a secure, HTTP-only cookie (`Secure`, `HttpOnly`, appropriate `SameSite`). The client never reads or stores the token in JavaScript.
- Each user has a role per property; endpoints check permissions on the server (see Users and permissions). The client hides UI by permission only as a convenience, never as the enforcement.
- The WebSocket connection is authenticated with the same session cookie.
- Implementation (server): Argon2 via pwdlib (`app/core/security.py`). Sessions live in the `sessions` table; the cookie holds a random token and the DB stores only its HMAC keyed by `SESSION_SECRET`. Logout/revocation sets `revoked_at`.
- Protect routes with `CurrentUser` or `Depends(require_permission(P.X))` from `app/api/deps.py` (`CurrentPermissions` gives the set for checks that depend on data; raise `PermissionDeniedError` for a 403); resolve a session token to a user with `services.auth.get_user_for_session` (reuse it for the WebSocket).
- Failed logins return one generic 401 message. Throttling (429) counts `auth.login_failed` rows in `audit_events` per username and per IP over a window (`LOGIN_*` settings). Auth events are written to `audit_events`.

### Users and permissions

Design rules:
- Permissions are defined in code in `server/app/core/permissions.py` as a single catalog (key, description, group). On startup they are synced to the `permissions` table: new keys inserted, removed keys deleted along with their `role_permissions` rows.
- Roles live in the database and are bundles of permissions. System roles (`is_system = true`) cannot be deleted. The Admin system role always has every permission and cannot be edited.
- Roles are assigned to users per property via `user_property_roles`. One role per user per property for now.
- Users are never deleted, only deactivated.
- Guard rules to enforce later in services: a user cannot grant permissions they do not have, cannot deactivate themselves or remove their own admin role, and each property must always keep at least one active admin.

Implementation notes:
- Check permission keys (`P.RESERVATION_CHECK_IN`), never role names. Add a permission by adding it to `PERMISSIONS` in `core/permissions.py`; the startup sync (`services/permissions.sync_permission_catalog`) inserts it and grants it to Admin roles. Migrations never import the catalog (they carry a frozen copy).
- `get_effective_permissions(db, user_id, property_id)` is the one source of a user's rights: empty for deactivated (`deactivated_at` set) or expired (`expires_at` passed) users and for users without a role at the property; the whole catalog for the Admin system role; keys not in the code catalog are ignored. Deactivated or expired users also cannot log in, and their sessions and WebSockets end.
- Default system roles per property (`DEFAULT_ROLES`): Admin, General manager, Front office manager, Receptionist, Housekeeping supervisor, Room attendant.
- Until rooms and reservations are property-scoped, requests act on the single property (`get_default_property_id`, `CurrentPropertyId`).
- Effective permissions are loaded once per request and cached on `request.state.permissions` (`get_current_permissions` in `api/deps.py`); `require_permission` reads that.
- `GET /api/auth/me` returns user info, the active `property` (`id`, `name`, `time_zone`), `role` (`id`, `name` or null) and sorted `permissions`; login and the WebSocket `hello` use the same shape.
- Any service that changes someone's effective permissions (role assignment, role permission edits, deactivation) must call `services/permissions.publish_permissions_changed(bus, property_id, user_ids)` after commit (`user_ids=None` = everyone at the property). The hub then re-checks those sockets immediately and closes ones without `dashboard.view` (4403) or with an ended session (4401); every heartbeat re-checks too, as a backstop.
- Data-dependent checks live in services: moving a room into or out of `out_of_order` needs `room.set_out_of_order`; guest email/phone in reservation details need `guest.view_contact`.
- Tests: `make_user(username, role=Roles.RECEPTIONIST, deactivated=..., expires_at=...)` and `login_as(Roles.X)` (role names in `tests/conftest.py`); `role=None` creates a user without a role.

### Realtime
- A WebSocket endpoint pushes room status changes to connected dashboards.
- **All mutations go through REST.** The WebSocket only sends from server to client; it never accepts writes.
- After a REST mutation succeeds, the service layer publishes the change so it is broadcast over the WebSocket.

## Conventions

- **Library versions:** use the current stable version of every library. Check the official docs for the installed version rather than assuming APIs from memory, especially for SQLAlchemy 2.x, Pydantic v2, TanStack Query, React Router and Tailwind, whose APIs have changed across major versions.
- **TypeScript:** strict mode on. No `any`, including implicit `any`; use `unknown` and narrow it instead.
- **Python:** type hints on every function, method and module-level variable. Tests use pytest.
- **Business logic** lives in `server/app/services`. Routers stay thin and contain no business rules or direct query logic.
- **Tests:** every endpoint (REST and WebSocket) has tests, covering the success path, auth/permission failures and validation errors.
- **Commits:** small, one feature or fix per commit, with tests in the same commit.
- **Secrets:** never put secrets in code or commit them. Configuration comes from `.env` files, which are gitignored. Keep a committed `.env.example` listing every variable with placeholder values, and update it whenever a new variable is added.

## Commands

Full stack in Docker (from the repo root; needs `.env`): `docker compose up --build -d` (db + server with migrations and reload + client on :5173); seed with `docker compose exec server python -m app.scripts.seed --reset`; server tests with `docker compose exec server pytest`. The server image keeps its venv in `/opt/venv`; the client container keeps Linux `node_modules` in a volume.

Database (from the repo root; needs a `.env` copied from `.env.example`):
- start: `docker compose up -d db`
- check health: `docker compose ps` (STATUS should show `healthy`)
- psql shell: `docker compose exec db psql -U hotel -d hotel`
- stop: `docker compose down` (add `-v` to delete the data volume)

Server (from `server/`; uv may need to be invoked as `python -m uv` if its Scripts dir is not on PATH; if the project venv is activated, `python` is `.venv` Python without uv, so use `.venv/Scripts/python -m <module>` or deactivate):
- install: `uv sync`
- migrate: `uv run alembic upgrade head`; new migration: `uv run alembic revision --autogenerate -m "..."`
- seed dev data: `uv run python -m app.scripts.seed` (`--reset` wipes app tables first, keeping properties/roles/permissions); dev logins are `<username>` / `<username>-dev-pass`: admin (Admin), manager (General manager), fomanager (Front office manager), frontdesk (Receptionist), housekeeping (Housekeeping supervisor), attendant (Room attendant)
- run: `uv run uvicorn app.main:create_app --factory --reload` (http://localhost:8000)
- test: `uv run pytest` (needs the db container; uses TEST_DATABASE_URL). API tests share one rolled-back transaction via the `app`/`client`/`db_session` fixtures; use `make_user` to create users. WebSocket tests need committed data: use `live_db` + `async with live_clients(test_settings) as make:` (not a fixture, because of anyio task-group rules)
- lint/format/types: `uv run ruff check .`, `uv run ruff format .`, `uv run mypy app tests alembic/env.py`

Client (from `client/`):
- install: `npm install`
- run: `npm run dev` (http://localhost:5173; proxies `/api` and `/ws` to `BACKEND_URL`, default http://localhost:8000)
- checks: `npm run typecheck`, `npm run lint`, `npm run format:check`, `npm test` (includes axe-core accessibility tests in `src/a11y.test.tsx`), `npm run build`
- end-to-end (needs db, backend seeded with `--reset`, and `npm run dev` running): `npm run test:e2e` (two logged-in clients; a check-out in one must reach the other within 1 s)
- API types: `npm run generate:api` with the server running; commit `src/api/schema.ts`
