"use client";

import { useEffect, useId, useRef, useState } from "react";
import { formatDuration } from "@/lib/settings";
import type { Language, Research, Scene, Script, VoiceGender } from "@/lib/types";
import { ResearchPanel } from "./ResearchPanel";
import { Button, Card, Spinner } from "./ui";
import { VoicePicker } from "./VoicePicker";

const voiceKey = (language: Language) => `ytc:voice:${language}`;

function loadSavedVoice(language: Language): string {
  try {
    return localStorage.getItem(voiceKey(language)) ?? "";
  } catch {
    return "";
  }
}

function saveVoice(language: Language, id: string) {
  try {
    localStorage.setItem(voiceKey(language), id);
  } catch {
    // storage unavailable
  }
}

const WORDS_PER_SECOND = 2.5;

export function countWords(s: string): number {
  return s.trim() ? s.trim().split(/\s+/).length : 0;
}

export function scriptWords(script: Script): number {
  return countWords(script.hook) + script.scenes.reduce((n, s) => n + countWords(s.narration), 0);
}

const focusRing =
  "outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-indigo-400 dark:focus-visible:ring-offset-zinc-900";
const fieldCls =
  "block w-full min-h-11 rounded-lg border border-zinc-400 bg-white px-3 py-2 text-base text-zinc-900 outline-none focus-visible:border-indigo-600 focus-visible:ring-2 focus-visible:ring-indigo-500 dark:border-zinc-600 dark:bg-zinc-950 dark:text-zinc-100 dark:focus-visible:ring-indigo-400 sm:text-sm";
const labelCls = "mb-1 block text-xs font-medium text-zinc-600 dark:text-zinc-400";
const btnCls = `min-h-11 motion-reduce:transition-none ${focusRing}`;

const iconProps = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
  className: "h-4 w-4 shrink-0",
} as const;

const WarnIcon = () => (
  <svg {...iconProps}>
    <path d="M12 3 2 20h20L12 3Z" />
    <path d="M12 10v4M12 17.5v.01" />
  </svg>
);
const OkIcon = () => (
  <svg {...iconProps}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </svg>
);

function SceneCard({
  scene,
  index,
  total,
  onPatch,
  onMove,
  onRemove,
}: {
  scene: Scene;
  index: number;
  total: number;
  onPatch: (patch: Partial<Scene>) => void;
  onMove: (d: -1 | 1) => void;
  onRemove: () => void;
}) {
  const uid = useId();
  const n = index + 1;
  const narrationId = `${uid}-narration`;
  const queryId = `${uid}-query`;
  const moodId = `${uid}-mood`;
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Scene {n}</h2>
        <div className="flex gap-2">
          <Button
            variant="ghost"
            className={`${btnCls} min-w-11 !px-2`}
            disabled={index === 0}
            onClick={() => onMove(-1)}
            aria-label={`Move scene ${n} up`}
          >
            <span aria-hidden>↑</span>
          </Button>
          <Button
            variant="ghost"
            className={`${btnCls} min-w-11 !px-2`}
            disabled={index === total - 1}
            onClick={() => onMove(1)}
            aria-label={`Move scene ${n} down`}
          >
            <span aria-hidden>↓</span>
          </Button>
          <Button
            variant="danger"
            className={`${btnCls} min-w-11 !px-3 !text-red-700 dark:!text-red-400`}
            onClick={onRemove}
            aria-label={`Delete scene ${n}`}
          >
            Delete
          </Button>
        </div>
      </div>
      <div>
        <div className="mb-1 flex justify-between gap-2 text-xs font-medium text-zinc-600 dark:text-zinc-400">
          <label htmlFor={narrationId}>Scene {n} narration</label>
          <span className="tabular-nums">{countWords(scene.narration)} words</span>
        </div>
        <textarea
          id={narrationId}
          className={fieldCls}
          rows={3}
          value={scene.narration}
          onChange={(e) => onPatch({ narration: e.target.value })}
        />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto_8rem]">
        <div>
          <label htmlFor={queryId} className={labelCls}>
            Scene {n} visual search query (English)
          </label>
          <input
            id={queryId}
            className={fieldCls}
            value={scene.visual_query}
            onChange={(e) => onPatch({ visual_query: e.target.value })}
          />
        </div>
        <div>
          <span className={labelCls} aria-hidden>
            Media
          </span>
          <div
            role="radiogroup"
            aria-label={`Scene ${n} media type`}
            className="flex min-h-11 overflow-hidden rounded-lg border border-zinc-400 text-sm dark:border-zinc-600"
          >
            {(["video", "image"] as const).map((m) => (
              <label
                key={m}
                className={`flex min-h-11 min-w-11 flex-1 cursor-pointer items-center justify-center px-4 capitalize transition-colors motion-reduce:transition-none has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-inset has-[:focus-visible]:ring-indigo-500 dark:has-[:focus-visible]:ring-indigo-400 ${
                  scene.media_type === m
                    ? "bg-indigo-600 text-white"
                    : "text-zinc-800 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
                }`}
              >
                <input
                  type="radio"
                  name={`${uid}-media`}
                  value={m}
                  checked={scene.media_type === m}
                  onChange={() => onPatch({ media_type: m })}
                  className="sr-only"
                />
                {m}
              </label>
            ))}
          </div>
        </div>
        <div>
          <label htmlFor={moodId} className={labelCls}>
            Scene {n} mood
          </label>
          <input
            id={moodId}
            className={fieldCls}
            value={scene.mood}
            onChange={(e) => onPatch({ mood: e.target.value })}
          />
        </div>
      </div>
    </Card>
  );
}

