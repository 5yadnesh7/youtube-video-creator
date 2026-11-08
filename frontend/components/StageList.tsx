"use client";

import { useEffect, useId, useState } from "react";
import type { Stage } from "@/lib/types";
import { FOCUS } from "./ResultView";
import { Card } from "./ui";

const GROUPS: Record<string, string> = {
  research: "Planning",
  script: "Planning",
  voice: "Audio",
  visuals: "Video",
  clips: "Video",
  merge: "Video",
  finish: "Finishing",
  seo: "Finishing",
};

function ms(iso: string | null): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : t;
}

export function fmtDuration(totalSec: number): string {
  const s = Math.max(0, Math.round(totalSec));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

function stageSeconds(st: Stage, now: number): number | null {
  const a = ms(st.started_at);
  if (a === null) return null;
  const b = ms(st.finished_at) ?? now;
  return (b - a) / 1000;
}

function useNow(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [enabled]);
  return now;
}

function Icon({ status }: { status: Stage["status"] }) {
  const base = "h-5 w-5 shrink-0";
  switch (status) {
    case "done":
      return (
        <svg aria-hidden viewBox="0 0 20 20" className={`${base} fill-none stroke-emerald-700 dark:stroke-emerald-400`} strokeWidth="2">
          <circle cx="10" cy="10" r="8" />
          <path d="M6.5 10.5l2.5 2.5 4.5-5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      );
    case "running":
      return (
        <svg aria-hidden viewBox="0 0 20 20" className={`${base} fill-none`} strokeWidth="2.5">
          <circle cx="10" cy="10" r="7.5" className="stroke-indigo-200 dark:stroke-indigo-900" />
          <path
            d="M10 2.5a7.5 7.5 0 0 1 7.5 7.5"
            strokeLinecap="round"
            className="origin-center animate-spin stroke-indigo-600 motion-reduce:animate-none dark:stroke-indigo-400"
          />
        </svg>
      );
    case "failed":
      return (
        <svg aria-hidden viewBox="0 0 20 20" className={`${base} fill-none stroke-red-700 dark:stroke-red-400`} strokeWidth="2">
          <circle cx="10" cy="10" r="8" />
          <path d="M7 7l6 6M13 7l-6 6" strokeLinecap="round" />
        </svg>
      );
    case "cancelled":
      return (
        <svg aria-hidden viewBox="0 0 20 20" className={`${base} fill-none stroke-zinc-600 dark:stroke-zinc-400`} strokeWidth="2">
          <circle cx="10" cy="10" r="8" />
          <rect x="7" y="7" width="6" height="6" rx="1" className="fill-zinc-600 dark:fill-zinc-400" />
        </svg>
      );
    default:
      return (
        <svg aria-hidden viewBox="0 0 20 20" className={`${base} fill-none stroke-zinc-500`} strokeWidth="2">
          <circle cx="10" cy="10" r="8" />
        </svg>
      );
  }
}

function countText(st: Stage): string {
  if (st.detail) return st.detail;
  if (st.total > 0) return `${st.done} of ${st.total}`;
  if (st.percent > 0) return `${st.percent}%`;
  return "";
}

function rightText(st: Stage, now: number): string {
  const dur = stageSeconds(st, now);
  switch (st.status) {
    case "done":
      return dur === null ? "Done" : `Done · ${fmtDuration(dur)}`;
    case "running":
      return [countText(st), dur === null ? "" : fmtDuration(dur)].filter(Boolean).join(" · ") || "In progress";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Stopped";
    default:
      return "Waiting";
  }
}

function summaryLine(stages: Stage[]): string {
  const running = stages.filter((s) => s.status === "running");
  if (running.length === 1) {
    const st = running[0];
    const extra = countText(st);
    return `Step ${stages.indexOf(st) + 1} of ${stages.length} · ${st.label}${extra ? ` · ${extra}` : ""}`;
  }
  if (running.length > 1) {
    const first = stages.indexOf(running[0]) + 1;
    const last = stages.indexOf(running[running.length - 1]) + 1;
    const parts = running.map((st) => {
      const extra = countText(st);
      return `${st.label}${extra ? ` (${extra})` : ""}`;
    });
    return `Steps ${first}–${last} of ${stages.length} · ${parts.join(" + ")}`;
  }
  const failed = stages.find((s) => s.status === "failed");
  if (failed) return `Failed at ${failed.label}`;
  if (stages.some((s) => s.status === "cancelled")) return "Stopped";
  const done = stages.filter((s) => s.status === "done").length;
  return done === stages.length ? `All ${done} steps done` : `${done} of ${stages.length} steps done`;
}

function Rows({ stages, now }: { stages: Stage[]; now: number }) {
  return (
    <ol className="space-y-1">
      {stages.map((st, i) => {
        const group = GROUPS[st.key] ?? "";
        const prevGroup = i > 0 ? (GROUPS[stages[i - 1].key] ?? "") : "";
        const heading = group && group !== prevGroup ? group : "";
        const running = st.status === "running";
        const pct = st.percent > 0 ? st.percent : st.total > 0 ? Math.round((st.done / st.total) * 100) : 0;
        const showBar = running && (st.total > 0 || st.percent > 0);
        return (
          <li key={st.key} aria-current={running ? "step" : undefined}>
            {heading && (
              <p className="mb-1 mt-2 text-xs font-medium uppercase tracking-wide text-zinc-600 dark:text-zinc-400">
                {heading}
              </p>
            )}
            <div className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 py-1.5">
              <span className="flex min-w-0 basis-48 flex-1 items-center gap-2.5">
                <Icon status={st.status} />
                <span
                  className={`min-w-0 break-words text-sm ${
                    running ? "font-semibold" : st.status === "pending" ? "text-zinc-600 dark:text-zinc-400" : ""
                  }`}
                >
                  {st.label}
                </span>
              </span>
              <span
                className={`text-right text-sm tabular-nums ${
                  st.status === "done"
                    ? "text-emerald-800 dark:text-emerald-400"
                    : st.status === "failed"
                      ? "text-red-700 dark:text-red-400"
                      : "text-zinc-600 dark:text-zinc-400"
                }`}
              >
                {rightText(st, now)}
              </span>
              {showBar && (
                <div
                  className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
                  role="progressbar"
                  aria-label={`${st.label} progress`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={pct}
                >
                  <div
                    className="h-full rounded-full bg-indigo-600 transition-all duration-500 motion-reduce:transition-none dark:bg-indigo-400"
                    style={{ width: `${Math.max(2, pct)}%` }}
                  />
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 20 20"
      className={`h-4 w-4 shrink-0 fill-none stroke-current transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`}
      strokeWidth="2"
    >
      <path d="M5 8l5 5 5-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function StageList({ stages, mode = "live" }: { stages: Stage[]; mode?: "live" | "summary" }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const live = mode === "live";
  const now = useNow(live && open && stages.some((s) => s.status === "running"));

  if (stages.length === 0) return null;

  if (!live) {
    const finished = stages.filter((s) => s.status === "done");
    if (finished.length === 0) return null;
    const starts = finished.map((s) => ms(s.started_at)).filter((n): n is number => n !== null);
    const ends = finished.map((s) => ms(s.finished_at)).filter((n): n is number => n !== null);
    const sum = finished.reduce((acc, s) => acc + (stageSeconds(s, 0) ?? 0), 0);
    const total = starts.length && ends.length ? (Math.max(...ends) - Math.min(...starts)) / 1000 : sum;
    return (
      <Card className="p-0">
        <h2 className="text-sm font-semibold">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((o) => !o)}
            className={`flex min-h-11 w-full items-center justify-between gap-2 rounded-xl px-4 py-2 text-left ${FOCUS}`}
          >
            <span className="min-w-0 break-words">
              How this video was made{" "}
              <span className="font-normal text-zinc-600 dark:text-zinc-400">· Total {fmtDuration(total)}</span>
            </span>
            <Chevron open={open} />
          </button>
        </h2>
        <div id={panelId} hidden={!open} className="px-4 pb-4">
          <Rows stages={finished} now={0} />
          <p className="mt-2 border-t border-edge-soft pt-2 text-sm font-medium tabular-nums">
            Total time · {fmtDuration(total)}
          </p>
        </div>
      </Card>
    );
  }

  const runningNow = stages.filter((s) => s.status === "running");
  const announce = runningNow.length
    ? `${runningNow.map((s) => s.label).join(" and ")}, step ${stages.indexOf(runningNow[0]) + 1} of ${stages.length}`
    : summaryLine(stages);

  return (
    <div>
      <p role="status" aria-live="polite" className="sr-only">
        {announce}
      </p>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-lg px-1 py-2 text-left text-sm ${FOCUS}`}
      >
        <span className="min-w-0 break-words font-medium">{summaryLine(stages)}</span>
        <span className="flex shrink-0 items-center gap-1.5 text-zinc-700 dark:text-zinc-300">
          {open ? "Hide details" : "Show details"}
          <Chevron open={open} />
        </span>
      </button>
      <div id={panelId} hidden={!open} className="mt-1 rounded-lg border border-edge-soft px-3 py-2">
        <Rows stages={stages} now={now} />
      </div>
    </div>
  );
}
