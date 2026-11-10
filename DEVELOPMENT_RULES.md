# Development Rules — AI Employee Attendance

These rules are mandatory for every contributor, human or AI assistant. If a rule blocks you, raise it. Do not silently work around it.

## 1. Process and phase gates

1. Work proceeds **phase by phase** as defined in [DEVELOPMENT_PROGRESS.md](DEVELOPMENT_PROGRESS.md). **No phase starts without explicit owner approval** of the previous gate.
2. Architecture changes require updating [ARCHITECTURE.md](ARCHITECTURE.md) (and schema/API docs where affected) **in the same change** and owner sign-off for anything listed as **[DECISION NEEDED]**.
3. Update DEVELOPMENT_PROGRESS.md (checklist + session log) at the end of each work session.
4. Docs are the contract: if code and [API_CONTRACTS.md](API_CONTRACTS.md) / [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) differ, fix one of them before merging.

## 2. Source control

- Git, trunk-based: `main` is always deployable. Work happens on short-lived branches `feat/…`, `fix/…`, `chore/…`, `docs/…`.
- **Conventional Commits**: `feat(api): add camera test endpoint`.
- Every merge goes through a PR: CI green and at least one review (self-review checklist if solo).
- Never commit: secrets, `.env`, model weight files, real face images, real employee data, database dumps.
- `.gitignore` and a pre-commit hook (`gitleaks`, `detect-secrets`) must be set up in Phase 2.

## 3. Code standards

### Python (api, recognition)
- Python 3.12–3.14 (Docker images use 3.12). Dependencies are **pinned to exact versions** in `requirements.txt` / `requirements-dev.txt` and installed into `apps/api/.venv`.
- `ruff` (lint + format), `mypy --strict` on new modules, no `# type: ignore` without a comment explaining why.
- Layering in Core API: `router → service → repository`. Routers contain no business logic. Repositories contain no business rules.
- Pydantic v2 schemas for every request/response. Never return ORM objects directly.
- Async I/O in the API. CPU-heavy work goes to the Worker or Recognition service, never the request path.
- No raw SQL strings except in Alembic migrations or reviewed, parameterised `text()` with a comment.

### TypeScript (web)
- TypeScript `strict: true`. ESLint + Prettier. No `any` without a justification comment.
- API types are **generated** from OpenAPI (`openapi-typescript`). Do not hand-write duplicates.
- Next.js App Router. Styling with Tailwind CSS only (no extra CSS frameworks without approval).
- Next.js never accesses the database. All data goes through the Core API, via `lib/api.ts` (`api.request` / `useApiQuery`). No ad-hoc `fetch` in components.
- Use the design-system components in `components/ui` and the tokens in `globals.css`. No raw hex colours, and no new one-off button or table styles.
- Every data view handles **loading, error (with retry) and empty** states (DataTable / ErrorState / EmptyState).
- Unbuilt features may only appear as a `DevPlaceholder` / `PlannedPage` clearly labelled "ISHLAB CHIQILMOQDA". Never fake data in production pages. Sample data is allowed only on the dev-only `/design-system` page and must be labelled.
- Never store tokens in localStorage, sessionStorage or non-HttpOnly cookies.
- Dates and times are shown in the organisation time zone (`lib/format.ts`), never the browser's.
- Read `apps/web/AGENTS.md`: Next.js 16 APIs differ from older versions (e.g. error boundaries get `retry`).
- Server state uses a single data-fetching approach (chosen in Phase 3, e.g. TanStack Query). No ad-hoc global stores for server data.
- All user-visible strings go through the i18n layer (`uz`, `ru`, `en`; library chosen in Phase 3). No hard-coded UI text after Phase 3. The Phase 2 skeleton page is exempt.
- Dates are formatted only with the shared helper that applies the site timezone.

## 4. Database

- Every schema change is an **Alembic migration**, reviewed, reversible (`downgrade` implemented) and tested on a copy of realistic data volume.
- Never edit an already-merged migration.
- Destructive migrations (drop/rename column) use expand → migrate → contract across releases.
- Every new table follows [DATABASE_SCHEMA.md §1](DATABASE_SCHEMA.md) conventions and is added to the doc.

## 5. API

- Follow [API_CONTRACTS.md §1](API_CONTRACTS.md) conventions (Problem Details errors, pagination, ETags).
- **Every endpoint declares its permission** through the shared dependency. CI test enumerates routes and fails if any route lacks a permission declaration (except allow-listed public ones).
- Data scoping is applied in the service/repository layer and tested for MANAGER and EMPLOYEE roles on every list/detail endpoint.
- Breaking changes are forbidden within `/api/v1`.

