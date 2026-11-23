# Development Progress — AI Employee Attendance

> Update this file at the end of every work session and at every phase gate.
> **Rule:** a phase starts only after the project owner explicitly approves the previous phase's gate.

## Current status

| Item | Value |
|------|-------|
| Current phase | **Phase 12 — Recognition → attendance (behind a master switch, OFF)** |
| Phase state | ✅ Implemented and tested (319 API + 31 camera + 61 recognition + 61 web tests). Phase 11 verified by the owner (laptop camera: recognised 56×, similarity 0.74–0.88). ⏳ Owner tests with several colleagues, then switches attendance on. |
| Next phase | Defined by the owner's next instruction (blocked until approval). Attendance integration stays blocked until the Phase 9 evaluation report is accepted. |
| Last updated | 2026-11-08 |

## Roadmap

Estimates assume 1–2 full-time developers and are indicative only. Re-estimate at each gate.
Phase numbers follow the owner's instructions. Phase 3 became "Database implementation", so later phases shift by one.

| Phase | Name | Key deliverables | Gate / exit criteria | Est. |
|-------|------|------------------|----------------------|------|
| 1 | Architecture & documentation | The six docs, risk register, licensing analysis | Owner approves architecture; open questions answered or deferred explicitly | 1 wk |
| 2 | Foundation | Monorepo, Docker Compose (dev), Postgres + Alembic baseline, Valkey, Core API skeleton, health endpoints, config/secrets handling, structured logging with redaction, Next.js app shell | Both apps start; README for setup | 1–2 wk |
| 3 | **Database implementation** ✅ | 11 tables, FKs, indexes, CHECKs, triggers, migrations, column encryption, transactions, init tooling, tests | Connection, creation, migrations, relationships, constraints tested | 1 wk |
| 4 | **Backend auth & authorization** ✅ | Login/logout, EdDSA JWT access tokens, server-side sessions, rotating refresh cookie, Argon2id, RBAC (SUPER_ADMIN/ADMIN/VIEWER), rate limiting, user admin API | Auth tests incl. permission matrix and route-protection guard | 1 wk |
| 5 | **Employee management backend** ✅ | Employees CRUD/search/filter/pagination, activate/deactivate, departments, profile photos, biometric consent with signed-form evidence, MANUAL alternative | CRUD + authorization tests | 1 wk |
| 6 | **Attendance engine** ✅ | IN/OUT events, arrival/departure, worked, late, early, absence, history, corrections, schedules per department | Unit + integration tests for attendance rules | 1 wk |
| 7 | **Frontend foundation** ✅ | Layout, sidebar, top bar, real login, dashboard, design system, states | Component + API-client tests, build | 1 wk |
| 8 | **Complete frontend pages** ✅ | 11 pages + schedules, search/filter/pagination, validation, confirmations | Web tests, build, live endpoint check | 1 wk |
| 10 | **Camera integration** ✅ | Camera service (RTSP / USB / TEST providers), monitoring, reconnection, status reporting, camera API with encrypted write-only credentials, stream quality gate, cameras page | Provider/worker/API tests without hardware; live end-to-end with TEST cameras; real cameras tested by the owner | 1–2 wk |
| 9 | **AI face recognition engine** ✅ (evaluation pending) | Independent service: licence gate, detection, alignment, quality, embedding, conservative matching, encrypted gallery, perf logging, evaluation harness + protocol | Unit/integration tests; owner-run evaluation report (FAR/FRR, 1/3/5 m, lighting, angles, latency) accepted before any attendance link | 1–2 wk |
| — | Later | XLSX import, MFA (TOTP), holidays/leave, shift rotations | | |
| 5 | AI recognition core (offline) | Engine interfaces; detector/embedder/liveness adapters for chosen licence path; alignment; quality gate; `/templates:extract`, `/quality`, `/search`; **benchmark harness** (FAR/FRR, latency) on pilot photos | Licence decision recorded; benchmark report; threshold proposal; no unlicensed weights in repo/image | 2–3 wk |
| 6 | Biometric enrollment | Consent flow, enrollment wizard (webcam + upload), encryption (envelope, key IDs), duplicate check, gallery delta sync + tombstones, consent revocation purge | P1–P4, P12 verified by tests; purge verified end-to-end | 1–2 wk |
| 7 | Camera integration | Camera CRUD, credential encryption, test connection, ONVIF discovery/probe, go2rtc config + WebRTC live view with signed tokens, recognition supervisor + per-camera workers, reconnect, heartbeats, health page, ROI editor | 4 cameras (or RTSP replays) streaming 24 h without leak/crash; live view < 1 s latency on LAN | 2–3 wk |
| 8 | Real-time recognition → punches | Tracking, best-of-track, Valkey stream, Worker consumer, idempotency, debounce, punches, live dashboard via WebSocket, unknown-face review | Frame→dashboard p95 ≤ 3 s; no duplicate punches under redelivery test | 2 wk |
| 9 | Attendance engine | Schedules (fixed/flexible/shift), night shifts, holidays, leave, day computation, recompute jobs, nightly close, corrections workflow | Table-driven test suite incl. midnight-crossing, DST-agnostic, leave/holiday precedence; owner validates against real schedules | 2–3 wk |
| 10 | Reports & exports | Board, daily, timesheet, lateness, absence, overtime; XLSX/CSV/PDF export; DSAR export | Owner signs off report formats vs current paper/Excel reports | 1–2 wk |
| 11 | Security & privacy hardening | Retention jobs, rate limiting, CSP/headers, dependency + image scans, backup/restore scripts, threat model review, penetration test checklist, DPIA support data | Security checklist 100%; restore test passed; legal review of consent/notice done (owner) | 1–2 wk |
| 12 | Pilot | Deploy to staging/prod server, 1 entrance, 20–50 consenting employees, 2–4 weeks; threshold calibration on real data; installer checklist validation | Recognition ≥ 98%, FMR ≤ 0.1% at pilot door; user feedback triaged | 2–4 wk |
| 13 | Rollout & handover | All cameras, all employees enrolled, admin/HR training, runbooks (ops, incident, key rotation, model upgrade), monitoring alerts | Owner acceptance | 1–2 wk |

Indicative total is **~5–7 months**.

## Phase 1 checklist

- [x] Analyze project workspace (result: empty, so greenfield design with documented assumptions)
- [x] Final system architecture — [ARCHITECTURE.md §1](ARCHITECTURE.md)
- [x] Frontend / backend responsibilities — [ARCHITECTURE.md §2](ARCHITECTURE.md)
- [x] Database ERD — [DATABASE_SCHEMA.md §2](DATABASE_SCHEMA.md)
- [x] Table specifications — [DATABASE_SCHEMA.md §4](DATABASE_SCHEMA.md)
- [x] API endpoint list — [API_CONTRACTS.md](API_CONTRACTS.md)
- [x] AI recognition service architecture — [ARCHITECTURE.md §4](ARCHITECTURE.md)
- [x] Camera integration design — [ARCHITECTURE.md §5](ARCHITECTURE.md)
- [x] Authentication & authorization — [ARCHITECTURE.md §7](ARCHITECTURE.md)
- [x] Security & biometric privacy — [ARCHITECTURE.md §8–9](ARCHITECTURE.md)
- [x] Development roadmap — this file
- [x] Risks, compatibility issues, model licensing — [ARCHITECTURE.md §10–12](ARCHITECTURE.md)
- [x] **Owner approval of architecture.** Implied by the Phase 2 instruction (2026-11-07), with the frontend changed to Next.js + Tailwind.
- [ ] Owner answers to open questions ([PROJECT_OVERVIEW.md §8](PROJECT_OVERVIEW.md)). Still open; not blocking Phase 2/3.

## Phase 2 checklist

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Next.js + TypeScript | ✅ | `apps/web`: Next.js 16.4.5, React 19.3.0, TS 5, App Router, `src/` dir |
| 2 | FastAPI backend | ✅ | `apps/api`: FastAPI 0.162.1, `/health`, `/ready`, `/api/v1/system/version`, `/docs` |
| 3 | Tailwind CSS | ✅ | Tailwind 4 (CSS-first `@import "tailwindcss"`), system fonts (no external font CDN, on-prem safe) |
| 4 | PostgreSQL connection settings | ✅ | Async SQLAlchemy 2.1 + psycopg 3.3, lazy engine, pool settings, Alembic configured (no migrations yet) |
| 5 | Environment variables | ✅ | `pydantic-settings`, typed. `POSTGRES_PASSWORD` required (no default), so the app refuses to start without it. |
| 6 | Docker Compose (dev) | ⚠️ written, **not run** | `docker-compose.yml`: postgres 16.6, valkey 8.0, api, web. Docker is not installed on the dev machine, so this is unverified. |
| 7 | Dev scripts | ✅ | Root `npm run setup / dev / dev:api / dev:web / test / lint / typecheck` (cross-platform Node scripts in `tools/`) |
| 8 | `.env.example` files | ✅ | Root, `apps/api`, `apps/web`. `npm run setup` generates random local passwords into `.env` files. |
| 9 | Git + `.gitignore` | ✅ | `git init -b main`, `.gitignore`, `.gitattributes`, `.editorconfig`. Verified that `.env*`, `.venv`, `node_modules`, and model weights are ignored. **No commit made yet.** |
| 10 | README.md | ✅ | Installation for local mode and Docker mode |

### Phase 2 verification results (2026-11-07, Windows 11, Node 25.7, Python 3.14.3)

| Check | Result |
|-------|--------|
| `npm run setup` | ✅ venv + deps + env files created |
| `npm run dev`, API | ✅ `/health` 200, `/api/v1/system/version` 200, `/docs` 200, `/ready` 503 without DB (expected) |
| `npm run dev`, Web | ✅ http://localhost:3000 renders, API status indicator, CORS allows `http://localhost:3000` |
| Backend tests (pytest) | ✅ 3 passed (health, version, log redaction) |
| ruff lint + format, mypy strict | ✅ clean |
| ESLint, `tsc --noEmit`, `next build` | ✅ clean |
| Password leak check in dev log | ✅ DB password not present |
| `npm audit` (production deps) | ✅ 0 vulnerabilities |
| `npm audit` (dev deps) | ⚠️ 8 moderate in `minimatch` via `typescript-eslint` (lint tooling only, not shipped). Fix requires a breaking forced upgrade, so revisit when `eslint-config-next` updates. |
| Docker Compose stack | ⚠️ Not verified (Docker not installed) |

### Known notes
- Python on the dev machine is 3.14. Docker images use 3.12. All pins are compatible with 3.12–3.14.
- Node 25 is a "current" release. Node 24 LTS is recommended for production images (Compose uses `node:24-alpine`).
- Starlette warns that `httpx` in `TestClient` is deprecated in favour of `httpx2`. Harmless for now; revisit in Phase 3.
- CI pipeline (GitHub Actions or other) is deferred until a remote repository exists.

## Phase 3 checklist — Database implementation

Phase 2 was implicitly approved by the Phase 3 instruction (2026-11-07).

