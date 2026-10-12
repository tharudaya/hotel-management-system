# Hotel Management System

A hotel management system, starting with a **live front desk dashboard**: staff sign in and
see every room's occupancy, housekeeping status, current guest and flags, updated in real
time as anyone checks guests in or out or changes a room's status.

- `server/` — FastAPI, PostgreSQL, SQLAlchemy (async), WebSocket hub ([server/README.md](server/README.md))
- `client/` — React, TypeScript, Vite, Tailwind, TanStack Query ([client/README.md](client/README.md))
- `docker-compose.yml` — database, API and client for local development
- `CLAUDE.md` — conventions and design rules for anyone (human or AI) changing the code

## Quick start (Docker)

Requires Docker with Compose v2.

```sh
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD (and the same password inside DATABASE_URL and
# TEST_DATABASE_URL), and set SESSION_SECRET to a random value:
#   python -c "import secrets; print(secrets.token_urlsafe(64))"

docker compose up --build -d                                   # db + server + client
docker compose exec server python -m app.scripts.seed --reset   # demo data for today
```

Open **http://localhost:5173** and sign in (see [Demo accounts](#demo-accounts)). The API is
on http://localhost:8000 (interactive docs at `/docs`). Code changes reload automatically
in both containers. Stop with `docker compose down`; add `-v` to also delete the database.

## Local development without Docker for the apps

