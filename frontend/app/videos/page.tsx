"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import type { Job } from "@/lib/types";
import { Card, EmptyState, ErrorBox, Skeleton, StatusBadge } from "@/components/ui";

const busy = (jobs: Job[]) => jobs.some((j) => j.status === "scripting" || j.status === "rendering");

export default function VideosPage() {
  const { data: jobs, error, loading, refresh } = usePolling(api.listJobs, 4000, busy);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Your videos</h1>
        <Link href="/" className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500">
          New video
        </Link>
      </div>
      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      ) : !jobs ? (
        <ErrorBox message={error ?? "Failed to load"} onRetry={refresh} title="Could not load history" />
      ) : jobs.length === 0 ? (
        <EmptyState title="No videos yet">
          <Link href="/" className="text-indigo-500 underline">
            Create your first one
          </Link>
        </EmptyState>
      ) : (
        <ul className="space-y-3">
          {jobs.map((j) => (
            <li key={j.id}>
              <Link href={`/videos/${encodeURIComponent(j.id)}`}>
                <Card className="flex flex-wrap items-center justify-between gap-2 transition-colors hover:border-indigo-400">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{j.script?.title || j.topic}</p>
                    <p className="text-xs text-zinc-500">
                      {j.created_at ? new Date(j.created_at).toLocaleString() : ""}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-xs dark:bg-zinc-700">
                      {j.settings.format === "vertical" ? "9:16" : "16:9"}
                    </span>
                    <StatusBadge status={j.status} />
                  </div>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
