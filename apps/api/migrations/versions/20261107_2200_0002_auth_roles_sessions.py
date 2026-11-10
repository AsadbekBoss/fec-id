"""auth: three roles, login sessions, rotating refresh tokens

* user_role becomes (SUPER_ADMIN, ADMIN, VIEWER), as defined by the owner in Phase 4.
  Existing rows map HR_ADMIN -> ADMIN and MANAGER/SECURITY_OPERATOR/EMPLOYEE -> VIEWER.
* The MANAGER/EMPLOYEE -> employee_id CHECK is dropped (those roles no longer exist).
  users.employee_id stays as an optional link.
* New tables auth_sessions and refresh_tokens.
* Settings: mfa_required_roles uses the new role names. security.session_days is removed,
  because session lifetime is security configuration and comes only from the environment
  (SESSION_DAYS).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07 22:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _swap_role_enum(values: tuple[str, ...], mapping_sql: str) -> None:
    op.execute(f"CREATE TYPE user_role_new AS ENUM ({', '.join(repr(v) for v in values)})")
    op.execute(
        f"ALTER TABLE users ALTER COLUMN role TYPE user_role_new USING ({mapping_sql})::user_role_new"
    )
    op.execute("DROP TYPE user_role")
    op.execute("ALTER TYPE user_role_new RENAME TO user_role")


def upgrade() -> None:
    op.drop_constraint(op.f('ck_users_role_requires_employee'), 'users', type_='check')
    _swap_role_enum(
        ('SUPER_ADMIN', 'ADMIN', 'VIEWER'),
        "CASE role::text WHEN 'SUPER_ADMIN' THEN 'SUPER_ADMIN' "
        "WHEN 'HR_ADMIN' THEN 'ADMIN' ELSE 'VIEWER' END",
    )

    op.create_table(
        'auth_sessions',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoke_reason', sa.String(length=32), nullable=True),
        sa.Column('ip', postgresql.INET(), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint('expires_at > created_at', name=op.f('ck_auth_sessions_expires_after_created')),
        sa.CheckConstraint('(revoked_at IS NULL) = (revoke_reason IS NULL)', name=op.f('ck_auth_sessions_revoked_has_reason')),
        sa.CheckConstraint(
            "revoke_reason IS NULL OR revoke_reason IN "
            "('LOGOUT', 'LOGOUT_ALL', 'PASSWORD_CHANGED', 'TOKEN_REUSE', 'ADMIN', 'DEACTIVATED')",
            name=op.f('ck_auth_sessions_revoke_reason_known'),
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions')),
    )
    op.create_index('ix_auth_sessions_expires_at', 'auth_sessions', ['expires_at'], unique=False)
    op.create_index('ix_auth_sessions_user_active', 'auth_sessions', ['user_id'], unique=False,
                    postgresql_where=sa.text('revoked_at IS NULL'))

    op.create_table(
        'refresh_tokens',
        sa.Column('session_id', sa.UUID(), nullable=False),
        sa.Column('token_hash', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint('expires_at > created_at', name=op.f('ck_refresh_tokens_expires_after_created')),
        sa.CheckConstraint('octet_length(token_hash) = 32', name=op.f('ck_refresh_tokens_hash_is_sha256')),
        sa.ForeignKeyConstraint(['session_id'], ['auth_sessions.id'], name=op.f('fk_refresh_tokens_session_id_auth_sessions'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
        sa.UniqueConstraint('token_hash', name=op.f('uq_refresh_tokens_token_hash')),
    )
    op.create_index(op.f('ix_refresh_tokens_session_id'), 'refresh_tokens', ['session_id'], unique=False)

    op.execute(
        "UPDATE system_settings SET value = '[\"SUPER_ADMIN\", \"ADMIN\"]'::jsonb "
        "WHERE key = 'security.mfa_required_roles'"
    )
    op.execute("DELETE FROM system_settings WHERE key = 'security.session_days'")


def downgrade() -> None:
    op.execute(
        "INSERT INTO system_settings (key, value, description) VALUES "
        "('security.session_days', '7'::jsonb, 'Refresh token lifetime.') ON CONFLICT DO NOTHING"
    )
    op.execute(
        "UPDATE system_settings SET value = '[\"SUPER_ADMIN\", \"HR_ADMIN\"]'::jsonb "
        "WHERE key = 'security.mfa_required_roles'"
    )
    op.drop_index(op.f('ix_refresh_tokens_session_id'), table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index('ix_auth_sessions_user_active', table_name='auth_sessions',
                  postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_index('ix_auth_sessions_expires_at', table_name='auth_sessions')
    op.drop_table('auth_sessions')

    _swap_role_enum(
        ('SUPER_ADMIN', 'HR_ADMIN', 'MANAGER', 'SECURITY_OPERATOR', 'EMPLOYEE'),
        "CASE role::text WHEN 'SUPER_ADMIN' THEN 'SUPER_ADMIN' "
        "WHEN 'ADMIN' THEN 'HR_ADMIN' ELSE 'SECURITY_OPERATOR' END",
    )
    op.create_check_constraint(
        op.f('ck_users_role_requires_employee'), 'users',
        "role NOT IN ('MANAGER', 'EMPLOYEE') OR employee_id IS NOT NULL",
    )
