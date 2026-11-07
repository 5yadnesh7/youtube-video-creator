import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";
import { NavLinks, NavList } from "@/components/ui";
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
  title: "AI Video Creator",
  description: "Turn trending topics into YouTube-ready videos with AI",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-2 focus:z-50 focus:inline-flex focus:min-h-11 focus:items-center focus:rounded-lg focus:bg-indigo-600 focus:px-4 focus:text-sm focus:font-medium focus:text-white"
        >
          Skip to main content
        </a>
        <header className="sticky top-0 z-20 border-b border-edge-soft bg-surface/90 backdrop-blur">
          <nav
            aria-label="Primary"
            className="mx-auto flex h-14 max-w-4xl items-center justify-between gap-2 px-4"
          >
            <Link href="/" className="inline-flex min-h-11 items-center rounded-lg font-semibold">
              AI Video Creator
            </Link>
            <Suspense fallback={<NavList pathname={null} />}>
              <NavLinks />
            </Suspense>
          </nav>
        </header>
        <main id="main-content" tabIndex={-1} className="mx-auto w-full max-w-4xl flex-1 px-4 py-6 outline-none">
          {children}
        </main>
      </body>
    </html>
  );
}
