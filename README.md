# AI Employee Attendance

Face-recognition based employee attendance system (on-premises).

> **Status:** Phase 8 — all main pages implemented on the existing API. Cameras, AI enrollment and server-side report exports still wait for their backend phases (shown in the UI).
> See [DEVELOPMENT_PROGRESS.md](DEVELOPMENT_PROGRESS.md).

| Part | Tech | Path | Dev URL |
|------|------|------|---------|
| Frontend | Next.js 16 · React 19 · TypeScript · Tailwind CSS 4 | [apps/web](apps/web) | http://localhost:3000 |
| Core API | Python 3.12–3.14 · FastAPI · SQLAlchemy 2 (async, asyncpg) · Alembic | [apps/api](apps/api) | http://localhost:8000 (docs: `/docs`) |
| Database | PostgreSQL 16+ (17 tested) | local install or Docker | localhost:5432 |
| Queue/cache | Valkey 8 (used from Phase 6) | Docker | localhost:6379 |
| Recognition | Phase 4+ | [apps/recognition](apps/recognition) | — |

## Prerequisites

| Tool | Version | Needed for |
|------|---------|------------|
| Node.js | ≥ 20.9 (24 LTS recommended) | Frontend, root scripts |
| Python | 3.12, 3.13 or 3.14 | Backend (local mode) |
| Git | any recent | Source control |
| Docker Desktop (WSL2 backend on Windows) | ≥ 4.x with Compose v2 | PostgreSQL / full stack in containers |

## Option A — Run locally (fastest for development)

```bash
# 1. One-time setup: creates .env files with random secrets, Python venv, npm install
npm run setup

# 2. One-time database setup (see "Database" below)
npm run db:init       # create role + database (asks for the PostgreSQL "postgres" password)
npm run db:migrate    # create tables

# 3. One-time: create the first administrator (password is prompted, not echoed)
npm run user:create -- --username admin --role SUPER_ADMIN

# 4. Start backend + frontend together
npm run dev
```

You can also run each app in its own terminal:

```bash
npm run dev:api     # FastAPI on http://localhost:8000
npm run dev:web     # Next.js on http://localhost:3000
```

If PostgreSQL is not running, the API **still starts**. `/health` returns `200`, and `/ready` returns `503` with `"database": false`.

## Option B — Full stack in Docker

```bash
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
# or run `npm run setup`, which generates POSTGRES_PASSWORD, VALKEY_PASSWORD and DATA_ENCRYPTION_KEYS
docker compose up --build
```

## Verify

| Check | Expected |
|-------|----------|
| http://localhost:8000/health | `{"status":"ok","version":"0.1.0"}` |
| http://localhost:8000/ready | `200` when DB reachable, otherwise `503` |
| http://localhost:8000/docs | Swagger UI |
| http://localhost:3000 | Login page. After login: dashboard with today's attendance |

## Frontend notes

- Open the app at **http://localhost:3000**, not `127.0.0.1:3000`. The refresh cookie and CORS are set up for `localhost`. With `127.0.0.1` the session is lost on every page reload.
- `/design-system` (development only) shows every UI component and state.
- Panels marked **ISHLAB CHIQILMOQDA** list backend endpoints that do not exist yet. The rest of each page is real.

## Useful commands

```bash
npm run test         # web tests (Vitest) + backend tests (DB tests skipped unless TEST_DATABASE_URL is set)
npm run test:web     # frontend only
npm run test:api     # backend only
npm run lint         # eslint (web) + ruff (api)
npm run typecheck    # tsc (web) + mypy (api)
npm run test:rec     # recognition service tests (see apps/recognition/README.md)
npm run cam:setup    # camera service venv (once); then copy apps/camera/.env.example to .env
npm run cam:dev      # camera service on http://127.0.0.1:8200 (needs CAMERA_SERVICE_URL/TOKEN in apps/api/.env)
npm run test:cam     # camera service tests (no hardware needed)
npm run rec:bench    # recognition latency on this machine
npm --prefix apps/web run build   # production build of the frontend
```

## Database

Schema: [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md). Tables are created **only** by Alembic migrations.

### First-time initialisation

**A. PostgreSQL installed on this computer** (e.g. the Windows installer, service `postgresql-x64-17`):

```bash
npm run setup          # if not done yet: writes POSTGRES_* and DATA_ENCRYPTION_KEYS to apps/api/.env
npm run db:init        # creates role + database; psql prompts for the "postgres" superuser password
npm run db:migrate     # creates the 11 tables, triggers and default settings
npm run db:current     # should print: 0001 (head)
```

