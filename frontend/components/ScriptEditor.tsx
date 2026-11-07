"use client";

import { useState } from "react";
import { formatDuration } from "@/lib/settings";
import type { Language, Research, Scene, Script, VoiceGender } from "@/lib/types";
import { ResearchPanel } from "./ResearchPanel";
import { Button, Card, Spinner, inputCls } from "./ui";
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

  const words = scriptWords(script);
  const est = words / WORDS_PER_SECOND;
  const targetWords = Math.round(targetSeconds * WORDS_PER_SECOND);
  const ratio = est / targetSeconds;
  const tone =
    ratio > 1.15 || ratio < 0.8
      ? "text-amber-600 dark:text-amber-400"
      : "text-emerald-600 dark:text-emerald-400";

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

  return (
    <div className="space-y-4">
      <Card className="sticky top-14 z-10 flex flex-wrap items-center justify-between gap-2 text-sm">
        <span>
          <strong>{words}</strong> words / ~{targetWords} target
        </span>
        <span className={tone}>
          Est. spoken {formatDuration(est)} vs target {formatDuration(targetSeconds)}
        </span>
      </Card>

      <ResearchPanel research={research} />

      <Card className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-zinc-500">Title</label>
          <input
            className={inputCls}
            value={script.title}
            onChange={(e) => setScript({ ...script, title: e.target.value })}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-zinc-500">Hook</label>
          <textarea
            className={inputCls}
            rows={2}
            value={script.hook}
            onChange={(e) => setScript({ ...script, hook: e.target.value })}
          />
        </div>
      </Card>

      {script.scenes.map((sc, i) => (
        <Card key={i} className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold">Scene {i + 1}</span>
            <div className="flex gap-1">
              <Button variant="ghost" className="!px-2 !py-1" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">
                ↑
              </Button>
              <Button
                variant="ghost"
                className="!px-2 !py-1"
                disabled={i === script.scenes.length - 1}
                onClick={() => move(i, 1)}
                aria-label="Move down"
              >
                ↓
              </Button>
              <Button variant="danger" className="!px-2 !py-1" onClick={() => remove(i)} aria-label="Delete scene">
                Delete
              </Button>
            </div>
          </div>
          <div>
            <label className="mb-1 flex justify-between text-xs font-medium text-zinc-500">
              <span>Narration</span>
              <span>{countWords(sc.narration)} words</span>
            </label>
            <textarea
              className={inputCls}
              rows={3}
              value={sc.narration}
              onChange={(e) => patchScene(i, { narration: e.target.value })}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-[1fr_auto_10rem]">
            <div>
              <label className="mb-1 block text-xs font-medium text-zinc-500">Visual search query (English)</label>
              <input
                className={inputCls}
                value={sc.visual_query}
                onChange={(e) => patchScene(i, { visual_query: e.target.value })}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-zinc-500">Media</label>
              <div className="flex overflow-hidden rounded-lg border border-zinc-300 text-sm dark:border-zinc-700">
                {(["video", "image"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => patchScene(i, { media_type: m })}
                    className={`px-3 py-2 ${
                      sc.media_type === m ? "bg-indigo-600 text-white" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
                    }`}
                  >
                    {m}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-zinc-500">Mood</label>
              <input
                className={inputCls}
                value={sc.mood}
                onChange={(e) => patchScene(i, { mood: e.target.value })}
              />
            </div>
          </div>
        </Card>
      ))}

      <Button variant="secondary" onClick={add} className="w-full">
        + Add scene
      </Button>

      <Card className="space-y-3">
        <h3 className="text-sm font-semibold">Voice</h3>
        <VoicePicker language={language} gender={gender} value={voiceId} onChange={setVoiceId} disabled={busy} />
      </Card>

      <div className="flex flex-wrap justify-between gap-3">
        <Button variant="secondary" disabled={busy} onClick={onRegenerate}>
          Regenerate script
        </Button>
        <Button
          disabled={busy || !valid}
          onClick={() => {
            if (voiceId) saveVoice(language, voiceId);
            onGenerate(script, voiceId);
          }}
        >
          {busy && <Spinner className="h-3.5 w-3.5" />}
          Generate video
        </Button>
      </div>
    </div>
  );
}
