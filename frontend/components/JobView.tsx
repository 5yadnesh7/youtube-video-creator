"use client";

import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import type { Job, JobStatus, Script } from "@/lib/types";
import { ResultView } from "./ResultView";
import { ScriptEditor } from "./ScriptEditor";
import { Button, Card, ErrorBox, Skeleton, Spinner, StatusBadge } from "./ui";

const active = (j: Job) => j.status === "scripting" || j.status === "rendering";

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

  if (loading) return <Skeleton className="h-64 w-full" />;
  if (!job) return <ErrorBox message={error ?? "Job not found"} onRetry={refresh} />;

  const target = job.settings.duration_seconds;
  const header = (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <h2 className="text-xl font-semibold">{job.script?.title || job.topic}</h2>
      <StatusBadge status={job.status} />
      <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-xs dark:bg-zinc-700">
        {job.settings.format === "vertical" ? "9:16" : "16:9"}
      </span>
    </div>
  );

  const pollErr = error && active(job) && (
    <p className="mb-3 text-sm text-amber-600 dark:text-amber-400">Connection issue, retrying: {error}</p>
  );
  const actionErr = actionError && <div className="mb-4"><ErrorBox message={actionError} /></div>;

  const retryScript = () => run(() => api.regenerateScript(job.id));
  const doRender = () => run(() => api.render(job.id));

  if (job.status === "scripting") {
    const researching = !/writing/i.test(job.step ?? "");
    const stageOn = "font-semibold text-indigo-600 dark:text-indigo-400";
    const stageOff = "text-zinc-500";
    return (
      <div>
        {header}
        {pollErr}
        <Card className="space-y-3">
          <div className="flex items-center gap-3">
            <Spinner />
            <p className="text-lg font-semibold">{job.step || "Researching topic"}</p>
          </div>
          <p className="text-sm">
            <span className={researching ? stageOn : stageOff}>1. Researching the topic</span>
            <span className="text-zinc-400"> → </span>
            <span className={researching ? stageOff : stageOn}>2. Writing the script</span>
          </p>
          <Skeleton className="h-10" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </Card>
      </div>
    );
  }

  if (job.status === "script_ready" && job.script) {
    return (
      <div>
        {header}
        {actionErr}
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
        <Card className="space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium">{job.step || "Rendering"}</span>
            <span className="tabular-nums">{job.progress}%</span>
          </div>
          <div
            className="h-3 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
            role="progressbar"
            aria-valuenow={job.progress}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full bg-indigo-600 transition-all duration-500"
              style={{ width: `${Math.max(2, job.progress)}%` }}
            />
          </div>
          <p className="text-xs text-zinc-500">This can take a few minutes. You can leave and come back from Videos.</p>
        </Card>
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
      <ErrorBox
        title={job.script ? "Rendering failed" : "Script generation failed"}
        message={job.error || "Unknown error"}
      />
      <div className="mt-4 flex gap-2">
        <Button disabled={busy} onClick={job.script ? doRender : retryScript}>
          {busy && <Spinner className="h-3.5 w-3.5" />}
          Retry
        </Button>
        {onNew && (
          <Button variant="secondary" onClick={onNew}>
            Start over
          </Button>
        )}
      </div>
    </div>
  );
}
