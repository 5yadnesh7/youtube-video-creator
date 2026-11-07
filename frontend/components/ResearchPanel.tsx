"use client";

import { useState } from "react";
import type { Research, ResearchSource } from "@/lib/types";
import { Card } from "./ui";

export function SourcesList({ sources, compact = false }: { sources: ResearchSource[]; compact?: boolean }) {
  return (
    <ol className={`list-decimal space-y-1 pl-5 ${compact ? "text-xs" : "text-sm"}`}>
      {sources.map((s, i) => (
        <li key={`${s.url}-${i}`}>
          <a
            href={s.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-indigo-600 hover:underline dark:text-indigo-400"
          >
            {s.title || s.url}
          </a>
          {(s.publisher || s.published) && (
            <span className="text-zinc-500"> - {[s.publisher, s.published].filter(Boolean).join(", ")}</span>
          )}
        </li>
      ))}
    </ol>
  );
}

export function ResearchPanel({ research }: { research?: Research | null }) {
  const [open, setOpen] = useState(false);
  const grounded = !!research?.grounded;
  const count = research?.sources.length ?? 0;
  const lines = (research?.brief ?? "").split("\n").map((l) => l.trim()).filter(Boolean);
  const canExpand = !!research && (count > 0 || lines.length > 0);

  return (
    <Card className="space-y-2">
      <button
        type="button"
        aria-expanded={open}
        disabled={!canExpand}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-3 text-left"
      >
        <span className="text-sm font-semibold">Research &amp; sources</span>
        {canExpand && <span className="text-xs text-zinc-500">{open ? "Hide" : "Show"}</span>}
      </button>
      {grounded ? (
        <p className="text-sm text-emerald-600 dark:text-emerald-400">Based on {count} sources</p>
      ) : (
        <p className="text-sm text-amber-600 dark:text-amber-400">
          No sources found — script was written from the model&apos;s own knowledge and may be inaccurate
        </p>
      )}
      {research?.notes && <p className="text-xs text-zinc-500">{research.notes}</p>}
      {open && research && (
        <div className="space-y-3 border-t border-zinc-200 pt-3 dark:border-zinc-800">
          {count > 0 && <SourcesList sources={research.sources} />}
          {lines.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-zinc-500">Fact brief</p>
              <div className="space-y-1 rounded-lg bg-zinc-50 p-3 text-sm dark:bg-zinc-950">
                {lines.map((l, i) => (
                  <p key={i} className="whitespace-pre-wrap">
                    {l}
                  </p>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
