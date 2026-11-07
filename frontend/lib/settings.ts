import type { JobSettings, VideoFormat } from "./types";

const KEY = "yvc.settings.v1";

export const DEFAULT_SETTINGS: JobSettings = {
  format: "vertical",
  duration_seconds: 60,
  language: "english",
  voice: "male",
  model: null,
};

export const DURATIONS: Record<VideoFormat, { value: number; label: string }[]> = {
  vertical: [
    { value: 30, label: "30 sec" },
    { value: 45, label: "45 sec" },
    { value: 60, label: "60 sec" },
  ],
  horizontal: [
    { value: 60, label: "1 min" },
    { value: 180, label: "3 min" },
    { value: 300, label: "5 min" },
    { value: 600, label: "10 min" },
  ],
};

export function loadSettings(): JobSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const s = { ...DEFAULT_SETTINGS, ...JSON.parse(raw) } as JobSettings;
      if (!DURATIONS[s.format]?.some((d) => d.value === s.duration_seconds)) {
        s.format = DEFAULT_SETTINGS.format;
        s.duration_seconds = 60;
      }
      return s;
    }
  } catch {
    // storage unavailable or corrupt
  }
  return DEFAULT_SETTINGS;
}

export function saveSettings(s: JobSettings) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    // ignore
  }
}

export function formatDuration(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return s ? `${m}m ${s}s` : `${m}m`;
}
