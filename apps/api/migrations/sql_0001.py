"""Raw SQL for revision 0001. Frozen with that revision: never edit after it is applied anywhere;
add a new revision instead (DEVELOPMENT_RULES §4)."""

EXTENSIONS_SQL = [
    "CREATE EXTENSION IF NOT EXISTS citext",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
]

# Generic updated_at maintenance.
FUNCTIONS_SQL = [
    r"""
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$
"""
]

# Tables that carry updated_at.
UPDATED_AT_TABLES = (
    "departments",
    "work_schedules",
    "employees",
    "users",
    "face_embeddings",
    "cameras",
    "attendance_daily",
    "system_settings",
)

# One statement per item (asyncpg cannot run multi-statement strings).
#  * updated_at triggers on every mutable table
#  * audit_logs: append-only. UPDATE/DELETE/TRUNCATE rejected unless the owner disables the
#    trigger (visible in the PostgreSQL log).
#  * face_embeddings: insert/update only under an ACTIVE consent of the same employee.
#    FOR SHARE locks the consent row, so a concurrent revocation either waits and then purges
#    the new row, or wins first and the insert fails.
#  * face_embeddings: any update bumps gallery_version (recognition delta sync).
#  * biometric_consents: identity fields immutable; revoked consent fully immutable; an active
#    consent cannot be deleted; revocation deletes all embeddings created under it (same tx).
TRIGGERS_SQL = [
    """
CREATE TRIGGER trg_departments_updated_at BEFORE UPDATE ON departments FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_work_schedules_updated_at BEFORE UPDATE ON work_schedules FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_employees_updated_at BEFORE UPDATE ON employees FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_face_embeddings_updated_at BEFORE UPDATE ON face_embeddings FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_cameras_updated_at BEFORE UPDATE ON cameras FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_attendance_daily_updated_at BEFORE UPDATE ON attendance_daily FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE TRIGGER trg_system_settings_updated_at BEFORE UPDATE ON system_settings FOR EACH ROW EXECUTE FUNCTION set_updated_at()
""",
    """
CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only (% rejected)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$
""",
    """
CREATE TRIGGER trg_audit_logs_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()
""",
    """
CREATE TRIGGER trg_audit_logs_no_truncate
    BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_append_only()
""",
    """
CREATE OR REPLACE FUNCTION face_embeddings_require_active_consent() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM biometric_consents c
     WHERE c.id = NEW.consent_id
       AND c.employee_id = NEW.employee_id
       AND c.revoked_at IS NULL
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'face embedding requires an active biometric consent for employee %',
            NEW.employee_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""",
    """
CREATE TRIGGER trg_face_embeddings_require_consent
    BEFORE INSERT OR UPDATE OF consent_id, employee_id ON face_embeddings
    FOR EACH ROW EXECUTE FUNCTION face_embeddings_require_active_consent()
""",
    """
CREATE OR REPLACE FUNCTION face_embeddings_bump_gallery_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.gallery_version := nextval('face_embeddings_gallery_version_seq');
    RETURN NEW;
END $$
""",
    """
CREATE TRIGGER trg_face_embeddings_gallery_version
    BEFORE UPDATE ON face_embeddings
    FOR EACH ROW EXECUTE FUNCTION face_embeddings_bump_gallery_version()
""",
    """
CREATE OR REPLACE FUNCTION biometric_consents_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.revoked_at IS NULL THEN
            RAISE EXCEPTION 'active biometric consent cannot be deleted; revoke it first'
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'revoked biometric consent is immutable'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.id <> OLD.id
       OR NEW.employee_id <> OLD.employee_id
       OR NEW.granted_at <> OLD.granted_at
       OR NEW.consent_text_version <> OLD.consent_text_version
       OR NEW.recorded_by_user_id <> OLD.recorded_by_user_id
       OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'biometric consent identity fields are immutable'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$
""",
    """
CREATE TRIGGER trg_biometric_consents_guard
    BEFORE UPDATE OR DELETE ON biometric_consents
    FOR EACH ROW EXECUTE FUNCTION biometric_consents_guard()
""",
    """
CREATE OR REPLACE FUNCTION biometric_consents_purge_on_revoke() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM face_embeddings WHERE consent_id = NEW.id;
    RETURN NULL;
END $$
""",
    """
CREATE TRIGGER trg_biometric_consents_purge_on_revoke
    AFTER UPDATE OF revoked_at ON biometric_consents
    FOR EACH ROW
    WHEN (OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL)
    EXECUTE FUNCTION biometric_consents_purge_on_revoke()
""",
]

DOWNGRADE_SQL = [
    f"DROP FUNCTION IF EXISTS {fn}() CASCADE"
    for fn in (
        "biometric_consents_purge_on_revoke",
        "biometric_consents_guard",
        "face_embeddings_bump_gallery_version",
        "face_embeddings_require_active_consent",
        "audit_logs_append_only",
        "set_updated_at",
    )
]

ENUM_TYPES = (
    "actor_type",
    "attendance_method",
    "attendance_status",
    "camera_direction",
    "camera_status",
    "camera_vendor",
    "employee_status",
    "event_source",
    "punch_direction",
    "recognition_decision",
    "user_role",
)

# Defaults from API_CONTRACTS.md §2.11. No secrets here.
SEED_SETTINGS = [
    {
        "key": "recognition.match_threshold",
        "value": 0.45,
        "description": "Cosine threshold. PLACEHOLDER: must be calibrated on pilot data (Phase 4/11).",
    },
    {
        "key": "recognition.margin",
        "value": 0.05,
        "description": "Required top1 - top2 similarity margin.",
    },
    {
        "key": "recognition.liveness_enabled",
        "value": False,
        "description": "Anti-spoofing check (decision D4).",
    },
    {
        "key": "recognition.store_unknown_snapshots",
        "value": True,
        "description": "Keep face crops of unknown persons for review.",
    },
    {
        "key": "attendance.debounce_seconds",
        "value": 60,
        "description": "Ignore repeat sightings of the same employee at the same camera.",
    },
    {
        "key": "attendance.auto_approve_hr_corrections",
        "value": True,
        "description": "Corrections made by HR_ADMIN are approved immediately.",
    },
    {
        "key": "retention.snapshot_days",
        "value": 30,
        "description": "Days to keep matched face snapshots.",
    },
    {
        "key": "retention.unknown_snapshot_days",
        "value": 7,
        "description": "Days to keep unknown-face snapshots.",
    },
    {
        "key": "retention.terminated_biometric_days",
        "value": 7,
        "description": "Days after termination before biometric data is purged.",
    },
    {
        "key": "retention.event_months",
        "value": 12,
        "description": "Months to keep attendance_events rows.",
    },
    {"key": "security.session_days", "value": 7, "description": "Refresh token lifetime."},
    {
        "key": "security.mfa_required_roles",
        "value": ["SUPER_ADMIN", "HR_ADMIN"],
        "description": "Roles that must use TOTP MFA.",
    },
]