Requires Python 3.14 with [uv](https://docs.astral.sh/uv/) and Node 22.22+. The database
still runs in Docker.

```sh
cp .env.example .env              # as above; keep DATABASE_URL pointing at localhost
docker compose up -d db

cd server
uv sync
uv run alembic upgrade head
uv run python -m app.scripts.seed --reset
uv run uvicorn app.main:create_app --factory --reload      # http://localhost:8000

cd ../client                      # in a second terminal
npm install
npm run dev                       # http://localhost:5173 (proxies /api and /ws)
```

For plain-HTTP local development set `SESSION_COOKIE_SECURE=false` in `.env`.
Don't run both this and the Docker apps at once: they use the same ports.

## Demo accounts

Created by the seed script, **for development only** (the script refuses to run when
`ENVIRONMENT=production`):

| Role                    | Username       | Password                | Can do                                                                         |
| ----------------------- | -------------- | ----------------------- | ------------------------------------------------------------------------------ |
| Admin                   | `admin`        | `admin-dev-pass`        | Everything                                                                     |
| General manager         | `manager`      | `manager-dev-pass`      | Everything except property settings                                            |
| Front office manager    | `fomanager`    | `fomanager-dev-pass`    | Dashboard, rooms (incl. out of order), reservations, guests, payments, reports |
| Receptionist            | `frontdesk`    | `frontdesk-dev-pass`    | Dashboard, check in/out, housekeeping status (not out of order)                |
| Housekeeping supervisor | `housekeeping` | `housekeeping-dev-pass` | Dashboard (no reservation details), housekeeping incl. out of order            |
| Room attendant          | `attendant`    | `attendant-dev-pass`    | Housekeeping status via the API (no dashboard)                                 |

Roles are bundles of permissions stored per property; see "Users and permissions" in
`CLAUDE.md`. The permission catalog is in `server/app/core/permissions.py`.

The seed creates 40 rooms on 4 floors with today's arrivals, departures, stayovers, a
turnover room (departure and arrival on the same day), vacant and out-of-order rooms.
"Today" is the hotel's business date (`HOTEL_TIMEZONE`); reseed with `--reset` on another
day to get fresh data.

## Tests and checks

| What                         | Command                                                                                         |
| ---------------------------- | ----------------------------------------------------------------------------------------------- |
| Server tests (pytest)        | `cd server && uv run pytest` (needs the db container)                                           |
| Server lint / format / types | `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy app tests alembic/env.py` |
| Migrations match models      | `uv run alembic check`                                                                          |
| Client tests (Vitest + RTL)  | `cd client && npm test`                                                                         |
| Client lint / format / types | `npm run lint` · `npm run format:check` · `npm run typecheck`                                   |
| Client production build      | `npm run build`                                                                                 |
| End-to-end realtime check    | `npm run test:e2e` (needs the full stack running and freshly seeded)                            |
| Server tests in Docker       | `docker compose exec server pytest`                                                             |

The server suite runs against a separate `hotel_test` database that it creates and
migrates itself. The client suite includes automated accessibility checks (axe-core) for
the login page, dashboard and detail panel. The end-to-end check logs in twice, checks a
guest out in one session and requires the other to see it within one second.

## Architecture

```
 Browser (React SPA)                     FastAPI server                      PostgreSQL
 ───────────────────                     ──────────────                      ──────────
 TanStack Query cache ── REST /api ───▶  routers (thin) ──▶ services ──────▶ rooms, reservations,
   ▲                    (cookie)                            │  one tx,        guests, users,
   │                                                        │  audit row      sessions, audit_events
   │                                                        ▼
   └──── WebSocket /ws ◀── hub ◀── in-process event bus ◀── publish after commit
         (cookie)          seq numbers, heartbeat
```

- **One origin.** In development Vite serves the app and proxies `/api` and `/ws` to the
  API, so the session cookie is first-party and the browser never makes cross-origin calls.
- **Auth.** Username and password (Argon2 hashes) create a server-side session. The cookie
  carries only a random token; the database stores its HMAC. Sessions can be revoked, and
  logging out closes that session's WebSockets. Failed logins are rate limited and audited.
- **Permissions are enforced on the server** for every route. Users get a role per
  property; roles are database bundles of permissions from a catalog defined in code. A
  test lists the permission each operation needs and fails if a new endpoint isn't
  classified. The client only hides UI.
- **Reads.** `GET /api/dashboard/rooms` and `/summary` derive each room's occupancy
  (vacant, occupied, arriving today, departing today, out of order) from reservations, for
  the hotel's business date, in a fixed number of queries.
- **Writes go through REST only.** Check-in, check-out and housekeeping changes each run
  in one transaction with an audit row, are validated in the service layer (allowed
  housekeeping transitions are an explicit table), and use the room's `version` for
  optimistic locking: a stale or concurrent change gets `409` and the client refetches.
- **Realtime.** After a write commits, the service publishes `room_status_changed` (the
  room's new dashboard entry) and `summary_changed`. The hub numbers events and pushes
  them to every dashboard. The client patches its cache from events (batched, version
  guarded), refetches the full snapshot after every (re)connect or sequence gap, and shows
  Live / Reconnecting / Offline in the header.

More detail: [server/README.md](server/README.md), [client/README.md](client/README.md),
the WebSocket protocol at the top of `server/app/realtime/hub.py`, and `CLAUDE.md`.

### Security summary

- Session cookie: `HttpOnly`, `SameSite=Lax`, `Secure` (required in production).
- CORS allows only the origins in `CORS_ORIGINS`, with credentials; `*` is rejected.
- State-changing API requests and WebSocket connections from any other `Origin` are refused.
- Responses carry `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`;
  API responses are `Cache-Control: no-store` (they contain guest data).
- Production refuses to start with insecure cookies or a weak `SESSION_SECRET`, and hides
  `/docs` and `/openapi.json`.
- Input is validated by Pydantic schemas (lengths, ranges, enums); database constraints
  back up the business rules (no double-booking, one guest party per room, valid dates).
- Secrets live only in `.env` (git-ignored); `.env.example` lists every setting.
- Invite and password-reset links are single use, expire (72 h / 1 h), and only a hash
  of the token is stored. Forgot-password gives the same answer whether or not the
  account exists. New passwords need 12+ characters and are checked against a list of
  common passwords. Production refuses the console email backend.

## Configuration

All settings are environment variables, documented in [.env.example](.env.example). The
most important: `DATABASE_URL`, `SESSION_SECRET`, `SESSION_COOKIE_SECURE`, `CORS_ORIGINS`,
`HOTEL_TIMEZONE`, `HOTEL_NAME`, `ENVIRONMENT`, `APP_BASE_URL` (used in emailed links) and
`EMAIL_BACKEND` (`console` prints emails, including links, to the server log in
development).

## Known limitations and next steps

1. **Single server process.** The event bus is in-process; running several API instances
   needs Redis pub/sub or Postgres LISTEN/NOTIFY (and a shared sequence). Until then, run one.
2. **No production deployment yet.** Only development images exist. A production setup
   needs a built client served as static files, a non-reload server image, TLS, a
   Content-Security-Policy, and `--forwarded-allow-ips` so client IPs (used for login
   throttling and audit) are correct behind a proxy.
3. **Unassigned arrivals can't be checked in from the dashboard.** They are counted in the
   summary but have no tile; a "today's arrivals" list with room assignment is needed.
4. **User management is API-only and incomplete.** `/api/admin/users` creates, edits,
   (de)activates users, assigns roles, revokes sessions and issues invite/reset links, but
   the admin UI covers users and roles; the audit log (`/api/admin/audit`) has an API but
   no screen yet.
   There is no real email backend yet (only `console` and `disabled`); without one,
   admins share links in person and forgot-password sends nothing.
5. **Room attendants have no UI.** They can use the API but lack `dashboard.view`; a
   housekeeping view is a natural next feature.
6. **Expired sessions and audit rows are never purged.** Add a periodic cleanup job and a
   retention policy for `audit_events` (it contains IP addresses).
7. **Early check-out keeps the booked dates**, so it is not counted in "departures today".
8. **The client copies the housekeeping transition table** to decide which buttons to show;
   keep it in sync with the server (or serve it from an endpoint).
9. **`npm run generate:api` needs the server running**; a schema export script would let CI
   check that the generated types are current.
10. **Do-not-disturb flag is missing.** The original brief lists per-room flags
    (maintenance, VIP, do-not-disturb). Maintenance is covered by "out of order" and VIP by
    the reservation, but there is no do-not-disturb flag yet (needs a column, an action and
    a tile badge).
11. **Login throttling can lock out a known username** for 15 minutes after five wrong
    passwords (by design, but an attacker can use it to annoy a user). Consider throttling
    per username _and_ IP pair, CAPTCHA, or alerting instead.
12. **TypeScript is pinned to 6.0** until `typescript-eslint` supports 7, and
    `openapi-typescript` uses an npm override for its TypeScript peer.
