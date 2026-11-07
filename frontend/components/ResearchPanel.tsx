"use client";

import { useId, useState } from "react";
import type { Research, ResearchSource } from "@/lib/types";
import { Card } from "./ui";

const iconProps = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
  className: "h-4 w-4 shrink-0",
} as const;

const ExternalIcon = () => (
  <svg {...iconProps} className="ml-1 inline h-3 w-3 align-baseline">
    <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
  </svg>
);
const CheckIcon = () => (
  <svg {...iconProps}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </svg>
);
const WarnIcon = () => (
  <svg {...iconProps}>
    <path d="M12 3 2 20h20L12 3Z" />
    <path d="M12 10v4M12 17.5v.01" />
  </svg>
);

const focusRing =
  "outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-indigo-400 dark:focus-visible:ring-offset-zinc-900";

export function SourcesList({ sources, compact = false }: { sources: ResearchSource[]; compact?: boolean }) {
  return (
    <ol className={`list-decimal space-y-2 pl-5 ${compact ? "text-xs" : "text-sm"}`}>
      {sources.map((s, i) => (
        <li key={`${s.url}-${i}`} className="[overflow-wrap:anywhere]">
          <a
            href={s.url}
            target="_blank"
            rel="noopener noreferrer"
            className={`rounded text-indigo-700 underline-offset-2 hover:underline dark:text-indigo-400 ${focusRing}`}
          >
            {s.title || s.url}
            <ExternalIcon />
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
          {(s.publisher || s.published) && (
            <span className="text-zinc-600 dark:text-zinc-400">
              {" "}
              - {[s.publisher, s.published].filter(Boolean).join(", ")}
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

function BriefItem({ line }: { line: string }) {
  const text = line.replace(/^[-*•]\s+/, "");
  const m = text.match(/^CONFLICT:\s*([\s\S]*)$/);
  if (m) {
    return (
      <li className="list-none [overflow-wrap:anywhere]">
        <span className="inline-flex items-center gap-1 font-bold text-amber-700 dark:text-amber-400">
          <WarnIcon />
          CONFLICT:
        </span>{" "}
        {m[1]}
      </li>
    );
  }
  return <li className="[overflow-wrap:anywhere]">{text}</li>;
}

export function ResearchPanel({ research }: { research?: Research | null }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const grounded = !!research?.grounded;
  const count = research?.sources.length ?? 0;
  const lines = (research?.brief ?? "").split("\n").map((l) => l.trim()).filter(Boolean);
  const canExpand = !!research && (count > 0 || lines.length > 0);

  return (
    <Card className="space-y-2">
      <h2 className="text-base font-semibold">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          disabled={!canExpand}
          onClick={() => setOpen((o) => !o)}
          className={`-mx-2 flex min-h-11 w-[calc(100%+1rem)] items-center justify-between gap-3 rounded-lg px-2 text-left font-semibold disabled:cursor-default ${focusRing}`}
        >
          <span>Research &amp; sources</span>
          {canExpand && (
            <span className="flex items-center gap-1 text-xs font-medium text-zinc-600 dark:text-zinc-400">
              {open ? "Hide" : "Show"}
              <svg
                {...iconProps}
                className={`h-4 w-4 transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`}
              >
                <path d="m6 9 6 6 6-6" />
              </svg>
            </span>
          )}
        </button>
      </h2>
      {grounded ? (
        <p className="flex items-center gap-1.5 text-sm font-medium text-emerald-700 dark:text-emerald-400">
          <CheckIcon />
          Based on {count} sources
        </p>
      ) : (
        <p className="flex items-start gap-1.5 text-sm font-medium text-amber-700 dark:text-amber-400">
          <span className="mt-0.5">
            <WarnIcon />
          </span>
          No sources found — script was written from the model&apos;s own knowledge and may be inaccurate
        </p>
      )}
      {research?.notes && <p className="text-xs text-zinc-600 dark:text-zinc-400">{research.notes}</p>}
      <div id={panelId} hidden={!open || !research}>
        {open && research && (
          <div className="space-y-4 border-t border-zinc-300 pt-3 dark:border-zinc-700">
            {count > 0 && (
              <section aria-labelledby={`${panelId}-sources`}>
                <h3 id={`${panelId}-sources`} className="mb-2 text-sm font-semibold">
                  Sources
                </h3>
                <SourcesList sources={research.sources} />
              </section>
            )}
            {lines.length > 0 && (
              <section aria-labelledby={`${panelId}-brief`}>
                <h3 id={`${panelId}-brief`} className="mb-2 text-sm font-semibold">
                  Fact brief
                </h3>
                <ul className="list-disc space-y-1.5 rounded-lg bg-zinc-50 p-3 pl-7 text-sm dark:bg-zinc-950">
                  {lines.map((l, i) => (
                    <BriefItem key={i} line={l} />
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
