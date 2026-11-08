"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { JobView } from "@/components/JobView";
import { SettingsStep } from "@/components/SettingsStep";
import { TopicStep } from "@/components/TopicStep";
import { api, errorMessage } from "@/lib/api";
import { DEFAULT_SETTINGS, loadSettings, saveSettings } from "@/lib/settings";
import type { JobSettings, JobStatus } from "@/lib/types";

const STEPS = ["Settings", "Topic", "Script", "Render", "Result"];
const JOB_TITLES: Record<number, string> = { 2: "Script", 3: "Rendering video", 4: "Your video" };

export default function CreatePage() {
  const [settings, setSettings] = useState<JobSettings>(DEFAULT_SETTINGS);
  const [hydrated, setHydrated] = useState(false);
  const [step, setStep] = useState<"settings" | "topic">("settings");
  const [jobId, setJobId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState(2);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSettings(loadSettings());
    setHydrated(true);
  }, []);

  const update = (s: JobSettings) => {
    setSettings(s);
    saveSettings(s);
  };

  const start = async (topic: string, trend?: { url?: string | null; angle?: string | null }) => {
    setCreating(true);
    setError(null);
    try {
      const job = await api.createJob(topic, settings, trend);
      setJobId(job.id);
      setStage(2);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setCreating(false);
    }
  };

  const reset = () => {
    setJobId(null);
    setStep("settings");
    setError(null);
  };

  const current = jobId ? stage : step === "settings" ? 0 : 1;

  const titleRef = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    titleRef.current?.focus();
  }, [step, jobId]);

  const title = jobId ? JOB_TITLES[current] ?? STEPS[current] : step === "settings" ? "Video settings" : "Choose a topic";

  return (
    <div className="space-y-6">
      <div className="sm:hidden">
        <p className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
          Step {current + 1} of {STEPS.length} · {STEPS[current]}
        </p>
        <div
          role="progressbar"
          aria-label="Wizard progress"
          aria-valuemin={1}
          aria-valuemax={STEPS.length}
          aria-valuenow={current + 1}
          aria-valuetext={`Step ${current + 1} of ${STEPS.length}: ${STEPS[current]}`}
          className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
        >
          <div
            className="h-full rounded-full bg-indigo-600 transition-[width] motion-reduce:transition-none dark:bg-indigo-400"
            style={{ width: `${((current + 1) / STEPS.length) * 100}%` }}
          />
        </div>
      </div>
      <ol aria-label="Progress" className="hidden flex-wrap gap-x-4 gap-y-1 text-sm sm:flex">
        {STEPS.map((s, i) => (
          <li
            key={s}
            aria-current={i === current ? "step" : undefined}
            className={
              i === current
                ? "font-semibold text-indigo-700 underline decoration-2 underline-offset-4 dark:text-indigo-300"
                : i < current
                  ? "text-zinc-700 dark:text-zinc-300"
                  : "text-zinc-600 dark:text-zinc-400"
            }
          >
            {i < current && <span aria-hidden>✓ </span>}
            {i + 1}. {s}
            {i < current && <span className="sr-only"> (completed)</span>}
          </li>
        ))}
      </ol>

      <h1 ref={titleRef} tabIndex={-1} className="text-xl font-semibold outline-none">
        {title}
      </h1>

      {jobId ? (
        <JobViewWithStage jobId={jobId} onNew={reset} onStage={setStage} />
      ) : !hydrated ? null : step === "settings" ? (
        <SettingsStep settings={settings} onChange={update} onNext={() => setStep("topic")} />
      ) : (
        <TopicStep
          creating={creating}
          error={error}
          onBack={() => setStep("settings")}
          onSubmit={start}
        />
      )}
    </div>
  );
}

function JobViewWithStage({
  jobId,
  onNew,
  onStage,
}: {
  jobId: string;
  onNew: () => void;
  onStage: (n: number) => void;
}) {
  const handleStatus = useCallback(
    (s: JobStatus) => onStage(s === "rendering" ? 3 : s === "done" ? 4 : 2),
    // cancelled/failed/script_ready all stay on the Script step
    [onStage],
  );
  return <JobView jobId={jobId} onNew={onNew} onStatusChange={handleStatus} />;
}
