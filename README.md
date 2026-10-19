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

## First admin on a fresh install

The seed script is for development only. On a real install, after the migrations, create
the first admin from the server directory (the password is asked for twice, never put on
the command line):

```sh
uv run python -m app.scripts.create_admin --username admin --full-name "Ada Admin"
# with Docker: docker compose exec server python -m app.scripts.create_admin ...
# automation: printf '%s\n' "$PASSWORD" | ... --password-stdin
```

It refuses while the property has an active admin (create further admins in the app). It
also works as a recovery path when every admin is deactivated, expired or never activated.

## Setting up rooms

Rooms and room types are managed under **Admin > Rooms**, which needs the `room.manage`
permission (only the Admin role has it by default).

1. **Room types** tab: add the kinds of rooms the hotel sells. Each has a short code
   (e.g. `DLX`, stored in capitals and unique), a name, the maximum number of guests
   (1–12), an optional bed setup ("1 king") and a description.
2. **Rooms** tab: add each room with its number (e.g. `304` or `PH1`), floor and room type.
   Only active room types can be chosen. New rooms start *Ready* (inspected) and appear on
   the front desk dashboard at once, for everyone, without reloading.
3. Edit a room's number, floor, type or notes at any time, except that its type cannot
   change while a guest is checked in. If someone else changed the room meanwhile, you are
   told and the list reloads.

Nothing is ever deleted; rooms and room types are **retired** instead, and can be
reactivated. A retired room leaves the dashboard and all counts, and cannot be checked in
to. The app explains when something cannot be retired:

- a room with a guest checked in, or with upcoming reservations assigned to it;
- a room type that still has active rooms, or upcoming reservations booked as that type.

A room of a retired type cannot be reactivated until the type is. Every change is in the
audit log (**Admin > Audit log**, filter *Room setup*).

## Users and permissions

**Model.** A *permission* is a key such as `reservation.check_in`, defined in code
(`server/app/core/permissions.py`, the single catalog). A *role* is a named bundle of
permissions stored in the database, per property. Each user has at most one role per
property. Six system roles are created for every property; the **Admin** role always has
every permission (including ones added later) and cannot be changed at all. Other system
roles keep their name and description but their permissions can be edited; custom roles
can be created, copied, edited and deleted (only when nobody has them).

**Enforcement.** The server checks a permission on every request
(`require_permission(P.X)`), and the rules that depend on data live in the services. The
client only hides what you cannot use, as a convenience. A user's effective permissions
are read fresh on every request: changing a role, a role's permissions, deactivating or
expiring a user takes effect immediately, including on open WebSockets (closed with
4401/4403, or told `permissions_changed` so the page refetches).

**Guard rules** (all enforced on the server, with tests in
`server/tests/test_privilege_escalation.py`):

- You cannot grant a permission you do not hold: not by editing a role, creating or
  copying one, or assigning one to a user.
- You cannot manage someone more powerful than you: users whose role, at *any* property,
  grants something you do not hold there; roles that contain such permissions.
- You cannot act on yourself in ways that lock you out: change your own role, deactivate
  or expire yourself.
- A property always keeps at least one active Admin (not deactivated, expired or merely
  invited); concurrent changes are serialised with a row lock.
- The Admin role cannot be changed or deleted; system roles cannot be renamed or deleted;
  a role that someone has cannot be deleted.
- Users are never deleted, only deactivated. Audit rows can never be changed or deleted
  (database trigger).

**Adding a permission.**

1. Add a constant to `P` and an entry to `PERMISSIONS` in
   `server/app/core/permissions.py` (key `area.verb`, description, group). On startup it
   is synced to the database and granted to every Admin role. Add it to `DEFAULT_ROLES`
   for roles that should get it on new properties; existing roles get it through the
   roles screen.
2. Protect the endpoint with `Depends(require_permission(P.NEW_KEY))`, or check it in the
   service if it depends on data.
