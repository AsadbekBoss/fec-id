/** Response types mirroring API_CONTRACTS.md (implemented sections only). */

export type Page<T> = { items: T[]; page: number; page_size: number; total: number };

export type Role = "SUPER_ADMIN" | "ADMIN" | "VIEWER";

// --- users (§2.2) ---
export type UserAdmin = {
  id: string;
  username: string;
  email: string | null;
  role: Role;
  branch_id: string;
  is_active: boolean;
  locale: string;
  last_login_at: string | null;
  created_at: string;
  failed_login_count: number;
  locked_until: string | null;
  password_changed_at: string;
  updated_at: string;
};

// --- settings (§2.2a) ---
export type Setting = { key: string; value: unknown; description: string | null; updated_at: string };

// --- departments (§2.3) ---
// --- branches (Phase 21) ---
export type Branch = {
  id: string;
  name: string;
  location: string | null;
  is_active: boolean;
  is_main: boolean;
  employee_count: number;
  user_count: number;
  camera_count: number;
};

export type BranchOverview = {
  date: string;
  branches: { branch: Branch; totals: Record<string, number> }[];
  totals: Record<string, number>;
};

export type Department = {
  id: string;
  branch_id: string;
  name: string;
  code: string | null;
  parent_id: string | null;
  head_employee_id: string | null;
  work_schedule_id: string | null;
  employee_count: number;
  created_at: string;
  updated_at: string;
};

// --- employees (§2.4, §2.5) ---
export type EmployeeStatus = "ACTIVE" | "ON_LEAVE" | "SUSPENDED" | "TERMINATED";
export type AttendanceMethod = "FACE" | "MANUAL";
export type BiometricStatus =
  | "NOT_APPLICABLE"
  | "NO_CONSENT"
  | "CONSENT_PENDING_DOCUMENT"
  | "READY_FOR_ENROLLMENT"
  | "ENROLLED"
  | "CONSENT_REVOKED";

export type Employee = {
  id: string;
  branch_id: string;
  employee_code: string;
  last_name: string;
  first_name: string;
  middle_name: string | null;
  full_name: string;
  department: { id: string; name: string };
  work_schedule_id: string | null;
  position_title: string | null;
  phone: string | null;
  email: string | null;
  hire_date: string;
  termination_date: string | null;
  status: EmployeeStatus;
  is_active: boolean;
  attendance_method: AttendanceMethod;
  has_photo: boolean;
  photo_updated_at: string | null;
  biometric: {
    status: BiometricStatus;
    consent_id: string | null;
    consent_granted_at: string | null;
    consent_documented: boolean;
    face_templates: number;
    can_enroll: boolean;
  };
  created_at: string;
  updated_at: string;
};

export type Consent = {
  id: string;
  employee_id: string;
  consent_text_version: string;
  granted_at: string;
  revoked_at: string | null;
  revocation_reason: string | null;
  recorded_by_user_id: string;
  has_document: boolean;
  document_content_type: string | null;
  document_uploaded_at: string | null;
  is_active: boolean;
  created_at: string;
};

// --- work schedules (§2.9) ---
export type WorkSchedule = {
  id: string;
  branch_id: string;
  name: string;
  timezone: string;
  start_time: string;
  end_time: string;
  working_days: number[];
  break_minutes: number;
  grace_in_minutes: number;
  grace_out_minutes: number;
  early_window_minutes: number;
  late_window_minutes: number;
  overtime_enabled: boolean;
  is_active: boolean;
  crosses_midnight: boolean;
  created_at: string;
  updated_at: string;
};

// --- attendance (§2.8) ---
export type DayStatus = "PRESENT" | "LATE" | "INCOMPLETE" | "ABSENT" | "WEEKEND" | "NOT_SCHEDULED" | "PENDING";

export type Day = {
  employee: {
    id: string;
    employee_code: string;
    full_name: string;
    department_id: string;
    has_photo: boolean;
    photo_updated_at: string | null;
  };
  work_date: string;
  status: DayStatus;
  in_progress: boolean;
  schedule: { id: string; name: string } | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  scheduled_minutes: number;
  arrival: string | null;
  departure: string | null;
  worked_minutes: number;
  late_minutes: number;
  early_leave_minutes: number;
  overtime_minutes: number;
  has_manual_events: boolean;
};

export type AttendanceEvent = {
  id: number;
  event_uid: string;
  event_time: string;
  work_date: string | null;
  source: "CAMERA" | "MANUAL" | "IMPORT";
  employee_id: string | null;
  camera_id: string | null;
  decision: "MATCHED" | "UNKNOWN" | "AMBIGUOUS" | "SPOOF_SUSPECTED" | null;
  direction: "IN" | "OUT" | null;
  counts_for_attendance: boolean;
  ignore_reason: string | null;
  similarity: number | null;
  is_voided: boolean;
  void_reason: string | null;
  voided_at: string | null;
  voided_by_user_id: string | null;
  correction_id: string | null;
  created_by_user_id: string | null;
  note: string | null;
  created_at: string;
};

export type DayDetail = Day & { events: AttendanceEvent[] };

export type Board = {
  date: string;
  totals: Record<string, number>;
  items: Day[];
  page: number;
  page_size: number;
  total: number;
};

