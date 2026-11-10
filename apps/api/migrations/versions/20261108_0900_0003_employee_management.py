"""employee management: photos, consent documents, duplicate prevention

* employees: photo_key / photo_updated_at (profile photo; never used for recognition).
  employee_code stored upper-case (CHECK). Partial unique indexes on email and phone among
  non-deleted employees.
* biometric_consents: document_content_type / document_uploaded_at (signed form metadata).
* face_embeddings trigger now requires a DOCUMENTED consent (signed form uploaded), so nobody
  can be enrolled without documented consent, even by direct SQL.
* Setting biometric.consent_text_version: the consent wording version that must be recorded.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08 09:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGGER_FN = r"""
CREATE OR REPLACE FUNCTION face_embeddings_require_active_consent() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM biometric_consents c
     WHERE c.id = NEW.consent_id
       AND c.employee_id = NEW.employee_id
       AND c.revoked_at IS NULL
       {extra}
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '{message} %',
            NEW.employee_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
"""


def upgrade() -> None:
    op.add_column('employees', sa.Column('photo_key', sa.String(length=512), nullable=True))
    op.add_column('employees', sa.Column('photo_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE employees SET employee_code = upper(employee_code)")
    op.create_check_constraint(op.f('ck_employees_code_upper'), 'employees',
                               'employee_code = upper(employee_code)')
    op.create_check_constraint(op.f('ck_employees_photo_fields_together'), 'employees',
                               '(photo_key IS NULL) = (photo_updated_at IS NULL)')
    op.create_index('uq_employees_email_active', 'employees', ['email'], unique=True,
                    postgresql_where=sa.text('deleted_at IS NULL AND email IS NOT NULL'))
    op.create_index('uq_employees_phone_active', 'employees', ['phone'], unique=True,
                    postgresql_where=sa.text('deleted_at IS NULL AND phone IS NOT NULL'))

    op.add_column('biometric_consents',
                  sa.Column('document_content_type', sa.String(length=64), nullable=True))
    op.add_column('biometric_consents',
                  sa.Column('document_uploaded_at', sa.DateTime(timezone=True), nullable=True))
    # Consents created before this revision with a document key get a nominal timestamp/type.
    op.execute(
        "UPDATE biometric_consents SET document_uploaded_at = created_at, "
        "document_content_type = 'application/pdf' WHERE signed_document_key IS NOT NULL"
    )
    op.create_check_constraint(
        op.f('ck_biometric_consents_document_fields_together'), 'biometric_consents',
        '(signed_document_key IS NULL) = (document_content_type IS NULL) '
        'AND (signed_document_key IS NULL) = (document_uploaded_at IS NULL)',
    )
    op.create_check_constraint(
        op.f('ck_biometric_consents_document_type_allowed'), 'biometric_consents',
        "document_content_type IS NULL OR document_content_type IN ('application/pdf', 'image/jpeg')",
    )

    op.execute(_TRIGGER_FN.format(
        extra="AND c.signed_document_key IS NOT NULL",
        message="face embedding requires an active, documented biometric consent for employee",
    ))

    op.execute(
        "INSERT INTO system_settings (key, value, description) VALUES "
        "('biometric.consent_text_version', '\"2026-11-v1\"'::jsonb, "
        "'Version of the consent wording employees sign. Consents must reference it.') "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM system_settings WHERE key = 'biometric.consent_text_version'")
    op.execute(_TRIGGER_FN.format(
        extra="",
        message="face embedding requires an active biometric consent for employee",
    ))
    op.drop_constraint(op.f('ck_biometric_consents_document_type_allowed'), 'biometric_consents', type_='check')
    op.drop_constraint(op.f('ck_biometric_consents_document_fields_together'), 'biometric_consents', type_='check')
    op.drop_column('biometric_consents', 'document_uploaded_at')
    op.drop_column('biometric_consents', 'document_content_type')
    op.drop_index('uq_employees_phone_active', table_name='employees',
                  postgresql_where=sa.text('deleted_at IS NULL AND phone IS NOT NULL'))
    op.drop_index('uq_employees_email_active', table_name='employees',
                  postgresql_where=sa.text('deleted_at IS NULL AND email IS NOT NULL'))
    op.drop_constraint(op.f('ck_employees_photo_fields_together'), 'employees', type_='check')
    op.drop_constraint(op.f('ck_employees_code_upper'), 'employees', type_='check')
    op.drop_column('employees', 'photo_updated_at')
    op.drop_column('employees', 'photo_key')