export function ScriptEditor({
  initial,
  research,
  targetSeconds,
  language,
  gender,
  initialVoiceId,
  busy,
  onRegenerate,
  onGenerate,
}: {
  initial: Script;
  research?: Research | null;
  targetSeconds: number;
  language: Language;
  gender: VoiceGender;
  initialVoiceId?: string | null;
  busy: boolean;
  onRegenerate: () => void;
  onGenerate: (script: Script, voiceId: string) => void;
}) {
  const [script, setScript] = useState<Script>(initial);
  const [voiceId, setVoiceId] = useState(() => initialVoiceId || loadSavedVoice(language));
  const [confirming, setConfirming] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const regenRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const hookId = useId();

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  const words = scriptWords(script);
  const est = words / WORDS_PER_SECOND;
  const targetWords = Math.round(targetSeconds * WORDS_PER_SECOND);
  const ratio = est / targetSeconds;
  const offTarget = ratio > 1.15 || ratio < 0.8;
  const delta = Math.abs(targetWords - words);
  const dirty = JSON.stringify(script) !== JSON.stringify(initial);

  const summary = `${words} words, about ${formatDuration(est)} spoken. ${
    offTarget
      ? ratio < 0.8
        ? `Add about ${delta} words to reach the target.`
        : `Remove about ${delta} words to reach the target.`
      : "On target."
  }`;
  const [announced, setAnnounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setAnnounced(summary), 1200);
    return () => clearTimeout(t);
  }, [summary]);

  const patchScene = (i: number, patch: Partial<Scene>) =>
    setScript((s) => ({ ...s, scenes: s.scenes.map((sc, j) => (j === i ? { ...sc, ...patch } : sc)) }));
  const move = (i: number, d: -1 | 1) =>
    setScript((s) => {
      const scenes = [...s.scenes];
      [scenes[i], scenes[i + d]] = [scenes[i + d], scenes[i]];
      return { ...s, scenes };
    });
  const remove = (i: number) =>
    setScript((s) => ({ ...s, scenes: s.scenes.filter((_, j) => j !== i) }));
  const add = () =>
    setScript((s) => ({
      ...s,
      scenes: [...s.scenes, { narration: "", visual_query: "", media_type: "video", mood: "neutral" }],
    }));

  const valid = script.scenes.length > 0 && script.scenes.every((s) => s.narration.trim());

  const cancelConfirm = () => {
    setConfirming(false);
    requestAnimationFrame(() => regenRef.current?.focus());
  };

  return (
    <div className="space-y-4 pb-2">
      <Card className="sticky top-14 z-10 text-sm">
        <p role="status" className="sr-only">
          {announced}
        </p>
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 tabular-nums">
          <span>
            <strong>{words}</strong> words · about {formatDuration(est)} spoken · target{" "}
            {formatDuration(targetSeconds)}
          </span>
          {offTarget ? (
            <span className="inline-flex items-center gap-1.5 font-medium text-amber-700 dark:text-amber-400">
              <WarnIcon />
              {ratio < 0.8
                ? `Add about ${delta} words to reach the target`
                : `Remove about ${delta} words to reach the target`}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 font-medium text-emerald-700 dark:text-emerald-400">
              <OkIcon />
              On target
            </span>
          )}
        </div>
      </Card>

      <ResearchPanel research={research} />

      <Card className="space-y-3">
        <div>
          <label htmlFor={titleId} className={labelCls}>
            Title
          </label>
          <input
            id={titleId}
            className={fieldCls}
            value={script.title}
            onChange={(e) => setScript({ ...script, title: e.target.value })}
          />
        </div>
        <div>
          <label htmlFor={hookId} className={labelCls}>
            Hook
          </label>
          <textarea
            id={hookId}
            className={fieldCls}
            rows={2}
            value={script.hook}
            onChange={(e) => setScript({ ...script, hook: e.target.value })}
          />
        </div>
      </Card>

      {script.scenes.map((sc, i) => (
        <SceneCard
          key={i}
          scene={sc}
          index={i}
          total={script.scenes.length}
          onPatch={(p) => patchScene(i, p)}
          onMove={(d) => move(i, d)}
          onRemove={() => remove(i)}
        />
      ))}

      <Button variant="secondary" onClick={add} className={`${btnCls} w-full`}>
        + Add scene
      </Button>

      <Card className="space-y-3">
        <h2 className="text-base font-semibold">Voice</h2>
        <VoicePicker language={language} gender={gender} value={voiceId} onChange={setVoiceId} disabled={busy} />
      </Card>

      <div className="sticky bottom-0 z-20 -mx-1 border-t border-zinc-300 bg-white px-1 pb-[env(safe-area-inset-bottom)] pt-3 dark:border-zinc-700 dark:bg-zinc-950 sm:static sm:mx-0 sm:border-0 sm:bg-transparent sm:px-0 sm:pt-0 sm:dark:bg-transparent">
        {confirming ? (
          <div role="group" aria-label="Confirm regenerate" className="space-y-2 pb-3 sm:pb-0">
            <p className="text-sm font-medium">Replace your edits with a new script?</p>
            <div className="flex flex-wrap gap-3">
              <button
                ref={confirmRef}
                type="button"
                disabled={busy}
                onClick={() => {
                  setConfirming(false);
                  onRegenerate();
                }}
                className={`inline-flex min-h-11 flex-1 items-center justify-center rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-60 motion-reduce:transition-none sm:flex-none ${focusRing}`}
              >
                Yes, regenerate
              </button>
              <Button variant="secondary" className={`${btnCls} flex-1 sm:flex-none`} onClick={cancelConfirm}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3 pb-3 sm:justify-between sm:pb-0">
            <button
              ref={regenRef}
              type="button"
              disabled={busy}
              onClick={() => (dirty ? setConfirming(true) : onRegenerate())}
              className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-zinc-400 bg-white px-4 py-2 text-sm font-medium text-zinc-800 transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-60 motion-reduce:transition-none dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800 ${focusRing}`}
            >
              Regenerate script
            </button>
            <Button
              className={`${btnCls} flex-1 sm:flex-none`}
              disabled={busy || !valid}
              onClick={() => {
                if (voiceId) saveVoice(language, voiceId);
                onGenerate(script, voiceId);
              }}
            >
              {busy && <Spinner className="h-3.5 w-3.5 motion-reduce:animate-none" />}
              Generate video
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