| # | Requirement | Status | Where |
|---|-------------|--------|-------|
| — | 11 tables: users, employees, departments, biometric_consents, face_embeddings, cameras, attendance_events, attendance_daily, work_schedules, audit_logs, system_settings | ✅ | [apps/api/app/models/](apps/api/app/models/) |
| 1 | Relationships and foreign keys | ✅ 20 FKs incl. composite consent FK; explicit ON DELETE per FK; ORM relationships (`lazy="raise"`) | DATABASE_SCHEMA §2 |
| 2 | Indexes and constraints | ✅ 44 indexes (partial, expression, GIN trigram, BRIN), 58 CHECK constraints, 9 unique + 3 partial unique | DATABASE_SCHEMA §4, §8 |
| 3 | Migrations | ✅ Alembic revision `0001` (reviewed autogenerate + triggers + seed). Reversible. `alembic check` clean. Offline `--sql` renders | [migrations/](apps/api/migrations/) |
| 4 | Timestamps and status fields | ✅ `created_at`/`updated_at` (trigger-maintained), `deleted_at` soft delete, 11 native enums | DATABASE_SCHEMA §1, §3 |
| 5 | Transaction handling | ✅ request-scoped unit of work, `session_scope()`, `savepoint()`, audit in the same transaction | [app/db/session.py](apps/api/app/db/session.py) |
| 6 | Protect biometric data | ✅ AES-256-GCM + HKDF subkeys + row-bound AAD + key rotation; DB triggers: consent-gated insert, purge on revoke, immutable consents; ciphertext-length CHECK; no images in DB | DATABASE_SCHEMA §5–6 |
| 7 | Avoid unnecessary personal data | ✅ No national ID, birth date, address, gender or photo. Phone/email optional. Audit writer refuses sensitive fields | DATABASE_SCHEMA §4.4, §6 |
| 8 | Initialization instructions | ✅ `npm run db:init` ([tools/db-init.sql](tools/db-init.sql), safe to re-run) + `db:migrate/current/check/revision/downgrade` | README § Database |

### Phase 3 test results (2026-11-07, PostgreSQL 17.6 throwaway cluster, Python 3.14.3)

`89 passed` with `TEST_DATABASE_URL` set. Without it: `14 passed, 75 skipped` (unit tests only). ruff, ruff format, mypy --strict: clean.

| Area | Tests | What is verified |
|------|-------|------------------|
| Connection | 3 | Connect, server ≥ 16, extensions, app engine sets UTC / statement_timeout / application_name |
| Migrations | 3 | upgrade → 11 tables + 12 settings; downgrade → no tables, enums or trigger functions left; re-upgrade; **models = migrations (no drift)**; offline SQL |
| Relationships | 3 | Department tree + head, employee ↔ user/schedule/consents/embeddings/events/daily, camera ↔ events, generated search_name |
| Constraints | 42 | Uniques (incl. case-insensitive and soft-delete aware), FK RESTRICT/SET NULL, all CHECK rules per table, updated_at trigger, server defaults |
| Biometric protection | 12 | Encrypted at rest, no plaintext stored, consent required, cross-employee consent blocked, one active consent, revoke purges in the same transaction, consent immutability, raw vectors rejected, row-swap detection, gallery_version bump |
| Audit + transactions | 12 | Append-only (UPDATE/DELETE/TRUNCATE rejected), sensitive-field refusal, commit, rollback incl. audit row, savepoint isolation |
| Crypto (unit) | 11 | Round trip, random nonces, tamper/AAD/purpose/truncation failures, key rotation, keyring validation, float32 packing |
| API health (unit) | 3 | /health, /api/v1/system/version, log redaction |

Also verified manually: `npm run db:init` twice (safe to re-run) → non-superuser role, no CREATEDB, UTC database; `npm run db:migrate`; `/ready` returns `{"database": true}` against a migrated DB.

### Issues found and fixed during Phase 3
- **Phase 2 bug:** psycopg async cannot run on Windows' default event loop, so `/ready` would always report the DB down on Windows. Switched to **asyncpg** (ARCHITECTURE C13).
- `database_url` was a pydantic `computed_field`, which would have included the DB password in `model_dump()`. It's now a plain property.
- Nullable JSONB columns stored Python `None` as JSON `null` (which violated a CHECK). They now use `none_as_null=True`.

### Not done / notes
- The live dev database on your PostgreSQL 17 service is **not** created yet. That needs the `postgres` superuser password: run `npm run db:init` yourself (README § Database). All testing used a separate throwaway cluster on port 55432, so your server was untouched.
- Docker Compose is still unverified (Docker not installed). The `api` service now runs `alembic upgrade head` on start.
- Tables outside the 11-table scope are deferred to their feature phases (DATABASE_SCHEMA §9).
- `.venv` contains a leftover locked folder `~sycopg_binary` from uninstalling psycopg. It's harmless and git-ignored; delete it after a reboot.

## Phase 4 checklist — Backend authentication and authorization

Phase 3 was implicitly approved by the Phase 4 instruction (2026-11-07).

| Requirement | Status | Where |
|-------------|--------|-------|
| Login | ✅ `POST /api/v1/auth/login` | [app/api/v1/auth.py](apps/api/app/api/v1/auth.py), [app/auth/service.py](apps/api/app/auth/service.py) |
| Logout | ✅ `POST /api/v1/auth/logout`: revokes the server-side session immediately | same |
| Access token | ✅ JWT EdDSA/Ed25519, 15 min, `kid` rotation; plus rotating HttpOnly refresh cookie with reuse detection | [app/auth/tokens.py](apps/api/app/auth/tokens.py) |
| Secure password hashing | ✅ Argon2id 64 MiB/t=3/p=4, NFKC, auto-rehash, concurrency cap, constant-work unknown users, password policy | [app/auth/passwords.py](apps/api/app/auth/passwords.py) |
| JWT validation | ✅ alg pinned, iss/aud/exp/nbf/iat/sub/sid/jti required, `typ=access`, known `kid`, session + user checked in DB per request | [app/auth/dependencies.py](apps/api/app/auth/dependencies.py) |
| Current user endpoint | ✅ `GET /api/v1/auth/me` (user, permissions, session) | |
| RBAC: SUPER_ADMIN / ADMIN / VIEWER | ✅ permission-based (`require(Permission.X)`); role always read from DB | [app/auth/permissions.py](apps/api/app/auth/permissions.py) |
| Validate all inputs | ✅ strict Pydantic schemas (unknown fields → 422, length limits, patterns); validation errors never echo values | [app/schemas/](apps/api/app/schemas/), [app/api/errors.py](apps/api/app/api/errors.py) |
| Protect private endpoints | ✅ all `/api/v1` routes except login/refresh/version require auth; **test fails if a new route is unprotected** | test_auth_api.py |
| HTTP status codes | ✅ 200/201/204/401/403/404/409/422/429/500 with RFC 9457 bodies and stable `code`s | API_CONTRACTS §1.1 |
| Login rate limiting | ✅ 3 layers: in-memory flood guard (before hashing), DB-backed per-IP failure limit, per-account lockout; `Retry-After`; trusted-proxy-aware client IP | API_CONTRACTS §1.2 |
| Secure token handling | ✅ access token in memory only (contract); refresh token HttpOnly+Secure+SameSite=Strict, path-scoped, hashed at rest, rotated, reuse ⇒ session revoked; CSRF header on refresh; `Cache-Control: no-store` | |
| Authentication tests | ✅ 75 new tests (see below) | tests/test_auth_*.py |
| Clean reusable architecture | ✅ `tokens` / `passwords` / `permissions` / `service` / `dependencies` layers; endpoints only declare permissions | |
| No frontend-only auth | ✅ all enforcement is server-side; the frontend is unchanged | |
| API_CONTRACTS.md updated | ✅ §1.1 errors, §1.2 auth model and matrix, §2.1 auth, §2.2 users, §2.2a settings | |

