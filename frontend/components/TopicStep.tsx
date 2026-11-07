"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Trend } from "@/lib/types";
import { Button, Card, EmptyState, ErrorBox, Skeleton, Spinner, inputCls } from "./ui";

export function TopicStep({
  creating,
  error,
  onBack,
  onSubmit,
}: {
  creating: boolean;
  error: string | null;
  onBack: () => void;
  onSubmit: (topic: string, trend?: { url?: string | null; angle?: string | null }) => void;
}) {
  const [trends, setTrends] = useState<Trend[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [custom, setCustom] = useState("");
  const groupId = useId();

  const load = useCallback(async (force: boolean) => {
    setRefreshing(true);
    try {
      const t = await api.trends(force);
      setTrends(t);
      setLoadError(null);
    } catch (e) {
      setLoadError(errorMessage(e));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(false);
  }, [load]);

  const customTrim = custom.trim();
  const topic = customTrim || selected || "";
  const pickedTrend = customTrim ? undefined : trends?.find((t) => t.title === selected);

  return (
    <div className="space-y-4">
      <Card className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 id={groupId} className="text-lg font-semibold">
            Trending topics
          </h2>
          <Button variant="secondary" disabled={refreshing} aria-busy={refreshing} onClick={() => load(true)}>
            {refreshing && <Spinner className="h-3.5 w-3.5" />}
            Refresh
          </Button>
        </div>
        <p role="status" aria-live="polite" className="sr-only">
          {refreshing ? "Loading trending topics" : trends ? `${trends.length} trending topics loaded` : ""}
        </p>

        {loadError ? (
          <ErrorBox message={loadError} title="Could not load trends" onRetry={() => load(false)} />
        ) : !trends ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-24" />
            ))}
          </div>
        ) : trends.length === 0 ? (
          <EmptyState title="No trends found">Try refresh, or enter a custom topic below.</EmptyState>
        ) : (
          <div role="radiogroup" aria-labelledby={groupId} className="grid gap-3 sm:grid-cols-2">
            {trends.map((t) => {
              const checked = selected === t.title && !customTrim;
              return (
                <label
                  key={t.title}
                  className={`ring-within relative block min-h-11 cursor-pointer rounded-xl border-2 p-3 pr-10 text-left transition-colors motion-reduce:transition-none ${
                    checked
                      ? "border-indigo-600 bg-indigo-50 dark:border-indigo-400 dark:bg-indigo-950/50"
                      : "border-edge-soft hover:bg-surface-muted"
                  }`}
                >
                  <input
                    type="radio"
                    name="trend"
                    className="peer sr-only"
                    checked={checked}
                    onChange={() => {
                      setSelected(t.title);
                      setCustom("");
                    }}
                  />
                  <span
                    aria-hidden
                    className="absolute right-3 top-3 hidden h-5 w-5 items-center justify-center rounded-full bg-indigo-600 text-white peer-checked:flex dark:bg-indigo-400 dark:text-zinc-950"
                  >
                    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2.5">
                      <path d="M3 8.5l3.2 3L13 4.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </span>
                  <span className="block font-medium leading-snug">{t.title}</span>
                  <span className="mt-1.5 flex flex-wrap gap-1.5 text-xs">
                    <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-zinc-800 dark:bg-zinc-700 dark:text-zinc-100">
                      {t.source.replace("_", " ")}
                    </span>
                    <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200">
                      {t.category}
                    </span>
                  </span>
                  {t.angle && <span className="mt-2 block text-xs text-zinc-600 dark:text-zinc-400">{t.angle}</span>}
                </label>
              );
            })}
          </div>
        )}
      </Card>

      <Card className="space-y-2">
        <label htmlFor="custom-topic" className="block text-sm font-medium">
          Or enter your own topic
        </label>
        <input
          id="custom-topic"
          className={inputCls}
          placeholder="e.g. Why the moon is drifting away from Earth"
          value={custom}
          aria-describedby={customTrim ? "custom-topic-note" : undefined}
          onChange={(e) => setCustom(e.target.value)}
        />
        {customTrim && (
          <p id="custom-topic-note" className="text-sm text-zinc-600 dark:text-zinc-400">
            Custom topic overrides the selected trend.
          </p>
        )}
      </Card>

      {error && <ErrorBox message={error} title="Could not start script generation" />}

      <div className="flex items-center justify-between gap-3">
        <Button variant="ghost" onClick={onBack} disabled={creating}>
          Back
        </Button>
        <Button disabled={!topic || creating} onClick={() => onSubmit(topic, pickedTrend ? { url: pickedTrend.url, angle: pickedTrend.angle } : undefined)}>
          {creating && <Spinner className="h-3.5 w-3.5" />}
          Generate script
        </Button>
      </div>
      {topic && (
        <p className="text-right text-sm text-zinc-600 dark:text-zinc-400">
          Topic: <span className="font-medium">{topic}</span>
        </p>
      )}
    </div>
  );
}
