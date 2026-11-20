# API Contracts — AI Employee Attendance

> Status: Sections marked **✅ Implemented** exist in code and are covered by tests (Phase 4). Other sections are still the **proposal** for later phases.
> Live schema: `GET /openapi.json`, UI at `/docs` (both disabled when `APP_ENV=production`). This file is the source of truth for **intent**. If code and this file differ, fix one before merging.

## 1. General conventions

| Topic | Rule |
|-------|------|
| Base URL | `https://<host>/api/v1` |
| Format | JSON, UTF-8. `snake_case` field names. |
| Auth | `Authorization: Bearer <access_jwt>` on all endpoints except those marked **public** |
| IDs | UUID strings |
| Time | ISO-8601 with offset, returned in UTC (`2026-11-07T04:15:00Z`). `date` fields are `YYYY-MM-DD` in the site timezone. |
| Pagination | `?page=1&page_size=50` (max 200). Response: `{ "items": [...], "page": 1, "page_size": 50, "total": 1234 }` |
| Sorting | `?sort=last_name,-hire_date` |
| Filtering | Explicit query params per endpoint (no generic filter language) |
| Idempotency | `Idempotency-Key` header supported on POST endpoints that create punches, corrections and exports |
| Concurrency | Mutable resources return `ETag`. PUT/PATCH accept `If-Match`, and a stale value gets `412`. |
| Request ID | `X-Request-ID` accepted/returned; included in logs and audit |
| Versioning | URL major version. Additive changes are allowed within v1. Breaking changes mean v2. |

### 1.1 Error format (RFC 9457 Problem Details) ✅ Implemented

All errors use `Content-Type: application/problem+json`:

```jsonc
{
  "type": "https://errors.attendance.local/validation-error",
  "title": "Validation failed",
  "status": 422,
  "code": "VALIDATION_ERROR",
  "detail": "One or more fields are invalid.",
  "errors": [{ "field": "password", "code": "string_too_long", "message": "String should have at most 128 characters" }]
}
```

Validation errors **never echo submitted values** (they could contain passwords). Unexpected server errors return a generic `500 INTERNAL_ERROR` with no internal details. Clients should branch on `code`, not on `title` or `detail`.

| HTTP | `code` | When |
|------|--------|------|
| 401 | `NOT_AUTHENTICATED` | No or malformed `Authorization: Bearer` header |
| 401 | `INVALID_TOKEN` | Bad signature, wrong issuer/audience/type, unknown key id, malformed |
| 401 | `TOKEN_EXPIRED` | Access token expired, so call `/auth/refresh` |
| 401 | `SESSION_ENDED` | Session revoked (logout, password change, deactivation, token reuse) or expired. Sign in again. |
| 401 | `INVALID_CREDENTIALS` | Login failed. Same response for unknown user, wrong password and inactive account. |
| 401 | `INVALID_REFRESH_TOKEN` | Refresh cookie missing, invalid, expired or reused |
| 403 | `FORBIDDEN` | Authenticated but the role lacks the permission |
| 403 | `CSRF_CHECK_FAILED` | `/auth/refresh` called without `X-Requested-With` |
| 404 | `NOT_FOUND` | |
| 405 | `METHOD_NOT_ALLOWED` | |
| 409 | `USER_EXISTS`, `LAST_SUPER_ADMIN` | |
| 409 | `EMPLOYEE_CODE_EXISTS`, `EMPLOYEE_EMAIL_EXISTS`, `EMPLOYEE_PHONE_EXISTS`, `INVALID_STATUS_TRANSITION`, `DEPARTMENT_EXISTS`, `DEPARTMENT_NOT_EMPTY` | Employee management (§2.3–2.4) |
| 409 | `CONSENT_ALREADY_ACTIVE`, `CONSENT_REVOKED`, `DOCUMENT_ALREADY_ATTACHED`, `EMPLOYEE_TERMINATED`, `BIOMETRICS_NOT_APPLICABLE`, `ACTIVE_CONSENT_EXISTS` | Biometric consent (§2.5) |
| 409 | `INVALID_SEQUENCE`, `SCHEDULE_EXISTS`, `SCHEDULE_IN_USE` | Attendance (§2.8–2.9) |
| 413 | `PAYLOAD_TOO_LARGE` | Upload over the size limit |
| 422 | `VALIDATION_ERROR` | Schema violation, unknown field, or password policy (`errors[].code = "password_policy"`) |
| 422 | `INVALID_FILE` | Upload is not an accepted/valid file (`errors[].code`: `unsupported_type`, `invalid_image`, `too_small`, `invalid_document`, `empty`) |
| 429 | `TOO_MANY_ATTEMPTS` | Login rate limit or temporary account lock. Includes a `Retry-After` header (seconds). |
| 500 | `INTERNAL_ERROR` | |

All 401 responses carry `WWW-Authenticate: Bearer realm="attendance"`.

Planned for later phases: `DUPLICATE_FACE`, `CONSENT_REQUIRED`, `SCHEDULE_OVERLAP` (409); `PRECONDITION_FAILED` (412); `NO_FACE_DETECTED`, `MULTIPLE_FACES`, `LOW_QUALITY_IMAGE` (422); `RECOGNITION_UNAVAILABLE` (503).

### 1.2 Authentication and authorization ✅ Implemented

**Roles** (owner-defined, Phase 4):

| Role | Intended for |
|------|-------------|
| `SUPER_ADMIN` | System owner: user accounts, system settings, cameras, audit log. Everything. |
| `ADMIN` | Day-to-day operators (HR): employees, biometrics, attendance and reports. Can view users and settings, but cannot change them. |
| `VIEWER` | Read-only: employees, cameras, attendance and reports. No biometrics, users, settings or audit. |

**Permission matrix** (source of truth: [app/auth/permissions.py](apps/api/app/auth/permissions.py)):

| Permission | SUPER_ADMIN | ADMIN | VIEWER |
|------------|:-:|:-:|:-:|
| `users:read` | ✔ | ✔ | — |
| `users:write` | ✔ | — | — |
| `settings:read` | ✔ | ✔ | — |
| `settings:write` | ✔ | — | — |
| `audit:read` | ✔ | — | — |
| `system:read` | ✔ | ✔ | ✔ |
| `employees:read` | ✔ | ✔ | ✔ |
| `employees:write` | ✔ | ✔ | — |
| `biometrics:read` / `biometrics:write` | ✔ | ✔ | — |
| `cameras:read` | ✔ | ✔ | ✔ |
| `cameras:write` | ✔ | — | — |
| `attendance:read` | ✔ | ✔ | ✔ |
| `attendance:write` | ✔ | ✔ | — |
| `reports:read` | ✔ | ✔ | ✔ |
| `notifications:read` / `notifications:write` | ✔ | ✔ | — |
| `branches:manage` (every branch) | ✔ | — | — |

Every non-public endpoint declares a permission through `require(Permission.X)`. A test fails if any `/api/v1` route lacks authentication. **The role is read from the database on every request**, never from the token, so role changes and deactivation take effect immediately.

**Tokens:**

