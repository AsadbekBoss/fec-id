"use client";

/** Last-resort boundary (errors in the root layout itself). Must render its own <html>. */
export default function GlobalError({ retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <html lang="uz">
      <body style={{ fontFamily: "system-ui, sans-serif", display: "grid", placeItems: "center", minHeight: "100vh", margin: 0 }}>
        <div style={{ textAlign: "center" }}>
          <h1 style={{ fontSize: 20, color: "#13203c" }}>Xatolik yuz berdi</h1>
          <button
            type="button"
            onClick={() => retry()}
            style={{ marginTop: 16, padding: "8px 16px", borderRadius: 8, background: "#1b2c50", color: "#fff", border: 0 }}
          >
            Qayta urinish
          </button>
        </div>
      </body>
    </html>
  );
}