3. List the endpoint in `ACCESS` in `server/tests/test_security.py`; the automatic tests
   then check it with and without the permission for every default role.
4. In the client, add the key to `P` in `client/src/auth/permissions.ts` and use
   `can(P.NEW_KEY)` / `<Can permission=...>`; regenerate the API types.

**Accounts.** New users are *invited*: they choose their password through a single-use
link (72 hours; resets 1 hour). Links carry the token after `#`, which browsers never
send to a server, and the API takes it in the request body, so tokens never land in
logs. Rejected links, failed logins and forgot-password requests are rate-limited per IP
(and per username/email). Passwords need 12+ characters and are checked against a list of
common passwords. Users with an access end date are swept every 5 minutes
(`EXPIRY_SWEEP_INTERVAL_SECONDS`): sessions revoked, sockets closed, one `user.expired`
audit entry (`python -m app.scripts.expire_users` does the same from cron).

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
- The audit trail (data changes and sign-ins) is append-only: a database trigger refuses
  updates and deletes. The app's database user owns the tables, so it could still drop the
  trigger; a separate restricted database role would close that gap.

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
3. **Unassigned arrivals can't be checked in from the dashboard yet.** The API can assign
   rooms (`/assign-room`) and the summary lists today's arrivals (unassigned first); the
   screen for it is the next step.
4. **No real email backend yet** (only `console` for development and `disabled`). Until
   one is added, admins share invite links in person, password resets by email fail with
   a clear error, and forgot-password sends nothing.
5. **Room attendants have no UI.** They can use the API but lack `dashboard.view`; a
   housekeeping view is a natural next feature.
6. **Old sessions and audit rows are never purged.** Expired users' sessions are revoked
   by the sweep, but rows stay. Audit rows are append-only by design; a retention policy
   (e.g. archiving `audit_events`, which holds IP addresses) needs a deliberate,
   privileged procedure that drops the trigger.
7. **Overstays are not billed for the extra nights.** Early check-out shortens the stay
   and its total; a guest who stays past the booked date keeps the booked dates and price.
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

### Users and permissions: remaining risks and next steps

13. **No multi-factor authentication.** A stolen password is enough. Add TOTP or WebAuthn,
    required first for roles with `user.manage`, `role.manage` or `audit.view`, with
    recovery codes and an admin-assisted reset.
14. **No single sign-on.** Hotel groups will want OIDC/SAML (Entra ID, Google). Keep the
    local Admin account as a break-glass login, map identity-provider groups to roles per
    property, and keep deactivation in sync (SCIM, or a sign-in-time check).
15. **No manager override at the desk.** Some actions (refunds, rate overrides, early
    check-in) need a manager's approval at the moment, without a full sign-in switch.
    Suggested design: a manager PIN or quick re-authentication that grants one specific
    permission for one action, audited with both the clerk and the approving manager.
16. **The database user owns the audit tables**, so it could drop the append-only
    trigger. Run the app as a separate PostgreSQL role with only INSERT/SELECT on audit
    tables (migrations as the owner).
17. **Admins see activation links for users without email.** By design (hand over in
    person), and audited, but whoever holds the link can set that user's password. Prefer
    giving every user an email address; consider a first-login confirmation step.
18. **Users are global, not per property.** The users list shows everyone; with several
    properties, scope the list to users with a role at the current property.
19. **Sessions have an absolute lifetime (8 h) but no idle timeout**, and users cannot see
    or end their own other sessions. Add an idle timeout for shared front desk PCs and a
    "your sessions" page.
20. **Passwords are checked against a bundled common-password list only.** Add a breached
    password check (e.g. the k-anonymity Pwned Passwords API).
21. **Roles have no version number**: two admins editing the same role at once means the
    last save wins (both are audited). Add optimistic locking like rooms have.
22. **Accessibility was checked with axe, keyboard-only tests and a code review, not with
    a real screen reader.** Test the admin screens with NVDA (Windows) and VoiceOver (macOS
    and iOS) before relying on them.

