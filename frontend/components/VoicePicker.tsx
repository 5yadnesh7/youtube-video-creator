"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Language, VoiceGender, VoiceOption, VoicesResponse } from "@/lib/types";
import { Button, Skeleton } from "./ui";

const focusRing =
  "outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-indigo-400 dark:focus-visible:ring-offset-zinc-900";

const MAX_PREVIEW_MS = 5000;

type Filter = "all" | VoiceGender;
type Preview = { id: string; state: "loading" | "playing" } | null;

const svgProps = {
  viewBox: "0 0 24 24",
  fill: "currentColor",
  "aria-hidden": true,
  className: "h-4 w-4",
} as const;

const PlayIcon = () => (
  <svg {...svgProps}>
    <path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5Z" />
  </svg>
);
const StopIcon = () => (
  <svg {...svgProps}>
    <rect x="6" y="6" width="12" height="12" rx="2" />
  </svg>
);
const CheckIcon = () => (
  <svg {...svgProps} className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </svg>
);

export function VoicePicker({
  language,
  gender,
  value,
  onChange,
  onLoaded,
  disabled = false,
}: {
  language: Language;
  gender: VoiceGender;
  value: string;
  onChange: (voiceId: string) => void;
  onLoaded?: (r: VoicesResponse) => void;
  disabled?: boolean;
}) {
  const [data, setData] = useState<VoicesResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [chip, setChip] = useState<Filter | null>(null);
  const [preview, setPreview] = useState<Preview>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reqRef = useRef(0);
  const latest = useRef({ value, onChange, onLoaded });
  useEffect(() => {
    latest.current = { value, onChange, onLoaded };
  });
  const groupName = useId();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [scrollable, setScrollable] = useState(false);

  const stop = useCallback(() => {
    reqRef.current++;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
    const audio = audioRef.current;
    audioRef.current = null;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
    }
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
    setPreview(null);
  }, []);

  useEffect(() => stop, [stop]);

  useEffect(() => {
    let cancelled = false;
    api
      .getVoices(language)
      .then((r) => {
        if (cancelled) return;
        setData(r);
        setLoadError(null);
        const { value: v, onChange: change, onLoaded: loaded } = latest.current;
        if (!v || !r.voices.some((o) => o.id === v)) change(r.default_id);
        loaded?.(r);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [language, attempt]);

  const togglePreview = async (v: VoiceOption) => {
    if (preview?.id === v.id) return stop();
    stop();
    const req = reqRef.current;
    setErrors((e) => ({ ...e, [v.id]: "" }));
    setPreview({ id: v.id, state: "loading" });
    try {
      const blob = await api.previewVoice(v.id, language);
      if (req !== reqRef.current) return;
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => {
        if (audioRef.current === audio) stop();
      };
      audio.onerror = () => {
        if (audioRef.current !== audio) return;
        stop();
        setErrors((e) => ({ ...e, [v.id]: "Preview unavailable" }));
      };
      await audio.play();
      if (req !== reqRef.current) return;
      setPreview({ id: v.id, state: "playing" });
      timerRef.current = setTimeout(() => {
        if (audioRef.current === audio) stop();
      }, MAX_PREVIEW_MS);
    } catch {
      if (req !== reqRef.current) return;
      stop();
      setErrors((e) => ({ ...e, [v.id]: "Preview unavailable" }));
    }
  };

  const voices = data?.voices ?? [];
  const selectedGender = voices.find((o) => o.id === value)?.gender;
  const autoFilter: Filter = !selectedGender || selectedGender === gender ? gender : "all";
  const filter = chip ?? autoFilter;
  const shown = voices.filter((o) => filter === "all" || o.gender === filter);
  const groups = new Map<string, VoiceOption[]>();
  for (const o of shown) {
    const g = groups.get(o.provider_label) ?? [];
    g.push(o);
    groups.set(o.provider_label, g);
  }

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const check = () => setScrollable(el.scrollHeight > el.clientHeight + 1);
    check();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(check);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  });

  const status = loadError
    ? "Could not load voices."
    : !data
      ? "Loading voices..."
      : preview?.state === "loading"
        ? "Loading preview..."
        : preview?.state === "playing"
          ? "Playing preview"
          : Object.values(errors).some(Boolean)
            ? "Preview unavailable for a voice."
            : "";

  return (
    <div className="space-y-3">
      <p className="sr-only" role="status" aria-live="polite">
        {status}
      </p>
      {loadError ? (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          <p>Could not load voices: {loadError}</p>
          <Button
            variant="secondary"
            className="mt-2"
            onClick={() => {
              setLoadError(null);
              setAttempt((a) => a + 1);
            }}
          >
            Retry
          </Button>
        </div>
      ) : !data ? (
        <div className="space-y-2" aria-hidden>
          <Skeleton className="h-8 w-48" />
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-11 w-full" />
          ))}
          <span className="text-xs text-zinc-600 dark:text-zinc-400">Loading voices...</span>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Filter voices by gender">
            {(["all", "male", "female"] as const).map((f) => (
              <button
                key={f}
                type="button"
                aria-pressed={filter === f}
                onClick={() => setChip(f)}
                className={`min-h-11 min-w-11 rounded-full border px-4 text-sm font-medium capitalize transition-colors motion-reduce:transition-none ${focusRing} ${
                  filter === f
                    ? "border-indigo-600 bg-indigo-600 text-white"
                    : "border-zinc-400 text-zinc-800 hover:bg-zinc-100 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-800"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
          <div
            ref={scrollRef}
            tabIndex={scrollable ? 0 : undefined}
            role={scrollable ? "region" : undefined}
            aria-label={scrollable ? "Voices" : undefined}
            className={`rounded-lg sm:max-h-80 sm:overflow-y-auto sm:p-1 ${scrollable ? focusRing : ""}`}
          >
          <div role="radiogroup" aria-label="Voice" className="space-y-3">
            {groups.size === 0 && <p className="text-sm text-zinc-600 dark:text-zinc-400">No voices match this filter.</p>}
            {[...groups].map(([label, list]) => (
              <div key={label} className="space-y-2">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-400">{label}</h3>
                {list.some((o) => o.experimental) && (
                  <p className="text-xs text-amber-800 dark:text-amber-300">
                    Experimental voices: Hindi support not verified for these voices
                  </p>
                )}
                {list.map((o) => {
                  const selected = o.id === value;
                  const p = preview?.id === o.id ? preview : null;
                  const err = errors[o.id];
                  return (
                    <div
                      key={o.id}
                      onClick={() => !disabled && onChange(o.id)}
                      className={`flex min-h-11 items-center gap-2 rounded-lg border px-2 transition-colors motion-reduce:transition-none has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-indigo-500 has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-white dark:has-[:focus-visible]:ring-indigo-400 dark:has-[:focus-visible]:ring-offset-zinc-900 ${
                        selected
                          ? "border-indigo-600 bg-indigo-50 dark:border-indigo-400 dark:bg-indigo-950/40"
                          : "border-zinc-400 hover:bg-zinc-50 dark:border-zinc-600 dark:hover:bg-zinc-800/60"
                      } ${disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
                    >
                      <button
                        type="button"
                        disabled={disabled}
                        aria-label={p ? "Stop preview" : `Preview voice ${o.name}`}
                        aria-pressed={p?.state === "playing"}
                        aria-busy={p?.state === "loading"}
                        onClick={(e) => {
                          e.stopPropagation();
                          void togglePreview(o);
                        }}
                        className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full disabled:cursor-not-allowed ${focusRing}`}
                      >
                        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-600 text-white">
                          {p?.state === "loading" ? (
                            <span
                              className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent motion-reduce:animate-none"
                              aria-hidden
                            />
                          ) : p ? (
                            <StopIcon />
                          ) : (
                            <PlayIcon />
                          )}
                        </span>
                      </button>
                      <label className="flex min-w-0 flex-1 cursor-[inherit] items-center gap-2 py-2">
                        <input
                          type="radio"
                          name={groupName}
                          value={o.id}
                          checked={selected}
                          disabled={disabled}
                          onChange={() => onChange(o.id)}
                          className="sr-only"
                        />
                        <span className="truncate text-sm font-medium">{o.name}</span>
                        <span className="shrink-0 rounded bg-zinc-200 px-1.5 py-0.5 text-xs capitalize text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200">
                          {o.gender}
                        </span>
                        {o.experimental && (
                          <span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800 dark:bg-amber-900/50 dark:text-amber-300">
                            Experimental
                          </span>
                        )}
                      </label>
                      {err ? (
                        <span role="alert" className="max-w-24 shrink-0 text-right text-xs font-medium text-red-700 dark:text-red-400">
                          {err}
                        </span>
                      ) : (
                        <span className="w-16 shrink-0 text-right text-xs text-zinc-600 dark:text-zinc-400" aria-hidden>
                          {p?.state === "loading" ? "Loading..." : p ? "Playing" : null}
                        </span>
                      )}
                      <span
                        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 ${
                          selected
                            ? "border-indigo-600 bg-indigo-600 text-white dark:border-indigo-400 dark:bg-indigo-500"
                            : "border-zinc-500 dark:border-zinc-400"
                        }`}
                        aria-hidden
                      >
                        {selected && <CheckIcon />}
                      </span>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
          </div>
        </>
      )}
    </div>
  );
}