`db:init` runs [tools/db-init.sql](tools/db-init.sql). It's safe to re-run, and it creates:
- a role from `POSTGRES_USER`, with the password from `apps/api/.env` and no superuser rights;
- a UTF-8 database from `POSTGRES_DB`, owned by that role, with timezone UTC;
- the `citext` and `pg_trgm` extensions.

Options: `npm run db:init -- --superuser <name>`, and `npm run db:init -- --createdb`, which lets the role create databases so it can run the DB tests.

**B. Docker:** `docker compose up -d postgres` creates the role and database from the root `.env` automatically, and the `api` container runs `alembic upgrade head` on start. For local mode against the Docker database, use the same password in `apps/api/.env`.

### Day-to-day

```bash
npm run db:migrate                      # apply new migrations
npm run db:downgrade                    # roll back one migration
npm run db:check                        # fail if models changed without a migration
npm run db:revision -- "add holidays"   # autogenerate a migration, then REVIEW it
```

### Database tests

The tests create a temporary database, migrate it, run, and drop it. They need a role with `CREATEDB`:

```powershell
npm run db:init -- --createdb
$env:TEST_DATABASE_URL = "postgresql+asyncpg://attendance:<password from apps/api/.env>@localhost:5432/postgres"
npm run test
```

### Users and login

Roles: `SUPER_ADMIN` (everything), `ADMIN` (operations, no user/settings changes), `VIEWER` (read-only). See [API_CONTRACTS.md §1.2](API_CONTRACTS.md).

```bash
npm run user:create -- --username admin --role SUPER_ADMIN   # first admin, from the server console
```

Further users are created through the API (`POST /api/v1/users`, SUPER_ADMIN only). To try the API: open http://localhost:8000/docs, call `POST /api/v1/auth/login`, copy `access_token`, click **Authorize** and paste it.

Auth settings in `apps/api/.env`: `JWT_SIGNING_KEYS` (generated by `npm run setup`), `ACCESS_TOKEN_MINUTES` (15), `SESSION_DAYS` (7), `LOGIN_MAX_FAILURES` (5), `LOGIN_LOCKOUT_MINUTES` (15), `TRUSTED_PROXIES` (set to your Nginx IP in production).

### File storage

Profile photos and signed consent forms are stored **encrypted** under `STORAGE_DIR` (default `apps/api/storage/`, git-ignored). Back this folder up together with the database. Both need `DATA_ENCRYPTION_KEYS` to be readable.

### Encryption key

`DATA_ENCRYPTION_KEYS` in `apps/api/.env` encrypts face embeddings and camera credentials. **Back it up separately from the database.** Without it, encrypted data cannot be recovered. To rotate keys, add `2:<new key>` to the list and set `DATA_ENCRYPTION_ACTIVE_KEY_ID=2`. Old data still decrypts with key 1.

## Environment variables

| File | Used by | Committed? |
|------|---------|------------|
| `.env.example` → `.env` | Docker Compose | Only `.example` |
| `apps/api/.env.example` → `apps/api/.env` | API in local mode | Only `.example` |
| `apps/web/.env.example` → `apps/web/.env.local` | Next.js | Only `.example` |

Secrets are never stored in source code. The API **refuses to start** if `POSTGRES_PASSWORD` is missing. `npm run setup` generates random local passwords.

## Project structure

```
.
├── apps/
│   ├── api/            FastAPI core API (app/, migrations/, tests/)
│   ├── web/            Next.js frontend (src/app, src/components, src/lib)
│   ├── recognition/    Face recognition service (Phase 9, evaluation only, not connected)
│   └── camera/         Camera integration service (Phase 10: RTSP / USB / TEST providers)
├── packages/contracts/ Shared event schemas (Phase 6+)
├── models/             Model licence manifest (weights never committed)
├── tools/              Cross-platform dev scripts (setup, dev, py)
├── docker-compose.yml  Development stack
└── *.md                Project documentation (see PROJECT_OVERVIEW.md)
```

## Documentation

[PROJECT_OVERVIEW](PROJECT_OVERVIEW.md) · [ARCHITECTURE](ARCHITECTURE.md) · [DATABASE_SCHEMA](DATABASE_SCHEMA.md) · [API_CONTRACTS](API_CONTRACTS.md) · [DEVELOPMENT_PROGRESS](DEVELOPMENT_PROGRESS.md) · [DEVELOPMENT_RULES](DEVELOPMENT_RULES.md)
