"use client";

import { useCallback, useEffect, useState } from "react";
import { JobView } from "@/components/JobView";
import { SettingsStep } from "@/components/SettingsStep";
import { TopicStep } from "@/components/TopicStep";
import { api, errorMessage } from "@/lib/api";
import { DEFAULT_SETTINGS, loadSettings, saveSettings } from "@/lib/settings";
import type { JobSettings, JobStatus } from "@/lib/types";

const STEPS = ["Settings", "Topic", "Script", "Render", "Result"];

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

  return (
    <div className="space-y-6">
      <ol className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {STEPS.map((s, i) => (
          <li
            key={s}
            className={
              i === current
                ? "font-semibold text-indigo-600 dark:text-indigo-400"
                : i < current
                  ? "text-zinc-700 dark:text-zinc-300"
                  : "text-zinc-400"
            }
          >
            {i + 1}. {s}
          </li>
        ))}
      </ol>

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
    [onStage],
  );
  return <JobView jobId={jobId} onNew={onNew} onStatusChange={handleStatus} />;
}
