"use client";

import { useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Job, Seo, VoicesResponse } from "@/lib/types";
import { SourcesList } from "./ResearchPanel";
import { Button, Card, CopyButton, Spinner } from "./ui";
import { VoicePicker } from "./VoicePicker";

function VoicePanel({ job, onRerender }: { job: Job; onRerender: (voiceId: string) => Promise<void> }) {
  const [data, setData] = useState<VoicesResponse | null>(null);
  const [selected, setSelected] = useState(job.settings.voice_id ?? "");
  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);

  const currentId = job.settings.voice_id ?? data?.default_id ?? null;
  const currentName = data?.voices.find((v) => v.id === currentId)?.name ?? currentId ?? "Auto";

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
    <Card className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Change voice</h3>
        <span className="text-xs text-zinc-500">Current: {currentName}</span>
      </div>
      <VoicePicker
        language={job.settings.language}
        gender={job.settings.voice}
        value={selected}
        onChange={setSelected}
        onLoaded={setData}
        disabled={rendering}
      />
      <Button onClick={rerender} disabled={!selected || selected === currentId || rendering}>
        {rendering && <Spinner className="h-3.5 w-3.5" />}
        Re-render with this voice
      </Button>
      {renderError && <p className="text-sm text-red-600 dark:text-red-400">{renderError}</p>}
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
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</span>
        <CopyButton text={text} />
      </div>
      <div className="rounded-lg bg-zinc-50 p-3 text-sm dark:bg-zinc-950">{children}</div>
    </div>
  );
}

export function SeoPanel({ seo }: { seo: Seo }) {
  const chapters = seo.chapters.map((c) => `${ts(c.start_seconds)} ${c.title}`).join("\n");
  const tags = seo.tags.join(", ");
  const hashtags = seo.hashtags.map((h) => (h.startsWith("#") ? h : `#${h}`)).join(" ");
  return (
    <Card className="space-y-4">
      <h2 className="text-lg font-semibold">YouTube SEO</h2>
      <Row label="Title" text={seo.title}>
        {seo.title}
      </Row>
      <Row label="Description" text={seo.description}>
        <p className="whitespace-pre-wrap">{seo.description}</p>
      </Row>
      <Row label="Tags" text={tags}>
        {tags || <span className="text-zinc-500">None</span>}
      </Row>
      <Row label="Hashtags" text={hashtags}>
        {hashtags || <span className="text-zinc-500">None</span>}
      </Row>
      <Row label="Chapters" text={chapters}>
        {chapters ? <pre className="font-sans whitespace-pre-wrap">{chapters}</pre> : <span className="text-zinc-500">None</span>}
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
  return (
    <div className={`grid gap-6 ${vertical ? "lg:grid-cols-[minmax(0,22rem)_1fr]" : "lg:grid-cols-2"}`}>
      <div className="space-y-3">
        <div
          className={`overflow-hidden rounded-xl bg-black ${vertical ? "mx-auto aspect-[9/16] max-w-xs lg:max-w-none" : "aspect-video"}`}
        >
          <video key={version} src={`${api.videoUrl(job.id)}?v=${version}`} controls playsInline className="h-full w-full" />
        </div>
        <div className="flex flex-wrap gap-2">
          <a
            href={`${api.videoUrl(job.id, true)}&v=${version}`}
            download
            className="inline-flex items-center rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
          >
            Download MP4
          </a>
          {onNew && (
            <button
              onClick={onNew}
              className="rounded-lg border border-zinc-300 px-4 py-2 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              Create another
            </button>
          )}
        </div>
        {onRerender && <VoicePanel job={job} onRerender={onRerender} />}
      </div>
      <div className="space-y-6">
        {job.seo ? (
          <SeoPanel seo={job.seo} />
        ) : (
          <Card className="text-sm text-zinc-500">No SEO metadata available for this video.</Card>
        )}
        {job.research?.grounded && job.research.sources.length > 0 && (
          <Card className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">Sources used</h2>
              <CopyButton
                label="Copy sources"
                text={job.research.sources
                  .map((s, i) => `[${i + 1}] ${[s.title, s.publisher, s.url].filter(Boolean).join(" - ")}`)
                  .join("\n")}
              />
            </div>
            <SourcesList sources={job.research.sources} compact />
          </Card>
        )}
      </div>
    </div>
  );
}
