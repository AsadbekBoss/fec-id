# AI Employee Attendance — Project Overview

> Status: **Phase 1 — Architecture proposal, awaiting approval.**
> No application code exists yet. Nothing in Phase 2+ starts until the architecture is approved.

## 1. Purpose

An on-premises system that records employee attendance automatically. IP cameras at entrances and exits feed a face recognition service. It identifies enrolled employees and turns each sighting into a check-in or check-out. HR and managers use a web dashboard to manage employees, schedules, corrections and reports.

## 2. Goals

| # | Goal | Measurable target (proposed) |
|---|------|------------------------------|
| G1 | Hands-free check-in / check-out | ≥ 98% of passes recognized at a correctly installed camera |
| G2 | Low false acceptance | False match rate ≤ 0.1% at the chosen threshold (measured on a pilot dataset) |
| G3 | Near real-time | Camera frame → attendance event visible on dashboard in ≤ 3 s (p95) |
| G4 | Accurate attendance math | Lateness, early leave, overtime and absences computed against schedules, holidays and leave |
| G5 | Lawful biometric processing | Written consent, encryption, retention limits, full audit trail |
| G6 | Operable by non-engineers | Camera setup, employee enrollment and reports done from the UI |

## 3. Non-goals (initial release)

- Physical access control (opening doors and turnstiles). It can be added later through relay/ISAPI integration.
- Payroll calculation. The system exports data for payroll but does not calculate it.
- Mobile app. The web UI is responsive and the employee self-service portal works on phones.
- Multi-tenant SaaS. This is a single organization with multiple sites.
- Emotion, age, gender or behaviour analytics. These are explicitly excluded for privacy reasons.

## 4. Users and roles

| Role | Who | Main capabilities |
|------|-----|-------------------|
| `SUPER_ADMIN` | IT / system owner | System settings, users, cameras, AI thresholds, audit log |
| `HR_ADMIN` | HR department | Employees, enrollment, consent, schedules, holidays, leave, corrections, all reports |
| `MANAGER` | Department heads | View and approve for **their department only**: attendance, leave, corrections |
| `SECURITY_OPERATOR` | Guard post | Live camera wall, live recognition feed, unknown-person alerts (cannot see other personal data) |
| `EMPLOYEE` | Every employee (optional portal) | Own attendance, own leave requests, correction requests, own consent status |

## 5. Core features

1. **Organization:** sites, departments (tree), positions, employees.
2. **Biometric enrollment:** consent capture, then 3–5 face photos (upload or webcam), quality check and template creation.
3. **Camera management:** add or discover (ONVIF) cameras, set direction (IN/OUT), region of interest, health monitoring, live view.
4. **Real-time recognition:** detection, tracking, quality gate, (optional) liveness, embedding, 1:N match, event.
5. **Attendance engine:** punches → daily records against schedules/shifts, grace periods, night shifts, holidays and leave.
6. **Corrections workflow:** manual punch add/void with reason, manager approval and full audit.
7. **Leave management:** leave types, requests, approvals.
8. **Reports:** daily, monthly timesheet, lateness, absence and overtime. Export to XLSX/CSV/PDF.
9. **Live dashboard:** who is in, late today, recent recognitions and unknown faces (WebSocket).
10. **Audit and privacy:** immutable audit log, retention jobs, consent revocation that deletes biometric data.

## 6. Proposed technology stack (summary)

| Layer | Choice |
|-------|--------|
| Frontend | Next.js 16 (App Router) + React 19 + TypeScript + Tailwind CSS 4 (changed from React/Vite/Ant Design in Phase 2) |
| Core API | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2 |
| AI service | Python 3.12, ONNX Runtime, OpenCV, PyAV (FFmpeg), NumPy |
| Database | PostgreSQL 16 |
| Cache / queue | Valkey 8 (BSD-licensed Redis fork) using Streams + Pub/Sub |
| Live video | go2rtc (RTSP → WebRTC for browsers) |
| File storage | Encrypted local volume behind a storage interface (S3-compatible optional) |
| Deployment | Docker Compose on one on-prem Linux server (GPU optional), Nginx (TLS) |

Full details and rationale are in [ARCHITECTURE.md](ARCHITECTURE.md).

## 7. Assumptions (please confirm or correct)

| # | Assumption | Impact if wrong |
|---|-----------|-----------------|
| A1 | Up to **2,000 employees**, **up to 32 cameras**, 1–5 sites | Above ~20k identities or ~64 streams, the gallery and worker design must scale out |
| A2 | Deployed **on-premises** on the company LAN where the cameras are | A cloud deployment needs VPN or edge agents and changes data-localization analysis |
| A3 | Cameras are **RTSP/ONVIF IP cameras** (for example Hikvision or Dahua) able to output **H.264** on at least one stream | H.265-only cameras need server-side transcoding for browser live view |
| A4 | The company deploys in **Uzbekistan**. Timezone Asia/Tashkent. UI languages Uzbek (Latin), Russian, English | Changes legal requirements (see ARCHITECTURE §9) and i18n scope |
| A5 | System is used **commercially** (by a company for its own employees) | Rules out non-commercial-only AI models in production (see ARCHITECTURE §10) |
| A6 | One recognition server; NVIDIA GPU **recommended** but CPU-only must work for ≤ 8 cameras | CPU-only above 8 cameras will not meet the latency target |
| A7 | Separate entry and exit cameras (or one camera per direction) define IN vs OUT | A single bidirectional camera needs line-crossing direction logic (later phase) |

## 8. Open questions for the project owner

1. Exact headcount, number of sites and cameras, and camera models (brand/model list)?
2. Is a GPU server available? If yes, which GPU?
3. AI model path (see ARCHITECTURE §10): (a) buy a commercial licence for a high-accuracy model, (b) use permissively-licensed models with lower accuracy, or (c) use cameras or terminals with built-in face recognition?
4. Is liveness (anti-spoofing against photos and screens) mandatory for v1?
5. Do employees need the self-service portal in v1?
6. Payroll or HR system to export to (1C, Excel template, other)?
7. Do shift and night-shift rules exist today? Please share an example schedule.
8. Data retention policy expectations (for example, recognition snapshots kept 30 days)?

## 9. Documentation map

| File | Contents |
|------|----------|
| [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) | This file: scope, roles, assumptions, open questions |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, responsibilities, AI service, cameras, auth, security, risks, licensing |
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | ERD and table specifications |
| [API_CONTRACTS.md](API_CONTRACTS.md) | REST + WebSocket + internal service API |
| [DEVELOPMENT_PROGRESS.md](DEVELOPMENT_PROGRESS.md) | Roadmap, phase status, decision log |
| [DEVELOPMENT_RULES.md](DEVELOPMENT_RULES.md) | Engineering rules, workflow, Definition of Done |