| Item | Design |
|------|--------|
| Access token | JWT, **EdDSA (Ed25519)**, 15 min (`ACCESS_TOKEN_MINUTES`). Claims: `iss=attendance-api`, `aud=attendance-web`, `sub` (user id), `sid` (session id), `role` (informational only), `typ=access`, `iat`, `nbf`, `exp`, `jti`. Header `kid` selects the key (rotation via `JWT_SIGNING_KEYS`). Algorithm pinned to EdDSA, so `alg=none` and HS256 key-confusion are rejected. |
| Where the client keeps it | **In memory only** (JS variable). Never in localStorage or sessionStorage. Sent as `Authorization: Bearer`. |
| Session | Server-side row per login (`auth_sessions`). Every request checks it is active, so logout works instantly. Absolute lifetime 7 days (`SESSION_DAYS`). |
| Refresh token | 256-bit random value in cookie `attendance_refresh`: `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`. Only its SHA-256 is stored. **Rotated on every refresh.** If an already-used token is presented, the whole session is revoked (theft detection). |
| CSRF | The refresh cookie is SameSite=Strict, and `/auth/refresh` additionally requires an `X-Requested-With` header. Other endpoints use Bearer headers, which browsers never attach automatically. |
| Passwords | Argon2id (64 MiB, t=3, p=4), NFKC-normalised. Rehashed automatically at login if parameters are strengthened. Policy: 12–128 characters, ≥ 4 distinct characters, not a common password, must not contain the username. |

**Login protection:**

| Layer | Limit | Scope |
|-------|-------|-------|
| In-memory flood guard (before any hashing) | 10 attempts/min per client IP (`LOGIN_RATE_PER_MINUTE`) | per API process |
| DB-backed IP limit | 20 failed logins per 15 min per IP (`LOGIN_IP_MAX_FAILURES`, `LOGIN_IP_WINDOW_MINUTES`) | all processes |
| Account lockout | 5 consecutive failures, then locked 15 min (`LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_MINUTES`). While locked, the password is not checked. | per account |
| Password change | 5 attempts per 15 min per user | per API process |

Client IP comes from `X-Forwarded-For` **only** if the direct peer is listed in `TRUSTED_PROXIES`. Unknown usernames take the same time as wrong passwords (dummy hash) and return the same response.

Known trade-offs:
- Lockout lets an attacker temporarily lock a known account. The lock is time-limited, and a SUPER_ADMIN can unlock it.
- Unknown usernames never lock, so after 5 attempts a real account answers 429 while a fake one answers 401.
- Two browser tabs refreshing at the same instant can trigger reuse detection. The web client must serialise refreshes (single in-flight refresh).

---

## 2. Public REST API

The permission column uses the keys from §1.2. Sections 2.6, 2.7 and 2.10 onward are **planned**: they still mention the Phase 1 roles (HR_ADMIN, MANAGER, ...), which will be mapped to SUPER_ADMIN/ADMIN/VIEWER permissions when implemented. Data scoping ("own department") is not part of the three-role model.

### 2.1 Auth ✅ Implemented

| Method | Path | Auth | Success | Errors | Description |
|--------|------|------|---------|--------|-------------|
| POST | `/auth/login` | public | 200 | 401 `INVALID_CREDENTIALS`, 422, 429 | `{username, password}` → tokens + refresh cookie |
| POST | `/auth/refresh` | refresh cookie + `X-Requested-With` | 200 | 401 `INVALID_REFRESH_TOKEN`, 403 `CSRF_CHECK_FAILED` | New access token. Rotates the cookie. |
| POST | `/auth/logout` | Bearer | 204 | 401 | Revokes the current session and clears the cookie |
| GET | `/auth/me` | Bearer | 200 | 401 | Current user, effective permissions, session |
| POST | `/auth/password` | Bearer | 204 | 401, 422, 429 | `{current_password, new_password}`. Signs out all **other** sessions. |

Planned (not yet implemented): TOTP MFA endpoints (`/auth/mfa/*`).

**`POST /auth/login`**

Request (unknown fields are rejected):
```json
{ "username": "admin", "password": "correct horse battery staple" }
```
| Field | Rule |
|-------|------|
| `username` | string, 1–64 chars, trimmed, case-insensitive match |
| `password` | string, 1–128 chars, not trimmed |

Response `200` (headers: `Cache-Control: no-store`, `Set-Cookie: attendance_refresh=...; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth; Max-Age=604800`):
```json
{
  "access_token": "eyJhbGciOiJFZERTQSIsImtpZCI6IjEiLCJ0eXAiOiJKV1QifQ...",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": {
    "id": "0193f0c2-...",
    "username": "admin",
    "email": null,
    "role": "SUPER_ADMIN",
    "is_active": true,
    "locale": "uz",
    "last_login_at": "2026-11-07T15:20:11Z",
    "created_at": "2026-11-07T15:00:00Z"
  }
}
```

**`POST /auth/refresh`**: no body. Headers: `X-Requested-With: XMLHttpRequest`; the cookie is sent automatically with `fetch(..., {credentials: "include"})`. Response: same shape as login, with a new cookie.

**`GET /auth/me`**: response `200`:
```json
{
  "user": { "id": "...", "username": "admin", "role": "SUPER_ADMIN", "...": "..." },
  "permissions": ["attendance:read", "attendance:write", "audit:read", "..."],
  "session": { "id": "...", "created_at": "...", "expires_at": "..." }
}
```

**Client flow (web app, Phase 5+):**
1. Log in and keep `access_token` in memory.
2. Call APIs with the Bearer token.
3. On `401 TOKEN_EXPIRED`, make **one** refresh call (serialised), then retry.
4. On `401 SESSION_ENDED` or `INVALID_REFRESH_TOKEN`, go to the login page.
5. On page reload, call `/auth/refresh` to restore the session from the cookie.

### 2.2 Users (system accounts) ✅ Implemented

| Method | Path | Permission | Success | Errors | Description |
|--------|------|------------|---------|--------|-------------|
| GET | `/users` | `users:read` | 200 | 401, 403, 422 | `?page=1&page_size=50` (≤200), `role`, `is_active`, `q` (username/email contains) → `Page[UserAdmin]` |
| POST | `/users` | `users:write` | 201 | 409 `USER_EXISTS`, 422 | `{username, password, role, email?, locale?}` |
| GET | `/users/{id}` | `users:read` | 200 | 404, 422 (bad uuid) | |
| PATCH | `/users/{id}` | `users:write` | 200 | 404, 409 `LAST_SUPER_ADMIN` / `USER_EXISTS`, 422 | Any of `{role, is_active, email, locale}`. Deactivation revokes all the user's sessions. |
| POST | `/users/{id}/password` | `users:write` | 204 | 404, 422 | `{new_password}`. Admin reset: clears the lock and revokes all the user's sessions. |
| POST | `/users/{id}/unlock` | `users:write` | 204 | 404 | Clears the failed-login lock |
| DELETE | `/users/{id}/sessions` | `users:write` | 204 | 404 | Signs the user out everywhere |

`UserAdmin` = `user` fields above plus `failed_login_count`, `locked_until`, `password_changed_at`, `updated_at`. Password hashes are never returned. All mutations write audit records (`USER_CREATED`, `USER_UPDATED` with a field diff, `USER_PASSWORD_RESET`, `USER_UNLOCKED`, `USER_SESSIONS_REVOKED`).

**First SUPER_ADMIN**: there is no public sign-up. Create it from the server console with `npm run user:create -- --username <name> --role SUPER_ADMIN`. The password is prompted, never passed as an argument.

### 2.2b Branches ✅ Implemented (Phase 21)

Every department, work schedule, employee, user and camera belongs to one branch. Scope: a branch user sees only `user.branch_id`; a user with `branches:manage` (SUPER_ADMIN) sees every branch, or one branch sent as header `X-Branch-Id: <uuid>` (`all` / absent = every branch; unknown id → 422). Rows of another branch answer **404**. New rows go to the selected branch (else the main branch); an employee goes to its department's branch. References in bodies must be in the same branch (422 `not_found`).

