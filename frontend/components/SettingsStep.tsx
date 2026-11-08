"use client";

import { useEffect, useId, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { DURATIONS } from "@/lib/settings";
import type { JobSettings, ModelsResponse, ProviderInfo, VideoFormat } from "@/lib/types";
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
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className={`inline-flex min-h-11 min-w-11 touch-manipulation cursor-pointer items-center justify-center gap-1.5 rounded-lg border px-4 py-2 text-sm transition-colors motion-reduce:transition-none ${
            o.value === value
              ? "border-indigo-600 bg-indigo-50 font-medium text-indigo-800 dark:border-indigo-400 dark:bg-indigo-950/50 dark:text-indigo-200"
              : "border-edge hover:bg-surface-muted"
          }`}
        >
          {o.value === value && <CheckIcon />}
          {o.label}
        </button>
      ))}
    </div>
  );
}

function CheckIcon() {
  return (
    <svg aria-hidden viewBox="0 0 16 16" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="2.5">
      <path d="M3 8.5l3.2 3L13 4.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  const labelId = useId();
  const cls = "mb-2 block text-sm font-medium text-zinc-700 dark:text-zinc-300";
  if (htmlFor) {
    return (
      <div>
        <label htmlFor={htmlFor} className={cls}>
          {label}
        </label>
        {children}
      </div>
    );
  }
  return (
    <div role="group" aria-labelledby={labelId}>
      <span id={labelId} className={cls}>
        {label}
      </span>
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
  const modelId = useId();
  const modelHelpId = useId();

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
      <Field label="Format">
        <div className="flex flex-wrap gap-3">
          {(["vertical", "horizontal"] as const).map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={settings.format === f}
              onClick={() => setFormat(f)}
              className={`flex min-h-11 flex-1 touch-manipulation cursor-pointer items-center gap-3 rounded-xl border p-3 text-left transition-colors motion-reduce:transition-none sm:w-44 sm:flex-none ${
                settings.format === f
                  ? "border-indigo-600 bg-indigo-50 text-indigo-800 dark:border-indigo-400 dark:bg-indigo-950/50 dark:text-indigo-200"
                  : "border-edge hover:bg-surface-muted"
              }`}
            >
              <span className="flex h-9 w-9 items-center justify-center">
                <FormatIcon format={f} />
              </span>
              <span className="text-sm">
                <span className="block font-medium">{f === "vertical" ? "Vertical" : "Horizontal"}</span>
                <span
                  className={`text-xs ${
                    settings.format === f ? "text-indigo-900 dark:text-indigo-200" : "text-zinc-600 dark:text-zinc-400"
                  }`}
                >
                  {f === "vertical" ? "9:16 Shorts" : "16:9 Video"}
                </span>
              </span>
              {settings.format === f && (
                <span className="ml-auto">
                  <CheckIcon />
                </span>
              )}
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

      <Field label="Text model" htmlFor={models && !error ? modelId : undefined}>
        {error ? (
          <ErrorBox message={error} title="Could not load models" onRetry={() => setAttempt((a) => a + 1)} />
        ) : !models ? (
          <Skeleton className="h-11 w-full" />
        ) : (
          <>
            <select
              id={modelId}
              className={inputCls}
              value={settings.model ?? models.default}
              onChange={(e) => set({ model: e.target.value })}
              aria-describedby={modelHelpId}
            >
              {models.providers.map((p) => (
                <optgroup key={p.id} label={providerGroupLabel(p)}>
                  {models.models.filter((m) => m.provider === p.id).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                      {m.id === models.default ? " (default)" : ""}
                    </option>
                  ))}
                  {!models.models.some((m) => m.provider === p.id) && (
                    <option disabled value={`__${p.id}`}>
                      {p.note || "No models available"}
                    </option>
                  )}
                </optgroup>
              ))}
            </select>
            <p id={modelHelpId} className="mt-1.5 text-sm text-zinc-600 dark:text-zinc-400">
              {modelHelp(models, settings.model ?? models.default)}
            </p>
          </>
        )}
      </Field>

      <div className="flex justify-end">
        <Button onClick={onNext}>Next: choose topic</Button>
      </div>
    </Card>
  );
}

function providerGroupLabel(p: ProviderInfo): string {
  if (!p.configured) return `${p.label} (not set up)`;
  if (!p.available) return `${p.label} (quota used up, others take over)`;
  return p.label;
}

function modelHelp(models: ModelsResponse, value: string): string {
  const picked = models.models.find((m) => m.id === value);
  if (!picked) return "";
  const others = models.providers.filter((p) => p.configured && p.id !== picked.provider).map((p) => p.label);
  if (!others.length) {
    return `${picked.provider_label} writes the script. No fallback is set up: add OLLAMA_API_KEY in .env to use Ollama cloud when this one fails.`;
  }
  return `${picked.provider_label} answers first. If it fails or runs out of quota, ${others.join(" or ")} takes over.`;
}
