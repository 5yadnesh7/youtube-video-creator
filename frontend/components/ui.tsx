"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ButtonHTMLAttributes, type ReactNode } from "react";

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" }) {
  const styles = {
    primary: "bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-indigo-600/50",
    secondary:
      "border border-edge bg-surface text-foreground hover:bg-surface-muted",
    ghost: "text-zinc-700 hover:bg-surface-muted dark:text-zinc-300",
    danger: "text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex min-h-11 min-w-11 touch-manipulation items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors motion-reduce:transition-none enabled:cursor-pointer disabled:cursor-not-allowed disabled:opacity-60 ${styles} ${className}`}
    />
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-edge-soft bg-surface p-4 shadow-sm ${className}`}>
      {children}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-lg bg-zinc-200 motion-reduce:animate-none dark:bg-zinc-800 ${className}`}
    />
  );
}

export function ErrorBox({
  message,
  onRetry,
  title = "Something went wrong",
}: {
  message: string;
  onRetry?: () => void;
  title?: string;
}) {
  const unreachable = message.includes("Cannot reach the backend");
  return (
    <div
      role="alert"
      className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
    >
      <p className="font-semibold">{unreachable ? "Backend not reachable" : title}</p>
      <p className="mt-1 whitespace-pre-wrap break-words">{message}</p>
      {onRetry && (
        <Button variant="secondary" className="mt-3" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-edge p-10 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mt-2 text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Spinner({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <span
      className={`inline-block animate-spin rounded-full border-2 border-current border-t-transparent motion-reduce:animate-none ${className}`}
      aria-hidden
    />
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Button variant="secondary" className="px-3" onClick={copy} aria-live="polite">
      {copied ? "Copied" : label}
    </Button>
  );
}

export const inputCls =
  "min-h-11 w-full rounded-lg border border-edge bg-field px-3 py-2 text-base text-foreground placeholder:text-muted focus:border-indigo-500 sm:text-sm";

export function StatusBadge({ status }: { status: string }) {
  const color =
    {
      done: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
      failed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
      rendering: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300",
      scripting: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-300",
      script_ready: "bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300",
    }[status] ?? "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300";
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${color}`}>
      {status.replace("_", " ")}
    </span>
  );
}

export function NavLinks() {
  return <NavList pathname={usePathname()} />;
}

export function NavList({ pathname }: { pathname: string | null }) {
  const items = [
    { href: "/", label: "Create", active: pathname === "/" },
    { href: "/videos", label: "Videos", active: !!pathname?.startsWith("/videos") },
  ];
  return (
    <ul className="flex gap-2 text-sm">
      {items.map((i) => (
        <li key={i.href}>
          <Link
            href={i.href}
            aria-current={i.active ? "page" : undefined}
            className={`inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg px-3 ${
              i.active
                ? "bg-indigo-100 font-semibold text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200"
                : "text-zinc-700 hover:bg-surface-muted dark:text-zinc-300"
            }`}
          >
            {i.label}
          </Link>
        </li>
      ))}
    </ul>
  );
}
