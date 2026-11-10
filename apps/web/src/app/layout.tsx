import type { Metadata, Viewport } from "next";
import { Providers } from "@/components/providers";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "AI Davomat", template: "%s · AI Davomat" },
  description: "Xodimlar davomatini boshqarish tizimi",
  robots: { index: false, follow: false }, // internal enterprise app
};

export const viewport: Viewport = {
  themeColor: "#0c152a",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="uz" className="h-full">
      <body className="min-h-full">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
