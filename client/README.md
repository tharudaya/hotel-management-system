# client

React + TypeScript (strict) front end: Vite, Tailwind CSS v4, React Router, TanStack Query.

## Setup and run

Requires Node 22.22+. Start the database and the server first (see the root and server READMEs).

```sh
cd client
npm install
npm run dev        # http://localhost:5173
```

The dev server proxies `/api` and `/ws` to the backend (`BACKEND_URL` in the root `.env`,
default `http://localhost:8000`), so the browser only talks to one origin and the session
cookie just works. Dev logins are listed in `server/README.md`.

## Scripts

| Command                           | What it does                                                             |
| --------------------------------- | ------------------------------------------------------------------------ |
| `npm run dev`                     | Vite dev server with proxy                                               |
| `npm run build`                   | Type-check and production build to `dist/`                               |
| `npm run typecheck`               | `tsc -b`                                                                 |
| `npm run lint`                    | ESLint (type-aware)                                                      |
| `npm run format` / `format:check` | Prettier                                                                 |
| `npm test` / `test:watch`         | Vitest + React Testing Library                                           |
| `npm run test:e2e`                | Two-window realtime check against running servers (see Realtime)         |
| `npm run generate:api`            | Regenerate `src/api/schema.ts` from the running backend's OpenAPI schema |

Run `npm run generate:api` (with the server running) after any backend API change and commit
the result; named aliases for the generated types live in `src/api/types.ts`.

## Realtime

`src/realtime/` keeps the dashboard live over `/ws`: events patch the TanStack Query cache
(no full refetch), bursts are batched, and every (re)connect refetches the snapshot. The
header shows Live / Reconnecting / Offline.

`npm run test:e2e` checks the whole path with the app's own realtime code: two logins,
a check-out in one, the other must update within a second. Start the database, reseed
(`python -m app.scripts.seed --reset` in `server/`), run the backend and `npm run dev` first.

## Layout

```
src/
  main.tsx          providers + 401 handler
  router.tsx        routes: /login, / (protected by RequireAuth)
  queryClient.ts    TanStack Query defaults
  api/client.ts     fetch wrapper: credentials, JSON, ApiError, 401 -> /login
  api/schema.ts     generated from OpenAPI (do not edit)
  api/types.ts      friendly names for generated types
  auth/             current-user query, login/logout mutations, RequireAuth
  pages/            LoginPage, DashboardPage
  dashboard/        summary bar, filters, room grid and tiles, detail panel, actions
  realtime/         WebSocket connection, cache patching, useRealtime, status chip
  e2e/              tests against running servers (npm run test:e2e)
  test/setup.ts     jest-dom matchers, cleanup
```

## Notes

- TypeScript is pinned to 6.0 because `typescript-eslint` does not support 7 yet.
  `openapi-typescript` still declares a TypeScript 5 peer; `overrides` in package.json
  points it at our TypeScript 6, which generates correctly.