Extras that support the requirements:
- User administration API: list, create, get, update, password reset, unlock, revoke sessions. It cannot remove the last active SUPER_ADMIN.
- `npm run user:create` CLI for the first SUPER_ADMIN (interactive password).
- Migration `0002` converts the role enum with a tested data mapping, and adds `auth_sessions` and `refresh_tokens`.
- Security headers (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`) on all responses, and `no-store` on API responses.

### Phase 4 test results (2026-11-07)

`164 passed` with `TEST_DATABASE_URL` (89 from Phase 3 + 75 new). Without a DB: `46 passed, 118 skipped`. ruff, ruff format and mypy --strict are clean.

| Area | Highlights |
|------|-----------|
| Unit: passwords | Argon2id format, production params, NFKC, rehash detection, policy rejects/accepts |
| Unit: tokens | Round trip, expiry, wrong aud/iss/typ/sub/nbf, missing claims, `alg=none`, **HS256 key confusion**, foreign key, unknown `kid`, rotation, refresh entropy |
| Unit: RBAC, limiter, client IP | Strict role hierarchy, limiter window and memory bound, X-Forwarded-For trust rules |
| API: login | Success (cookie flags, hash-only storage, audit), case-insensitive username, identical responses for unknown/wrong/inactive, lockout + expiry, IP limit, flood limit, validation cases, non-JSON |
| API: tokens and sessions | `/me`, bad-credential cases, expired token, forged session, role claim not trusted, role change immediate, deactivation immediate |
| API: refresh/logout/password | Rotation + **reuse detection revokes session**, garbage/missing cookie, expired session, logout, independent sessions, password change revokes other sessions |
| API: RBAC and protection | Role × endpoint matrix, **every private route requires auth** (static + live 401 check) |
| API: user admin and misc | Create (policy, bad role, duplicate, audit without password), last-SUPER_ADMIN guard, reset/unlock, 404/422, filters, security headers, failure counter persisted |
| Migration 0002 | Old roles map correctly on upgrade and back on downgrade |

Manually verified against a running server on a migrated DB: CLI-created SUPER_ADMIN (production Argon2 params) → login 401/200 → cookie flags → `/me` → `/users` 200 → 401 without token → refresh 200 → logout 204 → `/me` 401 `SESSION_ENDED`. The server log contains no password, token or hash.

### Issues found and fixed during Phase 4
- Alembic applies the naming convention to `drop_constraint` and `create_check_constraint` names too, so `op.f()` is required (otherwise you get `ck_users_ck_users_...`).
- FastAPI 0.16x includes routers lazily, so `app.routes` no longer lists endpoints. The route-protection test uses `fastapi.routing.iter_route_contexts`.
- A test cookie set with an explicit `domain="testserver"` was silently not sent, which made one reuse test pass for the wrong reason. Fixed, and the test now asserts the actual `TOKEN_REUSE` revocation.

### Notes / not done
- **MFA (TOTP)** is designed (ARCHITECTURE §7) but was not in the Phase 4 scope.
- The in-memory flood guard is per process. The DB-backed limits are global. Replace it with Valkey when Valkey is introduced.
- On Windows, `npm run user:create` must be run in a real terminal (`getpass` reads the console, not piped input).
- **Breaking change from Phase 3:** the roles HR_ADMIN/MANAGER/SECURITY_OPERATOR/EMPLOYEE no longer exist (mapped by migration `0002`).
- Your local PostgreSQL service still needs `npm run db:init` + `npm run db:migrate` + `npm run user:create` (see README).

## Phase 5 checklist — Employee management backend

Phase 4 was approved by the owner (login verified manually on 2026-11-07).

| Requirement | Status | Where |
|-------------|--------|-------|
| Create / update / get / list employees | ✅ `POST/PATCH/GET /employees`, `GET /employees/{id}` | [app/api/v1/employees.py](apps/api/app/api/v1/employees.py) |
| Search | ✅ `q`: name or code, case-insensitive, Uzbek apostrophe variants equivalent, LIKE wildcards escaped | [app/core/text.py](apps/api/app/core/text.py) |
| Filter by department | ✅ `department_id` (+ sub-departments by default), plus `status`, `attendance_method`, `biometric_status` | |
| Pagination | ✅ `page`, `page_size` ≤ 200, `total`; whitelisted `sort` | |
| Activate / deactivate | ✅ `/activate`, `/deactivate` (SUSPENDED or TERMINATED with date), reason required and audited | |
| Profile photo management | ✅ upload/replace/download/delete. Content-sniffed, re-encoded (EXIF stripped, ≤ 512 px), decompression-bomb safe, encrypted at rest | [app/core/uploads.py](apps/api/app/core/uploads.py), [app/core/storage.py](apps/api/app/core/storage.py) |
| Biometric consent status | ✅ `biometric` block on every employee (6 statuses, `can_enroll`), computed in SQL so it can be filtered | [app/services/employees.py](apps/api/app/services/employees.py) |
| Only authorized admins modify | ✅ writes need `employees:write` / `biometrics:write` (SUPER_ADMIN, ADMIN). VIEWER is read-only and cannot see consents. | |
| Validate employee identifiers | ✅ UUID path params; `employee_code` format + upper-case (API **and** DB CHECK); name/phone/email/hire_date rules; referenced department/schedule must exist | [app/schemas/employees.py](apps/api/app/schemas/employees.py) |
| Prevent duplicate records | ✅ code unique forever (case-insensitive), email and phone unique among non-deleted (partial unique indexes); friendly 409 codes | migration 0003 |
| Document biometric consent | ✅ consent records form version + operator confirmation; signed form (PDF/JPEG/PNG) attached as immutable, encrypted evidence; downloads audited | |
| Non-biometric alternative | ✅ `attendance_method = MANUAL` → `NOT_APPLICABLE`, consent/enrollment blocked; FACE→MANUAL blocked while consent active | |
| Never enroll without documented consent | ✅ **DB trigger** now requires an active consent *with signed form* for any face template; API exposes `can_enroll` | migration 0003 |
| Tests: CRUD + authorization | ✅ 40 new tests (below); route-protection guard now covers all new endpoints | |

### Phase 5 test results (2026-11-08)

`204 passed` with `TEST_DATABASE_URL` (164 + 40 new). Without a DB: `56 passed, 148 skipped`. ruff, ruff format and mypy --strict are clean.

| Area | Tests | Highlights |
|------|-------|-----------|
| Employee API | 28 | Normalisation (code, Uzbek ʻ, phone, email) + audit; 11 validation cases; duplicates (code case, email case, phone format); detail/404/422; pagination, search (apostrophe variants, case, code, wildcard escaping), department subtree filter, status/method/biometric filters, sort, bad query params; partial update + audit diff, null/empty rejections, duplicate on update; full status-transition matrix; VIEWER read-only on 10 endpoints; SUPER_ADMIN/ADMIN can write; photo lifecycle (encrypted on disk, ≤ 512 px, old file removed, delete); bad files (fake jpeg, tiny, missing, too large); consent lifecycle (version, unsigned, pending → documented → enrolled → revoked purges templates, duplicate, immutable document, audited download); MANUAL alternative; terminated; cross-employee consent; departments (duplicate, cycle, not-empty delete, soft delete) |
| Upload sanitisation | 10 | EXIF/GPS stripped, resize, PNG alpha → JPEG, polyglot payload removed, empty/text/GIF/truncated rejected, too small/large, decompression bomb, PDF accepted / truncated PDF rejected |
| DB biometric rules | 2 | Undocumented consent cannot enroll; document fields must be set together |

Also verified against a running server (temporary DB): create → duplicate 409 → apostrophe search → photo upload/download → consent → document → `READY_FOR_ENROLLMENT` → suspend. 2 encrypted files on disk, no password or document content in the log. Migration `0003` was applied to the owner's `attendance` database.

### Notes / not done
- Face **enrollment** endpoints (capturing templates) belong to the AI phase. Phase 5 provides the consent gate and `can_enroll`.
- Terminated employees keep their consent until the retention job (planned) purges biometrics after `retention.terminated_biometric_days`.
- If a request fails at the final COMMIT (rare), a newly written photo/document file can be left unreferenced. A storage cleanup job is planned.
- No ETag/If-Match concurrency control yet (last write wins).
- The consent form **text** itself is managed outside the system. Only its version is pinned.

## Phase 6 checklist — Attendance management engine

Phase 5 was approved by the owner's Phase 6 instruction (2026-11-08).

| Feature / rule | Status | How |
|----------------|--------|-----|
| IN / OUT attendance events | ✅ `POST /attendance/events` (camera/terminal; simulated, since cameras are not connected) and manual events via corrections | [app/attendance/service.py](apps/api/app/attendance/service.py) |
| Arrival / departure time | ✅ first IN / last OUT per **work date** (night shifts assigned via schedule windows) | [app/attendance/engine.py](apps/api/app/attendance/engine.py) |
| Working duration | ✅ sum of IN→OUT pairs; unpaid break deducted for a single ≥ 6 h pair | engine |
| Late arrival | ✅ beyond `grace_in` → minutes from shift start | engine |
| Early departure | ✅ beyond `grace_out` → minutes until shift end (not while still at work) | engine |
| Absence | ✅ ABSENT only after the day's window closed; PENDING before that; WEEKEND / NOT_SCHEDULED | engine |
| Attendance history | ✅ `GET /attendance/events` (incl. ignored & voided), `GET /attendance/days`, day detail, board | [app/api/v1/attendance.py](apps/api/app/api/v1/attendance.py) |
| 1. Prevent duplicate events | ✅ `event_uid` idempotency (200 replay); ±60 s debounce per camera; DB partial unique index on counted (employee, instant) | |
| 2. Repeated detection ≠ departure | ✅ BOTH cameras need ≥ 30 min after IN (`REPEATED_DETECTION`); IN cameras give `DUPLICATE_IN` | engine `classify_camera_punch` |
| 3. Validate event sequences | ✅ camera events must fit IN/OUT alternation incl. late arrivals (`OUT_OF_SEQUENCE`); corrections validated as a whole (409, atomic) | engine `validate_sequence` |
| 4. Configured work schedule | ✅ `/work-schedules` CRUD (time zone, hours, working days, break, grace, windows, overtime) | [app/api/v1/work_schedules.py](apps/api/app/api/v1/work_schedules.py) |
| 5. Manual corrections | ✅ `POST /attendance/corrections` add/void batch; MANUAL employees use it exclusively | |
| 6. Corrections in audit_logs | ✅ `ATTENDANCE_CORRECTED` (reason, added, voided, resulting status); events immutable, voids keep who/when/why (DB trigger) | migration 0004 |
| 7. Reliable timestamps | ✅ offset required; future (> 120 s skew) and stale (> 168 h) rejected; all math in UTC (DST-safe); server receive time stored; frozen-clock tests | |
| 8. Multiple departments | ✅ department default schedule inherited by subtree; employee override; department filters on days/board/events | |
| No real cameras | ✅ none connected; ingestion endpoint simulates the recognition service | |

### Phase 6 test results (2026-11-08)

`270 passed` with `TEST_DATABASE_URL` (204 + 66 new). Without a DB: `95 passed, 175 skipped`. ruff, ruff format and mypy --strict are clean.

| Area | Tests | Highlights |
|------|-------|-----------|
| Engine unit tests | 39 | Work-date assignment incl. night shift and UTC/local boundaries; BOTH/IN/OUT/NONE classification; repeated-detection threshold; late-arriving events; sequence validation (5 invalid shapes); on-time day; late with grace boundaries; early leave; grace out; break rules; absence vs pending at window boundary; incomplete vs in progress; weekend; overtime; night shift; no schedule; manual flag; **DST night shift** (Berlin 2026-03-29); mixed time zones |
| API integration | 26 | Schedule CRUD + 8 validation cases, in-use delete; schedule inheritance via department tree; full day with BOTH camera; idempotent replay; debounce, DUPLICATE_IN, OUT_WITHOUT_IN, MONITOR_ONLY; unmatched + payload rules; future/too-old/naive timestamps, unknown camera; inactive/manual/not-yet-hired employees; late+early; night-shift OUT to previous day; absence/weekend/pending; days filters + departments + range limits; board totals; recompute snapshots + audit; corrections (missing OUT + audit, atomic time change, invalid sequences with nothing applied, void breaking sequence, 7 validation cases, 404, VIEWER 403); MANUAL employee via corrections; history filters/pagination/permissions; department default schedule validation |
| DB rules | 1 new test + 5 new cases | Event immutability (no time/direction change, no delete, no un-void); new CHECKs and same-instant unique index |

Verified on a running server (temporary DB): schedule → IN counted → repeated sighting ignored (`REPEATED_DETECTION`) → OUT counted (worked minutes) → board totals. Migration `0004` applied to the owner's `attendance` database.

### Issues found and fixed during Phase 6
- **Test harness flaw:** rows created by a test after its last request were rolled back by the next *failing* request (shared savepoint), so earlier tests could pass for the wrong reason. Each simulated request now starts its own transaction, like production, and the whole suite was re-run.
- **DST arithmetic:** Python subtracts datetimes sharing one `ZoneInfo` by wall clock. The engine now normalises everything to UTC, and a Berlin DST test covers it.
- `tzdata` is now pinned explicitly (Windows has no system time-zone database).
- Two FKs from `attendance_events` to `users` needed an explicit ORM `foreign_keys`.

### Notes / not done
- Holidays, leave, shift rotations and schedule history are not implemented yet. Live results for past days use the current schedule. Use `/attendance/recompute` to freeze snapshots.
- `/attendance/events` ingestion currently needs an ADMIN user token. The recognition service will use a service token (later phase).
- No nightly job yet: absences appear live in `/days` and `/board`, and are stored when `/recompute` runs.

## Phase 7 checklist — Frontend foundation and design system

Phase 6 was approved by the owner's Phase 7 instruction.

| Requirement | Status | Where |
|-------------|--------|-------|
| Global layout | ✅ Root layout (Uzbek `lang`, metadata, `noindex`), providers (auth + toasts), 404, error boundaries | `app/layout.tsx`, `app/not-found.tsx`, `app/(app)/error.tsx`, `app/global-error.tsx` |
| Sidebar | ✅ Dark navy, sections, active state, permission-filtered items, mobile drawer | `components/layout/sidebar.tsx`, `nav.ts` |
| Top navigation | ✅ Page title, mobile menu, user menu (role, session expiry, logout) | `components/layout/topbar.tsx` |
| Login page | ✅ **Real backend login**: validation, show/hide password, 401 / 429 (Retry-After) / network messages, "session ended" notice, safe `?next=` | `app/login/` |
| Dashboard layout | ✅ `AppShell` guard + real dashboard (today's board, employee count, today's late arrivals); placeholders labelled | `app/(app)/dashboard` |
| Responsive design | ✅ Drawer < 1024 px, mobile-hidden table columns, horizontal scroll, bottom-sheet modals | |
| Reusable components | ✅ Button, Card/StatCard, DataTable/Pagination, Modal, Field/Input/Select/Textarea/Checkbox/PasswordInput/FormError, Toasts, Alert, Badge, Spinner/Skeleton, Loading/Empty/Error states, DevPlaceholder, PageHeader | `components/ui/` |
| Notifications | ✅ Toast system (aria-live, auto-dismiss, persistent errors) | `components/ui/toast.tsx` |
| Loading / error states | ✅ Skeletons, spinners, ErrorState (network / 403 / API) with retry; built into DataTable and `useApiQuery` | |
| Design direction | ✅ Navy / white / light gray / subtle blue tokens, system fonts, focus rings, reduced motion | `app/globals.css`, ARCHITECTURE §2.1 |
| TypeScript, Tailwind, Lucide | ✅ strict TS, Tailwind 4 tokens, lucide-react 1.70.0 | |
| No fake functionality | ✅ Only labelled `DevPlaceholder` / `PlannedPage`; sample data only on the dev-only `/design-system` page, labelled "Namuna" | |

### Phase 7 verification (2026-11-08)

- Web: `36 passed` (Vitest). **API client** (10): token only in memory, never in Web Storage; credentials sent; Problem Details + Retry-After; NetworkError; **single-flight refresh**; retry after TOKEN_EXPIRED; session-ended handling; refresh failure; 204/403; logout. **Components** (13): Button loading/type; Modal a11y, Esc, backdrop, non-dismissible; Field aria wiring; PasswordInput; toasts auto-dismiss vs persistent; DataTable rows/empty/error/loading; ErrorState 403/network; DevPlaceholder label. **Login/nav** (13): `safeNext` open-redirect cases, error messages, permission-filtered menu.
- API: `270 passed` (unchanged backend).
- `tsc --noEmit`, ESLint, `next build` (13 routes) all clean.
- Running servers: `/login`, `/dashboard`, `/employees`, `/attendance`, `/design-system` → 200, unknown → 404. No dev warnings. Security headers present. `X-Powered-By` removed. A cross-origin login from the web origin returns 200 with CORS credentials and the HttpOnly/Secure/SameSite=Strict refresh cookie. A foreign origin is rejected.
- **Not verified here:** clicking through in a real browser (no browser automation in this environment). Please test login → dashboard → reload (stays signed in) → logout at http://localhost:3000.

### Issues found and fixed during Phase 7
- `@types/node` ^20 conflicted with Vitest 5 (needs ≥ 22). Moved to 24 (matches the Node 24 LTS target).
- Next 16 instant-navigation validation flagged client-only authenticated pages. Configured `manual-warning` + `instant = false` on the authenticated layout, and the login page opts in.
- `useApiQuery` originally set state synchronously in an effect (React lint). `loading` is now derived from the request key.
- The password field inside `Field` lost its label association. Added a reusable `PasswordInput`.

### Notes / not done
- Only Uzbek strings are complete. Russian and English dictionaries can be added with the same shape.
- No Playwright E2E yet.
- Use `localhost` (not `127.0.0.1`) in development, see README.

## Phase 8 checklist — Complete frontend pages

Phase 7 was approved by the owner's Phase 8 instruction.

| # | Page | Route | Uses (existing API only) | Missing dependency (documented in the UI) |
|---|------|-------|---------------------------|-------------------------------------------|
| 1 | Dashboard | `/dashboard` | `/attendance/board`, `/employees` | Camera status, live recognition (camera / AI phases) |
| 2 | Employees | `/employees` | `GET/POST /employees` (q, department, status, method, biometric status, sort, pagination) | XLSX import (not in contracts yet) |
| 3 | Employee details | `/employees/[id]` | `GET/PATCH /employees/{id}`, `/activate`, `/deactivate`, `/photo` (PUT/GET/DELETE), `/consents` (+document, revoke), `/biometrics/consent-version`, `/attendance/days` | Face enrollment (§2.5) |
| 4 | Attendance | `/attendance` | `/attendance/board`, `/attendance/days/{e}/{d}`, `POST /attendance/corrections` | — |
| 5 | Attendance history | `/attendance/history` | `/attendance/days` (≤ 31 days), `/attendance/events` | — (employee names resolved per id, see note) |
| 6 | Cameras | `/cameras` | — | Entire camera API (§2.6) listed |
| 7 | AI Recognition | `/recognition` | `/attendance/events?source=CAMERA` (real, simulated events) | Recognition events / snapshots / review (§2.7), enrollment (§2.5) |
| 8 | Reports | `/reports` | `/attendance/days` (paged) → monthly timesheet + CSV in the browser | Server reports & XLSX/PDF exports (§2.10) |
| 9 | Departments | `/departments` | `GET/POST/PATCH/DELETE /departments`, `/work-schedules` | — |
| 10 | Administrators | `/users` | `GET/POST/PATCH /users`, `/password`, `/unlock`, `DELETE /sessions` | — |
| 11 | Settings | `/settings` | `GET /settings`, `/auth/me` data | `PATCH /settings` (§2.11) |
| + | Work schedules | `/schedules` | `GET/POST/PATCH/DELETE /work-schedules` | — |

| Requirement | Status |
|-------------|--------|
| Responsive | ✅ Filter bars stack on phones, secondary table columns hide below `md`, tables scroll, modals become bottom sheets, sidebar drawer |
| Search & filtering | ✅ Debounced search (employees, users), entity filters, async `EmployeePicker`, date/month pickers |
| Pagination | ✅ Server pagination everywhere the API pages. Reports page through all results. |
| Form validation | ✅ Client rules mirror the server (`lib/validation.ts`, employee form). Server 422/409 mapped onto fields (`lib/errors.ts`). |
| Confirmation dialogs | ✅ `ConfirmDialog` (optional required reason): deactivate/activate, delete department/schedule/photo, record/revoke consent, sign out user |
| Consistent design | ✅ Design-system components only. Labels and tones centralised in `lib/labels.ts`. |
| Loading & error states | ✅ `useApiQuery` + DataTable/ErrorState/EmptyState/skeletons on every view |
| Role-based navigation | ✅ Menu filtered by permission. Pages wrapped in `RequirePermission` (403 panel). Write actions hidden without permission. The server enforces everything. |
| No invented endpoints | ✅ Every call was verified against the running API (20 URL patterns → 200). Missing features are listed as `MissingApi` with contract references. |

### Phase 8 verification (2026-11-08)
- Web tests: **50 passed** (+14): timesheet aggregation, CSV (BOM, quoting, **formula-injection protection**), Tashkent time conversion, month/day arithmetic, password/schedule/employee validators, server-error mapping, department tree order, ConfirmDialog (required reason, server error stays open, disabled), **modal focus regression**.
- API tests: 270 passed (backend unchanged). `tsc`, ESLint and `next build` (17 routes) are clean.
- Running servers: all 12 page routes → 200, no dev warnings. Every API URL pattern built by the pages → 200 with a real token.
- **Not done here:** clicking through in a real browser (no browser automation available). Please test the flows listed in the hand-off message.

### Bugs found and fixed during Phase 8
- **Modal stole focus on every keystroke.** Its effect depended on the parent's `onClose` (a new function each render). Typing into any modal would have lost focus after one character. Fixed with a ref, and a regression test was added.
- **Native browser validation pre-empted our messages** in the confirm and correction forms (`required` without `noValidate`). Fixed.
- A hard-coded form id in `ConfirmDialog` could collide when two dialogs exist. It now uses `useId`.
- Dynamic routes needed a Suspense boundary around the shell for Next 16 prerendering.

### Notes / not done
- **Backend improvement suggested:** `GET /attendance/events` returns only `employee_id`. The UI resolves names per id (cached). Embedding `{employee_code, full_name}` in events would remove these lookups. (No endpoint was invented.)
- Page texts are Uzbek and inline in the page components. Shared labels and messages live in `lib/labels.ts` and `lib/errors.ts`. Moving all strings into `lib/i18n.ts` for ru/en is still to do.
- Timesheet is client-side and capped at 20 000 day-rows per run (≈ 650 employees × 31 days). Use the department filter beyond that, or wait for server reports.
- Profile photos are not shown in the employee list (one authenticated request per row would be wasteful). They are shown on the details page.

## Phase 9 checklist — AI face recognition engine

Phase 8 was approved by the owner's Phase 9 instruction. The code is in `apps/recognition/`: an independent service with its own venv and its own tests.

| # | Task | Status | Where |
|---|------|--------|-------|
| 1 | Evaluate pretrained models | ✅ YuNet+SFace (default) vs InsightFace SCRFD+ArcFace (research only). Both pipelines are implemented behind the same interfaces. | `recognition/models.py`, ARCHITECTURE §10.3 |
| 2 | Verify licensing | ✅ LICENSE files checked at the source (2026-11-08): YuNet **MIT** and SFace **Apache-2.0**, so commercial use is OK. InsightFace weights are **non-commercial only**. Enforced by `models/manifest.yaml` + registry: unlisted, unverified (sha256) and forbidden-in-production models are refused. | `recognition/registry.py`, `models/manifest.yaml` |
| 3 | Face detection | ✅ YuNet (OpenCV DNN). SCRFD decoder (ONNX Runtime) for research. | `models.py` |
| 4 | Face alignment | ✅ 5-point Umeyama similarity (no reflection) → 112×112 ArcFace template | `align.py` |
| 5 | Embedding generation | ✅ SFace 128-d (L2-normalised). ArcFace 512-d for research. | `models.py` |
| 6 | Authorised employee matching | ✅ 1:N cosine, best template per employee. The gallery holds only enrolled (consented) people of the same `model_id`. | `matcher.py` |
| 7 | Configurable thresholds | ✅ `MATCH_THRESHOLD` (default = manifest reference, must be calibrated), `MIN_MARGIN`, `UNCERTAIN_BAND`, and all quality limits via env | `config.py` |
| 8 | Image quality assessment | ✅ Face width, inter-ocular px, detector score, blur (Laplacian), brightness, yaw/roll from landmarks. LOW_QUALITY faces are never compared. | `quality.py` |
| 9 | Reject uncertain matches | ✅ MATCHED only with threshold + band **and** margin. Otherwise AMBIGUOUS (no identity returned or logged) or UNKNOWN. | `matcher.decide` |
| 10 | Performance logging | ✅ JSON log per face (decision, scores, quality reasons, per-stage ms; never embeddings or images), `/stats` p50/p95/p99, Prometheus `/metrics` | `metrics.py` |
| — | Template encryption & access | ✅ AES-256-GCM with the Core API keyring (interop test with a Core API ciphertext). AAD bound to template + subject + model. Consent ref required. Bearer token (≥ 24 chars, constant-time). | `crypto.py`, `gallery_store.py`, `api.py` |
| — | Evaluation harness | ✅ FTA, FNIR, MIR, FPIR, AMBIGUOUS count, rule-of-three bound, per lighting/angle/distance, threshold sweep + recommendation, latency, caveats. Consent id required on every row. | `evaluate.py`, `cli.py evaluate` |
| — | Evaluation protocol | ✅ Consent, ≥ 20–30 subjects + impostors, condition matrix (good/low/backlit × frontal/yaw15/yaw30/pitch × 1/3/5 m), face-pixel estimates, acceptance table | `apps/recognition/EVALUATION_PROTOCOL.md` |
| — | Not connected to attendance | ✅ No Core API calls and no events. `/ready` reports `attendance_integration: disabled`. | — |

### Phase 9 verification (2026-11-08, Windows 11, CPU, Python 3.14.3, OpenCV 5.0.0, ONNX Runtime 1.31.1)
- Recognition tests: **53 passed**. They cover:
  - alignment recovery;
  - the pose, blur and brightness gate;
  - all four decisions, including band and margin edges;
  - model-mismatch refusal;
  - Core API ciphertext interop and tamper, relabel and wrong-key detection;
  - consent required;
  - the licence gate (5 refusal cases);
  - SCRFD anchors and the metric maths;
  - an end-to-end harness run;
  - API auth (401), upload validation (413/422) and weak-token refusal;
  - a smoke test with the real YuNet+SFace weights.
- ruff and mypy (strict) are clean. `check-models`: both default models pass the licence check and are sha256-verified.
- Live service on :8100: `/health` and `/ready` return 200. `/stats` without a token returns 401. A blank image gives 0 faces. A non-image returns 422.
- **Latency on this machine** (`rec:bench`, synthetic frames): detection on a full 1920×1080 frame p50 **214 ms** / p95 477 ms; embedding p50 **27 ms** per face; alignment < 1 ms. This measures speed only.
- **Not done here (cannot be done here):** FAR/FRR and the 3 m / 5 m results, because no consented face data exists in this environment. The owner must run `rec:eval` with real volunteers at the real camera. The system makes **no promise** of recognition at 3–5 m. The protocol's estimates show that a wide-angle 1080p camera produces faces below the 80 px gate at those distances.

### Notes / not done
- SFace is less accurate than licensed ArcFace (D2). If the evaluation misses the targets, the options are D2-A (licensed InsightFace) or optics changes, not a lower threshold.
- There is no liveness / anti-spoofing yet (D4) and no tracking / best-of-track; tracking belongs to the camera phase.
- `/internal/v1/templates:extract` and `/search` (API_CONTRACTS §4.2) are not implemented. Enrolment from the Core API comes in a later phase. The evaluation uses an encrypted local gallery file.
- Docker: a Dockerfile and an opt-in compose profile `recognition` are written but **not verified**, because Docker is not installed on this machine.

## Phase 24 — Holidays and employee leave (owner chose "2")

| Item | Status |
|---|---|
| Why | ✅ the director's ranking must be fair: people on leave or sick, and holidays, were counted as absent |
| Data | ✅ migration 0012: `holidays` (day, name, branch_id NULL = every branch; unique per scope) and `employee_leaves` (VACATION / SICK / TRIP / OTHER, date range ≤ 366 days, note); both audited |
| Engine | ✅ `engine.excuse()` after `compute_day`: no arrival on a holiday → HOLIDAY, inside a leave → LEAVE (holiday wins); arrived anyway → a worked day, no lateness on a holiday. `OFF_STATUSES` (weekend, unscheduled, leave, holiday) are excluded from absences, rankings, Telegram summaries |
| API | ✅ `GET/POST/DELETE /holidays` (centre view → every branch, branch view → that branch; a branch cannot delete every-branch holidays: 403), `GET/POST/DELETE /leaves` (visible employees only, overlap → 409 `LEAVE_OVERLAP`); day output adds `leave_kind`, `holiday_name` |
| Reports | ✅ timesheet codes H bayram, T taʼtil, B kasallik, S safar, U uzrli; new "Uzrli" column; day details name the leave kind / holiday |
| Web | ✅ "Taʼtil va bayramlar" page (leave list + add with employee picker; holidays per year with scope badge), employee page leave card, new day statuses |
| Tests | ✅ API 389 + engine rules (+5: excuse rules, leave + holiday not absent, branch-only holiday, rights, cross-branch leave hidden, timesheet codes); web 80 (+3) |
| Owner steps | `npm run stop` → `npm run db:migrate` → `npm start` |

## Phase 23 — Attendance menu inside the Telegram bot (owner: "telegramning oʻzida boʻlsin")

| Item | Status |
|---|---|
| Access | ✅ only linked, active administrator chats; checked again on every button press. Centre chat: every branch; branch chat: its branch only (forged buttons for other branches → refused). Employee-only chats → "menu is for administrators"; strangers → the usual "use the link" text |
| Menu | ✅ `/menu` (also `/start` of a linked chat, "menyu"), `/bugun`, a typed date (`19.11.2026` / `19.11`). Centre: today's ranking, weekly / monthly ranking (current period "hozirgacha"), branch list → any branch's day / week / month, date picker (last 7 days or typed date). Every view has ◀️ / ▶️ navigation (no future), Menyu / Filiallar buttons; button presses edit the same message |
| Code | ✅ `app/services/bot_menu.py`; Telegram client: inline keyboards, `editMessageText`, `answerCallbackQuery`, `setMyCommands`; updates include `callback_query`; the worker long-polls all the time (25 s) |
| Tests | ✅ API 384 (+3: strangers / employee chats refused, centre rankings + branch drill-down + week / month + typed date, branch chat isolation incl. forged buttons); web 77 |

## Phase 22 — Director's report on Telegram (owner: "barcha filiallarda davomat reyting sifatida")

| Item | Status |
|---|---|
| What | ✅ new Telegram kind `director` (centre chats only, branch chats → 422 `centre_only`): every day at `notify.daily_summary_time` branches ranked by attendance (🥇🥈🥉, 🟢 ≥95% / 🟡 ≥80% / 🔴), totals, and the names of those not here yet per branch (worst branch first); Mondays also last week's ranking, the 1st also last month's, each with the 10 people absent / late most often |
| Rule | ✅ rate = came / scheduled working days of ACTIVE employees of open branches (weekends, unscheduled days, leave excluded); ties → fewer late arrivals. Code: `app/services/director.py`; sent once per (kind, key) like every message |
| API | ✅ `GET /notifications/telegram/director-preview?period=day|week|month` (`branches:manage`), not sent |
| Web | ✅ "Direktor hisoboti" checkbox on centre chats, preview button |
| Tests | ✅ API 381 (+3: daily ranking / names / once a day / branch chat excluded, Monday weekly with top lists, centre-only + preview rights); web 77 |

## Phase 21 — Branches (filials) (owner: "boshqa tumanlarga ham beriladigan qilamiz")

| Item | Status |
|---|---|
| Decision | ✅ owner: one central system now ("hozircha faqat sahifalar"); cameras stay on this computer. A branch admin manages its own employees, departments, schedules, Telegram, attendance and reports; the super admin (centre) sees and controls every branch |
| Data | ✅ migration 0011: `branches` table; `branch_id` on departments, work_schedules, employees, users, cameras (default = the main branch "Markaz", fixed id `…0001`, so every existing row moved there and nothing was lost); `telegram_recipients.branch_id` (NULL = centre). Employee codes, department codes / sibling names and schedule names are unique per branch. Downgrade refuses while other branches or OTHER_BRANCH events exist |
| Scope rule | ✅ `AuthContext.branch_id`: a branch user = own branch (from the DB on every request); SUPER_ADMIN = every branch or the one sent in `X-Branch-Id`. Other branches' rows return **404** (never 403). Body references (department, schedule, camera, employee) must be in the same branch (422). An employee's branch = its department's branch |
| Permissions | ✅ new `branches:manage` (SUPER_ADMIN): branches CRUD, overview, system status; new `notifications:read/write` (ADMIN): link / change the branch's Telegram chats. Bot master switch and times stay `settings:write` |
| Attendance | ✅ a camera counts only its branch's employees; others are identified but stored with `ignore_reason = OTHER_BRANCH`. Review queue is scoped by the camera's branch. A closed branch's users cannot sign in (`BRANCH_INACTIVE`) |
| Telegram | ✅ late / camera messages go to the centre + that branch's chats; with several branches the daily message is per branch (detailed) and the centre gets a one-line-per-branch overview; review counts per branch. The send log is visible to the centre only |
| Web | ✅ topbar branch switcher (super admin; remembered per browser, remounts the page) or own-branch badge; "Filiallar" page (open / rename / close / reopen / delete-if-empty, today's per-branch table); dashboard overview; users page branch column + select; camera form branch select (departments filtered); departments grouped by branch in selects; schedule select filtered by the employee's branch; Telegram shows which branch a chat belongs to |
| Tests | ✅ API: +9 `test_branches.py` (isolation, 404s, cross-branch references, per-branch codes, header switching, management rights, closing locks out, OTHER_BRANCH, users / cameras scope, overview) + per-branch daily Telegram test; updated system / notification permission tests. Web 77 |
| Owner steps | `npm run stop` → `npm run db:migrate` → `npm start`; then Filiallar → open a branch → Administratorlar → create its admin with that branch |

## Phase 20 — One review card per person (owner chose "2")

| Item | Status |
|---|---|
| Problem | ✅ a stranger standing at the door produced one card per minute (46 dismissed cards in the dev DB), and other people in the same minute got none |
| Recognition | ✅ `recognition/visitors.py`: UNKNOWN / AMBIGUOUS faces get an anonymous `visitor_id` ("v17"); same person (cosine ≥ max(threshold, 0.40), running mean follows pose/light) keeps it; forgotten 30 min after last seen; ≤500 in memory. Embeddings never leave the recognition process |
| Camera service | ✅ one review card per visitor id (no per-minute wait, every new person gets a card); a failed send is retried after 60 s; falls back to the old per-minute rule without a visitor id |
| Tests | ✅ recognition 63 (+2: same face keeps id under noise, TTL and size bound); camera 42 (+1: two alternating strangers → exactly two cards) |

## Phase 19 — Cameras serve departments (owner chose "2-yoʻl")

| Item | Status |
|---|---|
| Rule | ✅ `cameras.department_ids` (migration 0010, UUID[] default empty = everyone). Faces are still matched against EVERY employee (an outsider is identified as themselves, never mistaken for a member); a recognised employee whose department is not served (sub-departments included) is stored with `ignore_reason = OTHER_DEPARTMENT` and does not count |
| Why not a smaller gallery | ✅ measured: matching 5 vs 5000 templates = 0.005 vs 0.19 ms, while the face embedding takes 80–130 ms, so a per-camera gallery saves no time and would raise false matches for visitors from other departments |
| API | ✅ `department_ids` on camera create / update / out; unknown or deleted departments → 422; changing them does not bump `config_version` (no camera reconnect); audited |
| Web | ✅ camera form "Boʻlimlar" (tree checkboxes, empty = all), "Boʻlimlar" column, "Boshqa boʻlim" reason in the recognition log and live view |
| Tests | ✅ API 367 (+3: served / sub-department counted, other department identified but not counted, no departments = everyone, API validation and no reconnect); web 77 (+1) |

## Phase 18 — Automatic camera health (owner: "do not make me check every new camera")

| Item | Status |
|---|---|
| Measurements | ✅ camera worker: codec (FFmpeg FOURCC → H.264/H.265/MJPEG), CPU of the worker thread (`time.thread_time`, % of one core, every 5 s), read failures in the last 5 min, reconnects in the last hour (windows survive reconnects) |
| Verdict | ✅ `camera/health.py` (pure): bad = offline; warn = ≥3 reconnects/h, ≥10 lost frames/5 min, <5 fps, ≥70% CPU, H.265 or >1080p while CPU ≥40%; tip = H.265 / >1080p on a light load, >20 fps, RTSP narrower than 1280 px. Rates judged after 30 s of uptime |
| Transport | ✅ status reports carry `codec, cpu_percent, failures_5m, reconnects_1h, health{verdict, issues[{code, severity, …numbers}]}` (API schema extended) |
| Web | ✅ Cameras list: "Salomatlik" badge (Yaxshi / Diqqat / Ishlamayapti) with the reasons as tooltip; camera dialog: codec, CPU, "Avtomatik tekshiruv" list with plain advice per finding |
| Telegram | ✅ "system" subscribers get one message per camera per day while the verdict is "warn" (warnings only, no tips) |
| Real case | ✅ camera 103 (DS-2CD2443G0-I): 2560×1440 H.265 21 fps over Wi-Fi → 157 read failures; after 1920×1080 H.264 15 fps, bitrate 2048, I-frame 30: 0 failures / 30 s, worker ~27% of one core |
| Tests | ✅ camera 41 (+7 health rules, worker report); API 364 (+1 status schema, +1 daily health alert); web 76 (+3 advice texts) |

## Phase 17 — Telegram notifications (owner request)

| Item | Status |
|---|---|
| Bot | ✅ `TELEGRAM_BOT_TOKEN` in apps/api/.env only (never in DB, UI or logs: Bot API URLs are redacted by the log formatter); `app/services/telegram.py` (sendMessage, getUpdates, getMe) |
| Linking | ✅ Settings → "Qoʻshish" creates a one-time code (15 min) and a `t.me/<bot>?start=<code>` link; the worker long-polls getUpdates while a code waits, links the chat (person or group) and confirms in Telegram; an unknown `/start` gets a neutral reply |
| Messages | ✅ daily summary at `notify.daily_summary_time` (came, late with time and minutes, not here yet, on leave; skipped on days off); late arrival right away (arrivals of the last 30 min only); review reminder (new pending faces, at most every `notify.review_interval_minutes`); camera without frames for `notify.camera_offline_minutes` plus recovery, one per outage; backup older than 36 h (daily). Names and times only, no photos |
| Employee chats | ✅ (migration 0009: `telegram_recipients.employee_id`, unique, cascade) employee page → "Telegram ulash" one-time link; the employee presses Start and receives ONLY their own messages: `self_punch` ("ishga kelishingiz qayd etildi: 08:55" + lateness on a late first arrival; "ishdan ketishingiz qayd etildi") and `self_late`; recent punches only (30 min), once per event; one chat per employee (a new link moves it); an administrator who is also an employee keeps both roles; `GET/POST link/PATCH/DELETE /api/v1/employees/{id}/telegram` (employees:read / employees:write); checks every 15 s |
| Send once | ✅ `notification_log` (kind, key) unique; the row is inserted before sending, so restarts never repeat a message; blocked chats are deactivated |
| Switch | ✅ `notify.telegram_enabled` seeded OFF (migration 0008); settings whitelist: enabled, daily time (HH:MM), review interval (5–1440), camera minutes (1–1440) |
| Worker | ✅ runs inside the API process when a token is set (not in tests): checks every 30 s, update polling |
| API | ✅ `/api/v1/notifications/telegram` (status, link, recipients, test, daily-preview); read = settings:read, change = settings:write; audited |
| Web | ✅ Settings → "Telegram bildirishnomalari": setup steps without a token, on/off, times, recipients with per-kind checkboxes, link modal that waits for Start, test message, today's summary preview, recent sends |
| Tests | ✅ API 359 (+12: disabled, daily once / after the time / day off, late once / 30-min window, review throttling, camera outage and recovery once, blocked chat, link codes, API, permissions, settings validation, token redaction); web 73 |

## Phase 16b — Guided face scan and attendance avatars (owner requests)

| Item | Status |
|---|---|
| Guided enrolment | ✅ "Kamera bilan skanerlash" opens a modal: live preview (~5 fps, mirrored), face box (amber → green), 4 steps: straight → turn one side → the other side → straight + smile; capture after ~1 s of a correct pose; server re-checks the pose on the enrolled frame (`POSE_MISMATCH`); closes itself when done; optional "replace old templates" (limit 5) |
| API | ✅ `POST /employees/{id}/face-templates/scan-frame` (preview + face box + yaw, nothing stored, throttled audit); `from-camera` takes `pose` / `yaw_sign` and returns `yaw_deg` |
| Recognition | ✅ `templates/extract` returns `yaw_deg` / `roll_deg`; tests no longer pick up `CORE_API_URL` from the local .env (was 1 flaky failure) |
| Smooth preview | ✅ camera service `POST /internal/v1/stream` (MJPEG of the monitored worker's latest frames, ≤25 fps, ≤1280 px, ≤600 s, max 4 open, 409 NOT_LIVE when not monitored); API `GET /employees/{id}/face-templates/scan-stream?camera_id` relays it (15 fps, 640 px, 120 s, audited, DB session closed before streaming); the modal reads it with fetch (token in header), draws on a canvas, reconnects every 2 min, and analyses the face ~4×/s paced by video frames (no timers). Falls back to single frames after 3 failed connections |
| Attendance avatars | ✅ `DayOut.employee` has `has_photo` / `photo_updated_at`; Attendance and History tables show the profile photo (initials when none) |
| Speed fix | ✅ every outgoing httpx client built a new TLS context (~250 ms on Windows) on every call; `app/core/http.py` caches one (verification unchanged). Scan preview frame: ~550 ms → ~40 ms; also speeds up the live view, camera tests and system status |
| Tests | ✅ API 345 (+7 guided enrolment, +1 board photo flag); recognition 61; web 70 (+5 scan logic) |

## Phase 16 — One-command start, autostart and system status (owner chose "2")

| Item | Status |
|---|---|
| `npm start` (`tools/run.mjs`) | ✅ supervisor: API first (waits for /health), then recognition, camera, web (`next start -H 127.0.0.1`); production web build rebuilt automatically when sources are newer than `.next/BUILD_ID`; `--dev` (reload), `--only a,b`, `--quiet` |
| Self-healing | ✅ restart on exit with backoff 2 s → 60 s (reset after 2 min of uptime); restart when the health check fails for 1 minute (hung process); process trees killed with `taskkill /T` |
| Safety | ✅ single instance (`logs/supervisor.pid`), refuses busy ports with a clear message, services bound to 127.0.0.1, missing optional services skipped |
| Logs | ✅ `logs/<service>.log` + `supervisor.log`, rotated at 10 MB; `logs/` git-ignored |
| `npm run stop` / `status` | ✅ graceful stop through `logs/stop.request` (force after 15 s); status table from `logs/supervisor.json` |
| `npm run autostart` | ✅ Task Scheduler task `AttendanceSystem`: at logon + 20 s, hidden window, no time limit, ignore second instance, 3 retries; `autostart:remove` |
| `GET /api/v1/system/status` | ✅ api, database (migration), recognition, camera, last backup (warning > 36 h), free disk (warning < 5 GB), supervisor processes; `settings:read` |
| Web | ✅ "Tizim holati" page (`/system`) in the System menu, 10 s refresh |
| Tests | ✅ API 3 (all checks, backup + supervisor state, stale state, no secrets, permissions); web 3 (helpers). Manual: started api+camera under the supervisor, killed the camera process → restarted in 4 s, status endpoint showed every part ok, `npm run stop` left no process on the ports; busy-port refusal verified |

## Phase 15 — Review queue for uncertain recognitions (owner chose "3")

| Item | Status |
|---|---|
| Capture | ✅ camera service sends AMBIGUOUS faces (UNKNOWN only if `recognition.store_unknown_snapshots`) as a **face crop** (40 % margin, ≥128 px, never the full frame), at most one per camera+decision per minute, to `POST /internal/v1/recognition-reviews` (camera token) |
| Gates | ✅ attendance master switch on, `recognition.review_enabled` (new, migration 0007, default on), camera enabled + recognition + quality PASSED, clock checks, idempotent `event_uid` |
| Storage | ✅ crop re-encoded (EXIF dropped), stored AES-256-GCM (purpose `snapshot`); event stored as `AMBIGUOUS`/`UNKNOWN`, `UNMATCHED`, `review_status = PENDING`. **No suggestion of who it might be** is stored or shown (Phase 9 rule) |
| Decide | ✅ `assign {employee_id, direction?}` → MANUAL punch via the corrections flow (sequence rules apply; BOTH/NONE cameras need a direction); `dismiss`; crop deleted immediately; audited `REVIEW_ASSIGNED` / `REVIEW_DISMISSED` / `REVIEW_SNAPSHOT_VIEWED`. The face is never enrolled from a review |
| Expiry | ✅ pending reviews older than `retention.unknown_snapshot_days` (7) are closed and their crops deleted |
| Permissions | ✅ view `biometrics:read`; decide `biometrics:read` + `attendance:write` (ADMIN, SUPER_ADMIN) |
| Web | ✅ "AI tanish" → "Koʻrib chiqish" (photo, time, camera, employee picker, direction if needed, Tasdiqlash / Rad etish, 15 s refresh); Settings → two toggles; live view shows "Koʻrib chiqishga yuborildi" |
| Tests | ✅ API 6 (gates, encrypted crop, audit, assign → manual punch + crop deleted + 409 on repeat + 410 snapshot, BOTH needs direction, dismiss, expiry + clock checks, permissions); camera 2 (crop size, review once per minute, never reported as attendance) |

## Phase 14 — Reports: Excel & PDF (owner chose "2")

| Item | Status |
|---|---|
| `GET /api/v1/reports/timesheet?month=YYYY-MM` | ✅ employee × day grid (+ K C Y D J ·), totals: work days, came, late days, absent, worked h:mm, late / early minutes; Excel has a second "Kunlar" sheet with every day (arrival, departure, worked) |
| `GET /api/v1/reports/lateness?date_from&date_to` | ✅ date, employee, department, scheduled start, arrival, minutes late; totals |
| `GET /api/v1/reports/absence?date_from&date_to` | ✅ absent working days with the schedule |
| Formats | ✅ `format=json` (preview), `xlsx` (openpyxl 3.1.5, MIT; coloured cells, frozen header), `pdf` (reportlab 5.1.0, BSD; A4 landscape, TTF font with Uzbek letters, Helvetica fallback) |
| Rules | ✅ numbers from the attendance engine (same as Attendance pages, manual corrections included); `reports:read` (all roles); range ≤ 92 days; downloads audited `REPORT_EXPORTED` |
| Web | ✅ Reports page rebuilt: tabs, filters (month / range, department), preview table with coloured day codes, Excel / PDF download (replaces the client-side CSV) |
| Tests | ✅ API 5 (JSON values, Excel sheets and cells, PDF, audit, lateness/absence, validation, permissions, Uzbek letters in PDF); web +1. Real dev-DB timesheet rendered and checked visually. |

## Phase 13 — Encrypted backup & restore (owner: "most important first")

| Item | Status |
|---|---|
| `npm run backup` | ✅ pg_dump (custom format) + `storage/` + manifest (SHA-256, row counts, schema version) → tar.gz → AES-256-GCM (keyring, purpose `backup-archive`, header bound as AAD) → `backups/attendance-YYYYmmdd-HHMMSS.attbak`; keeps newest 14 |
| `npm run backup:verify` | ✅ decrypt, check every checksum, `pg_restore --list` |
| `npm run backup:restore` | ✅ into an existing (empty) database; refuses the live DB / non-empty storage without `--force` |
| `npm run backup:keys` | ✅ encryption + JWT keys in a scrypt+AES-GCM passphrase file; `--out` required (keep away from backups); `--show` for recovery |
| `npm run backup:schedule` | ✅ Windows Task Scheduler daily task (not registered automatically) |
| Tests | ✅ 5: tamper & header-change detection, wrong keys, key export passphrase, pruning, real pg_dump → verify → pg_restore into a second DB (row counts + storage files equal) |
| Live | ✅ first real backup of the dev DB: 138 KB, 14 tables, 85 rows, 4 files, verified |

## Phase 12 checklist — Recognition → attendance

The owner asked to continue with attendance integration. To respect D22 (no attendance before the evaluation), everything is built behind a **master switch `recognition.attendance_enabled`, seeded OFF (migration 0006)**; the owner turns it on in Settings after testing with colleagues.

| Item | Status |
|---|---|
| Camera service reports confident matches | ✅ only `MATCHED`, at most once per employee per camera per 5 min (was 30 s: a person sitting in view created a DEBOUNCED row every 30 s), to `POST /internal/v1/recognition-events` (camera token); outcome shown in the live view (IN / OUT / ignore reason / switch off) |
| Core API gate | ✅ switch off → nothing stored; camera must be enabled + recognition enabled + quality PASSED; only MATCHED accepted (422 otherwise) |
| Attendance rules | ✅ reuses the Phase 6 engine: idempotent `event_uid`, clock checks, debounce, IN/OUT/BOTH sequence, inactive / manual-method employees not counted |
| Settings write API | ✅ `PATCH /api/v1/settings/{key}` (settings:write = SUPER_ADMIN), whitelist: `recognition.attendance_enabled`, `attendance.debounce_seconds` (10–3600), `attendance.min_presence_minutes` (0–720); audited |
| Web | ✅ Settings: switch card with confirmation; AI tanish: "Davomat" column |
| Not yet | review queue for AMBIGUOUS/UNKNOWN (needs encrypted face crops + retention), liveness (D4) |

Verification: API 319 passed (+9: switch off records nothing, on → IN then DEBOUNCED, idempotent replay, camera gates ×3, AMBIGUOUS refused, future time refused, manual-method not counted, settings toggle audited / validation / non-writable key 404 / ADMIN 403). Camera 31 (+2: one report per cooldown, outcome stored). Web 61. Dev DB migrated to 0006 (switch false).

## Phase 11 checklist — Face enrolment and live recognition (no attendance)

Scope agreed with the owner: (1) enrol employee faces, (2) show live recognition. **Attendance integration is NOT part of it** (D22).

| Item | Status | Where |
|---|---|---|
| Enrol from an uploaded image | ✅ `POST /employees/{id}/face-templates` (multipart). Image sanitised (re-encoded JPEG, EXIF dropped), sent to the recognition service, **discarded**. Only the encrypted embedding is stored. | `app/api/v1/face_templates.py` |
| Enrol from a camera frame | ✅ `POST /employees/{id}/face-templates/from-camera {camera_id}` (snapshot via camera service, not stored) | same |
| Consent & eligibility | ✅ FACE method, not terminated, active **documented** consent (also DB trigger); 409 `CONSENT_REQUIRED` / `NOT_FACE_METHOD` / `EMPLOYEE_TERMINATED`; nothing is sent for processing otherwise | same |
| Quality / single face | ✅ exactly one face passing the recognition quality gate; 422 `FACE_NOT_ACCEPTED` with reason (no_face, multiple_faces, low_quality + reasons) | recognition `/internal/v1/templates/extract` |
| Duplicate check | ✅ a face already matching ANOTHER enrolled employee → 409 `FACE_MATCHES_OTHER_EMPLOYEE` | same |
| Limit, list, delete | ✅ max 5 templates (setting), metadata-only list, hard delete; all audited without vectors | same |
| Gallery to recognition | ✅ `GET /internal/v1/gallery` (encrypted) → recognition service decrypts in memory every 30 s (`GallerySync`); only ACTIVE + FACE + documented-consent employees; tampered/relabelled ciphertexts skipped | `app/api/internal.py`, `recognition/core_sync.py` |
| Token separation | ✅ gallery needs `RECOGNITION_GALLERY_TOKEN`, distinct from the token used to call the recognition service (which the camera service also holds) | config |
| Live recognition | ✅ camera service sends ~2 frames/s of cameras with `recognition_enabled` (only possible after a PASSED quality test) to `/internal/v1/recognize`; observations kept in a 300-entry in-memory ring, identical ones coalesced; **no images, no embeddings stored** | `apps/camera/camera/live.py` |
| Live view | ✅ `GET /api/v1/recognition/live` (biometrics:read): names only for MATCHED; AMBIGUOUS/UNKNOWN anonymous | `face_templates.py`, web `/recognition` |
| Thread safety | ✅ recognition engine serialises model inference (OpenCV DNN is not re-entrant); embeddings returned per call (no shared "last embedding"); gallery swapped atomically | `recognition/engine.py`, `matcher.py` |
| Web | ✅ employee page "Yuz namunalari" card (upload / from camera / list / delete, Uzbek error reasons); "AI tanish" page live table (2 s refresh) | `components/face-templates.tsx`, `components/live-recognition.tsx` |

### Phase 11 verification (2026-11-10)
- API **310 passed** (+13): encrypted-only storage, consent/method/terminated gates (no call to the service), rejections, duplicate refusal, limit + delete, from-camera, 503 when not configured, permissions, gallery token separation and eligibility filter, live view anonymity. Internal-route guard test now accepts either service token.
- Recognition **61 passed** (+8): extract endpoint (embedding, nearest, rejections, auth), gallery sync with Core-format AAD (tampered template skipped), failure keeps last gallery, engine under 4 concurrent threads.
- Camera **29 passed** (+3): identity only for MATCHED, coalescing, live loop only on enabled cameras and stopped on reconfiguration, observations API auth.
- Web **60 passed** (+2), tsc/eslint clean.
- **Live wiring check** (throwaway DB, three real services): no consent → 409; real YuNet on a face-less image → `no_face`; an encrypted template written by the Core API was decrypted and loaded by the recognition service; camera token and recognition-call token are refused by the gallery (401); TEST camera live loop active (~185 ms per frame). Logs contain no embeddings or ciphertexts.
- **Not verified here:** recognising a real person. No consented face data exists in this environment. The owner tests with the laptop camera.
- Fixed in passing: camera worker polled non-blocking webcams (DirectShow) at ~56 000 grabs/s; now capped at 1.5× the camera's fps (regression test).

## Phase 10 checklist — Camera integration module

Phase 9 was approved by the owner's Phase 10 instruction. Code: `apps/camera/` (independent service) + camera API in `apps/api` + `/cameras` page.

| Requirement | Status | Where |
|---|---|---|
| USB webcam provider | ✅ OpenCV (DirectShow on Windows, V4L2 on Linux). Device index or `/dev/videoN`. | `camera/providers/opencv_sources.py` |
| RTSP IP camera provider | ✅ OpenCV/FFmpeg, TCP by default, open/read timeouts, TCP pre-check to tell *unreachable* from *wrong path/credentials* | same |
| Test camera provider | ✅ Synthetic pattern or replay of images/video from `TEST_MEDIA_DIR`; failure simulation `offline`, `disconnect`, `flaky`, `freeze` | `camera/providers/test_source.py` |
| Replaceable providers | ✅ `FrameSource` protocol + registry (`register()`); worker, quality test and API do not know the camera type | `providers/base.py`, `providers/registry.py` |
| Connection monitoring | ✅ One worker thread per enabled camera: measured fps, frames, read failures, reconnects, last frame. States CONNECTING / ONLINE / DEGRADED / OFFLINE | `camera/worker.py` |
| Stream error handling | ✅ Error codes (UNREACHABLE, OPEN_FAILED, DEVICE_NOT_FOUND, STREAM_LOST, …), stall detection (no frame for 10 s), errors redacted | `worker.py`, `providers/base.py` |
| Automatic reconnection | ✅ Exponential backoff 1 s → 60 s with jitter; reset after the first frame | `worker.Backoff` |
| Configuration management | ✅ Camera CRUD, enable/disable, `config_version`; the camera service pulls enabled cameras every 10 s and restarts only changed workers | `app/api/v1/cameras.py`, `camera/supervisor.py`, `camera/core_client.py` |
| Status reporting | ✅ Camera service → `POST /internal/v1/cameras/status` → `cameras.status`, `runtime`, `last_error`; status changes audited; stale reports shown as "unknown" | `app/api/internal.py` |
| Entrance / exit roles | ✅ `direction` IN (entrance) / OUT (exit) / BOTH / NONE (monitor only) | model + UI |
| RTSP passwords never reach the browser | ✅ Write-only, AES-256-GCM in the DB, only `has_credentials` + redacted URL returned; decrypted only for the camera service on the internal API; FFmpeg logging silenced; tests assert absence in responses, audit and logs | `schemas/cameras.py`, `services/camera_service.py` |
| Credentials stored securely | ✅ Encrypted columns (existing keyring, purpose `device-credential`, AAD bound to row + field). DB CHECK: both-or-neither, ciphertext only, never in stream paths | migration 0005 |
| No continuous recording | ✅ Nothing is recorded: only the latest sampled frame is held in memory; snapshots on demand, not stored, audited | `worker.py`, `/ready` says `recording: disabled` |
| Local processing | ✅ Frames are decoded and analysed on the camera-service host; only small JPEG snapshots/samples go to the Core API/recognition service on request | — |
| Stream not assumed suitable | ✅ `quality_status` UNTESTED by default; quality test (resolution, fps, frozen, exposure, sharpness, and **faces** via the recognition service) → PASSED / FAILED / INCONCLUSIVE. `recognition_enabled` can be switched on **only after PASSED**; any stream change resets to UNTESTED | `camera/quality.py`, `cameras.py` |
| Frontend | ✅ `/cameras`: list with filters and 10 s refresh, create/edit (provider-specific fields, write-only password), details (status, runtime, errors), connection test, snapshot, quality test with report, enable/disable, recognition toggle (gated), delete. Dashboard camera status card. | `apps/web` |

### Phase 10 verification (2026-11-08)
- API tests: **297 passed** with PostgreSQL. 27 new tests cover:
  - credentials never in responses, audit or DB plaintext;
  - provider validation, provider switch, name conflict;
  - the quality gate for recognition, invalid verdict;
  - enable/disable/delete, probe and snapshot through a fake camera service, 503 when not configured or unreachable;
  - permissions (ADMIN/VIEWER cannot write or view snapshots);
  - internal config + status flow incl. stale/old-version reports, and a guard test that every `/internal` route needs the service token.
  - The model/migration drift check passes for migration 0005.
- Camera service: **25 tests**. They cover the pattern/media/simulated providers, path traversal, RTSP URL encoding and redaction, unreachable classification, mocked USB, registry replacement, worker online/reconnect/offline/degraded, consumers with nothing written to disk, supervisor apply, quality verdicts (incl. fake face analyser), the API with auth, and Core sync with a mock transport.
- Web: **58 tests** (+8 camera form/status logic), ESLint, tsc and `next build` (18 routes) are clean.
- **Live end-to-end** (throwaway DB, three real services):
  - TEST entrance → ACTIVE at ~15 fps.
  - The disconnecting TEST exit → DEGRADED, then reconnect.
  - RTSP to a closed port → OFFLINE / UNREACHABLE after 3 retries.
  - Snapshot JPEG came from the live monitor.
  - Quality test on synthetic frames → **INCONCLUSIVE** (recognition service used, no face).
  - Enabling recognition → 409.
  - Internal APIs without a token → 401.
  - The RTSP password was absent from all API responses and both service logs.
- **Not done here:** no real RTSP camera or webcam was used. No IP camera exists in this environment, and I did not open your webcam without asking. Docker is not installed, so the compose service is untested.

### Notes / not done
- Your dev database is still at migration 0004: run `npm run db:migrate` before using the cameras page.
- No WebRTC live view (go2rtc), ONVIF discovery or ROI editor yet (API_CONTRACTS §2.6 rows marked planned). The snapshot button is the preview.
- Frames are not yet passed to the recognition service continuously (`add_consumer` hook exists). That is the attendance-integration phase, blocked by the Phase 9 evaluation.
- Snapshot viewing needs `cameras:write` (SUPER_ADMIN), stricter than the original contract (`cameras:read`), because it shows people.

## Decisions required from owner (blocking)

| ID | Decision | Options | Recommendation | Blocks |
|----|----------|---------|----------------|--------|
| D1 | Approve overall architecture and stack | Approve / request changes | Approve | Phase 2 |
| D2 | AI model licence path | A licensed InsightFace · B permissive (YuNet+SFace) · C commercial SDK · D camera-side recognition | Build with B, quote A, decide after Phase 4 benchmark. **Status (Phase 9):** B is implemented as the default. A is wired but refused in production until licensed. Decide after the owner-run evaluation. | Attendance integration |
| D3 | Keep encrypted enrollment images? | Keep (re-extract on model upgrade) / delete after extraction | Keep, encrypted, with consent wording covering it | Phase 5 |
| D4 | Liveness in v1? | Yes / No / Optional per camera | Optional per camera, off by default | Phase 4 |
| D5 | Employee self-service portal in v1? | Yes / No | Yes (read-only + requests) | Phase 3 scope |
| D6 | Hardware: GPU available? | Yes (model) / No | NVIDIA 8–12 GB if > 8 cameras | Phase 6 sizing |

## Decision log (approved decisions)

| Date | ID | Decision | By |
|------|----|----------|----|
| 2026-11-07 | D1 | Architecture approved (owner proceeded to Phase 2) | Owner |
| 2026-11-07 | D7 | Frontend stack: **Next.js 16 + Tailwind CSS 4** instead of React/Vite/Ant Design. Next.js is UI-only; no DB access. | Owner |
| 2026-11-07 | D8 | Python deps: pinned `requirements*.txt` + venv (instead of uv) | Implementation |
| 2026-11-07 | D9 | Phase 3 = Database implementation (owner-defined); roadmap renumbered | Owner |
| 2026-11-07 | D10 | DB driver asyncpg (not psycopg) for Windows compatibility | Implementation |
| 2026-11-07 | D11 | `attendance_events` merges recognition events and punches; `work_schedules` is fixed-weekly in v1 | Implementation (owner table list) |
| 2026-11-07 | D12 | Roles = SUPER_ADMIN / ADMIN / VIEWER (replaces the 5-role Phase 1 model) | Owner |
| 2026-11-07 | D13 | Server-side sessions + rotating refresh cookie + EdDSA access JWT; Argon2id | Implementation |
| 2026-11-08 | D14 | Profile photos stored (owner request), separate from biometrics, encrypted | Owner |
| 2026-11-08 | D15 | Consent is valid for enrollment only with the signed form attached (DB-enforced) | Implementation |
| 2026-11-08 | D16 | Daily attendance computed live from immutable events; corrections = void + add (never edit) | Implementation |
| 2026-11-08 | D17 | BOTH-direction cameras: 30 min minimum presence before a sighting can be an OUT | Implementation (configurable) |
| 2026-11-08 | D18 | Frontend: client-rendered authenticated area, in-memory token, own lightweight design system (no UI kit) | Implementation |
| 2026-11-08 | D19 | Monthly timesheet built client-side from /attendance/days until server reports exist | Implementation |
| 2026-11-08 | D20 | Default recognition engine YuNet + SFace (commercially usable licences). InsightFace only behind `ALLOW_RESEARCH_MODELS`, never in production. | Implementation (D2 still open) |
| 2026-11-08 | D21 | Conservative decision policy: MATCHED needs threshold + uncertain band + margin; AMBIGUOUS and LOW_QUALITY are never identified | Implementation (owner rule) |
| 2026-11-08 | D22 | Recognition service stays disconnected from attendance until the evaluation report is accepted | Owner rule |
| 2026-11-08 | D23 | Separate camera service (`apps/camera`) with replaceable providers RTSP / USB / TEST; config pulled from the Core API, status pushed back (HTTP, internal network, shared token). Valkey heartbeats deferred. | Implementation |
| 2026-11-08 | D24 | Recognition on a camera requires a PASSED stream quality test; changing the stream resets it | Implementation (owner rule) |
| 2026-11-10 | D28 | Recognition→attendance built but gated by `recognition.attendance_enabled` (default OFF); owner enables after testing with colleagues | Owner request + D22 |
| 2026-11-10 | D26 | Enrolment images are NOT stored (D3 resolved for now: delete after extraction). Re-enrolment needed on a model change. | Implementation (owner may revisit) |
| 2026-11-10 | D27 | Live recognition observations are in memory only (no recognition-event table yet); they never create attendance events | Implementation (D22) |
| 2026-11-08 | D25 | No video recording at all in v1; snapshots on demand only, not stored, audited, SUPER_ADMIN only | Implementation (owner rule) |

## Session log

| Date | Phase | Work done | Next |
|------|-------|-----------|------|
| 2026-11-07 | 1 | Workspace analyzed (empty). Produced PROJECT_OVERVIEW, ARCHITECTURE, DATABASE_SCHEMA, API_CONTRACTS, DEVELOPMENT_PROGRESS, DEVELOPMENT_RULES. | Owner review & approval; answer open questions |
| 2026-11-07 | 2 | Monorepo skeleton: Next.js + Tailwind web, FastAPI api, Alembic, env config, Docker Compose, dev scripts, git, README. Verified locally (see Phase 2 results). Docs updated for Next.js. | Owner approval of Phase 2; install Docker Desktop to verify Compose; decide on initial commit |
| 2026-11-07 | 3 | SQLAlchemy models (11 tables), Alembic 0001 with triggers + seed, column encryption, transaction helpers, audit writer, db-init tooling, 89 tests. Fixed Windows driver bug. Updated DATABASE_SCHEMA, README, ARCHITECTURE (C13–C14). | Owner approval of Phase 3; owner runs `npm run db:init` on the local PostgreSQL service |
| 2026-11-07 | 4 | Auth package (tokens, passwords, permissions, rate limit, service, dependencies), auth/users/settings endpoints, Problem Details errors, security headers, CLI, migration 0002, 75 new tests. Updated API_CONTRACTS, DATABASE_SCHEMA, ARCHITECTURE §7, README. | Owner approval of Phase 4 |
| 2026-11-08 | 5 | Employees/departments/photo/consent API, upload sanitiser, encrypted storage, migration 0003, shared test helpers, 40 new tests. Updated API_CONTRACTS §1.1/§2.3–2.5, DATABASE_SCHEMA, ARCHITECTURE §9, README. | Owner approval of Phase 5 |
| 2026-11-08 | 6 | Attendance engine (pure rules), service, schedules/attendance API, migration 0004 (immutable events), test-harness fix, 66 new tests. Updated API_CONTRACTS §2.8–2.9, DATABASE_SCHEMA, ARCHITECTURE §6, README. | Owner approval of Phase 6 |
| 2026-11-08 | 7 | Frontend foundation: tokens, 15 UI components, layout/sidebar/topbar, real login, dashboard, placeholders, dev style guide, security headers, 36 web tests. Docs updated (ARCHITECTURE §2.1, DEVELOPMENT_RULES, README). | Owner approval of Phase 7; owner tests login in a browser |
| 2026-11-08 | 10 | Camera service (providers, worker, supervisor, quality test, internal API, Core sync), migration 0005, camera API + internal API, cameras page + dashboard card, 60 new tests, live end-to-end. Docs updated. | Owner approval of Phase 10; owner runs `npm run db:migrate` and tests a real camera |
| 2026-11-08 | 9 | Recognition service: licence verification + manifest gate, YuNet/SFace (+ SCRFD/ArcFace for research), alignment, quality gate, matcher, encrypted gallery, perf logging, internal API, CLI (check/download/bench/enroll/evaluate), evaluation harness + protocol, 53 tests. Docs updated. | Owner approval of Phase 9; owner runs the evaluation with consenting volunteers |
| 2026-11-08 | 8 | 11 pages + schedules, shared entity widgets, day-detail/corrections, client timesheet/CSV, validation + error mapping, modal focus fix, 14 new web tests. Docs updated. | Owner approval of Phase 8; owner clicks through flows in a browser |
