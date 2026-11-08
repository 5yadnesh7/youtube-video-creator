"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ApiError, api, errorMessage } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import type { Job, JobStatus, Script, Stage } from "@/lib/types";
import { StageList } from "./StageList";
import { FOCUS, FormatChip, ResultView, StatusPill } from "./ResultView";
import { ScriptEditor } from "./ScriptEditor";
import { Button, Card, ErrorBox, Skeleton, Spinner } from "./ui";

const active = (j: Job) => j.status === "scripting" || j.status === "rendering";

function useElapsed(): number {
  const [sec, setSec] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const t = setInterval(() => setSec(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(t);
  }, []);
  return sec;
}

const fmtElapsed = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function stepHint(step: string): string {
  const s = step.toLowerCase();
  if (/voice|speech|tts|audio|narrat/.test(s)) return "Turning the script into narration audio.";
  if (/image|media|visual|footage|clip|download/.test(s)) return "Collecting images and clips for each scene.";
  if (/caption|subtitle/.test(s)) return "Timing captions to the narration.";
  if (/seo|metadata/.test(s)) return "Writing the YouTube title, description and tags.";
  if (/render|assembl|encod|compos|final|mux/.test(s)) return "Stitching scenes, audio and captions into the MP4.";
  return "Working through the pipeline; the step name above updates as it moves along.";
}

