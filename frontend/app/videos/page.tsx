"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import type { Job } from "@/lib/types";
import { EmptyState, ErrorBox, Skeleton } from "@/components/ui";
import { FOCUS, FormatChip, StatusPill } from "@/components/ResultView";

const busy = (jobs: Job[]) => jobs.some((j) => j.status === "scripting" || j.status === "rendering");

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31536000],
  ["month", 2592000],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
];

function relative(iso: string): string {
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, secs] of UNITS) {
    if (Math.abs(diff) >= secs) return rtf.format(Math.round(diff / secs), unit);
  }
  return "just now";
}

const cta = `inline-flex min-h-11 items-center justify-center rounded-lg bg-indigo-600 px-5 text-sm font-medium text-white hover:bg-indigo-500 ${FOCUS}`;

function JobCard({ j }: { j: Job }) {
  const title = j.script?.title || j.topic;
  const full = j.created_at ? new Date(j.created_at).toLocaleString() : "";
  const rel = j.created_at ? relative(j.created_at) : "";
  return (
    <Link
      href={`/videos/${encodeURIComponent(j.id)}`}
      className={`flex h-full min-h-24 flex-col justify-between gap-3 rounded-xl border border-zinc-300 bg-white p-4 shadow-sm transition-colors hover:border-indigo-500 motion-reduce:transition-none dark:border-zinc-700 dark:bg-zinc-900 dark:hover:border-indigo-400 ${FOCUS}`}
    >
      <p className="line-clamp-2 min-w-0 font-medium [overflow-wrap:anywhere]">{title}</p>
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill status={j.status} />
        {j.status === "rendering" && (
          <span className="text-xs font-medium tabular-nums text-amber-800 dark:text-amber-300">{j.progress}%</span>
        )}
        <FormatChip format={j.settings.format} />
        {full && (
          <time dateTime={j.created_at} title={full} className="ml-auto text-xs text-zinc-600 dark:text-zinc-400">
            {rel}
          </time>
        )}
      </div>
    </Link>
  );
}

export default function VideosPage() {
  const { data: jobs, error, loading, refresh } = usePolling(api.listJobs, 4000, busy);
  const running = jobs?.filter((j) => j.status === "scripting" || j.status === "rendering").length ?? 0;
  const summary = jobs
    ? `${jobs.length} ${jobs.length === 1 ? "video" : "videos"}${running ? `, ${running} in progress` : ""}`
    : "";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Your videos</h1>
        <Link href="/" className={cta}>
          New video
        </Link>
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {summary}
      </p>
      {loading ? (
        <div role="status">
          <span className="sr-only">Loading…</span>
          <div className="grid gap-3 md:grid-cols-2" aria-hidden>
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-24 motion-reduce:animate-none" />
            ))}
          </div>
        </div>
      ) : !jobs ? (
        <ErrorBox message={error ?? "Failed to load"} onRetry={refresh} title="Could not load history" />
      ) : jobs.length === 0 ? (
        <EmptyState title="No videos yet">
          <Link href="/" className={cta}>
            Create your first video
          </Link>
        </EmptyState>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {jobs.map((j) => (
            <li key={j.id} className="min-w-0">
              <JobCard j={j} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
