import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI Quant Terminal",
  description:
    "Local research terminal for market data, strategies, backtests, portfolio risk and reproducible experiments.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-surface-0 text-text-primary">
        {children}
      </body>
    </html>
  );
}