## 6. Security (non-negotiable)

1. Never log: passwords, tokens, embeddings, images, camera credentials, full RTSP URLs. The logging redaction filter must stay enabled. Tests assert it.
2. Secrets come only from environment/secret files. Never put defaults for secrets in code.
3. Use the shared `crypto` module for all encryption. Never implement crypto ad hoc. AES-256-GCM, random 96-bit nonces, key IDs stored with ciphertext.
4. Validate all uploads (type by magic bytes, size, decode + re-encode images).
5. Add a new dependency only when needed and well maintained, with a licence on the allow-list (§8). Record the reason in the PR.
6. Mutating endpoints must write an audit record. Audit coverage is tested.
7. Internal endpoints (`/internal/*`) must never be routed through Nginx. There is a test for the Nginx config.

## 7. Biometric data handling

1. No real face images in the repo, tests, fixtures, issues, PRs, screenshots or chat logs. Use synthetic or licensed public test sets whose licence allows it, stored outside the repo.
2. Pilot or real data stays on the on-prem server. It is never copied to personal machines or cloud services.
3. Embeddings never leave the backend boundary (no public API returns them; no frontend code receives them).
4. Any feature that stores, extends or reuses biometric data needs a note in ARCHITECTURE §9 and owner approval.
5. Emotion, age, gender, ethnicity or other attribute inference is prohibited in this codebase.

## 8. Licensing

1. Licence allow-list for dependencies: MIT, BSD-2/3, Apache-2.0, ISC, PostgreSQL, MPL-2.0, LGPL (dynamic linking only). **Anything else (GPL, AGPL, SSPL, RSAL, non-commercial, unknown) needs owner approval.**
2. **AI model weights:**
   - Model files are never committed to git. `models/manifest.yaml` lists each model: name, version, source URL, SHA-256, **licence**, commercial-use status and licence proof location.
   - CI fails if the production image contains a model not in the manifest or whose `commercial_use` is not `allowed`/`licensed`.
   - Non-commercial weights (for example InsightFace pretrained packs without a purchased licence) may only be used in **isolated local research** and never in shared images, staging or production.
3. Run a licence scan (`pip-licenses`, `license-checker`) in CI. The report is attached to each release.

## 9. Testing

| Layer | Requirement |
|-------|-------------|
| Attendance engine | Pure functions, table-driven tests. **100% branch coverage** for the computation module. Must include midnight-crossing shifts, holidays, leave, transferred working days, missing punches and duplicate punches. |
| API | pytest + httpx against a real Postgres (testcontainers). Permission matrix tests per role. |
| Recognition | Unit tests for pre/post-processing (alignment, NMS, matching). Integration test with RTSP replay of a synthetic video. Benchmark harness kept in `tools/benchmark`. |
| Frontend | Vitest + Testing Library (`npm run test:web`): API client token handling, components (a11y behaviour), login helpers. Playwright E2E planned. |
| Coverage | ≥ 80% lines on `apps/api` services. No PR may lower coverage by more than 1%. |
| Performance | Phase 6+: 24 h soak test for camera workers (memory stable within ±10%). |

## 10. Definition of Done (per feature)

- [ ] Code follows the standards above and is type-checked, linted and formatted
- [ ] Tests written and passing; CI green
- [ ] Permissions + data scoping enforced and tested
- [ ] Audit records written for mutations
- [ ] No sensitive data in logs (checked)
- [ ] i18n strings added for uz/ru/en
- [ ] Docs updated (API_CONTRACTS, DATABASE_SCHEMA, ARCHITECTURE as relevant)
- [ ] DEVELOPMENT_PROGRESS.md updated
- [ ] Migration reversible (if any)
- [ ] Reviewed

## 11. Environments and configuration

- 12-factor configuration through environment variables, validated at start-up with `pydantic-settings`. The service refuses to start on invalid config.
- `dev`: `docker-compose.dev.yml`, seeded fake data, RTSP replay of synthetic videos.
- `prod`: `docker-compose.prod.yml`, images pinned by digest, no dev tools, read-only root filesystems where possible, non-root users.
- Time: all servers run NTP, and containers use UTC. Business time zone comes from `sites.timezone` only.

## 12. AI assistant-specific rules

- Do not start a new phase or make architecture decisions marked **[DECISION NEEDED]** without explicit owner approval.
- Do not download or add model weights without recording the licence in `models/manifest.yaml` and confirming commercial-use status with the owner.
- When uncertain about a requirement, ask. Do not invent business rules (especially for attendance computation and leave).