| Method | Path | Permission | Success | Errors | Description |
|--------|------|------------|---------|--------|-------------|
| GET | `/branches` | any signed-in user | 200 | 401 | All branches (manager) or the own one → `[{id, name, location, is_active, is_main, employee_count, user_count, camera_count}]` |
| POST | `/branches` | `branches:manage` | 201 | 409 `BRANCH_EXISTS`, 422 | `{name, location?}` |
| PATCH | `/branches/{id}` | `branches:manage` | 200 | 404, 409 `MAIN_BRANCH` / `BRANCH_EXISTS` | `{name?, location?, is_active?}`. Closing: its users get 401 `BRANCH_INACTIVE` and cannot sign in; data is kept |
| DELETE | `/branches/{id}` | `branches:manage` | 204 | 404, 409 `MAIN_BRANCH` / `BRANCH_NOT_EMPTY` | Only a branch with no rows at all |
| GET | `/branches/overview?date=` | `branches:manage` | 200 | 403 | `{date, branches: [{branch, totals}], totals}`; totals as in `/attendance/board` |

`/auth/me` adds `branch {id, name}` and `all_branches`. Users, employees, departments, schedules and cameras carry `branch_id`; `POST/PATCH /users` and `/cameras` accept `branch_id`. Ignore reason `OTHER_BRANCH`: a camera recognised an employee of another branch. Audit: `BRANCH_CREATED`, `BRANCH_UPDATED`, `BRANCH_DELETED`.

### 2.2a Settings ✅ Implemented (read-only)

| Method | Path | Permission | Description |
|--------|------|------------|-------------|
| GET | `/settings` | `settings:read` | `[{key, value, description, updated_at}]` |

### Audit events written by auth

`AUTH_LOGIN_SUCCEEDED`, `AUTH_LOGIN_FAILED` (user id only if the account exists; attempted usernames are never stored), `AUTH_LOGIN_BLOCKED` (IP limit or account lock), `AUTH_LOGOUT`, `AUTH_PASSWORD_CHANGED`, `AUTH_REFRESH_REUSE_DETECTED`.

### 2.3 Departments ✅ Implemented

| Method | Path | Permission | Success | Errors | Description |
|--------|------|------------|---------|--------|-------------|
| GET | `/departments` | `employees:read` | 200 | 401, 403 | Flat list (sorted by name) with `employee_count`. The client builds the tree from `parent_id`. |
| POST | `/departments` | `employees:write` | 201 | 404 (parent), 409 `DEPARTMENT_EXISTS`, 422 | `{name, code?, parent_id?}` |
| PATCH | `/departments/{id}` | `employees:write` | 200 | 404, 409, 422 (`errors[].code = "cycle"` if moved under itself or a descendant) | Any of `{name, code, parent_id, head_employee_id}` |
| DELETE | `/departments/{id}` | `employees:write` | 204 | 404, 409 `DEPARTMENT_NOT_EMPTY` | Soft delete. Only allowed with no employees and no sub-departments. |

Sibling names are unique case-insensitively, and so are codes. Planned (not implemented): sites, positions.

### 2.4 Employees ✅ Implemented

| Method | Path | Permission | Success | Errors | Description |
|--------|------|------------|---------|--------|-------------|
| GET | `/employees` | `employees:read` | 200 | 422 | List, search, filter, paginate (below) |
| POST | `/employees` | `employees:write` | 201 | 409, 422 | Create |
| GET | `/employees/{id}` | `employees:read` | 200 | 404, 422 | Details |
| PATCH | `/employees/{id}` | `employees:write` | 200 | 404, 409, 422 | Partial update. Explicit `null` clears an optional field. Required fields cannot be null. |
| POST | `/employees/{id}/deactivate` | `employees:write` | 200 | 404, 409 `INVALID_STATUS_TRANSITION`, 422 | `{status: "SUSPENDED" \| "TERMINATED", termination_date?, reason}`. `termination_date` applies to TERMINATED only (default today, ≥ hire_date). |
| POST | `/employees/{id}/activate` | `employees:write` | 200 | 404, 409 | `{reason?}`. Sets ACTIVE and clears `termination_date`. |
| PUT | `/employees/{id}/photo` | `employees:write` | 200 | 404, 413, 422 `INVALID_FILE` | multipart field `file`: JPEG/PNG ≤ 5 MB, ≥ 64 px |
| GET | `/employees/{id}/photo` | `employees:read` | 200 `image/jpeg` | 404 | `Cache-Control: private, no-store` |
| DELETE | `/employees/{id}/photo` | `employees:write` | 204 | 404 | Deletes the file |

**List query parameters:**

