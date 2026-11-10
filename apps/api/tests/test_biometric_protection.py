"""Biometric safeguards enforced by the database and the encryption layer."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.models import BiometricConsent, FaceEmbedding
from tests import factories as f
from tests.conftest import requires_db
from tests.test_constraints import rejects

pytestmark = requires_db


async def _count_embeddings(session: AsyncSession, employee_id: object) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(FaceEmbedding)
            .where(FaceEmbedding.employee_id == employee_id)
        )
    ).scalar_one()


async def test_embedding_is_encrypted_and_roundtrips(session: AsyncSession) -> None:
    emp = await f.employee(session)
    hr = await f.user(session)
    cons = await f.consent(session, emp, hr)
    vec = f.random_vector(512)
    fe = await f.embedding(session, emp, cons, hr, vector=vec)

    raw = (
        await session.execute(
            text("SELECT embedding_enc, dim, key_id FROM face_embeddings WHERE id = :id"),
            {"id": fe.id},
        )
    ).one()
    assert raw.dim == 512 and raw.key_id == 1
    assert crypto.pack_vector(vec) not in raw.embedding_enc  # plaintext is not stored
    assert fe.get_vector() == pytest.approx(vec, abs=1e-6)
    assert "embedding_enc" not in repr(fe) and str(vec[0]) not in repr(fe)


async def test_embedding_requires_consent(session: AsyncSession) -> None:
    emp = await f.employee(session)
    hr = await f.user(session)
    cons = await f.consent(session, emp, hr)
    cons.revoked_at = datetime.now(UTC)
    cons.revocation_reason = "employee request"
    await session.flush()

    async def bad() -> None:
        await f.embedding(session, emp, cons, hr)

    await rejects(session, bad, "requires an active, documented biometric consent")


async def test_consent_must_belong_to_same_employee(session: AsyncSession) -> None:
    hr = await f.user(session)
    alice = await f.employee(session)
    bob = await f.employee(session)
    alice_consent = await f.consent(session, alice, hr)

    async def bad() -> None:
        await f.embedding(session, bob, alice_consent, hr)

    # Trigger fires first (BEFORE INSERT); the composite FK is the second line of defence.
    await rejects(session, bad, "requires an active, documented biometric consent")


async def test_composite_fk_defined_as_second_line_of_defence(session: AsyncSession) -> None:
    """The trigger runs first, so verify the composite FK (consent_id, employee_id) ->
    biometric_consents(id, employee_id) exists in the catalog, in case triggers are ever
    disabled."""
    row = (
        await session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'fk_face_embeddings_consent_same_employee'"
            )
        )
    ).scalar_one()
    assert row == (
        "FOREIGN KEY (consent_id, employee_id) REFERENCES biometric_consents(id, employee_id) "
        "ON DELETE CASCADE"
    )


async def test_only_one_active_consent(session: AsyncSession) -> None:
    emp = await f.employee(session)
    hr = await f.user(session)
    await f.consent(session, emp, hr)

    async def bad() -> None:
        await f.consent(session, emp, hr)

    await rejects(session, bad, "uq_biometric_consents_one_active")


async def test_revocation_purges_embeddings_in_same_transaction(session: AsyncSession) -> None:
    emp = await f.employee(session)
    other = await f.employee(session)
    hr = await f.user(session)
    cons = await f.consent(session, emp, hr)
    other_cons = await f.consent(session, other, hr)
    for _ in range(3):
        await f.embedding(session, emp, cons, hr)
    await f.embedding(session, other, other_cons, hr)
    assert await _count_embeddings(session, emp.id) == 3

    await session.execute(
        update(BiometricConsent)
        .where(BiometricConsent.id == cons.id)
        .values(revoked_at=func.now(), revocation_reason="withdrawn")
    )
    assert await _count_embeddings(session, emp.id) == 0
    assert await _count_embeddings(session, other.id) == 1  # others untouched


async def test_revocation_requires_reason(session: AsyncSession) -> None:
    emp = await f.employee(session)
    hr = await f.user(session)
    cons = await f.consent(session, emp, hr)

    async def bad() -> None:
        await session.execute(
            update(BiometricConsent)
            .where(BiometricConsent.id == cons.id)
            .values(revoked_at=func.now())
        )

    await rejects(session, bad, "ck_biometric_consents_revocation_has_reason")


async def test_consent_immutability(session: AsyncSession) -> None:
    emp = await f.employee(session)
    other = await f.employee(session)
    hr = await f.user(session)
    cons = await f.consent(session, emp, hr)

    async def reassign() -> None:
        await session.execute(
            update(BiometricConsent)
            .where(BiometricConsent.id == cons.id)
            .values(employee_id=other.id)
        )

    await rejects(session, reassign, "identity fields are immutable")

    async def delete_active() -> None:
        await session.execute(
            text("DELETE FROM biometric_consents WHERE id = :id"), {"id": cons.id}
        )

    await rejects(session, delete_active, "cannot be deleted; revoke it first")

    await session.execute(
        update(BiometricConsent)
        .where(BiometricConsent.id == cons.id)
        .values(revoked_at=func.now(), revocation_reason="withdrawn")
    )

    async def unrevoke() -> None:
        await session.execute(
            update(BiometricConsent)
            .where(BiometricConsent.id == cons.id)
            .values(revoked_at=None, revocation_reason=None)
        )

    await rejects(session, unrevoke, "revoked biometric consent is immutable")


async def test_plaintext_vector_rejected_by_length_check(session: AsyncSession) -> None:
    emp = await f.employee(session)
    hr = await f.user(session)
    cons = await f.consent(session, emp, hr)

    async def bad() -> None:
        session.add(
            FaceEmbedding(
                employee_id=emp.id,
                consent_id=cons.id,
                model_id="m@1",
                dim=128,
                key_id=1,
                embedding_enc=crypto.pack_vector(f.random_vector(128)),  # raw float32, no crypto
                quality_score=0.9,
                created_by_user_id=hr.id,
            )
        )

    await rejects(session, bad, "ck_face_embeddings_ciphertext")


async def test_ciphertext_swapped_between_rows_fails_to_decrypt(session: AsyncSession) -> None:
    hr = await f.user(session)
    a = await f.employee(session)
    b = await f.employee(session)
    fa = await f.embedding(session, a, await f.consent(session, a, hr), hr)
    fb = await f.embedding(session, b, await f.consent(session, b, hr), hr)
    fb.embedding_enc = fa.embedding_enc  # attacker copies Alice's template onto Bob's row
    with pytest.raises(crypto.DecryptionError):
        fb.get_vector()


async def test_gallery_version_bumps_on_update(session: AsyncSession) -> None:
    emp = await f.employee(session)
    hr = await f.user(session)
    fe = await f.embedding(session, emp, await f.consent(session, emp, hr), hr)
    await session.refresh(fe)
    v1 = fe.gallery_version
    fe.is_active = False
    await session.flush()
    await session.refresh(fe)
    assert fe.gallery_version > v1


async def test_employee_with_consent_cannot_be_hard_deleted(session: AsyncSession) -> None:
    """Consent records are legal proof: an employee row cannot disappear while one exists.
    (Embeddings themselves cascade with the employee.)"""
    emp = await f.employee(session)
    hr = await f.user(session)
    cons = await f.consent(session, emp, hr)
    await f.embedding(session, emp, cons, hr)

    async def bad() -> None:
        await session.execute(text("DELETE FROM employees WHERE id = :id"), {"id": emp.id})

    await rejects(session, bad, "fk_biometric_consents_employee_id_employees")


async def test_undocumented_consent_cannot_enroll(session: AsyncSession) -> None:
    """Revision 0003: a consent without the signed form attached does not allow enrollment."""
    emp = await f.employee(session)
    hr = await f.user(session)
    cons = await f.consent(session, emp, hr, documented=False)

    async def bad() -> None:
        await f.embedding(session, emp, cons, hr)

    await rejects(session, bad, "requires an active, documented biometric consent")


async def test_document_fields_must_be_set_together(session: AsyncSession) -> None:
    emp = await f.employee(session)
    hr = await f.user(session)
    cons = await f.consent(session, emp, hr, documented=False)

    async def bad() -> None:
        await session.execute(
            update(BiometricConsent)
            .where(BiometricConsent.id == cons.id)
            .values(signed_document_key="consent_documents/x/y.bin")
        )

    await rejects(session, bad, "ck_biometric_consents_document_fields_together")
