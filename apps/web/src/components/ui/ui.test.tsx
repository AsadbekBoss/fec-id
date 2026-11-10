import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Field, Input, PasswordInput } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { DevPlaceholder, ErrorState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";
import { ToastProvider, useToast } from "@/components/ui/toast";

describe("Button", () => {
  it("is disabled and busy while loading", async () => {
    const onClick = vi.fn();
    render(<Button loading onClick={onClick}>Saqlash</Button>);
    const btn = screen.getByRole("button", { name: "Saqlash" });
    expect(btn).toHaveProperty("disabled", true);
    expect(btn.getAttribute("aria-busy")).toBe("true");
    await userEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("defaults to type=button (never submits forms by accident)", () => {
    render(<Button>OK</Button>);
    expect(screen.getByRole("button").getAttribute("type")).toBe("button");
  });
});

describe("Modal", () => {
  it("is an accessible dialog that closes on Escape and backdrop click", async () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Sarlavha" description="Tavsif">
        <input aria-label="ichki" />
      </Modal>,
    );
    const dialog = screen.getByRole("dialog", { name: "Sarlavha" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(screen.getByText("Tavsif").id).toBe(dialog.getAttribute("aria-describedby"));
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByTestId("modal-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("ignores Escape and backdrop when not dismissible", () => {
    const onClose = vi.fn();
    render(<Modal open onClose={onClose} title="Majburiy" dismissible={false} />);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByTestId("modal-backdrop"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("renders nothing when closed", () => {
    render(<Modal open={false} onClose={() => undefined} title="Yopiq" />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("Field", () => {
  it("links label, hint and error to the control", () => {
    render(
      <Field label="Telefon" error="Notoʻgʻri">
        <Input />
      </Field>,
    );
    const input = screen.getByLabelText("Telefon");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    const describedBy = input.getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(describedBy)?.textContent).toContain("Notoʻgʻri");
  });

  it("PasswordInput toggles visibility and stays labelled", async () => {
    render(
      <Field label="Parol">
        <PasswordInput showLabel="Koʻrsatish" hideLabel="Yashirish" />
      </Field>,
    );
    const input = screen.getByLabelText("Parol");
    expect(input.getAttribute("type")).toBe("password");
    await userEvent.click(screen.getByRole("button", { name: "Koʻrsatish" }));
    expect(input.getAttribute("type")).toBe("text");
  });
});

describe("Toasts", () => {
  function Trigger({ tone }: { tone: "success" | "error" }) {
    const toast = useToast();
    return <button onClick={() => (tone === "success" ? toast.success("Saqlandi") : toast.error("Xato"))}>go</button>;
  }

  it("success toasts auto-dismiss, error toasts persist", () => {
    vi.useFakeTimers();
    try {
      render(
        <ToastProvider>
          <Trigger tone="success" />
        </ToastProvider>,
      );
      fireEvent.click(screen.getByText("go"));
      expect(screen.getByRole("status").textContent).toContain("Saqlandi");
      act(() => {
        vi.advanceTimersByTime(5100);
      });
      expect(screen.queryByText("Saqlandi")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("error toasts use role=alert and stay until closed", async () => {
    render(
      <ToastProvider>
        <Trigger tone="error" />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("go"));
    expect(screen.getByRole("alert").textContent).toContain("Xato");
    await userEvent.click(screen.getByRole("button", { name: "Yopish" }));
    expect(screen.queryByText("Xato")).toBeNull();
  });
});

describe("DataTable", () => {
  const columns = [{ key: "n", header: "Nomi", cell: (r: { id: string; n: string }) => r.n }];

  it("shows rows", () => {
    render(<DataTable columns={columns} rows={[{ id: "1", n: "Birinchi" }]} rowKey={(r) => r.id} />);
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText("Birinchi")).toBeTruthy();
  });

  it("shows empty, error (with retry) and loading states", async () => {
    const retry = vi.fn();
    const { rerender, container } = render(<DataTable columns={columns} rows={[]} rowKey={(r) => r.id} />);
    expect(screen.getByText("Maʼlumot yoʻq")).toBeTruthy();
    rerender(<DataTable columns={columns} rows={undefined} rowKey={(r) => r.id} error={new NetworkError()} onRetry={retry} />);
    await userEvent.click(screen.getByRole("button", { name: /Qayta urinish/ }));
    expect(retry).toHaveBeenCalled();
    rerender(<DataTable columns={columns} rows={undefined} rowKey={(r) => r.id} loading />);
    expect(container.querySelector(".animate-pulse")).toBeTruthy();
  });
});

describe("States", () => {
  it("ErrorState explains 403 and network failures", () => {
    const { rerender } = render(<ErrorState error={new ApiError(403, "FORBIDDEN", "")} />);
    expect(screen.getByRole("alert").textContent).toContain("ruxsatingiz yoʻq");
    rerender(<ErrorState error={new NetworkError()} />);
    expect(screen.getByRole("alert").textContent).toContain("Server bilan aloqa yoʻq");
  });

  it("DevPlaceholder is visibly labelled as in development", () => {
    render(<DevPlaceholder title="Kameralar" phase="Kamera bosqichi" />);
    expect(screen.getByText(/ISHLAB CHIQILMOQDA/).textContent).toContain("Kamera bosqichi");
  });
});

describe("Modal focus", () => {
  it("does not steal focus while typing when the parent re-renders with a new onClose", async () => {
    function Parent() {
      const [v, setV] = useState("");
      return (
        <Modal open onClose={() => undefined} title="Yozish">
          <input aria-label="matn" value={v} onChange={(e) => setV(e.target.value)} />
        </Modal>
      );
    }
    render(<Parent />);
    const input = screen.getByLabelText("matn");
    await userEvent.click(input);
    await userEvent.type(input, "salom dunyo");
    expect((input as HTMLInputElement).value).toBe("salom dunyo");
    expect(document.activeElement).toBe(input);
  });
});
