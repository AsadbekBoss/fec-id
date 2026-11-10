import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { ConfirmDialog } from "@/components/ui/confirm";

describe("ConfirmDialog", () => {
  it("requires a reason when reasonLabel is set and passes it on", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
      <ConfirmDialog open onClose={onClose} title="Bekor qilish" confirmLabel="Tasdiqlash" reasonLabel="Sabab" onConfirm={onConfirm} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Tasdiqlash" }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByText("Kamida 3 ta belgi kiriting.")).toBeTruthy();

    await userEvent.type(screen.getByLabelText(/Sabab/), "  xodim talabi  ");
    await userEvent.click(screen.getByRole("button", { name: "Tasdiqlash" }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith("xodim talabi"));
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps the dialog open and shows server errors", async () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        open
        onClose={onClose}
        title="Oʻchirish"
        confirmLabel="Oʻchirish"
        tone="danger"
        onConfirm={() => Promise.reject(new ApiError(409, "DEPARTMENT_NOT_EMPTY", ""))}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Oʻchirish" }));
    expect(await screen.findByText(/xodimlar yoki ichki boʻlimlar bor/)).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("confirmDisabled blocks submission", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open onClose={() => undefined} title="T" confirmLabel="OK" confirmDisabled onConfirm={onConfirm} />);
    const btn = screen.getByRole("button", { name: "OK" });
    expect(btn).toHaveProperty("disabled", true);
  });
});