// --- cameras (API_CONTRACTS §2.6, Phase 10) ---
export type CameraProvider = "RTSP" | "USB" | "TEST";
export type CameraDirection = "IN" | "OUT" | "BOTH" | "NONE";
export type CameraStatus = "DRAFT" | "ACTIVE" | "DEGRADED" | "OFFLINE" | "DISABLED";
export type CameraQualityStatus = "UNTESTED" | "PASSED" | "FAILED" | "INCONCLUSIVE";
export type TestSimulate = "none" | "flaky" | "disconnect" | "offline" | "freeze";

import type { CameraHealth } from "@/lib/camera-health";

export type CameraRuntime = {
  state: "CONNECTING" | "ONLINE" | "DEGRADED" | "OFFLINE" | "STOPPED";
  last_frame_at: string | null;
  last_error: string | null;
  error_code: string | null;
  measured_fps: number | null;
  frames_total: number | null;
  read_failures: number | null;
  reconnects: number | null;
  width: number | null;
  height: number | null;
  uptime_seconds: number | null;
  // automatic health check of the camera service
  codec?: string | null;
  cpu_percent?: number | null;
  failures_5m?: number | null;
  reconnects_1h?: number | null;
  health?: CameraHealth | null;
};

export type QualityReport = {
  verdict: CameraQualityStatus;
  reasons: string[];
  tested_at: string;
  duration_seconds: number;
  stream: {
    frames: number;
    width?: number;
    height?: number;
    measured_fps?: number | null;
    read_failures?: number;
    frozen_ratio?: number;
    brightness_mean?: number;
    sharpness_mean?: number;
  };
  faces: {
    analysed_frames: number;
    frames_with_face: number;
    faces: number;
    passed: number;
    pass_rate: number | null;
    median_face_width_px: number | null;
    min_face_px_target: number;
    rejection_reasons: Record<string, number>;
  } | null;
  recognition_service: string;
  note: string;
  error_code?: string;
  error?: string;
};

export type CameraSettings = {
  sample_fps?: number;
  min_face_px?: number;
  transport?: "tcp" | "udp";
  test?: { source?: "pattern" | "media"; media?: string | null; width?: number; height?: number; fps?: number; simulate?: TestSimulate } | null;
};

export type Camera = {
  id: string;
  branch_id: string;
  name: string;
  location: string | null;
  provider: CameraProvider;
  vendor: "HIKVISION" | "DAHUA" | "GENERIC_ONVIF" | "GENERIC";
  model: string | null;
  host: string | null;
  rtsp_port: number;
  onvif_port: number | null;
  main_stream_path: string | null;
  sub_stream_path: string | null;
  device: string | null;
  has_credentials: boolean;
  stream_url_redacted: string | null;
  direction: CameraDirection;
  recognition_enabled: boolean;
  is_enabled: boolean;
  status: CameraStatus;
  monitoring: { reported_at: string | null; stale: boolean; runtime: CameraRuntime | null } | null;
  last_seen_at: string | null;
  last_error: string | null;
  quality_status: CameraQualityStatus;
  quality_tested_at: string | null;
  quality_report: QualityReport | null;
  roi: unknown[] | null;
  settings: CameraSettings;
  /** Departments this camera counts attendance for (empty = everyone). */
  department_ids: string[];
  config_version: number;
  created_at: string;
  updated_at: string;
};

export type ProbeResult = {
  ok: boolean;
  error_code: string | null;
  error: string | null;
  width: number | null;
  height: number | null;
  reported_fps: number | null;
  open_ms: number | null;
  backend: string | null;
};

// --- face enrolment and live recognition (Phase 11) ---
export type FaceTemplate = {
  id: string;
  model_id: string;
  dim: number;
  quality_score: number;
  created_at: string;
  created_by_user_id: string;
};

export type LiveDecision = "MATCHED" | "UNKNOWN" | "AMBIGUOUS" | "LOW_QUALITY";

export type LiveObservation = {
  seq: number;
  camera_id: string;
  decision: LiveDecision;
  employee: { id: string; employee_code: string; full_name: string } | null;
  score: number | null;
  face_width_px: number | null;
  quality_reasons: string[];
  count: number;
  /** Outcome of reporting to attendance: IN / OUT / ignore reason / why not sent; null = not reported. */
  attendance: string | null;
  first_seen: string;
  last_seen: string;
};

export type LiveResponse = {
  observations: LiveObservation[];
  next: number;
  cameras: { camera_id: string; name: string; recognition: string; last_error: string | null; frames_sent: number; latency_ms: number | null }[];
};

// --- review queue (Phase 15) ---
export type ReviewItem = {
  id: number;
  event_time: string;
  camera_id: string | null;
  camera_name: string | null;
  camera_direction: CameraDirection | null;
  decision: "AMBIGUOUS" | "UNKNOWN";
  similarity: number | null;
  has_snapshot: boolean;
  review_status: "PENDING" | "ASSIGNED" | "DISMISSED";
  reviewed_at: string | null;
  reviewed_by_user_id: string | null;
  employee: { id: string; employee_code: string; full_name: string } | null;
  punch_event_id: number | null;
};
