import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "XAUUSDT Operations",
  description: "XAUUSDT paper operations dashboard — observability only",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-ink text-slate-200 antialiased">
        {children}
      </body>
    </html>
  );
}