| Param | Type | Notes |
|-------|------|-------|
| `q` | string ≤ 100 | Case-insensitive *contains* match on full name or employee code. Apostrophe variants (`'` `‘` `’` `ʻ` `` ` ``) are equivalent. `%` and `_` are literal. |
| `department_id` | uuid | With `include_subdepartments` (default **true**) the whole subtree is included |
| `status` | `ACTIVE` \| `ON_LEAVE` \| `SUSPENDED` \| `TERMINATED` | |
| `attendance_method` | `FACE` \| `MANUAL` | |
| `biometric_status` | see below | |
| `sort` | `last_name` \| `employee_code` \| `hire_date` \| `created_at`, prefix `-` for descending | default `last_name` |
| `page`, `page_size` | int, `page_size` ≤ 200 | default 1 / 50 |

**Create body** (unknown fields → 422):

| Field | Rule |
|-------|------|
| `employee_code` | required. Trimmed and **upper-cased**. `[A-Z0-9_.-]{1,50}`. Unique forever (also against deleted records). |
| `last_name`, `first_name` | required, ≤ 100. Letters of any script plus space, `-`, `.`, `ʻ`. Apostrophes normalised to `ʻ` (U+02BB). |
| `middle_name` | optional, same rule |
| `department_id` | required, must exist |
| `work_schedule_id` | optional, must exist |
| `position_title` | optional, ≤ 150 |
| `phone` | optional. Spaces, `()`, `-` removed, then `+?[0-9]{7,15}`. Unique among non-deleted employees. |
| `email` | optional, lower-cased, unique among non-deleted employees |
| `hire_date` | required, 1950-01-01 … today + 1 year |
| `attendance_method` | `FACE` (default) or `MANUAL` (non-biometric alternative) |

Status is not settable through create or PATCH. Use `/activate` and `/deactivate`, so every status change carries a reason in the audit log.

**Duplicate prevention**: `409` with `code` `EMPLOYEE_CODE_EXISTS`, `EMPLOYEE_EMAIL_EXISTS` or `EMPLOYEE_PHONE_EXISTS`. The API checks first for a friendly message, and database unique indexes are the final guarantee (race-safe).

**Employee object:**

```json
{
  "id": "01a1b6d9-...",
  "employee_code": "SM-1",
  "last_name": "Qoʻchqorov",
  "first_name": "Aziz",
  "middle_name": null,
  "full_name": "Qoʻchqorov Aziz",
  "department": { "id": "...", "name": "Buxgalteriya" },
  "work_schedule_id": null,
  "position_title": "Hisobchi",
  "phone": "+998901234567",
  "email": null,
  "hire_date": "2025-01-10",
  "termination_date": null,
  "status": "ACTIVE",
  "is_active": true,
  "attendance_method": "FACE",
  "has_photo": true,
  "photo_updated_at": "2026-11-08T09:12:00Z",
  "biometric": {
    "status": "READY_FOR_ENROLLMENT",
    "consent_id": "...",
    "consent_granted_at": "2026-11-08T09:13:00Z",
    "consent_documented": true,
    "face_templates": 0,
    "can_enroll": true
  },
  "created_at": "...",
  "updated_at": "..."
}
```

**Profile photo**: used only for identification in the UI. It is **never** used for face recognition (enrollment has its own consent-gated flow). The file type is decided by content, not by filename or Content-Type. Images are fully decoded and re-encoded to JPEG ≤ 512 px, which removes EXIF/GPS and any trailing payload. Files are stored AES-256-GCM encrypted. Replacing or deleting a photo deletes the old file.

### 2.5 Biometric consent ✅ Implemented (enrollment endpoints planned)

**Biometric status** (`biometric.status`, also a list filter):

| Status | Meaning | `can_enroll` |
|--------|---------|:-:|
| `NOT_APPLICABLE` | `attendance_method = MANUAL` (non-biometric attendance) | — |
| `NO_CONSENT` | No consent ever recorded | — |
| `CONSENT_PENDING_DOCUMENT` | Consent recorded, signed form not yet attached | — |
| `READY_FOR_ENROLLMENT` | Active consent **with signed form**, no face templates yet | ✔ (unless TERMINATED) |
| `ENROLLED` | Active documented consent and ≥ 1 face template | ✔ (unless TERMINATED) |
| `CONSENT_REVOKED` | Latest consent revoked (templates were deleted) | — |

| Method | Path | Permission | Success | Errors | Description |
|--------|------|------------|---------|--------|-------------|
| GET | `/biometrics/consent-version` | `biometrics:read` | 200 | | `{consent_text_version}`: the form version employees must sign (setting `biometric.consent_text_version`) |
| GET | `/employees/{id}/consents` | `biometrics:read` | 200 | 404 | History, newest first |
| POST | `/employees/{id}/consents` | `biometrics:write` | 201 | 404, 409, 422 | `{consent_text_version, employee_signed: true}` |
| PUT | `/employees/{id}/consents/{cid}/document` | `biometrics:write` | 200 | 404, 409, 413, 422 | multipart `file`: signed form, PDF/JPEG/PNG ≤ 10 MB |
| GET | `/employees/{id}/consents/{cid}/document` | `biometrics:read` | 200 | 404 | Download as attachment. **Audited** (`BIOMETRIC_CONSENT_DOCUMENT_VIEWED`). |
| POST | `/employees/{id}/consents/{cid}/revoke` | `biometrics:write` | 200 | 404, 409 `CONSENT_REVOKED`, 422 | `{reason}`. **Deletes all face templates of that consent in the same transaction.** |

Consent rules:
1. `consent_text_version` must equal the current version (`422 version_mismatch`), and `employee_signed` must be `true`. The operator confirms that the employee signed the form in person.
2. Only one active consent per employee (`409 CONSENT_ALREADY_ACTIVE`).
3. Not allowed for `TERMINATED` employees (`409 EMPLOYEE_TERMINATED`) or `MANUAL` attendance (`409 BIOMETRICS_NOT_APPLICABLE`).
4. The signed form is evidence: it cannot be replaced (`409 DOCUMENT_ALREADY_ATTACHED`), and a revoked consent is immutable. To change anything, revoke and record a new consent.
5. **Never enrolled without documented consent.** The database trigger rejects any face template unless the employee has an *active consent with the signed form attached* (revision `0003`). This holds even for direct SQL.
6. Switching an employee to `MANUAL` while a consent is active returns `409 ACTIVE_CONSENT_EXISTS` (revoke first).
7. A consent belonging to another employee answers 404.

**Non-biometric alternative**: employees who decline biometrics get `attendance_method = MANUAL`. They appear normally in all lists, cannot be enrolled, and their attendance will be recorded manually (attendance phase).

Planned (not implemented): face enrollment endpoints (`/employees/{id}/enrollments`, `/biometrics/quality-check`). These will require `biometric.can_enroll = true`.

### Audit events written by employee management

`EMPLOYEE_CREATED`, `EMPLOYEE_UPDATED` (field diff), `EMPLOYEE_DEACTIVATED` / `EMPLOYEE_ACTIVATED` (with reason), `EMPLOYEE_PHOTO_UPDATED` / `EMPLOYEE_PHOTO_DELETED`, `DEPARTMENT_CREATED` / `_UPDATED` / `_DELETED`, `BIOMETRIC_CONSENT_RECORDED`, `BIOMETRIC_CONSENT_DOCUMENTED`, `BIOMETRIC_CONSENT_DOCUMENT_VIEWED`, `BIOMETRIC_CONSENT_REVOKED` (with `templates_deleted` count).

### 2.7a Review queue ✅ Implemented (Phase 15)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/recognition/reviews?status=PENDING\|ASSIGNED\|DISMISSED\|ALL&camera_id&page&page_size≤100` | `biometrics:read` | `{id, event_time, camera_id, camera_name, camera_direction, decision, similarity, has_snapshot, review_status, reviewed_at, reviewed_by_user_id, employee?, punch_event_id?}` |
| GET | `/recognition/reviews/{id}/snapshot` | `biometrics:read` | JPEG face crop, `no-store`, audited; 410 `SNAPSHOT_DELETED` |
| POST | `/recognition/reviews/{id}/assign` `{employee_id, direction?, note?}` | + `attendance:write` | MANUAL punch via corrections; 409 `REVIEW_CLOSED`; 422 sequence errors / direction required |
| POST | `/recognition/reviews/{id}/dismiss` `{reason?}` | + `attendance:write` | 409 `REVIEW_CLOSED` |

Internal (camera token): `POST /internal/v1/recognition-reviews` multipart `meta` (JSON: event_uid, camera_id, event_time, decision AMBIGUOUS|UNKNOWN, model_id, similarity?, second_similarity?, quality_score?) + `file` (JPEG crop ≤ 512 KB) → `{stored, review_id?}` or `{stored:false, reason}`.

### 2.10a Reports ✅ Implemented (Phase 14)

Permission `reports:read`. `format` = `json` (default, `{kind, title, subtitle, columns:[{key,label,align}], rows, footer}`) | `xlsx` | `pdf` (attachment, `no-store`, audited `REPORT_EXPORTED`).

| Method | Path | Query |
|---|---|---|
| GET | `/reports/timesheet` | `month=YYYY-MM`, `department_id?` (subtree) |
| GET | `/reports/lateness` | `date_from`, `date_to` (≤ 92 days), `department_id?` |
| GET | `/reports/absence` | `date_from`, `date_to` (≤ 92 days), `department_id?` |

### 2.5a Face enrolment ✅ Implemented (Phase 11)

Replaces the planned `/enrollments` rows. Permission `biometrics:write` (list: `biometrics:read`).

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/employees/{id}/face-templates` | 200 `[{id, model_id, dim, quality_score, created_at, created_by_user_id}]` (never vectors) | 404 |
| POST | `/employees/{id}/face-templates` (multipart `file`, JPEG/PNG ≤ 8 MB) | 201 template | 404, 409 `CONSENT_REQUIRED` / `NOT_FACE_METHOD` / `EMPLOYEE_TERMINATED` / `TEMPLATE_LIMIT` / `FACE_MATCHES_OTHER_EMPLOYEE`, 413, 422 `INVALID_FILE` / `FACE_NOT_ACCEPTED` (`errors[0].code` = no_face, multiple_faces, low_quality; `message` = quality reasons), 503 `RECOGNITION_SERVICE_UNAVAILABLE` |
| POST | `/employees/{id}/face-templates/from-camera` `{camera_id, pose?: frontal|turned, yaw_sign?: -1|1}` | 201 | as above + 502/503 camera errors. With `pose`, the enrolled frame must match (frontal |yaw| ≤ 10°, turned |yaw| ≥ 12°, side = sign of yaw) else 422 `POSE_MISMATCH` (`errors[0].code`: not_frontal, not_turned, wrong_side, pose_unknown). Response adds `yaw_deg` (not stored). |
| GET | `/employees/{id}/face-templates/scan-stream?camera_id=` | 200 | `multipart/x-mixed-replace; boundary=frame` (JPEG parts with Content-Length), ~15 fps, 640 px, ends after 120 s; `biometrics:write`; 409 `CAMERA_NOT_LIVE` if the camera is not monitored, 429 `PREVIEW_BUSY`; nothing stored; audited like scan-frame |
| POST | `/employees/{id}/face-templates/scan-frame` `{camera_id, preview?: bool = true}` | 200 | Guided enrolment preview (`biometrics:write`): `{preview: data:image/jpeg;base64 (≤480 px, re-encoded), faces, face: {box [x,y,w,h] fractions, yaw_deg, roll_deg, passed, reasons, face_width_px} | null}` for the largest face. No identification, no embeddings, nothing stored; audited `ENROLMENT_PREVIEW_VIEWED` once per user+camera per 5 min |
| DELETE | `/employees/{id}/face-templates/{template_id}` | 204 | 404 |

All audited (`BIOMETRIC_TEMPLATE_ENROLLED` / `_DELETED`, with consent id and source, never vectors). The image is discarded after extraction.

`GET /recognition/live?since=&camera_id=` (`biometrics:read`): recent in-memory observations `{observations:[{seq, camera_id, decision, employee:{id, employee_code, full_name}|null, score, face_width_px, quality_reasons, count, first_seen, last_seen}], next, cameras:[{camera_id, name, recognition, last_error, frames_sent, latency_ms}]}`. `employee` is set **only** for MATCHED. Not stored; no attendance effect.

Settings: `PATCH /api/v1/settings/{key}` `{value}` (`settings:write`), writable keys only: `recognition.attendance_enabled` (bool), `attendance.debounce_seconds` (10–3600), `attendance.min_presence_minutes` (0–720); 404 for other keys, 422 invalid value; audited `SETTING_UPDATED`.

Recognition → attendance (camera token): `POST /internal/v1/recognition-events` `{event_uid, camera_id, employee_id, event_time, decision: "MATCHED", model_id, similarity?, second_similarity?, quality_score?}` → `{recorded:false, reason: attendance_disabled|camera_not_enabled}` or `{recorded:true, created, counted, direction, ignore_reason, work_date}`; 422 `EVENT_REJECTED` (clock/unknown employee).

Internal: `GET /internal/v1/gallery?model_id=` (Bearer `RECOGNITION_GALLERY_TOKEN`) → encrypted templates of eligible employees. Recognition service: `POST /internal/v1/templates/extract`. Camera service: `GET /internal/v1/observations`.

### 2.6 Cameras ✅ Implemented (Phase 10, except rows marked *planned*)

Differences from the original rows below:
- **providers** `RTSP` / `USB` / `TEST`;
- credentials optional (both or neither, RTSP only) and **write-only**: the response has `has_credentials` and `stream_url_redacted` instead;
- `site_id` and `status`-by-site filters are not implemented (no sites table);
- **snapshot requires `cameras:write`** (it shows people);
- `/health` is folded into the camera object (`monitoring.runtime`).

| Method | Path | Permission | Success | Errors | Description |
|--------|------|------------|---------|--------|-------------|
| GET | `/cameras` | `cameras:read` | 200 | 422 | `q`, `provider`, `direction`, `status`, `is_enabled`, `page`, `page_size` ≤ 200 |
| POST | `/cameras` | `cameras:write` | 201 | 409 `CAMERA_EXISTS` / `QUALITY_NOT_PASSED`, 422 | Created disabled (`DRAFT`), `quality_status` UNTESTED, recognition off. **Audited.** |
| GET | `/cameras/{id}` | `cameras:read` | 200 | 404 | Never returns credentials |
| PATCH | `/cameras/{id}` | `cameras:write` | 200 | 404, 409, 422 | Partial update. `username`+`password` replace credentials; `clear_credentials: true` removes them. Changing provider/address/path/device/credentials/settings resets quality to UNTESTED and turns recognition off. `recognition_enabled: true` needs PASSED (409 `QUALITY_NOT_PASSED`). Bumps `config_version`. **Audited.** |
| DELETE | `/cameras/{id}` | `cameras:write` | 204 | 404 | Soft delete; the camera service stops the worker |
| POST | `/cameras/{id}/enable` · `/disable` | `cameras:write` | 200 | 404 | Start/stop monitoring. **Audited.** |
| POST | `/cameras/{id}/test` | `cameras:write` | 200 | 404, 503 `CAMERA_SERVICE_UNAVAILABLE` | `{ok, error_code?, error?, width, height, reported_fps, open_ms, backend}`. A connection check, not a quality check. **Audited.** |
| GET | `/cameras/{id}/snapshot` | `cameras:write` | 200 `image/jpeg`, `no-store` | 404, 502 `CAMERA_UNREACHABLE`, 503 | Current frame, not stored. **Audited.** |
| POST | `/cameras/{id}/quality-test` | `cameras:write` | 200 (camera) | 404, 502 `BAD_QUALITY_REPORT`, 503 | `{duration_seconds: 2–20}`; stores `quality_status`, `quality_report`. **Audited.** |

Error codes in `error_code`: `UNREACHABLE`, `OPEN_FAILED`, `DEVICE_NOT_FOUND`, `STREAM_LOST`, `CONFIG_INVALID`, `MEDIA_NOT_FOUND`, `PROVIDER_UNAVAILABLE`, `BUSY`.

Original design (rows not listed above are **planned**: discover, probe, live token, ROI editor):

| Method | Path | Permission | Description |
|--------|------|------------|-------------|
| GET | `/cameras` | `cameras:read` | Filters: `site_id`, `status`, `direction` |
| POST | `/cameras` | `cameras:write` | Create (status `DRAFT`). Credentials write-only. |
| GET | `/cameras/{id}` | `cameras:read` | Never returns credentials |
| PATCH | `/cameras/{id}` | `cameras:write` | Bumps `config_version`, pushes config |
| DELETE | `/cameras/{id}` | `cameras:write` | Soft delete, stops worker |
| POST | `/cameras/{id}/test` | `cameras:write` | Connect, read 1 frame → `{ok, codec, resolution, fps, error?}` |
| POST | `/cameras/{id}/enable` · `/disable` | `cameras:write` | |
| GET | `/cameras/{id}/snapshot` | `cameras:read` | Current frame JPEG (for ROI editor), audited |
| GET | `/cameras/{id}/health` | `cameras:read` | fps, decode errors, faces/min, last frame, uptime (24 h series) |
| POST | `/cameras/discover` | `cameras:write` | ONVIF WS-Discovery on configured subnet → device list |
| POST | `/cameras/probe` | `cameras:write` | `{host, port, username, password}` → ONVIF profiles + stream URIs |
| POST | `/live/token` | `live:view` | `{camera_id}` → `{webrtc_url, token, expires_in: 60}` for go2rtc via Nginx |

Camera create body:

```json
{
  "site_id": "...",
  "name": "Main entrance IN",
  "vendor": "HIKVISION",
  "host": "10.20.0.11",
  "rtsp_port": 554,
  "main_stream_path": "/Streams/Channels/101",
  "sub_stream_path": "/Streams/Channels/102",
  "username": "attendance",
  "password": "********",
  "direction": "IN",
  "recognition_enabled": true,
  "roi": [[0.2, 0.1], [0.8, 0.1], [0.8, 0.95], [0.2, 0.95]],
  "settings": { "fps": 5, "min_face_px": 80, "liveness": false }
}
```

### 2.7 Recognition events

| Method | Path | Permission | Description |
|--------|------|------------|-------------|
| GET | `/recognition-events` | `recognition:read` | Filters: `camera_id`, `employee_id`, `decision`, `from`, `to`, `review_status` |
| GET | `/recognition-events/{id}` | `recognition:read` | |
| GET | `/recognition-events/{id}/snapshot` | `recognition:read` | Face crop JPEG, audited. `410` if purged. |
| POST | `/recognition-events/{id}/review` | `recognition:review` | `{action: "DISMISS" \| "ASSIGN", employee_id?}`. ASSIGN creates a MANUAL punch through the corrections flow and **does not** enroll the face. |

### 2.8 Attendance ✅ Implemented (Phase 6)

| Method | Path | Permission | Success | Errors | Description |
|--------|------|------------|---------|--------|-------------|
| POST | `/attendance/events` | `attendance:write` | 201, or **200 on idempotent replay** | 422 | Ingest a recognition/terminal event. Cameras are not connected yet, so this is used to simulate them. The recognition service will call it later with a service token. |
| GET | `/attendance/events` | `attendance:read` | 200 | 422 | Event history: `employee_id`, `department_id` (subtree), `camera_id`, `work_date_from`, `work_date_to`, `counted`, `include_voided` (default true), `source`, `page`, `page_size` ≤ 200. Newest first. |
| POST | `/attendance/corrections` | `attendance:write` | 200 | 404, 409 `INVALID_SEQUENCE`, 422 | Atomic batch of `add` / `void` operations for one employee's work date. **Audited.** |
| GET | `/attendance/days` | `attendance:read` | 200 | 422 | Daily results, **computed live**: `date_from`, `date_to` (≤ 31 days), `employee_id`, `department_id`, `include_subdepartments`, `status`, `page`, `page_size` ≤ 500 |
| GET | `/attendance/days/{employee_id}/{work_date}` | `attendance:read` | 200 | 404 | One day plus **all** its events (counted, ignored, voided) |
| GET | `/attendance/board` | `attendance:read` | 200 | 422 | `?date=&department_id=&status=`: totals per status + `IN_PROGRESS` + `EMPLOYEES`, and the list |
| POST | `/attendance/recompute` | `attendance:write` | 200 | 422 | Stores snapshots in `attendance_daily` for a range (e.g. before payroll). **Audited.** |

**Event ingestion body:**
```jsonc
{
  "event_uid": "6f1c…",                     // producer idempotency key
  "camera_id": "…",
  "event_time": "2026-11-02T08:55:00+05:00",   // MUST carry an offset
  "decision": "MATCHED",                       // MATCHED | UNKNOWN | AMBIGUOUS | SPOOF_SUSPECTED
  "employee_id": "…",                          // required for MATCHED, forbidden otherwise
  "model_id": "sface@2021dec",
  "similarity": 0.68, "second_similarity": 0.31, "liveness_score": null, "quality_score": 0.9,
  "track_id": "cam3-000184"
}
```
Response: `{event: Event, day: Day | null}`. `day` is returned when the event counted.

**Event object:** `id`, `event_uid`, `event_time`, `work_date`, `source` (CAMERA/MANUAL/IMPORT), `employee_id`, `camera_id`, `decision`, `direction`, `counts_for_attendance`, `ignore_reason`, `similarity`, `is_voided`, `void_reason`, `voided_at`, `voided_by_user_id`, `correction_id`, `created_by_user_id`, `note`, `created_at` (server receive time).

**How a MATCHED camera event is classified** (stored either way. Only *counted* events affect attendance):

| Check (in order) | Result if it fails |
|------------------|-------------------|
| `event_uid` already stored | 200, same event returned, nothing changes (**duplicate prevention**) |
| `event_time` has an offset, ≤ now + 120 s, ≥ now − 168 h | 422 `naive_timestamp` / `in_future` / `too_old` (**reliable timestamps**) |
| Employee is employed on the work date | `OUTSIDE_EMPLOYMENT` |
| Employee is ACTIVE | `EMPLOYEE_INACTIVE` |
| Employee uses FACE attendance | `NOT_BIOMETRIC` |
| Camera direction is not NONE and recognition is enabled | `MONITOR_ONLY` |
| No other sighting of this employee at this camera within ±60 s | `DEBOUNCED` (**duplicate prevention**) |
| BOTH camera, already IN: at least 30 min since the IN | `REPEATED_DETECTION` (**a repeated face detection is never a departure**) |
| IN camera: not already IN | `DUPLICATE_IN` |
| OUT camera: currently IN | `OUT_WITHOUT_IN` |
| A late-arriving event must fit between its neighbours | `OUT_OF_SEQUENCE` (**sequence validation**) |

Unmatched decisions are stored with `ignore_reason = UNMATCHED` and no employee.

**Work date**: punches belong to the shift whose window `[start − early_window, end + late_window]` contains them, so a 22:00–06:00 shift's 06:02 OUT belongs to the previous day. Without a schedule, the local calendar date in `attendance.default_timezone` is used.

**Corrections body:**
```jsonc
{
  "employee_id": "…",
  "work_date": "2026-11-02",
  "reason": "Kamera ishlamay qoldi",           // 3–500 chars, required
  "operations": [                              // 1–20, applied atomically
    {"op": "void", "event_id": 1234},
    {"op": "add", "direction": "IN", "event_time": "2026-11-02T09:00:00+05:00", "note": "…"}
  ]
}
```
Rules:
- `void` only targets counted, non-voided events of that employee and work date (else 422 `event_not_found`).
- `add` times must belong to `work_date` under the employee's schedule (422 `wrong_work_date`) and not be in the future.
- After applying everything, the day must be a valid sequence: strictly increasing times, starting with IN, alternating. Otherwise **nothing is applied** (409 `INVALID_SEQUENCE`).
- Voided events stay in history (`is_voided`, `void_reason`, `voided_at`, `voided_by_user_id`). Added events have `source = MANUAL` and share a `correction_id`.
- An `ATTENDANCE_CORRECTED` audit record is written with the employee, work date, reason, added and voided events, and the resulting status.
- Employees with `attendance_method = MANUAL` get all their attendance through corrections.

Response: `{correction_id, added: [Event], voided: [Event], day: Day}`.

**Day object:**
```jsonc
{
  "employee": {"id": "…", "employee_code": "E-1", "full_name": "Karimov Aziz", "department_id": "…"},
  "work_date": "2026-11-02",
  "status": "LATE",              // PRESENT | LATE | INCOMPLETE | ABSENT | WEEKEND | NOT_SCHEDULED | PENDING
  "in_progress": false,          // IN without OUT while the day is still open
  "schedule": {"id": "…", "name": "Ofis 9-18"},
  "scheduled_start": "2026-11-02T04:00:00Z", "scheduled_end": "2026-11-02T13:00:00Z",
  "scheduled_minutes": 480,
  "arrival": "2026-11-02T04:20:00Z",   // first IN
  "departure": "2026-11-02T12:00:00Z", // last OUT
  "worked_minutes": 400, "late_minutes": 20, "early_leave_minutes": 60, "overtime_minutes": 0,
  "has_manual_events": false
}
```

**Calculation rules** (engine `1.0`, [app/attendance/engine.py](apps/api/app/attendance/engine.py)):

| Value | Rule |
|-------|------|
| Arrival / departure | First IN / last OUT of the work date |
| Worked | Sum of IN→OUT pairs. If there is exactly one pair of ≥ 6 h, the schedule's unpaid break is deducted. With several pairs, the gaps are the breaks. |
| Late | Working day, first IN later than start + `grace_in` → minutes **from shift start** |
| Early leave | Working day, day not open, last OUT earlier than end − `grace_out` → minutes **until shift end** |
| Overtime | Only if `overtime_enabled`: worked − scheduled (all worked time on non-working days) |
| ABSENT | Working day, no counted events, and the day's window has ended |
| PENDING | Working day not over yet and no arrival: never stored, never counted as absence |
| INCOMPLETE | Last event is IN and the window has ended (forgot to punch out) |
| WEEKEND | Not a working day of the schedule (worked time still counted) |
| NOT_SCHEDULED | No schedule resolved for the employee |
| Schedule used | Employee's schedule, else the nearest department (or ancestor) default schedule; inactive schedules are skipped |

All arithmetic is in UTC, so DST transitions are handled correctly.

### 2.9 Work schedules ✅ Implemented (Phase 6)

| Method | Path | Permission | Success | Errors | Description |
|--------|------|------------|---------|--------|-------------|
| GET | `/work-schedules` | `attendance:read` | 200 | | List |
| GET | `/work-schedules/{id}` | `attendance:read` | 200 | 404 | |
| POST | `/work-schedules` | `attendance:write` | 201 | 409 `SCHEDULE_EXISTS`, 422 | Create |
| PATCH | `/work-schedules/{id}` | `attendance:write` | 200 | 404, 409, 422 | Partial update, incl. `is_active` |
| DELETE | `/work-schedules/{id}` | `attendance:write` | 204 | 404, 409 `SCHEDULE_IN_USE` | Only when not assigned to employees or departments |

Fields: `name`, `timezone` (IANA, default `Asia/Tashkent`), `start_time`, `end_time` (`end ≤ start` ⇒ night shift, `crosses_midnight: true`), `working_days` (ISO 1=Mon…7=Sun, unique), `break_minutes` (< shift length), `grace_in_minutes`, `grace_out_minutes` (0–240), `early_window_minutes`, `late_window_minutes` (0–720), `overtime_enabled`, `is_active`. All changes are audited (`WORK_SCHEDULE_*`).

Assign a schedule to an employee (`PATCH /employees/{id}` `work_schedule_id`) or as a department default (`PATCH /departments/{id}` `work_schedule_id`), which applies to the whole subtree. This supports multiple departments with different hours.

**Known limitations** (planned): holidays, leave and shift rotations are not modelled yet. Schedules have no history, so live results for past days use the current schedule. Use `/attendance/recompute` to freeze snapshots.

### 2.10 Reports

| Method | Path | Permission | Description |
|--------|------|------------|-------------|
| GET | `/reports/daily` | `reports:read` (scoped) | `?date=&department_id=` (JSON preview) |
| GET | `/reports/timesheet` | `reports:read` (scoped) | `?month=2026-11&department_id=` (JSON grid) |
| GET | `/reports/lateness` | `reports:read` (scoped) | `?from=&to=&department_id=&min_minutes=` |
| GET | `/reports/absence` | `reports:read` (scoped) | |
| GET | `/reports/overtime` | `reports:read` (scoped) | |
| POST | `/reports/exports` | `reports:export` (scoped) | `{report_type, params, format}` → `202 {export_id}` |
| GET | `/reports/exports/{id}` | owner | Status |
| GET | `/reports/exports/{id}/download` | owner | File (24 h), audited |
| POST | `/employees/{id}/subject-access-export` | `biometrics:enroll` | DSAR ZIP → export job |

### 2.10a Telegram notifications (Phase 17)

| Method | Path | Permission | Description |
|--------|------|------------|-------------|
| GET | `/notifications/telegram` | `notifications:read` | `{configured, bot_username, bot_error, enabled, daily_summary_time, review_interval_minutes, camera_offline_minutes, worker_running, worker_last_run, worker_error, recipients[{id, title, username, is_active, subscriptions[], last_error, created_at}], recent[{kind, created_at, recipients, delivered, summary}]}`. Never returns the token |
| POST | `/notifications/telegram/link` `{title}` | `notifications:write` | `{code, link: https://t.me/<bot>?start=<code>, bot_username, expires_at}`; one-time, 15 min; 409 `TELEGRAM_NOT_CONFIGURED` without a token |
| PATCH | `/notifications/telegram/recipients/{id}` `{title?, is_active?, subscriptions?}` | `notifications:write` | subscriptions ⊆ daily, late, reviews, system; audited |
| DELETE | `/notifications/telegram/recipients/{id}` | `notifications:write` | 204, audited |
| POST | `/notifications/telegram/test` `{recipient_id?}` | `notifications:write` | `{recipients, delivered, errors[]}` |
| GET | `/notifications/telegram/daily-preview` | `notifications:read` | `{text}` today's summary (null on a day off), not sent |
| GET | `/notifications/telegram/director-preview?period=day\|week\|month` | `branches:manage` | `{text}` the director's report (every branch ranked, missing people), not sent. Kind `director` can be subscribed by centre chats only (branch chat → 422 `centre_only`) |

| GET | `/employees/{id}/telegram` | `employees:read` | `{configured, enabled, bot_username, linked, username, is_active, subscriptions (self_punch|self_late)[], last_error, linked_at}` |
| POST | `/employees/{id}/telegram/link` | `employees:write` | one-time link for the employee (same shape as `/notifications/telegram/link`) |
| PATCH | `/employees/{id}/telegram` `{is_active?, subscriptions?: (self_punch|self_late)[]}` | `employees:write` | admin kinds are rejected (422) |
| DELETE | `/employees/{id}/telegram` | `employees:write` | 204; unlinks (the chat stays if it also receives admin messages) |

Settings (PATCH `/settings/{key}`): `notify.telegram_enabled` (bool, default false), `notify.daily_summary_time` ("HH:MM"), `notify.review_interval_minutes` (5–1440), `notify.camera_offline_minutes` (1–1440).

### 2.11 Audit, settings, system

| Method | Path | Permission | Description |
|--------|------|------------|-------------|
| GET | `/audit-logs` | `audit:read` | `?actor_user_id=&action=&entity_type=&entity_id=&from=&to=` |
| GET | `/settings` | `settings:read` | All typed settings |
| PATCH | `/settings` | `settings:write` | Partial update, validated per key, audited |
| GET | `/system/status` | `branches:manage` | **Implemented (Phase 16).** `{status: ok|warning|error, checked_at, version, environment, api_uptime_seconds, checks[{name, title, status: ok|warning|error|not_configured, detail, latency_ms, info}], supervisor{running, mode, started_at, updated_at, services[{name,title,port,state,started_at,restarts,last_exit_code,last_exit_at,last_error,log}]}|null}`. Checks: api, database (migration), recognition (`/ready`: templates, employees), camera (`/ready`: cameras), backup (newest `.attbak`, warning after 36 h), disk (warning under 5 GB). `supervisor` comes from `logs/supervisor.json` written by `tools/run.mjs` (stale after 30 s → `running: false`). No secrets, URLs or file contents. Replaces the planned `/system/health` (no Valkey or go2rtc in this deployment). |
| GET | `/system/version` | public | `{api, web, recognition, model_ids[]}` |

Typed settings keys (v1):

| Key | Type | Default |
|-----|------|---------|
| `recognition.match_threshold` | float | model-specific (calibrated) |
| `recognition.margin` | float | 0.05 |
| `recognition.liveness_enabled` | bool | false |
| `recognition.store_unknown_snapshots` | bool | true |
| `attendance.debounce_seconds` | int | 60 |
| `attendance.auto_approve_hr_corrections` | bool | true |
| `retention.snapshot_days` | int | 30 |
| `retention.unknown_snapshot_days` | int | 7 |
| `retention.enrollment_image_days` | int \| null | null (keep while enrolled) |
| `retention.terminated_biometric_days` | int | 7 |
| `retention.event_months` | int | 12 |
| `biometric.consent_text_version` | string | `"2026-11-v1"` (since Phase 5) |
| `attendance.min_presence_minutes` | int | 30 (BOTH cameras: shorter = repeated detection) |
| `attendance.max_event_age_hours` | int | 168 |
| `attendance.max_clock_skew_seconds` | int | 120 |
| `attendance.default_timezone` | string | `"Asia/Tashkent"` |
| `security.mfa_required_roles` | string[] | `["SUPER_ADMIN","ADMIN"]` (MFA not implemented yet) |

Session lifetime, token TTL and login limits are **environment** settings (`SESSION_DAYS`, `ACCESS_TOKEN_MINUTES`, `LOGIN_*`), not database settings, so they cannot be changed through the API.

---

## 3. WebSocket API

`wss://<host>/api/v1/ws`

1. Client connects, then sends within 5 s: `{"type":"auth","token":"<access_jwt>"}`.
2. Server replies `{"type":"auth_ok"}` or closes with `4401`.
3. Client subscribes: `{"type":"subscribe","channels":["attendance","recognition","cameras"],"site_id":"..."}`. Channels outside the caller's permissions are refused.
4. Before the token expires, the client sends `{"type":"auth","token":"<new>"}`. Otherwise the server closes with `4401`.
5. Heartbeat: server `ping` every 25 s, client must `pong`.

Server messages:

```json
{ "type": "attendance.punch", "data": { "employee_id": "...", "employee_name": "Karimov Aziz",
  "department_id": "...", "direction": "IN", "punch_time": "...", "camera_id": "...", "status_after": "LATE" } }

{ "type": "recognition.event", "data": { "event_id": 88123, "camera_id": "...", "decision": "UNKNOWN",
  "event_time": "...", "has_snapshot": true } }

{ "type": "camera.status", "data": { "camera_id": "...", "status": "OFFLINE", "since": "..." } }
```

Data scoping applies. Managers receive `attendance.punch` only for their departments. `recognition.event` goes only to roles with `recognition:read`.

---

## 4. Internal service APIs (Docker internal network only, not routed by Nginx)

### 4.1 Core API → served for internal services

**Implemented for the camera service (Phase 10)**, `app/api/internal.py`. Auth: `Authorization: Bearer <CAMERA_SERVICE_TOKEN>` (≥ 24 chars, constant-time; unset → every call 401). Not routed by the production reverse proxy.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/internal/v1/cameras` | Enabled cameras as stream specs **with decrypted credentials** (`no-store`, never logged): `{cameras:[{camera_id, config_version, name, provider, direction, sample_fps, min_face_px, rtsp?:{host,port,path,transport,username,password}, usb?:{device}, test?:{…}}]}` |
| POST | `/internal/v1/cameras/status` | `{reports:[{camera_id, config_version, state, last_frame_at, last_error, error_code, measured_fps, frames_total, read_failures, reconnects, width, height, uptime_seconds}]}` → 204. Reports for disabled cameras or an older `config_version` are ignored. |

Camera service API (called by the Core API, same token): `POST /internal/v1/probe`, `/snapshot`, `/quality-test` with `{source: <stream spec>}`, `GET /internal/v1/status`, `GET /health`, `/ready`.

Original design for the recognition service (planned; `service_clients` scopes not implemented):

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| GET | `/internal/v1/gallery?since_version=0&model_id=...` | `gallery:read` | Delta: `{version, upserts:[{template_id, employee_id, embedding_enc(base64), dim}], deletes:[template_id]}` |
| GET | `/internal/v1/cameras` | `cameras:read` | Enabled cameras with **decrypted** RTSP URLs (TLS not needed on internal net, but the response is never logged) |
| GET | `/internal/v1/settings/recognition` | `cameras:read` | Thresholds, liveness flag, sampling defaults |

The gallery key (DEK for templates) is delivered to the recognition service as a Docker secret file at start-up, **not** over this API.

### 4.2 Recognition service internal API (called by Core API)

Auth: `Authorization: Bearer <core_to_rec_token>`.

**Implemented in Phase 9** (`apps/recognition/recognition/api.py`). The Core API does NOT call it yet.

| Method | Path | Auth | Response |
|--------|------|------|----------|
| GET | `/health` | — | `{status}` |
| GET | `/ready` | — | `{status, detector, embedder, gallery_templates, gallery_employees, attendance_integration: "disabled (evaluation phase)"}` |
| GET | `/stats` · `/metrics` | token | counts, decisions, quality rejections, latency p50/p95/p99 · Prometheus text |
| POST | `/internal/v1/quality` | token | multipart `file` → `{faces:[{box, detection_score, quality:{passed, reasons[], face_width_px, interocular_px, blur_variance, brightness, yaw_deg, roll_deg}, decision:null, employee_id:null, timings_ms}], image_size, timings_ms}` |
| POST | `/internal/v1/recognize` | token | Same shape, plus `decision` (`MATCHED`/`AMBIGUOUS`/`UNKNOWN`/`LOW_QUALITY`), `top1_score` and `top2_score`. `employee_id` is set **only when MATCHED**. |

Errors: 401 (missing or invalid token), 413 (larger than `MAX_UPLOAD_BYTES`), 422 (not a decodable image). Embeddings are never returned.
The rows below are still **planned** (enrolment and camera phases):

| Method | Path | Description |
|--------|------|-------------|
| POST | `/internal/v1/templates:extract` | multipart `images[]` → per image `{status, face_count, quality, embedding(base64 float32), dim, model_id, reason?}` |
| POST | `/internal/v1/quality` | Single image → `{face_count, quality, issues:["too_small","blurry","pose","dark"]}` |
| POST | `/internal/v1/search` | `{embedding}` → `{top: [{employee_id, similarity}]}` (duplicate check at enrollment) |
| GET | `/health` · `/ready` · `/metrics` | Liveness / readiness / Prometheus |
| GET | `/internal/v1/cameras/status` | Per-camera worker state |

### 4.3 Message contracts (Valkey)

**Stream `recognition.events`** (producer: recognition; consumer group `attendance-workers`):

```json
{
  "schema": "recognition.event.v1",
  "event_uid": "0193a3f2-...",
  "event_time": "2026-11-07T04:15:02.481Z",
  "camera_id": "...",
  "camera_config_version": 3,
  "track_id": "cam3-000184",
  "decision": "MATCHED",
  "employee_id": "...",
  "similarity": 0.682,
  "second_similarity": 0.311,
  "liveness_score": null,
  "quality_score": 0.87,
  "face_bbox": { "x": 0.41, "y": 0.22, "w": 0.09, "h": 0.14 },
  "snapshot_b64_enc": "...",
  "model_id": "sface@2021dec"
}
```

The snapshot is encrypted by the recognition service with the snapshot DEK before it enters the stream. The Worker writes it to file storage as-is.

**Stream `camera.heartbeats`** (MAXLEN ~10000):
`{camera_id, ts, state, fps_in, fps_processed, decode_errors, faces_per_min, last_frame_at}`

**Pub/Sub channels**

| Channel | Publisher | Payload |
|---------|-----------|---------|
| `gallery.updated` | Core API | `{version}` |
| `camera.config` | Core API | `{camera_id, config_version, action: "upsert" \| "delete"}` |
| `settings.recognition` | Core API | `{changed_keys[]}` |
| `ws.events` | Worker / Core API | WebSocket message envelope (§3) |

All contracts live in `packages/contracts` as versioned Pydantic models. A breaking change creates a new `schema` version, and consumers must accept N and N-1.
