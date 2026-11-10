"use client";

import { useId, useState } from "react";
import { errorMessage } from "@/lib/errors";
import { Button } from "@/components/ui/button";
import { Field, FormError, Textarea } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";

/**
 * Confirmation dialog for consequential actions. With `reasonLabel` set, a reason (3–500 chars)
 * is required and passed to `onConfirm`. Errors stay inside the dialog, so the user can fix or
 * cancel.
 */
export function ConfirmDialog({
  open,
  onClose,
  title,
  description,
  confirmLabel,
  tone = "primary",
  reasonLabel,
  onConfirm,
  children,
  confirmDisabled = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  confirmLabel: string;
  tone?: "primary" | "danger";
  reasonLabel?: string;
  onConfirm: (reason: string) => Promise<void>;
  children?: React.ReactNode;
  /** Keep the confirm button disabled until extra inputs are valid (e.g. a required checkbox). */
  confirmDisabled?: boolean;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const formId = useId(); // unique: several dialogs may coexist on one page

  const reasonError = reasonLabel && touched && reason.trim().length < 3 ? "Kamida 3 ta belgi kiriting." : null;

  const close = () => {
    if (busy) return;
    setReason("");
    setError(null);
    setTouched(false);
    onClose();
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (confirmDisabled || (reasonLabel && reason.trim().length < 3)) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(reason.trim());
      setReason("");
      setTouched(false);
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title={title}
      description={description}
      dismissible={!busy}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={close} disabled={busy}>
            Bekor qilish
          </Button>
          <Button variant={tone} type="submit" form={formId} loading={busy} disabled={confirmDisabled}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="space-y-4">
        <FormError>{error}</FormError>
        {children}
        {reasonLabel ? (
          <Field label={reasonLabel} required error={reasonError}>
            <Textarea value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
          </Field>
        ) : null}
      </form>
    </Modal>
  );
}
