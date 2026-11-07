"use client";

import { useCallback, useEffect, useState } from "react";
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
          <h2 className="text-lg font-semibold">Trending topics</h2>
          <Button variant="secondary" disabled={refreshing} onClick={() => load(true)}>
            {refreshing && <Spinner className="h-3.5 w-3.5" />}
            Refresh
          </Button>
        </div>

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
          <div className={`grid gap-3 sm:grid-cols-2 ${customTrim ? "opacity-50" : ""}`}>
            {trends.map((t) => (
              <button
                key={t.title}
                type="button"
                onClick={() => {
                  setSelected(t.title);
                  setCustom("");
                }}
                className={`rounded-xl border p-3 text-left transition-colors ${
                  selected === t.title && !customTrim
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/50"
                    : "border-zinc-200 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800/60"
                }`}
              >
                <p className="font-medium leading-snug">{t.title}</p>
                <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs">
                  <span className="rounded bg-zinc-200 px-1.5 py-0.5 dark:bg-zinc-700">
                    {t.source.replace("_", " ")}
                  </span>
                  <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
                    {t.category}
                  </span>
                </div>
                {t.angle && <p className="mt-2 text-xs text-zinc-500">{t.angle}</p>}
              </button>
            ))}
          </div>
        )}
      </Card>

      <Card className="space-y-2">
        <label htmlFor="custom-topic" className="text-sm font-medium">
          Or enter your own topic
        </label>
        <input
          id="custom-topic"
          className={inputCls}
          placeholder="e.g. Why the moon is drifting away from Earth"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
        />
        {customTrim && <p className="text-xs text-zinc-500">Custom topic overrides the selected trend.</p>}
      </Card>

      {error && <ErrorBox message={error} title="Could not start script generation" />}

      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={onBack} disabled={creating}>
          Back
        </Button>
        <Button disabled={!topic || creating} onClick={() => onSubmit(topic, pickedTrend ? { url: pickedTrend.url, angle: pickedTrend.angle } : undefined)}>
          {creating && <Spinner className="h-3.5 w-3.5" />}
          Generate script
        </Button>
      </div>
      {topic && (
        <p className="text-right text-xs text-zinc-500">
          Topic: <span className="font-medium">{topic}</span>
        </p>
      )}
    </div>
  );
}
