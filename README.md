# Hotel Management System

A hotel management system, starting with a live front desk dashboard of all rooms.

- `client/`: React + TypeScript front end
- `server/`: FastAPI back end
- `docker-compose.yml`: PostgreSQL for local development

## Prerequisites

- Docker with Docker Compose v2 (`docker compose`)

## Start the database

1. Create your local env file and set real values (at least `POSTGRES_PASSWORD`, `SESSION_SECRET`, and the password inside `DATABASE_URL`):

   ```sh
   cp .env.example .env
   ```

2. Start PostgreSQL in the background:

   ```sh
   docker compose up -d db
   ```

3. Check that it is healthy (the `STATUS` column should show `healthy`):

   ```sh
   docker compose ps
   ```

4. Optionally open a psql shell:

   ```sh
   docker compose exec db psql -U hotel -d hotel
   ```

Stop it with `docker compose down`. Data is kept in the `hotel-db-data` volume; `docker compose down -v` deletes it.
