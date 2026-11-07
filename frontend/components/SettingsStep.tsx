"use client";

import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { DURATIONS } from "@/lib/settings";
import type { JobSettings, ModelsResponse, VideoFormat } from "@/lib/types";
import { Button, Card, ErrorBox, Skeleton, inputCls } from "./ui";

function FormatIcon({ format }: { format: VideoFormat }) {
  return format === "vertical" ? (
    <span className="block h-8 w-[18px] rounded-sm border-2 border-current" />
  ) : (
    <span className="block h-[18px] w-8 rounded-sm border-2 border-current" />
  );
}

function Segmented<T extends string | number>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          onClick={() => onChange(o.value)}
          className={`rounded-lg border px-3.5 py-2 text-sm transition-colors ${
            o.value === value
              ? "border-indigo-500 bg-indigo-50 font-medium text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300"
              : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-2 block text-sm font-medium text-zinc-600 dark:text-zinc-400">
        {label}
      </label>
      {children}
    </div>
  );
}

export function SettingsStep({
  settings,
  onChange,
  onNext,
}: {
  settings: JobSettings;
  onChange: (s: JobSettings) => void;
  onNext: () => void;
}) {
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .models()
      .then((m) => {
        if (cancelled) return;
        setModels(m);
        setError(null);
        const valid = settings.model && m.models.some((x) => x.id === settings.model);
        if (!valid) onChange({ ...settings, model: m.default });
      })
      .catch((e) => !cancelled && setError(errorMessage(e)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  const set = (patch: Partial<JobSettings>) => onChange({ ...settings, ...patch });

  const setFormat = (format: VideoFormat) => {
    const opts = DURATIONS[format];
    const keep = opts.some((d) => d.value === settings.duration_seconds);
    set({ format, duration_seconds: keep ? settings.duration_seconds : 60 });
  };

  return (
    <Card className="space-y-6">
      <h2 className="text-lg font-semibold">Video settings</h2>

      <Field label="Format">
        <div className="flex gap-3">
          {(["vertical", "horizontal"] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFormat(f)}
              className={`flex w-40 items-center gap-3 rounded-xl border p-3 text-left transition-colors ${
                settings.format === f
                  ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300"
                  : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
              }`}
            >
              <span className="flex h-9 w-9 items-center justify-center">
                <FormatIcon format={f} />
              </span>
              <span className="text-sm">
                <span className="block font-medium">{f === "vertical" ? "Vertical" : "Horizontal"}</span>
                <span className="text-xs opacity-70">{f === "vertical" ? "9:16 Shorts" : "16:9 Video"}</span>
              </span>
            </button>
          ))}
        </div>
      </Field>

      <Field label="Duration">
        <Segmented
          value={settings.duration_seconds}
          options={DURATIONS[settings.format]}
          onChange={(v) => set({ duration_seconds: v })}
        />
      </Field>

      <div className="grid gap-6 sm:grid-cols-2">
        <Field label="Language">
          <Segmented
            value={settings.language}
            options={[
              { value: "english", label: "English" },
              { value: "hinglish", label: "Hinglish" },
            ]}
            onChange={(v) => set({ language: v })}
          />
        </Field>
        <Field label="Voice">
          <Segmented
            value={settings.voice}
            options={[
              { value: "male", label: "Male" },
              { value: "female", label: "Female" },
            ]}
            onChange={(v) => set({ voice: v })}
          />
        </Field>
      </div>

      <Field label="Text model">
        {error ? (
          <ErrorBox message={error} title="Could not load models" onRetry={() => setAttempt((a) => a + 1)} />
        ) : !models ? (
          <Skeleton className="h-10 w-full" />
        ) : (
          <select
            className={inputCls}
            value={settings.model ?? models.default}
            onChange={(e) => set({ model: e.target.value })}
          >
            {models.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
                {m.id === models.default ? " (default)" : ""}
              </option>
            ))}
          </select>
        )}
      </Field>

      <div className="flex justify-end">
        <Button onClick={onNext}>Next: choose topic</Button>
      </div>
    </Card>
  );
}
