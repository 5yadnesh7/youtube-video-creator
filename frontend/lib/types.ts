export type VideoFormat = "vertical" | "horizontal";
export type Language = "english" | "hinglish";
export type VoiceGender = "male" | "female";
export type MediaType = "video" | "image";
export type JobStatus = "scripting" | "script_ready" | "rendering" | "done" | "failed";

export interface JobSettings {
  format: VideoFormat;
  duration_seconds: number;
  language: Language;
  voice: VoiceGender;
  model?: string | null;
  voice_id?: string | null;
}

export interface ModelInfo {
  id: string;
  name: string;
  context_length: number;
}

export interface ModelsResponse {
  models: ModelInfo[];
  default: string;
}

export interface Trend {
  title: string;
  source: string;
  category: string;
  angle: string;
  url?: string | null;
}

export interface Scene {
  narration: string;
  visual_query: string;
  media_type: MediaType;
  mood: string;
}

export interface Script {
  title: string;
  hook: string;
  scenes: Scene[];
}

export interface Chapter {
  start_seconds: number;
  title: string;
}

export interface Seo {
  title: string;
  description: string;
  tags: string[];
  hashtags: string[];
  chapters: Chapter[];
}

export interface CreateJobRequest {
  topic: string;
  topic_url?: string | null;
  topic_angle?: string | null;
  settings: JobSettings;
}

export interface ResearchSource {
  title: string;
  url: string;
  publisher: string;
  published: string;
}

export interface Research {
  brief: string;
  sources: ResearchSource[];
  grounded: boolean;
  notes: string;
}

export interface UpdateScriptRequest {
  script: Script;
}

export interface Job {
  id: string;
  topic: string;
  settings: JobSettings;
  status: JobStatus;
  step: string;
  progress: number;
  script?: Script | null;
  seo?: Seo | null;
  research?: Research | null;
  error?: string | null;
  video_ready: boolean;
  created_at: string;
}

export type VoiceProvider = "heygen" | "fish" | "edge";

export interface VoiceOption {
  id: string;
  provider: VoiceProvider;
  provider_label: string;
  name: string;
  gender: "male" | "female" | "unknown";
  experimental: boolean;
}

export interface VoicesResponse {
  voices: VoiceOption[];
  default_id: string;
}

export interface VoicePreviewRequest {
  voice_id: string;
  language: Language;
}

export interface RenderRequest {
  voice_id?: string;
  reuse_media?: boolean;
}
