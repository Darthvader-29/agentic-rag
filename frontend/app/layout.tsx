import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";

import { Providers } from "@/app/providers";
import { Toaster } from "@/components/ui/sonner";
import { flags } from "@/lib/flags";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "Agentic RAG",
    template: "%s · Agentic RAG",
  },
  description:
    "An agentic Retrieval-Augmented Generation chat assistant with document upload and web search.",
  applicationName: "Agentic RAG",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <Providers>{children}</Providers>
        <Toaster />
        {/* Phase 7 (FE-3): Vercel Web Analytics — flag-gated. Renders nothing when observability
            is off; on Vercel it auto-detects the project, locally it's an inert no-op. */}
        {flags.observability && <Analytics />}
      </body>
    </html>
  );
}