function parseLimitError(msg: string): { resetAt: string } | null {
  if (!/daily limit|free-models-per-day|per-day/i.test(msg)) return null;
  const m = msg.match(/reset['"\s:=]*?(\d{10,13})/i);
  if (m) {
    const n = Number(m[1]);
    const d = new Date(m[1].length <= 10 ? n * 1000 : n);
    if (!Number.isNaN(d.getTime())) {
      return { resetAt: d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) };
    }
  }
  const t = msg.match(/resets?\s+(?:at|in|on)?\s*([^.\n]{3,40})/i);
  return { resetAt: t ? t[1].trim() : "around midnight UTC" };
}

function FailedCard({
  job,
  busy,
  onRetry,
  onNew,
}: {
  job: Job;
  busy: boolean;
  onRetry: () => void;
  onNew?: () => void;
}) {
  const raw = job.error || "Unknown error";
  const limit = parseLimitError(raw);
  const title = job.script ? "Rendering failed" : "Script generation failed";
  return (
    <div className="space-y-4">
      {limit ? (
        <div
          role="alert"
          className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
        >
          <p className="font-semibold">Daily AI quota reached</p>
          <p className="mt-1">
            OpenRouter&apos;s free quota (50 requests/day) is used up. It resets at {limit.resetAt}.
          </p>
          <p className="mt-2">Options:</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            <li>Wait until the quota resets, then retry.</li>
            <li>
              Add credits on{" "}
              <a
                href="https://openrouter.ai/credits"
                target="_blank"
                rel="noreferrer"
                className={`inline-flex min-h-6 items-center rounded font-medium underline ${FOCUS}`}
              >
                openrouter.ai
              </a>
              .
            </li>
          </ul>
          <details className="mt-3">
            <summary className={`flex min-h-11 cursor-pointer items-center rounded ${FOCUS}`}>Show raw error</summary>
            <pre className="mt-1 max-w-full whitespace-pre-wrap rounded bg-white/70 p-2 text-xs [overflow-wrap:anywhere] dark:bg-black/30">
              {raw}
            </pre>
          </details>
        </div>
      ) : (
        <div
          role="alert"
          className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
        >
          <p className="font-semibold">{title}</p>
          <p className="mt-1 whitespace-pre-wrap [overflow-wrap:anywhere]">{raw}</p>
        </div>
      )}
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        <Button disabled={busy} onClick={onRetry} className={`min-h-11 ${FOCUS}`}>
          {busy && <Spinner className="h-3.5 w-3.5 motion-reduce:animate-none" />}
          {busy ? "Retrying…" : "Retry"}
        </Button>
        <Link
          href="/videos"
          className={`inline-flex min-h-11 items-center justify-center rounded-lg border border-zinc-400 px-4 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-600 dark:hover:bg-zinc-800 ${FOCUS}`}
        >
          Back to videos
        </Link>
        {onNew && (
          <Button variant="secondary" onClick={onNew} className={`min-h-11 !border-zinc-400 dark:!border-zinc-600 ${FOCUS}`}>
            Start over
          </Button>
        )}
      </div>
    </div>
  );
}

function StopControl({
  jobId,
  onUpdated,
  onRefresh,
}: {
  jobId: string;
  onUpdated: (j: Job) => void;
  onRefresh: () => void;
}) {
  const [phase, setPhase] = useState<"idle" | "confirm" | "stopping">("idle");
  const [err, setErr] = useState<string | null>(null);
  const stopRef = useRef<HTMLButtonElement>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  const prev = useRef(phase);

  useEffect(() => {
    if (phase === "confirm") keepRef.current?.focus();
    else if (phase === "idle" && prev.current === "confirm") stopRef.current?.focus();
    prev.current = phase;
  }, [phase]);

  const confirm = async () => {
    setPhase("stopping");
    setErr(null);
    try {
      onUpdated(await api.cancelJob(jobId));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        onRefresh();
      } else {
        setErr(errorMessage(e));
      }
      setPhase("idle");
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex min-h-11 flex-wrap items-center gap-2">
        {phase === "idle" && (
          <button
            ref={stopRef}
            type="button"
            onClick={() => setPhase("confirm")}
            className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-red-700 px-4 text-sm font-medium text-red-800 hover:bg-red-50 dark:border-red-400 dark:text-red-300 dark:hover:bg-red-950/40 ${FOCUS}`}
          >
            <svg aria-hidden viewBox="0 0 20 20" className="h-4 w-4 fill-current">
              <rect x="4" y="4" width="12" height="12" rx="2" />
            </svg>
            Stop generation
          </button>
        )}
        {phase === "confirm" && (
          <div role="group" aria-label="Confirm stop" className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">Stop generating?</span>
            <button
              type="button"
              onClick={confirm}
              className={`inline-flex min-h-11 items-center justify-center rounded-lg bg-red-700 px-4 text-sm font-medium text-white hover:bg-red-800 ${FOCUS}`}
            >
              Yes, stop
            </button>
            <button
              ref={keepRef}
              type="button"
              onClick={() => setPhase("idle")}
              className={`inline-flex min-h-11 items-center justify-center rounded-lg border border-zinc-400 px-4 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-600 dark:hover:bg-zinc-800 ${FOCUS}`}
            >
              Keep going
            </button>
          </div>
        )}
        {phase === "stopping" && (
          <button
            type="button"
            disabled
            aria-busy="true"
            className="inline-flex min-h-11 cursor-not-allowed items-center justify-center gap-2 rounded-lg border border-zinc-400 px-4 text-sm font-medium opacity-70 dark:border-zinc-600"
          >
            <Spinner className="h-3.5 w-3.5 motion-reduce:animate-none" />
            Stopping…
          </button>
        )}
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {phase === "stopping" ? "Stopping generation" : ""}
      </p>
      {err && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          Could not stop: {err}
        </p>
      )}
    </div>
  );
}

type StopProps = { jobId: string; onUpdated: (j: Job) => void; onRefresh: () => void };

function RenderProgress({
  step,
  progress,
  stages,
  stop,
}: {
  step: string;
  progress: number;
  stages: Stage[];
  stop: StopProps;
}) {
  const elapsed = useElapsed();
  const [announce, setAnnounce] = useState("");
  const last = useRef({ step: "", bucket: -1 });
  const name = step || "Rendering";
  useEffect(() => {
    const bucket = Math.floor(progress / 25);
    if (last.current.step !== name || last.current.bucket !== bucket) {
      last.current = { step: name, bucket };
      setAnnounce(`${name}, ${progress}%`);
    }
  }, [name, progress]);
  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="flex min-w-0 items-center gap-2 font-medium">
          <Spinner className="h-4 w-4 shrink-0 motion-reduce:animate-none" />
          <span className="min-w-0 break-words">{name}</span>
        </span>
        <span className="font-semibold tabular-nums">{progress}%</span>
      </div>
      <div
        className="h-3 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
        role="progressbar"
        aria-label="Video render progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
        aria-valuetext={`${name}, ${progress}%`}
      >
        <div
          className="h-full rounded-full bg-indigo-600 transition-all duration-500 motion-reduce:transition-none dark:bg-indigo-500"
          style={{ width: `${Math.max(2, progress)}%` }}
        />
      </div>
      <StageList stages={stages} />
      {stages.length === 0 && <p className="text-sm text-zinc-700 dark:text-zinc-300">{stepHint(name)}</p>}
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Elapsed {fmtElapsed(elapsed)}. This can take a few minutes. You can leave and come back from{" "}
        <Link href="/videos" className={`rounded underline ${FOCUS}`}>
          Videos
        </Link>
        .
      </p>
      <StopControl {...stop} />
      <p role="status" aria-live="polite" className="sr-only">
        {announce}
      </p>
    </Card>
  );
}

function ScriptingProgress({ step, stages: live, stop }: { step: string; stages: Stage[]; stop: StopProps }) {
  const elapsed = useElapsed();
  const researching = !/writing/i.test(step);
  const stages = [
    { n: 1, label: "Researching the topic", active: researching, done: !researching },
    { n: 2, label: "Writing the script", active: !researching, done: false },
  ];
  return (
    <Card className="space-y-3">
      <div className="flex items-center gap-3">
        <Spinner className="motion-reduce:animate-none" />
        <p className="text-lg font-semibold">{step || "Researching topic"}</p>
      </div>
      <StageList stages={live} />
      {live.length === 0 && (
      <ol className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        {stages.map((st) => (
          <li
            key={st.n}
            aria-current={st.active ? "step" : undefined}
            className={`flex items-center gap-1.5 ${
              st.active
                ? "font-semibold text-indigo-700 dark:text-indigo-300"
                : st.done
                  ? "text-emerald-700 dark:text-emerald-400"
                  : "text-zinc-600 dark:text-zinc-400"
            }`}
          >
            <span aria-hidden>{st.done ? "✓" : st.active ? "●" : "○"}</span>
            {st.n}. {st.label}
            <span className="sr-only">{st.done ? " (finished)" : st.active ? " (in progress)" : " (waiting)"}</span>
          </li>
        ))}
      </ol>
      )}
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Research can take up to about a minute. Elapsed {fmtElapsed(elapsed)}.
      </p>
      <StopControl {...stop} />
      <div aria-hidden className="space-y-3">
        <Skeleton className="h-10 motion-reduce:animate-none" />
        <Skeleton className="h-24 motion-reduce:animate-none" />
        <Skeleton className="h-24 motion-reduce:animate-none" />
      </div>
      {live.length === 0 && (
        <p role="status" aria-live="polite" className="sr-only">
          {researching ? "Researching the topic" : "Writing the script"}
        </p>
      )}
    </Card>
  );
}

export function JobView({
  jobId,
  onNew,
  onStatusChange,
}: {
  jobId: string;
  onNew?: () => void;
  onStatusChange?: (s: JobStatus) => void;
}) {
  const { data: job, error, loading, refresh, set } = usePolling(() => api.getJob(jobId), 1500, active);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const status = job?.status;

  useEffect(() => {
    if (status) onStatusChange?.(status);
  }, [status, onStatusChange]);

  const run = async (fn: () => Promise<Job>) => {
    setBusy(true);
    setActionError(null);
    try {
      set(await fn());
    } catch (e) {
      setActionError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const rerender = async (voiceId: string) => {
    if (!job) return;
    set(await api.render(job.id, { voice_id: voiceId, reuse_media: true }));
  };

  if (loading) {
    return (
      <div role="status">
        <span className="sr-only">Loading…</span>
        <Skeleton className="h-64 w-full motion-reduce:animate-none" />
      </div>
    );
  }
  if (!job) return <ErrorBox message={error ?? "Job not found"} onRetry={refresh} />;

  const target = job.settings.duration_seconds;
  const header = (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <h1 className="min-w-0 text-xl font-semibold [overflow-wrap:anywhere]">{job.script?.title || job.topic}</h1>
      <StatusPill status={job.status} />
      <FormatChip format={job.settings.format} />
    </div>
  );

  const pollErr = error && active(job) && (
    <p role="status" className="mb-3 text-sm text-amber-700 dark:text-amber-400">
      Connection issue, retrying: {error}
    </p>
  );
  const actionErr = actionError && (
    <div className="mb-4">
      <ErrorBox message={actionError} />
    </div>
  );

  const retryScript = () => run(() => api.regenerateScript(job.id));
  const doRender = () => run(() => api.render(job.id));
  const stop: StopProps = { jobId: job.id, onUpdated: set, onRefresh: () => void refresh() };

  if (job.status === "cancelled") {
    return (
      <div>
        {header}
        {actionErr}
        <Card className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold">Generation stopped</h2>
            <p className="mt-1 text-sm text-zinc-700 dark:text-zinc-300">No script was saved.</p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <Button disabled={busy} onClick={retryScript} className={`min-h-11 ${FOCUS}`}>
              {busy && <Spinner className="h-3.5 w-3.5 motion-reduce:animate-none" />}
              {busy ? "Starting…" : "Try again"}
            </Button>
            {onNew ? (
              <Button variant="secondary" onClick={onNew} className={`min-h-11 !border-zinc-400 dark:!border-zinc-600 ${FOCUS}`}>
                Start over
              </Button>
            ) : (
              <Link
                href="/"
                className={`inline-flex min-h-11 items-center justify-center rounded-lg border border-zinc-400 px-4 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-600 dark:hover:bg-zinc-800 ${FOCUS}`}
              >
                Start over
              </Link>
            )}
          </div>
        </Card>
      </div>
    );
  }

  if (job.status === "scripting") {
    return (
      <div>
        {header}
        {pollErr}
        <ScriptingProgress step={job.step} stages={job.stages ?? []} stop={stop} />
      </div>
    );
  }

  if (job.status === "script_ready" && job.script) {
    return (
      <div>
        {header}
        {actionErr}
        {job.error && job.step === "Stopped" ? (
          <div
            role="status"
            className="mb-4 rounded-xl border border-zinc-300 bg-zinc-100 p-4 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-100"
          >
            <p className="font-semibold">Generation stopped</p>
            <p className="mt-1 [overflow-wrap:anywhere]">{job.error}</p>
            <p className="mt-2">Your script is saved — change anything and generate again.</p>
          </div>
        ) : job.error ? (
          <div
            role="alert"
            className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
          >
            <p className="font-semibold">The last video was not finished</p>
            <p className="mt-1 [overflow-wrap:anywhere]">{job.error}</p>
            <p className="mt-2">
              Your script is saved. Choose a voice in the Voice section below and generate again. Scenes that
              were already voiced are reused when you keep the same voice.
            </p>
          </div>
        ) : null}
        <ScriptEditor
          key={`${job.id}-${job.script.title}-${job.script.scenes.length}-${job.script.hook}`}
          initial={job.script}
          research={job.research}
          targetSeconds={target}
          language={job.settings.language}
          gender={job.settings.voice}
          initialVoiceId={job.settings.voice_id}
          busy={busy}
          onRegenerate={retryScript}
          onGenerate={(script: Script, voiceId: string) =>
            run(async () => {
              await api.updateScript(job.id, script);
              return api.render(job.id, voiceId ? { voice_id: voiceId } : undefined);
            })
          }
        />
      </div>
    );
  }

  if (job.status === "rendering") {
    return (
      <div>
        {header}
        {pollErr}
        <RenderProgress step={job.step} progress={job.progress} stages={job.stages ?? []} stop={stop} />
      </div>
    );
  }

  if (job.status === "done") {
    return (
      <div>
        {header}
        <ResultView job={job} onNew={onNew} onRerender={rerender} />
      </div>
    );
  }

  return (
    <div>
      {header}
      {actionErr}
      <FailedCard job={job} busy={busy} onRetry={job.script ? doRender : retryScript} onNew={onNew} />
    </div>
  );
}
