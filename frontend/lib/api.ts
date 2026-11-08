import type {
  CreateJobRequest,
  Job,
  JobSettings,
  Language,
  ModelsResponse,
  RenderRequest,
  Script,
  Trend,
  UpdateScriptRequest,
  VoicePreviewRequest,
  VoicesResponse,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number = 0,
    public readonly unreachable = false,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function detailToString(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc)
            ? (d as { loc: unknown[] }).loc.slice(1).join(".")
            : "";
          return `${loc ? loc + ": " : ""}${(d as { msg: string }).msg}`;
        }
        return JSON.stringify(d);
      })
      .join("; ");
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      `Cannot reach the backend at ${API_URL}. Start FastAPI on :8000 (e.g. uvicorn app.main:app --port 8000).`,
      0,
      true,
    );
  }
  if (!res.ok) {
    let message = `Request failed (${res.status} ${res.statusText})`;
    try {
      const body = await res.json();
      const d = detailToString(body?.detail) ?? detailToString(body?.message);
      if (d) message = d;
    } catch {
      // non-JSON error body
    }
    throw new ApiError(message, res.status);
  }
  return (await res.json()) as T;
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

const jobPath = (id: string) => `/api/jobs/${encodeURIComponent(id)}`;

export const api = {
  models: () => request<ModelsResponse>("/api/models"),
  trends: (force = false) => request<Trend[]>(`/api/trends?force=${force}`),
  createJob: (topic: string, settings: JobSettings, trend?: { url?: string | null; angle?: string | null }) =>
    request<Job>(
      "/api/jobs",
      send("POST", {
        topic,
        ...(trend?.url ? { topic_url: trend.url } : {}),
        ...(trend?.angle ? { topic_angle: trend.angle } : {}),
        settings,
      } satisfies CreateJobRequest),
    ),
  getJob: (id: string) => request<Job>(jobPath(id)),
  listJobs: () => request<Job[]>("/api/jobs"),
  updateScript: (id: string, script: Script) =>
    request<Job>(`${jobPath(id)}/script`, send("PUT", { script } satisfies UpdateScriptRequest)),
  regenerateScript: (id: string) => request<Job>(`${jobPath(id)}/script/regenerate`, send("POST")),
  render: (id: string, body?: RenderRequest) =>
    request<Job>(`${jobPath(id)}/render`, send("POST", body)),
  cancelJob: (id: string) => request<Job>(`${jobPath(id)}/cancel`, send("POST")),
  getVoices: (language: Language) =>
    request<VoicesResponse>(`/api/voices?language=${encodeURIComponent(language)}`),
  previewVoice: async (voice_id: string, language: Language): Promise<Blob> => {
    let res: Response;
    try {
      res = await fetch(`${API_URL}/api/voices/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice_id, language } satisfies VoicePreviewRequest),
        cache: "no-store",
      });
    } catch {
      throw new ApiError(`Cannot reach the backend at ${API_URL}.`, 0, true);
    }
    if (!res.ok) {
      let message = `Preview failed (${res.status})`;
      try {
        const d = detailToString((await res.json())?.detail);
        if (d) message = d;
      } catch {
        // non-JSON error body
      }
      throw new ApiError(message, res.status);
    }
    return res.blob();
  },
  videoUrl: (id: string, download = false) =>
    `${API_URL}${jobPath(id)}/video${download ? "?download=1" : ""}`,
};

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
