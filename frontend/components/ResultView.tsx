"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { api, errorMessage } from "@/lib/api";
import type { Job, Seo, VoicesResponse } from "@/lib/types";
import { SourcesList } from "./ResearchPanel";
import { Button, Card, Spinner } from "./ui";
import { StageList } from "./StageList";
import { VoicePicker } from "./VoicePicker";

export const FOCUS =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-indigo-400 dark:focus-visible:ring-offset-zinc-900";

const STATUS_META: Record<string, { label: string; icon: string; cls: string }> = {
  done: {
    label: "Done",
    icon: "✓",
    cls: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  },
  failed: { label: "Failed", icon: "✕", cls: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" },
  rendering: {
    label: "Rendering",
    icon: "◐",
    cls: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
  scripting: {
    label: "Writing script",
    icon: "✎",
    cls: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  },
  cancelled: {
    label: "Stopped",
    icon: "■",
    cls: "bg-zinc-200 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200",
  },
  script_ready: {
    label: "Script ready",
    icon: "●",
    cls: "bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300",
  },
};

export function statusLabel(status: string): string {
  return STATUS_META[status]?.label ?? status.replace(/_/g, " ");
}

export function StatusPill({ status }: { status: string }) {
  const m = STATUS_META[status] ?? {
    label: status.replace(/_/g, " "),
    icon: "•",
    cls: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${m.cls}`}>
      <span aria-hidden>{m.icon}</span>
      {m.label}
    </span>
  );
}

export function FormatChip({ format }: { format: string }) {
  const vertical = format === "vertical";
  return (
    <span className="inline-flex items-center rounded bg-zinc-200 px-1.5 py-0.5 text-xs text-zinc-800 dark:bg-zinc-700 dark:text-zinc-100">
      {vertical ? "9:16 vertical" : "16:9 horizontal"}
    </span>
  );
}

function CopyBtn({ text, label = "Copy", what }: { text: string; label?: string; what: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
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
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  };
  return (
    <>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? `${what} copied` : `${label} ${what.toLowerCase()}`}
        className={`inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-lg border border-zinc-400 bg-white px-3 text-sm font-medium text-zinc-800 transition-colors hover:bg-zinc-100 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800 ${FOCUS}`}
      >
        {copied ? (
          <svg aria-hidden viewBox="0 0 20 20" className="h-4 w-4 fill-none stroke-current" strokeWidth="2.5">
            <path d="M4 10.5l4 4 8-9" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : null}
        {copied ? "Copied" : label}
      </button>
      <span role="status" aria-live="polite" className="sr-only">
        {copied ? `${what} copied` : ""}
      </span>
    </>
  );
}

function VoicePanel({ job, onRerender }: { job: Job; onRerender: (voiceId: string) => Promise<void> }) {
  const [data, setData] = useState<VoicesResponse | null>(null);
  const [selected, setSelected] = useState(job.settings.voice_id ?? "");
  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const helpId = useId();

  const currentId = job.settings.voice_id ?? data?.default_id ?? null;
  const currentName = data?.voices.find((v) => v.id === currentId)?.name ?? currentId ?? "Auto";
  const same = !selected || selected === currentId;

  const rerender = async () => {
    setRendering(true);
    setRenderError(null);
    try {
      await onRerender(selected);
    } catch (e) {
      setRenderError(errorMessage(e));
      setRendering(false);
    }
  };

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
            Change voice <span className="font-normal text-zinc-600 dark:text-zinc-400">· Current: {currentName}</span>
          </span>
          <svg
            aria-hidden
            viewBox="0 0 20 20"
            className={`h-4 w-4 shrink-0 fill-none stroke-current transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`}
            strokeWidth="2"
          >
            <path d="M5 8l5 5 5-5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </h2>
      <div id={panelId} hidden={!open} className="space-y-3 px-4 pb-4">
        {/* Kept mounted so the voice list loads once and `currentName` resolves while collapsed */}
        <VoicePicker
          language={job.settings.language}
          gender={job.settings.voice}
          value={selected}
          onChange={setSelected}
          onLoaded={setData}
          disabled={rendering}
        />
        <Button
          onClick={rerender}
          disabled={same || rendering}
          aria-describedby={helpId}
          className={`min-h-11 ${FOCUS}`}
        >
          {rendering && <Spinner className="h-3.5 w-3.5 motion-reduce:animate-none" />}
          {rendering ? "Re-rendering…" : "Re-render with this voice"}
        </Button>
        {same && !rendering && (
          <p id={helpId} className="text-sm text-zinc-600 dark:text-zinc-400">
            Pick a different voice to re-render.
          </p>
        )}
        {renderError && (
          <p role="alert" className="text-sm text-red-700 dark:text-red-400">
            {renderError}
          </p>
        )}
      </div>
    </Card>
  );
}

function ts(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function Row({ label, text, children }: { label: string; text: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-zinc-600 dark:text-zinc-400">{label}</span>
        <CopyBtn text={text} what={label} />
      </div>
      <div className="break-words rounded-lg bg-zinc-50 p-3 text-sm [overflow-wrap:anywhere] dark:bg-zinc-950">
        {children}
      </div>
    </div>
  );
}

const none = <span className="text-zinc-600 dark:text-zinc-400">None</span>;

export function SeoPanel({ seo }: { seo: Seo }) {
  const chapters = seo.chapters.map((c) => `${ts(c.start_seconds)} ${c.title}`).join("\n");
  const tags = seo.tags.join(", ");
  const hashtags = seo.hashtags.map((h) => (h.startsWith("#") ? h : `#${h}`)).join(" ");
  return (
    <Card className="min-w-0 space-y-4">
      <h2 className="text-lg font-semibold">YouTube SEO</h2>
      <Row label="Title" text={seo.title}>
        {seo.title}
      </Row>
      <Row label="Description" text={seo.description}>
        <p className="whitespace-pre-wrap">{seo.description}</p>
      </Row>
      <Row label="Tags" text={tags}>
        {tags || none}
      </Row>
      <Row label="Hashtags" text={hashtags}>
        {hashtags || none}
      </Row>
      <Row label="Chapters" text={chapters}>
        {chapters ? <pre className="max-w-full font-sans whitespace-pre-wrap">{chapters}</pre> : none}
      </Row>
    </Card>
  );
}

export function ResultView({
  job,
  onNew,
  onRerender,
}: {
  job: Job;
  onNew?: () => void;
  onRerender?: (voiceId: string) => Promise<void>;
}) {
  const [version] = useState(() => Date.now());
  const vertical = job.settings.format === "vertical";
  const sources = job.research?.grounded ? job.research.sources : [];
  return (
    <div
      className={`grid gap-6 ${vertical ? "lg:grid-cols-[380px_minmax(0,1fr)]" : "lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]"}`}
    >
      <div className="min-w-0 space-y-3 lg:sticky lg:top-4 lg:self-start">
        <div
          className="mx-auto w-full overflow-hidden rounded-xl bg-black"
          style={
            vertical
              ? { aspectRatio: "9 / 16", width: "min(100%, calc(70dvh * 9 / 16))", maxHeight: "70dvh" }
              : { aspectRatio: "16 / 9" }
          }
        >
          <video
            key={version}
            src={`${api.videoUrl(job.id)}?v=${version}`}
            controls
            playsInline
            preload="metadata"
            className="h-full w-full"
          />
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <a
            href={`${api.videoUrl(job.id, true)}&v=${version}`}
            download
            className={`inline-flex min-h-11 items-center justify-center rounded-lg bg-indigo-600 px-5 text-sm font-medium text-white hover:bg-indigo-500 ${FOCUS}`}
          >
            Download MP4
          </a>
          <Link
            href="/"
            onClick={onNew}
            className={`inline-flex min-h-11 items-center justify-center rounded-lg border border-zinc-400 px-5 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-600 dark:hover:bg-zinc-800 ${FOCUS}`}
          >
            Create another video
          </Link>
        </div>
      </div>
      <div className="min-w-0 space-y-6">
        {onRerender && <VoicePanel job={job} onRerender={onRerender} />}
        {job.seo ? (
          <SeoPanel seo={job.seo} />
        ) : (
          <Card className="text-sm text-zinc-600 dark:text-zinc-400">No SEO metadata available for this video.</Card>
        )}
        {job.stages && job.stages.length > 0 && <StageList stages={job.stages} mode="summary" />}
        {sources.length > 0 && (
          <Card className="min-w-0 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-lg font-semibold">Sources used</h2>
              <CopyBtn
                label="Copy"
                what="Sources"
                text={sources
                  .map((s, i) => `[${i + 1}] ${[s.title, s.publisher, s.url].filter(Boolean).join(" - ")}`)
                  .join("\n")}
              />
            </div>
            <div className="min-w-0 [overflow-wrap:anywhere]">
              <SourcesList sources={sources} compact />
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
