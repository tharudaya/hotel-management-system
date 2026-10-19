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

Status: server has data model + dev seed, auth (`/api/auth/*`), dashboard reads (`GET /api/dashboard/rooms`, `/summary`) and actions (`POST /api/reservations/{id}/check-in|check-out`, `PATCH /api/rooms/{id}/housekeeping-status`) publishing to an in-process event bus, and the WebSocket hub at `/ws`. Role administration under `/api/admin/roles`. Account links (invite activation, password reset, forgot password) under `/api/auth/*` and `POST /api/admin/users/{id}/invite|reset-password`. Client has login, an admin area at `/admin` with permission-based navigation the users screens (list, add, detail) and the roles screens (list, create, duplicate, detail with permission editor), forgot-password, `/activate/:token` and `/reset-password/:token` pages, protected routes, and the dashboard page (summary, filters, room grid, detail panel with actions). The dashboard is live over the WebSocket.

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
- Permissions in the client (`src/auth/`): `P` in `permissions.ts` mirrors the server catalog. In components use `usePermissions()` (`can(key)`, `canAny(...keys)`, read from the `/me` query so it updates on refetch) or `<Can permission=... | anyOf=[...] fallback=...>`; plain `can(user, key)` works where the user is at hand. Guard signed-in routes with `<RequirePermission permission | anyOf>`, which shows a "You do not have access" page (never a redirect to login).
- Admin area (`src/admin/`): `/admin` (needs any of `ADMIN_PERMISSIONS`: user.view, role.view, audit.view) with side navigation from `sections.ts` (Users, Roles, Audit log, each shown only with its permission); `/admin` opens the first allowed section. The header's Main nav shows Dashboard / Admin by permission. Audit log page (`src/admin/auditlog/`): filters in the URL (`actor`, `action`, `target_type`, `target_id`, `from`, `to`; text inputs debounced), `useInfiniteQuery` with the server cursor ("Load older entries"), rows expand into a before/after table (`AuditDetails`), targets link to user/role pages.
- Roles admin (`src/admin/roles/`): `PermissionEditor` (grouped checkboxes from `GET /api/admin/roles/permissions`, filter box, per-group select all with a mixed state, keys you don't hold disabled with the reason); `RoleDetailPage` saves through a summary `ConfirmDialog` (added/removed permissions, users affected) and sends only changed fields. Read-only cases come from the server hints (`is_admin`, `is_system`, `can_edit`) plus `role.manage`. Shared `components/ConfirmDialog` (Cancel focused first, errors inside).
- Users admin (`src/admin/users/`): list state (search `q`, `role`, `status`, `sort`, `page`) lives in the URL (`params.ts`); search is debounced 300 ms. Hooks in `queries.ts` (mutations use `meta.handlesForbidden` and show errors inline). Action buttons use `components/GuardedButton` (aria-disabled + tooltip with the server's reason from `UserDetail.restrictions`, never re-derived in the client). Confirmations use `components/Dialog` (focus trap, Escape, focus returns to the trigger; render only while open). Times show in the hotel time zone (`formatDateTime(iso, user.property.time_zone)`).
- Global 403 handling (`src/queryClient.ts`): any query/mutation 403 refetches `/me` and shows a toast (`components/toast.ts` store + `<Toaster>` in `AppLayout`); set `meta: { handlesForbidden: true }` on a query/mutation that shows 403s inline (e.g. `useRoomAction`). The WebSocket `permissions_changed` message also refetches `/me`.
- Realtime (`src/realtime/`): `connection.ts` is a framework-free `RealtimeConnection` (pong replies, per-connection seq baseline from `hello`, ignores seq <= last, resyncs on gaps, 50 ms batching, exponential backoff with jitter capped at 30 s, no retry after 4401/4403). `cache.ts` `applyEvents` patches the rooms/summary caches (version-guarded; unchanged rooms keep identity so memoised tiles skip renders). `useRealtime` (mounted once in `AppLayout`, only with `dashboard.view`) refetches the snapshot on every `hello` and replays events received during that refetch. The header shows `ConnectionIndicator`.
- Accessibility (client): every page sets its title with `useDocumentTitle` (signed-out pages via `AuthShell`); `components/RouteAnnouncer` (in `AppLayout`) announces the new page's h1 after path changes and moves focus there if it was lost; `Dialog` makes the rest of the page inert while open; `GuardedButton` tooltips close on Escape. Keyboard-only flows are tested in `src/keyboard.test.tsx` (start focus with `focusOn`, keep Tab walks short: long ones slow the whole suite).
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
- Mutations publish room events then one `summary_changed`; logout/re-login publish an internal `session_ended` the hub uses to close that session's sockets; `permissions_changed` (internal) makes the hub re-check the affected users' access and, if they keep it, send them a `permissions_changed` message (no seq consumed); the client then refetches `/api/auth/me`, reservations and the dashboard snapshot.
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
- Account links (`services/account_tokens.py`, table `user_tokens`): 256-bit random tokens, only their HMAC (keyed by `SESSION_SECRET`) is stored; single use; invites last 72 h, resets 1 h; issuing a token expires the user's other unused tokens. Errors: unknown token or closed account 404 `token_invalid`, 410 `token_used` / `token_expired`. Redeeming checks the password policy (422 with a `password_*` code; the token stays usable), never logs in, and a reset revokes all sessions. Admin invite/reset (`user.manage`, within the managing guards) emails the link, or returns it once (`delivery: "link"`) when the user has no email or sending fails. Forgot-password always answers 202 with the same text, sends in a background task, and throttles silently via `auth.password_reset_requested` rows. Plain tokens never go into the database, audit rows or logs (tests scan every table).
- Password policy: `app/core/passwords.check_new_password` (min 12 characters, bundled common-password list in `app/core/data/` also matched after stripping leading/trailing digits and symbols, must not contain the username). The client mirrors only the length (`MIN_PASSWORD_LENGTH`).
- Email: `app/core/email.py` `EmailSender` protocol on `app.state.email_sender` (`EmailSenderDep`); `EMAIL_BACKEND` is `console` (dev, logs the message) or `disabled`; production refuses `console`. Links use `APP_BASE_URL`. Tests get a `RecordingEmailSender` (`emails` fixture; set `.fail = True` to simulate failure).

### Users and permissions

Design rules:
- Permissions are defined in code in `server/app/core/permissions.py` as a single catalog (key, description, group). On startup they are synced to the `permissions` table: new keys inserted, removed keys deleted along with their `role_permissions` rows.
- Roles live in the database and are bundles of permissions. System roles (`is_system = true`) cannot be deleted. The Admin system role always has every permission and cannot be edited.
- Roles are assigned to users per property via `user_property_roles`. One role per user per property for now.
- Users are never deleted, only deactivated.
- Guard rules (enforced in services; full list in README "Users and permissions", tests in `tests/test_privilege_escalation.py`): no granting permissions you do not have; no managing users or roles more powerful than you (at any property); no changing your own role, deactivating or expiring yourself; each property always keeps at least one active admin; the Admin role is immutable.

Implementation notes:
- Check permission keys (`P.RESERVATION_CHECK_IN`), never role names. Add a permission by adding it to `P` and `PERMISSIONS` in `core/permissions.py`; the startup sync (`services/permissions.sync_permission_catalog`) inserts it and grants it to Admin roles. Then protect the endpoint, list it in `ACCESS` in `tests/test_security.py`, and add the key to the client's `P` (steps in README "Adding a permission"). Migrations never import the catalog (they carry a frozen copy).
- `get_effective_permissions(db, user_id, property_id)` is the one source of a user's rights: empty for deactivated (`deactivated_at` set) or expired (`expires_at` passed) users and for users without a role at the property; the whole catalog for the Admin system role; keys not in the code catalog are ignored. Deactivated or expired users also cannot log in, and their sessions and WebSockets end.
- Default system roles per property (`DEFAULT_ROLES`): Admin, General manager, Front office manager, Receptionist, Housekeeping supervisor, Room attendant.
- Until rooms and reservations are property-scoped, requests act on the single property (`get_default_property_id`, `CurrentPropertyId`).
- Effective permissions are loaded once per request and cached on `request.state.permissions` (`get_current_permissions` in `api/deps.py`); `require_permission` reads that.
- `GET /api/auth/me` returns user info, the active `property` (`id`, `name`, `time_zone`), `role` (`id`, `name` or null) and sorted `permissions`; login and the WebSocket `hello` use the same shape.
- Any service that changes someone's effective permissions (role assignment, role permission edits, deactivation) must call `services/permissions.publish_permissions_changed(bus, property_id, user_ids)` after commit (`user_ids=None` = everyone at the property). The hub then re-checks those sockets immediately and closes ones without `dashboard.view` (4403) or with an ended session (4401); every heartbeat re-checks too, as a backstop.
- Data-dependent checks live in services: moving a room into or out of `out_of_order` needs `room.set_out_of_order`; guest email/phone in reservation details need `guest.view_contact`.
- User administration: `/api/admin/users` (list with search/filter/sort/pagination, detail, create, PATCH profile, PUT role, deactivate, reactivate, revoke-sessions); all rules are in `services/user_admin.UserAdminService`: no granting or managing beyond your own permissions, no acting on yourself (deactivate, role change, expiring), the last active Admin of a property (not deactivated, expired or invited) can't be removed (guarded under a row lock on the Admin role), deactivation and role changes revoke sessions (`revoke_user_sessions` + `publish_sessions_ended`) and publish `permissions_changed`. User status is derived: deactivated > expired > invited (no password) > active. New users are invited (`password_hash` NULL, cannot log in) until they redeem an invite link.
- User admin additions for the UI: `UserCreate.role_id` assigns a role in the same transaction; `GET /api/admin/users/roles` (`user.view`) lists roles with `assignable` for the viewer; `UserDetail.restrictions` gives, per action (`edit`, `change_role`, `deactivate`, `reactivate`, `revoke_sessions`, `send_invite`, `reset_password`), the reason it is blocked for the viewer or null, computed by `UserAdminService._restrictions` from the same guards. `GET /api/admin/audit` (`audit.view`, `services/audit_log.py`) reads audit_log and audit_events as one newest-first stream (UNION ALL; `source` is `change` or `auth`; a sign-in's actor and target are the user), filters `actor` (username), `action` (enum of `AuditAction` + `AuditEventType`), `target_type`/`target_id`, `date_from`/`date_to` (inclusive days in the property time zone), keyset cursor pagination on (occurred_at, source, id) via `cursor`/`next_cursor`, and `target_label` (user name, role name even after deletion, room number).
- Role administration: `/api/admin/roles` (list with user/permission counts, `/permissions` catalog grouped by area, detail with keys and holders, create with optional `copy_from_role_id`, PATCH name/description/permissions (the list replaces the set), DELETE); rules in `services/role_admin.RoleAdminService`: the Admin role is immutable (409 `role_immutable`), system roles can't be renamed, re-described or deleted (409 `system_role`) but their permissions can be edited, you can only add keys you hold (403 `permission_not_held`) and only edit/delete roles whose keys are all within yours (403 `role_above_you`), unknown keys are 422 `unknown_permission`, names are unique per property ignoring case (409 `role_name_taken`), deleting a role someone has is 409 `role_in_use`. Permission changes are audited as `{"permissions": {"added", "removed"}}` and publish `permissions_changed` for the role's holders. Responses carry `can_edit`/`can_delete`/`can_duplicate` hints for the viewer (duplicating needs every permission the role grants, so an Admin can copy the Admin role but never edit it).
- Hardening (step 10): the manage guard (`UserAdminService._outranks_actor`) checks the target's role at every property against the actor's permissions there. Invite/reset tokens travel in the URL fragment (`/activate#<token>`) and request bodies (`POST /api/auth/activate/check`, `POST /api/auth/activate {token, password}`, same for `reset-password`), never in URL paths; rejected links are recorded as `auth.link_rejected` and throttled per IP (`LINK_MAX_FAILURES_PER_IP`, 429 via `TooManyRequestsError`). If emailing a link fails for a user who has an email, the API returns 503 `email_failed` and kills the token (never shows the link). `services/expiry.sweep_expired_users` (every `EXPIRY_SWEEP_INTERVAL_SECONDS` in the API process, advisory-locked; also `python -m app.scripts.expire_users`) revokes expired users' sessions, closes their sockets and writes one `user.expired` entry (the marker). `python -m app.scripts.create_admin` (`services/bootstrap.py`) creates the first admin while no active admin exists. Tests: `test_privilege_escalation.py` (assert the exact guard status, use valid input so validation is not what refuses), `test_immediate_effect.py` (live sessions and sockets).
- Audit rows are append-only: a trigger (`audit_rows_are_immutable`, migration ff3cb811073d) rejects UPDATE and DELETE on audit_log and audit_events (so users with a trail cannot be deleted; TRUNCATE still works for dev reset/tests). Tests that need old rows insert them with explicit timestamps; never update them. Audit timestamps default to `clock_timestamp()`. Action names are `area.past_tense` (test enforces the pattern); new actions also need a label in the client's `src/admin/audit.ts` `ACTIONS` (type-checked against the generated union).
- Audit: data changes go to `audit_log` via `services/audit.record_change` (one row per change, same transaction; `before`/`after` hold only changed fields, never secrets; actions in `models/audit.AuditAction`). `audit_events` is only for authentication events (`record_event`).
- Tests: `make_user(username, role=Roles.RECEPTIONIST, deactivated=..., expires_at=...)` and `login_as(Roles.X)` (role names in `tests/conftest.py`); `role=None` creates a user without a role.

### Realtime
- A WebSocket endpoint pushes room status changes to connected dashboards.
- **All mutations go through REST.** The WebSocket only sends from server to client; it never accepts writes.
- After a REST mutation succeeds, the service layer publishes the change so it is broadcast over the WebSocket.

### Room setup
- Rooms and room types belong to a property (`property_id`, required). Room numbers are unique per property (`uq_rooms_property_id_number`); room type codes and names are unique per property. A room references its type through the composite foreign key `(room_type_id, property_id)` -> `room_types(id, property_id)`, so a room can only use a room type of its own property. Tests and the seed must pass `property_id` when creating either (`get_default_property_id`).
- Managing rooms and room types needs `room.manage` ("Add, edit and retire rooms and room types"). Only the Admin system role has it: `ADMIN_ONLY` in `core/permissions.py` keeps it out of default roles that are defined by formula (General manager, Front office manager).
- API: `/api/admin/room-types` (GET with `include_retired`, POST, PATCH, POST `/{id}/retire|reactivate`) and `/api/admin/rooms` (GET with `floor`, `room_type_id`, `status=active|retired|all`, sorted numerically; POST; PATCH; retire/reactivate). All need `room.manage`; rules in `services/room_setup.RoomSetupService`. Room PATCH/retire/reactivate send the room `version` (409 `version_conflict` when stale). Responses carry server-computed hints (`retire_blocked`, `type_change_blocked`) with the exact message the action would return. Codes: `room_in_use`, `room_type_in_use`, `room_type_retired`, `room_number_taken`, `room_type_code_taken`, `room_type_name_taken`, `invalid_transition`.
- Normalisation: room numbers and room type codes are trimmed and stored upper-case (so the case-sensitive unique constraints are case-insensitive in effect); room type names are compared ignoring case. `max_occupancy` is 1..12, floor 0..200. New rooms start `inspected`.
- "Upcoming" reservations = confirmed with check-out after today, plus checked in (overstays included).
- Every change audits (`room.created|updated|retired|reactivated`, `room_type.*`; target types `room`, `room_type`) and publishes `room_setup_changed` (broadcast with a seq); clients refetch rooms and summary. Retired rooms are excluded from dashboard reads and counts, and `room_changes.load_room` refuses them (no check-in or housekeeping changes). Reactivating a room of a retired type is refused.
- Screens (`client/src/admin/rooms/`): `/admin/rooms` (rooms grouped by floor; filters `floor`, `type`, `status` in the URL) and `/admin/rooms/types` (`?show=retired`), behind `RequirePermission(P.ROOM_MANAGE)`; the "Rooms" admin nav item needs `room.manage`. Add/edit forms are dialogs; retire/reactivate use `ConfirmDialog`; blocked actions show the server's `retire_blocked`/`type_change_blocked` text. A 409 `version_conflict` closes the dialog, refetches and shows an alert.
- Rules:
  - Rooms and room types are never deleted, only retired. A room is retired when `is_active` is false; `retired_at`/`retired_by` record when and by whom, and a check constraint (`ck_rooms_retired_consistent`) keeps `is_active` and `retired_at` in agreement. Room types have `is_active` only (who and when is in the audit log).
  - A room cannot be retired while a guest is checked in to it or a future reservation is assigned to it.
  - A room type cannot be retired while it has active rooms or future reservations.
  - A room's type cannot be changed while a guest is checked in to that room.
  - Every change (create, edit, retire, reactivate) is written to the audit log with `before`/`after`, like other data changes.

### Reservations
- Scope: reservations made by staff (phone, email, walk-in): availability search per room type for a date range; create, modify and cancel reservations, mark no-shows; find or create guests, with privacy rules; reservation list, booking form and detail screens; integration with check-in, check-out, the dashboard and room setup. Out of scope for now: online booking by guests, payments, pricing rules, channel managers, overbooking on purpose.
- Rules:
  - A stay is a range of nights: `check_in_date` inclusive, `check_out_date` exclusive, plain dates in the property time zone.
  - Reservations sell a room type; a physical room is assigned later (at the latest at check-in).
  - No overbooking: for every night of a stay, booked rooms of a type must not exceed active, sellable rooms of that type.
  - Availability checks and writes happen in one transaction, serialised per room type with a row lock on the room type, so concurrent bookings cannot oversell.
  - A database exclusion constraint prevents the same physical room being assigned to overlapping stays.
  - Prices are snapshotted per night on the reservation at booking time, stored as integer minor units with a currency code, never floats.
  - Reservation creation is idempotent using a client-generated idempotency key.
  - Reservations use optimistic locking (version column); conflicts return 409.
  - Status transitions: `confirmed` -> `checked_in` -> `checked_out`; `confirmed` -> `cancelled`; `confirmed` -> `no_show`. Nothing else.
  - Reservations are never deleted.
  - Guest contact details are only returned to users with `guest.view_contact` and are never written to application logs.
  - Every change is written to the audit log and publishes an event for the realtime hub.
- Decisions:
  - Reservations and guests belong to a property. `reservations.property_id` is enforced through composite foreign keys `(room_type_id|room_id|guest_id, property_id)` to `room_types|rooms|guests (id, property_id)`, so all four belong to one property.
  - Sellable rooms of a type for a night = active rooms of that type; rooms that are `out_of_order` now are subtracted for tonight only (out of order has no dates).
  - Availability always counts guests in house, including overstays whose `check_out_date` has passed. An early check-out shortens `check_out_date` to today (and drops the unused nights), so the remaining nights become sellable.
  - The starting price is the room type's `base_rate_minor`; changing a night's price needs `reservation.override_rate` (Admin, General manager, Front office manager). No-shows use `reservation.cancel`.
  - Audit rows record that guest contact details changed (field names), never their values.
- Data model (migration b5345677901f):
  - `properties.currency` (ISO 4217, default SEK), `max_stay_nights` (30), `booking_horizon_days` (730). `room_types.base_rate_minor` (default 0 = not set yet).
  - `reservations`: `source` (`phone|email|walk_in|other`), `total_minor` (BigInteger, sum of the nights) + `currency` (the property's at booking), `version` (SQLAlchemy `version_id_col`), `created_by`, `checked_in_at`, `checked_out_at`, `cancelled_at`/`cancelled_by`/`cancellation_reason` (also used for no-shows). Check constraints make each status timestamp present exactly when the status says so; set them in the same change as the status.
  - `reservation_nights` (PK `reservation_id, night_date`, `rate_minor`): one row per night of `[check_in_date, check_out_date)`; keep them and `total_minor` in step whenever dates or prices change.
  - Confirmation codes: `app/core/confirmation_codes.new_confirmation_code()` (8 random characters from `23456789ABCDEFGHJKMNPQRSTUVWXYZ`, format checked in the database, unique across properties; retry on a unique violation).
  - Guests: emails stored trimmed and lower-case (checked); generated columns `phone_digits` (digits only) and `search_text` (lower-case name, email and phone digits, GIN trigram index for `ILIKE '%term%'`; needs `pg_trgm`). Never write a guest's email or phone to logs (`Guest.__repr__` shows only the id).
  - `idempotency_keys` (unique `key, user_id`; `endpoint`, `request_hash`, `response_reference` like `reservation:42`), written in the same transaction as the record created; rows older than 24 h may be deleted.
  - Availability uses the GiST index `ix_reservations_availability` (`property_id, room_type_id, daterange`) over confirmed and checked-in stays.
  - Tests build guests and reservations with `tests/factories.py` (`new_guest`, `new_reservation`), which fill nights, totals, codes and status timestamps.

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
