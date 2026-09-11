import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { SiteHeader } from "@/components/site-header";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "FounderLens AI",
  description:
    "Evidence-grounded startup research over your own documents, with citations and a knowledge graph.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col font-sans">
        <SiteHeader />
        <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-12">{children}</main>
        <footer className="border-t border-black/10 px-6 py-6 text-xs text-black/40 dark:border-white/10 dark:text-white/40">
          <div className="mx-auto max-w-5xl">FounderLens AI — Phase 0 shell.</div>
        </footer>
      </body>
    </html>
  );
}
