"""Biometric consent and face embeddings (ARCHITECTURE §9).

Protections enforced *in the database* (see migration triggers), not only in application code:
* An embedding can only be inserted for an employee with an ACTIVE consent, and the consent must
  belong to the same employee (composite FK).
* Revoking a consent deletes every embedding created under it, in the same transaction.
* A revoked consent is immutable. Consent identity fields can never change.
* Embeddings are stored only as AES-256-GCM ciphertext. A CHECK verifies the exact ciphertext
  length for the declared dimension, so plaintext vectors cannot be stored by mistake.
* No face images are stored in the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Sequence,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core import crypto
from app.db.base import Base, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.organization import Employee, User

GALLERY_VERSION_SEQ = Sequence("face_embeddings_gallery_version_seq", metadata=Base.metadata)


class BiometricConsent(UUIDPrimaryKey, Base):
    __tablename__ = "biometric_consents"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    consent_text_version: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    # Scanned/signed consent form (encrypted file). A consent is "documented" only when this is
    # set, and face embeddings require a documented consent (DB trigger, revision 0003).
    signed_document_key: Mapped[str | None] = mapped_column(String(512))
    document_content_type: Mapped[str | None] = mapped_column(String(64))
    document_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    employee: Mapped[Employee] = relationship(back_populates="consents", lazy="raise")
    recorded_by: Mapped[User] = relationship(lazy="raise")
    face_embeddings: Mapped[list[FaceEmbedding]] = relationship(
        back_populates="consent",
        passive_deletes=True,
        lazy="raise",
        overlaps="face_embeddings,employee",
    )

    __table_args__ = (
        # Target for the composite FK from face_embeddings (same-employee guarantee).
        UniqueConstraint("id", "employee_id", name="uq_biometric_consents_id_employee"),
        Index(
            "uq_biometric_consents_one_active",
            "employee_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at", name="revoked_after_granted"
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revocation_reason IS NOT NULL", name="revocation_has_reason"
        ),
        CheckConstraint("length(btrim(consent_text_version)) > 0", name="version_not_blank"),
        CheckConstraint(
            "(signed_document_key IS NULL) = (document_content_type IS NULL) "
            "AND (signed_document_key IS NULL) = (document_uploaded_at IS NULL)",
            name="document_fields_together",
        ),
        CheckConstraint(
            "document_content_type IS NULL OR document_content_type IN "
            "('application/pdf', 'image/jpeg')",
            name="document_type_allowed",
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class FaceEmbedding(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "face_embeddings"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    consent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dim: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    embedding_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quality_score: Mapped[float] = mapped_column(REAL, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    gallery_version: Mapped[int] = mapped_column(
        BigInteger,
        GALLERY_VERSION_SEQ,
        server_default=GALLERY_VERSION_SEQ.next_value(),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    employee: Mapped[Employee] = relationship(
        back_populates="face_embeddings", lazy="raise", overlaps="face_embeddings"
    )
    consent: Mapped[BiometricConsent] = relationship(
        back_populates="face_embeddings", lazy="raise", overlaps="employee,face_embeddings"
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["consent_id", "employee_id"],
            ["biometric_consents.id", "biometric_consents.employee_id"],
            name="fk_face_embeddings_consent_same_employee",
            ondelete="CASCADE",
        ),
        CheckConstraint("dim IN (128, 256, 512, 1024)", name="dim_supported"),
        CheckConstraint("key_id > 0", name="key_id_positive"),
        # Exact AES-GCM layout: 2B key id + 12B nonce + dim*4 bytes + 16B tag.
        CheckConstraint(
            f"octet_length(embedding_enc) = {crypto.HEADER_LEN + crypto.TAG_LEN} + dim * 4",
            name="ciphertext_length",
        ),
        CheckConstraint(
            "get_byte(embedding_enc, 0) * 256 + get_byte(embedding_enc, 1) = key_id",
            name="ciphertext_key_id_matches",
        ),
        CheckConstraint("quality_score BETWEEN 0 AND 1", name="quality_range"),
        CheckConstraint("length(btrim(model_id)) > 0", name="model_id_not_blank"),
    )

    # --- encryption helpers -------------------------------------------------------------
    def _aad(self) -> str:
        return f"face_embeddings:{self.id}:{self.employee_id}:{self.model_id}"

    def set_vector(self, vector: list[float], keyring: crypto.Keyring | None = None) -> None:
        """Encrypts and stores the vector. Requires id, employee_id and model_id to be set."""
        if self.id is None:
            from app.db.ids import uuid7

            self.id = uuid7()
        if self.employee_id is None or not self.model_id:
            raise ValueError("employee_id and model_id must be set before set_vector()")
        blob = crypto.encrypt(
            crypto.pack_vector(vector), crypto.Purpose.BIOMETRIC, self._aad(), keyring
        )
        self.embedding_enc = blob
        self.key_id = crypto.key_id_of(blob)
        self.dim = len(vector)

    def get_vector(self, keyring: crypto.Keyring | None = None) -> list[float]:
        return crypto.unpack_vector(
            crypto.decrypt(self.embedding_enc, crypto.Purpose.BIOMETRIC, self._aad(), keyring)
        )

    def __repr__(self) -> str:  # never expose ciphertext or vectors
        return f"<FaceEmbedding id={self.id} employee_id={self.employee_id} model={self.model_id}>"
